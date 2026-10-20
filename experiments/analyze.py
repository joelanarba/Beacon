"""Regenerate tables and figures using retained artifacts only; never use Docker."""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import statistics
from datetime import datetime
from pathlib import Path

from experiments.protocol import (
    CONDITIONS,
    POLICIES,
    Settings,
    digest,
    fingerprint,
    trace,
    trials,
    verify_evidence,
    write_json,
)


def load_lines(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def percentile(values, fraction):
    """Linear interpolation between sorted order statistics (including n=1)."""
    values = sorted(values)
    if not values:
        return None
    position = (len(values) - 1) * fraction
    low = int(position)
    return values[low] + (values[min(low + 1, len(values) - 1)] - values[low]) * (
        position - low
    )


def duration(row):
    durable = row.get("durable") or {}
    if row.get("assignment_observed_by_deadline") is False:
        return None
    if durable.get("assigned_at") is None:
        return None
    return (
        datetime.fromisoformat(str(durable["assigned_at"]))
        - datetime.fromisoformat(str(durable["created_at"]))
    ).total_seconds()


def summarize_trial(directory, result, expected, *, warmup=False):
    reports = json.loads((directory / "reports.json").read_text())
    outbox = json.loads((directory / "outbox_full.json").read_text())
    launcher = json.loads((directory / "launcher.json").read_text())
    samples = load_lines(directory / "database_samples.jsonl")
    services = load_lines(directory / "service_samples.jsonl")
    reasons = []
    expected_trace = json.loads((directory / "trace.json").read_text())
    if [(r["index"], r["identity"], r["body"]) for r in reports] != [
        (r["index"], r["identity"], r["body"]) for r in expected_trace
    ]:
        reasons.append("Submitted reports differ from the saved paired trace")
    if len(reports) != expected or result["durable_incidents"] != expected:
        reasons.append("Submitted/durable report counts differ from the trace")
    if not result.get("invariants_passed") or result.get("deadletter_count", 0):
        reasons.append("Assignment/resource invariants or dead-letter check failed")
    ids = [r.get("acknowledged_incident_id") for r in reports]
    http_errors = sum(
        r.get("http_status") != 201 or not r.get("acknowledged_incident_id")
        for r in reports
    )
    if http_errors or len(set(ids)) != len(reports):
        reasons.append("HTTP acceptance failed or returned duplicate incident IDs")
    if any(
        not r.get("durable")
        or r["durable"]["id"] != r.get("acknowledged_incident_id")
        or r["durable"]["severity"] != r["body"]["severity"]
        for r in reports
    ):
        reasons.append("HTTP identity/severity did not reconcile with durable records")
    final_completed = sum(
        (r.get("durable") or {}).get("assignment_count") == 1 for r in reports
    )
    completed = sum(
        (r.get("durable") or {}).get("assignment_count") == 1
        and r.get("assignment_observed_by_deadline", True)
        for r in reports
    )
    if completed != expected or result.get("completion_elapsed") is None:
        reasons.append(
            "Unfinished work at the observation deadline (censored, not proven lost)"
        )
    if any(duration(r) is not None and duration(r) < 0 for r in reports):
        reasons.append("Negative database record-to-assignment duration")
    if any((r.get("durable") or {}).get("assignment_count", 0) > 1 for r in reports):
        reasons.append("Duplicate assignments")
    if any((r.get("durable") or {}).get("status") != "ASSIGNED" for r in reports):
        reasons.append("Final incident status is not ASSIGNED")

    policy = result["trial"]["policy"]
    priorities = {"CRITICAL": 10, "STANDARD": 1}
    if not outbox or any(
        e["priority"]
        != (0 if policy == "equal" else priorities.get(e["payload_severity"]))
        for e in outbox
    ):
        reasons.append("Persisted outbox priorities do not match the assigned policy")
    by_id = {e["event_id"]: e for e in outbox}
    deliveries = launcher.get("deliveries", [])
    if not deliveries or any(
        d["event_id"] not in by_id or d["priority"] != by_id[d["event_id"]]["priority"]
        for d in deliveries
    ):
        reasons.append(
            "Observed AMQP delivery priorities do not match persisted priorities"
        )
    reported = {
        e["event_id"] for e in outbox if e["routing_key"] == "incident.reported"
    }
    if len(reported) != expected or not reported <= {d["event_id"] for d in deliveries}:
        reasons.append("Missing report events or dispatch delivery observations")
    if launcher.get("policy") != policy or launcher.get("prefetch") != 1:
        reasons.append("Launcher policy/prefetch mismatch")
    if not samples or any("error" in sample for sample in samples):
        reasons.append("Database sampling is missing or failed")
    if not any(
        isinstance(s.get("queues"), list) and s["queues"] for s in services
    ) or not any(
        isinstance(s.get("resources"), list) and s["resources"] for s in services
    ):
        reasons.append("Live queue/resource sampling is missing")
    backlog = any(
        s.get("reported_critical", 0) > 0 and s.get("reported_standard", 0) > 0
        for s in samples
    )
    if not backlog and not warmup:
        reasons.append("No sampled backlog containing both severity classes")
    healthy = [
        e["elapsed"] for e in result.get("events", []) if e["event"] == "broker_healthy"
    ]
    stop_times = [
        e["elapsed"] for e in result.get("events", []) if e["event"] == "stop_requested"
    ]
    for sample in services:
        if "resources_error" in sample:
            reasons.append("Resource sampling failed")
            break
        if "queues_error" in sample and not (
            stop_times
            and healthy
            and sample["elapsed"] <= healthy[0]
            and sample["elapsed"] + sample["sample_seconds"] >= stop_times[0]
        ):
            reasons.append("Live queue sampling failed outside the injected outage")
            break
    if result["trial"]["condition"] == "broker_outage" and not warmup and not healthy:
        reasons.append("Required broker interruption/recovery was not observed")
    classes = {}
    for severity in priorities:
        members = [r for r in reports if r["body"]["severity"] == severity]
        values = [duration(r) for r in members if duration(r) is not None]
        censored = len(members) - len(values)
        classes[severity] = {
            "submitted": len(members),
            "completed": len(values),
            "censored": censored,
            "median_seconds": (
                statistics.median(values) if values and not censored else None
            ),
            "p95_seconds": percentile(values, 0.95) if not censored else None,
        }
    return {
        "status": "passed" if not reasons else "invalid",
        "invalid_reasons": reasons,
        "classes": classes,
        "submitted": len(reports),
        "acknowledged": len(reports) - http_errors,
        "http_failed": http_errors,
        "durable_without_ack": sum(
            bool(r.get("durable")) and not r.get("acknowledged_incident_id")
            for r in reports
        ),
        "durable_pending": sum(
            (r.get("durable") or {}).get("status") == "REPORTED" for r in reports
        ),
        "dead_lettered": result.get("deadletter_count", 0),
        "completed": completed,
        "final_completed": final_completed,
        "final_unresolved": expected - final_completed,
        "unresolved": expected - completed,
        "completion_censored": completed != expected,
        "observed_mixed_backlog": backlog,
        "max_scheduling_drift_seconds": max(
            (r["scheduling_drift_seconds"] for r in reports), default=None
        ),
        "recovery_seconds": (
            max(0, result["completion_elapsed"] - healthy[0])
            if healthy and result.get("completion_elapsed") is not None
            else None
        ),
        "recovery_censored": bool(healthy and result.get("completion_elapsed") is None),
        "completed_before_broker_health_observation": bool(
            healthy
            and result.get("completion_elapsed") is not None
            and result["completion_elapsed"] < healthy[0]
        ),
        "queue_resource_sample_errors": sum(
            any(k.endswith("_error") for k in s) for s in services
        ),
    }


def paired_summary(results):
    pairs, intervals = [], []
    for condition in CONDITIONS:
        seeds = sorted(
            {
                r["trial"]["seed"]
                for r in results
                if r["trial"]["condition"] == condition
            }
        )
        for severity in ("CRITICAL", "STANDARD"):
            for metric in ("median_seconds", "p95_seconds"):
                values = []
                for seed in seeds:
                    matched = {
                        r["trial"]["policy"]: r
                        for r in results
                        if r["trial"]["seed"] == seed
                        and r["trial"]["condition"] == condition
                    }
                    if set(matched) != set(POLICIES) or any(
                        r["status"] != "passed" or not r.get("cleanup")
                        for r in matched.values()
                    ):
                        continue
                    delta = (
                        matched["severity"]["classes"][severity][metric]
                        - matched["equal"]["classes"][severity][metric]
                    )
                    values.append(delta)
                    pairs.append(
                        {
                            "condition": condition,
                            "seed": seed,
                            "severity": severity,
                            "metric": metric,
                            "severity_minus_equal_seconds": delta,
                        }
                    )
                rng = random.Random(20261020)
                bootstrap = (
                    sorted(
                        statistics.mean(rng.choices(values, k=len(values)))
                        for _ in range(10000)
                    )
                    if len(values) >= 2
                    else []
                )
                intervals.append(
                    {
                        "condition": condition,
                        "severity": severity,
                        "metric": metric,
                        "eligible_pairs": len(values),
                        "mean_paired_difference_seconds": (
                            statistics.mean(values) if values else None
                        ),
                        "bootstrap_percentile_95_seconds": (
                            [percentile(bootstrap, p) for p in (0.025, 0.975)]
                            if bootstrap
                            else None
                        ),
                    }
                )
    return pairs, intervals


def write_csv(path, rows):
    if not rows:
        return
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def plots(output, results, root, label):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {"equal": "#335f9f", "severity": "#be522f"}
    fig, axes = plt.subplots(2, 2, figsize=(10, 7), constrained_layout=True)
    for row, condition in enumerate(CONDITIONS):
        for col, severity in enumerate(("CRITICAL", "STANDARD")):
            axis = axes[row, col]
            for result in results:
                if result["trial"]["condition"] != condition:
                    continue
                path = root / result["project"] / "reports.json"
                if not path.exists():
                    continue
                reports = json.loads(path.read_text())
                members = [r for r in reports if r["body"]["severity"] == severity]
                values = sorted(duration(r) for r in members if duration(r) is not None)
                if values:
                    axis.step(
                        [0, *values],
                        [0, *[i / len(members) for i in range(1, len(values) + 1)]],
                        where="post",
                        color=colors[result["trial"]["policy"]],
                        alpha=0.5,
                        linestyle="-" if result["status"] == "passed" else ":",
                    )
            axis.set(
                title=f"{condition}: {severity}",
                xlabel="Record-to-assignment seconds",
                ylabel="Assigned / submitted in class",
                ylim=(0, 1.02),
            )
            if not axis.lines:
                axis.text(
                    0.5, 0.5, "No observations", ha="center", transform=axis.transAxes
                )
    fig.suptitle(
        label + "\n"
        "One line per trial; dotted = invalid/censored; blue = equal, orange = severity"
    )
    fig.savefig(output / "class_ecdf.svg")
    fig.savefig(output / "class_ecdf.png", dpi=120)
    plt.close(fig)
    pairs, _ = paired_summary(results)
    fig, axis = plt.subplots(figsize=(9, 5), constrained_layout=True)
    for i, (condition, severity) in enumerate(
        (c, s) for c in CONDITIONS for s in ("CRITICAL", "STANDARD")
    ):
        values = [
            p["severity_minus_equal_seconds"]
            for p in pairs
            if p["condition"] == condition
            and p["severity"] == severity
            and p["metric"] == "p95_seconds"
        ]
        axis.scatter([i] * len(values), values)
    axis.axhline(0, color="black", linewidth=0.8)
    axis.set(
        xticks=range(4),
        xticklabels=[f"{c}\n{s}" for c in CONDITIONS for s in ("CRITICAL", "STANDARD")],
        ylabel="Paired p95 difference, severity minus equal (s)",
        title=label + "\nOne point per eligible paired trace seed",
    )
    fig.savefig(output / "paired_p95.svg")
    plt.close(fig)
    fig, axes = plt.subplots(
        max(1, len(results)),
        1,
        figsize=(10, max(3, 1.6 * len(results))),
        squeeze=False,
        constrained_layout=True,
    )
    for axis, result in zip(axes[:, 0], results, strict=False):
        path = root / result["project"] / "database_samples.jsonl"
        if path.exists():
            samples = load_lines(path)
            for field in ("durable_incidents", "assignments", "pending_outbox"):
                axis.plot(
                    [s["elapsed"] for s in samples],
                    [s.get(field, float("nan")) for s in samples],
                    label=field,
                )
        for event in result.get("events", []):
            axis.axvline(event["elapsed"], color="grey", alpha=0.5)
        axis.set(title=result["trial"]["trial"], xlabel="Controller elapsed seconds")
    if results:
        axes[0, 0].legend(fontsize=7)
    fig.suptitle(label + "\nVertical lines mark recorded fault events")
    fig.savefig(output / "recovery_timelines.svg")
    plt.close(fig)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = json.loads((args.run / "result.json").read_text())
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    results = manifest.get("results", [])
    rows, report_rows, class_rows = [], [], []
    for recorded in results:
        if not re.fullmatch(r"beacon-it-[a-f0-9]{32}", recorded["project"]):
            raise ValueError("Invalid study project directory")
        directory = args.run / recorded["project"]
        result = dict(recorded)
        try:
            verify_evidence(directory, result)
            if json.loads((directory / "trace.json").read_text()) != trace(
                Settings(**manifest["plan"]["settings"]), result["trial"]["seed"]
            ):
                raise ValueError("Saved trace does not match its paired seed/settings")
            if (directory / "reports.json").exists() and "classes" in result:
                result.update(
                    summarize_trial(
                        directory, result, manifest["plan"]["settings"]["reports"]
                    )
                )
            if (
                recorded["status"] != "passed"
                or not result.get("cleanup")
                or result.get("background_errors")
            ):
                result["status"] = "failed"
        except (OSError, ValueError, KeyError) as exc:
            result["status"] = "invalid"
            result.setdefault("invalid_reasons", []).append(str(exc))
        rows.append(result)
        for severity, metrics in result.get("classes", {}).items():
            class_rows.append(
                {
                    "trial": result["trial"]["trial"],
                    "policy": result["trial"]["policy"],
                    "condition": result["trial"]["condition"],
                    "severity": severity,
                    "status": result["status"],
                    "cleanup": result.get("cleanup"),
                    **metrics,
                }
            )
        path = directory / "reports.json"
        if path.exists():
            for report in json.loads(path.read_text()):
                report_rows.append(
                    {
                        "trial": result["trial"]["trial"],
                        "policy": result["trial"]["policy"],
                        "severity": report["body"]["severity"],
                        "identity": report["identity"],
                        "http_status": report["http_status"],
                        "record_to_assignment_seconds": duration(report),
                    }
                )
    pairs, intervals = paired_summary(rows)
    expected = trials(
        Settings(**manifest["plan"]["settings"]),
        calibration=manifest["plan"]["phase"] == "calibration",
    )
    complete = [r["trial"] for r in rows] == expected
    write_json(
        output / "summary.json",
        {
            "phase": manifest["plan"]["phase"],
            "source_manifest_sha256": digest(manifest),
            "analysis_source": fingerprint(),
            "run_status": manifest["status"],
            "complete_trial_collection": complete,
            "not_run": len(expected) - len(rows),
            "trials": rows,
            "paired_intervals": intervals,
            "method": (
                "10,000 deterministic bootstrap resamples of paired seeds; "
                "exploratory percentile interval for mean difference"
            ),
            "limitations": (
                "Incidents are not independent repetitions. Censored classes have "
                "no median/p95 estimate. Invalid trials remain visible and are "
                "excluded from paired contrasts."
            ),
        },
    )
    write_csv(output / "reports.csv", report_rows)
    write_csv(output / "trial_classes.csv", class_rows)
    write_csv(output / "paired_differences.csv", pairs)
    label = "SYNTHETIC OFFLINE FIXTURE; " if manifest.get("synthetic_fixture") else ""
    label += f"{manifest['plan']['phase']}; run status={manifest['status']}"
    plots(output, rows, args.run, label)
    print(
        f"Analysis written: {output}; phase={manifest['plan']['phase']}, "
        f"status={manifest['status']}"
    )
    return (
        0
        if complete
        and manifest["status"] == "passed"
        and all(r["status"] == "passed" and r.get("cleanup") for r in rows)
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
