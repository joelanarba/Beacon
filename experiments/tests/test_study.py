from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from experiments.analyze import paired_summary, summarize_trial
from experiments.protocol import (
    Settings,
    digest,
    evidence_hashes,
    plan,
    trace,
    trials,
    write_json,
)
from experiments.run import freeze, main, read_frozen, validate_calibration
from experiments.services import compose_config


@pytest.mark.parametrize(
    "kwargs",
    [
        {"reports": 0},
        {"reports": 42},
        {"reports": 401},
        {"reports": True},
        {"reports": 160.0},
        {"interval_ms": float("nan")},
        {"interval_ms": -1},
        {"dispatch_delay_ms": float("inf")},
        {"dispatch_delay_ms": 201},
        {"concurrency": 17},
        {"drain_seconds": 0},
        {"outage_hold_seconds": 0},
        {"order_seed": -1},
        {"database_sample_seconds": 0},
    ],
)
def test_rejects_invalid_or_changed_protocol(kwargs):
    with pytest.raises(ValueError):
        Settings(**kwargs)


def test_every_pair_uses_identical_trace_and_balanced_classes():
    settings = Settings()
    runs = trials(settings, calibration=False)
    assert len(runs) == 40
    for index in range(0, 40, 2):
        first, second = runs[index : index + 2]
        assert first["trace_sha256"] == second["trace_sha256"]
        assert {first["policy"], second["policy"]} == {"equal", "severity"}
        body = trace(settings, first["seed"])
        assert sum(r["body"]["severity"] == "CRITICAL" for r in body) == 40
        assert len({r["identity"] for r in body}) == 160
    assert {r["policy"] for r in runs[::2]} == {"equal", "severity"}
    assert {r["seed"] for r in trials(settings, calibration=True)} == {0}


def test_dry_run_never_uses_docker_or_overwrites_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "shutil.which", lambda _: pytest.fail("Dry run tried service discovery")
    )
    directory = tmp_path / "preview"
    assert main(["dry-run", "--output", str(directory)]) == 0
    initial = (directory / "result.json").read_bytes()
    assert json.loads(initial)["status"] == "planned"
    assert main(["dry-run", "--output", str(directory)]) == 2
    assert (directory / "result.json").read_bytes() == initial


def test_missing_docker_is_nonzero_with_all_trials_not_run(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    directory = tmp_path / "blocked"
    assert main(["calibrate", "--output", str(directory)]) == 1
    result = json.loads((directory / "result.json").read_text())
    assert result["status"] == "failed" and result["not_run"] == 4
    assert not result["results"]
    with pytest.raises(ValueError, match="passing real-service calibration"):
        freeze(directory, tmp_path / "frozen.json")


def test_frozen_parameters_cannot_be_edited_or_forged_by_dry_run(tmp_path):
    path = tmp_path / "frozen.json"
    write_json(path, {"payload": {}, "sha256": "changed"})
    with pytest.raises(ValueError, match="Frozen parameters"):
        read_frozen(path)


def test_study_config_retains_private_ownership_and_excludes_production_hooks():
    images = {
        s: "sha256:" + "a" * 64
        for s in ("app", "migrate", "postgres", "redis", "rabbitmq")
    }
    config = compose_config("equal", Settings(), images)
    for service in config["services"].values():
        assert "com.beacon.integration.run" in service["labels"]
        assert service["mem_limit"] and service["cpus"]
        assert not service.get("env_file")
        assert all(port.startswith("127.0.0.1::") for port in service.get("ports", []))
    assert all(not item.get("external") for item in config["volumes"].values())
    assert all(not item.get("external") for item in config["networks"].values())
    assert config["services"]["app"]["environment"]["BEACON_STUDY_POLICY"] == "equal"
    assert "experiments" in config["configs"]["integration-entry"]["file"]


@pytest.mark.parametrize("policy,expected", [("equal", 0), ("severity", 10)])
async def test_policy_preserves_payload_retry_identity_and_durability(
    policy, expected, monkeypatch
):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("SECRET_KEY", "synthetic-study-only-key-with-adequate-length")
    from events import outbox
    from events.bus import EventBus
    from experiments.worker_entry import policy_hooks
    from incidents import acceptance

    payload = {"severity": "CRITICAL", "incident_id": "synthetic"}
    session = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())
    published = AsyncMock()
    monkeypatch.setattr(EventBus, "publish", published)
    original = acceptance.enqueue_event
    with policy_hooks(policy, 0) as state:
        await acceptance.enqueue_event(
            session, "incident.reported", payload, event_id="stable", priority=10
        )
        first = session.execute.call_args.args[0].compile().params
        assert first["priority"] == expected and first["payload"] == payload
        session.commit.assert_not_awaited()
        message = SimpleNamespace(
            message_id="stable",
            priority=10,
            routing_key="incident.reported",
            body=json.dumps(payload).encode(),
        )
        await outbox.record_failure(
            session, message, "dispatch", RuntimeError("transient"), 0
        )
        retry = session.execute.call_args.args[0].compile().params
        assert retry["priority"] == expected and retry["delivery_attempt"] == 1
        assert retry["payload"] == payload and retry["event_id"] == "stable"
        session.commit.assert_awaited_once()
        bus = EventBus("unused", "beacon.events", 10)
        assert state["prefetch"] == bus._prefetch == 1
        await bus.publish(
            "incident.reported", payload, priority=10, message_id="stable"
        )
        assert published.call_args.args[3] == expected
    assert acceptance.enqueue_event is original
    assert payload["severity"] == "CRITICAL"


def evidence(
    directory, *, policy="equal", pending=False, backlog=True, settings=None, seed=1
):
    directory.mkdir(parents=True)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    reports, outbox, deliveries = [], [], []
    workload = (
        trace(settings, seed)
        if settings
        else [
            {"index": i, "identity": str(i), "body": {"severity": severity}}
            for i, severity in enumerate(
                ["CRITICAL", "STANDARD", "STANDARD", "STANDARD"]
            )
        ]
    )
    for scheduled in workload:
        index, severity = scheduled["index"], scheduled["body"]["severity"]
        completed = not (pending and index == 0)
        reports.append(
            {
                "index": index,
                "identity": scheduled["identity"],
                "http_status": 201,
                "acknowledged_incident_id": str(index),
                "body": scheduled["body"],
                "scheduling_drift_seconds": 0,
                "durable": {
                    "id": str(index),
                    "severity": severity,
                    "status": "ASSIGNED" if completed else "REPORTED",
                    "assignment_count": int(completed),
                    "created_at": start.isoformat(),
                    "assigned_at": (
                        (start + timedelta(seconds=index + 1)).isoformat()
                        if completed
                        else None
                    ),
                },
            }
        )
        event = f"incident.reported:{index}"
        priority = 0 if policy == "equal" else (10 if severity == "CRITICAL" else 1)
        outbox.append(
            {
                "event_id": event,
                "routing_key": "incident.reported",
                "priority": priority,
                "payload_severity": severity,
            }
        )
        deliveries.append({"event_id": event, "priority": priority})
    write_json(directory / "reports.json", reports)
    write_json(
        directory / "trace.json",
        workload,
    )
    write_json(directory / "outbox_full.json", outbox)
    write_json(
        directory / "launcher.json",
        {"policy": policy, "prefetch": 1, "deliveries": deliveries},
    )
    (directory / "database_samples.jsonl").write_text(
        json.dumps(
            {
                "reported_critical": int(backlog),
                "reported_standard": 3,
                "elapsed": 0,
                "assignments": 0,
                "durable_incidents": len(workload),
                "pending_outbox": len(workload),
            }
        )
        + "\n"
    )
    (directory / "service_samples.jsonl").write_text(
        json.dumps(
            {"queues": [{"messages_ready": 3}], "resources": [{"CPUPerc": "1%"}]}
        )
        + "\n"
    )
    return {
        "trial": {
            "trial": "synthetic",
            "policy": policy,
            "condition": "healthy",
            "seed": seed,
        },
        "durable_incidents": len(workload),
        "completion_elapsed": None if pending else len(workload) + 1,
        "invariants_passed": True,
        "events": [],
        "phase": "measured",
        "cleanup": True,
    }


def test_censored_reports_keep_denominators_and_suppress_completed_only_percentiles(
    tmp_path,
):
    result = evidence(tmp_path / "trial", pending=True)
    summary = summarize_trial(tmp_path / "trial", result, 4)
    assert summary["status"] == "invalid"
    assert summary["unresolved"] == 1
    assert summary["classes"]["CRITICAL"] == {
        "submitted": 1,
        "completed": 0,
        "censored": 1,
        "median_seconds": None,
        "p95_seconds": None,
    }
    assert "not proven lost" in " ".join(summary["invalid_reasons"])


def test_late_durable_completion_does_not_erase_deadline_censoring(tmp_path):
    result = evidence(tmp_path / "trial")
    result["completion_elapsed"] = None
    path = tmp_path / "trial/reports.json"
    reports = json.loads(path.read_text())
    reports[0]["assignment_observed_by_deadline"] = False
    path.write_text(json.dumps(reports))
    summary = summarize_trial(tmp_path / "trial", result, 4)
    assert summary["final_completed"] == 4 and summary["completed"] == 3
    assert summary["completion_censored"] is True
    assert summary["classes"]["CRITICAL"]["p95_seconds"] is None


def test_no_backlog_or_wrong_outbox_priority_invalidates_a_scheduling_claim(tmp_path):
    result = evidence(tmp_path / "trial", backlog=False)
    assert summarize_trial(tmp_path / "trial", result, 4)["status"] == "invalid"
    path = tmp_path / "trial" / "outbox_full.json"
    rows = json.loads(path.read_text())
    rows[0]["priority"] = 10
    path.write_text(json.dumps(rows))
    summary = summarize_trial(tmp_path / "trial", result, 4)
    assert any(
        "Persisted outbox priorities" in reason for reason in summary["invalid_reasons"]
    )


def test_paired_analysis_uses_seed_pairs_and_excludes_failed_cleanup(tmp_path):
    results = []
    for seed in (1, 2, 3):
        for policy in ("equal", "severity"):
            directory = tmp_path / f"{seed}-{policy}"
            result = evidence(directory, policy=policy)
            result["trial"]["seed"] = seed
            result.update(summarize_trial(directory, result, 4))
            if seed == 3:
                result["cleanup"] = False
            results.append(result)
    pairs, intervals = paired_summary(results)
    assert len(pairs) == 8
    healthy = [r for r in intervals if r["condition"] == "healthy"]
    assert all(r["eligible_pairs"] == 2 for r in healthy)
    assert all(r["bootstrap_percentile_95_seconds"] == [0, 0] for r in healthy)
    assert not any(r["seed"] == 3 for r in pairs)


def test_expected_plan_records_source_and_does_not_mix_calibration_seeds():
    measured = plan(Settings(), calibration=False)
    calibration = plan(Settings(), calibration=True)
    assert measured["source"] == calibration["source"]
    assert len(measured["trials"]) == 40 and len(calibration["trials"]) == 4
    assert digest(measured) == digest(plan(Settings(), calibration=False))


def test_freeze_rechecks_all_raw_trials_and_cleanup_then_detects_changed_data(tmp_path):
    settings = Settings(reports=40)
    record = {
        "status": "passed",
        "plan": plan(settings, calibration=True),
        "results": [],
        "warmups": [],
        "hardware": {"synthetic": True},
        "images": {
            s: "sha256:" + "a" * 64
            for s in ("app", "migrate", "postgres", "redis", "rabbitmq")
        },
        "controller_packages_sha256": "synthetic",
    }
    for i, spec in enumerate(record["plan"]["trials"]):
        for warmup in (True, False):
            name = "beacon-it-" + f"{i*2+int(warmup):032x}"
            directory = tmp_path / name
            result = evidence(directory, policy=spec["policy"])
            # Replicate the explicit synthetic fixture to the protocol count.
            reports = json.loads((directory / "reports.json").read_text())
            full = []
            count = 8 if warmup else settings.reports
            for j, scheduled in enumerate(trace(settings, spec["seed"], warmup=warmup)):
                item = json.loads(json.dumps(reports[j % 4]))
                item.update(
                    index=j,
                    identity=scheduled["identity"],
                    body=scheduled["body"],
                    acknowledged_incident_id=str(j),
                )
                item["durable"]["id"] = str(j)
                item["durable"]["severity"] = scheduled["body"]["severity"]
                full.append(item)
            (directory / "reports.json").write_text(json.dumps(full))
            (directory / "trace.json").write_text(
                json.dumps(trace(settings, spec["seed"], warmup=warmup))
            )
            outbox = [
                {
                    "event_id": f"incident.reported:{r['index']}",
                    "routing_key": "incident.reported",
                    "payload_severity": r["body"]["severity"],
                    "priority": (
                        0
                        if spec["policy"] == "equal"
                        else (10 if r["body"]["severity"] == "CRITICAL" else 1)
                    ),
                }
                for r in full
            ]
            (directory / "outbox_full.json").write_text(json.dumps(outbox))
            launcher = {
                "policy": spec["policy"],
                "prefetch": 1,
                "deliveries": [
                    {"event_id": o["event_id"], "priority": o["priority"]}
                    for o in outbox
                ],
            }
            (directory / "launcher.json").write_text(json.dumps(launcher))
            result.update(
                trial=spec,
                project=name,
                phase="warmup" if warmup else "measured",
                durable_incidents=count,
                events=[{"event": "broker_healthy", "elapsed": 1}],
            )
            result.update(summarize_trial(directory, result, count, warmup=warmup))
            result["evidence_sha256"] = evidence_hashes(directory)
            record["warmups" if warmup else "results"].append(result)
    write_json(tmp_path / "result.json", record)
    assert validate_calibration(tmp_path)["status"] == "passed"
    freeze(tmp_path, tmp_path / "frozen.json")
    assert read_frozen(tmp_path / "frozen.json")["settings"]["reports"] == 40
    first = record["results"][0]
    (tmp_path / first["project"] / "reports.json").write_text("[]")
    with pytest.raises(ValueError, match="evidence changed"):
        read_frozen(tmp_path / "frozen.json")


async def test_trial_captures_and_reports_cleanup_failure(tmp_path, monkeypatch):
    from experiments import services

    calls = []

    class BrokenStack:
        def __init__(self, run, settings, trial, images, **kwargs):
            self.project = "beacon-it-" + "a" * 32
            self.artifacts = tmp_path / self.project
            self.artifacts.mkdir()
            self.workload = trace(settings, trial["seed"])

        async def prepare(self):
            raise RuntimeError("synthetic setup failure")

        async def capture(self):
            calls.append("capture")

        def cleanup(self):
            calls.append("cleanup")
            raise RuntimeError("synthetic teardown failure")

    monkeypatch.setattr(services, "StudyStack", BrokenStack)
    run = SimpleNamespace(record=lambda row: None)
    result = await services.trial(
        run, Settings(), trials(Settings(), calibration=True)[0], {}
    )
    assert calls == ["capture", "cleanup"]
    assert result["status"] == "failed" and result["cleanup"] is False
    assert "synthetic teardown failure" in result["cleanup_error"]
    assert (tmp_path / result["project"] / "result.json").exists()


async def test_request_driver_preserves_rejections_and_threshold_without_live_http(
    tmp_path, monkeypatch
):
    from experiments.services import submit_trace

    real_client = httpx.AsyncClient
    identities = []

    def respond(request):
        index = int(request.headers["Idempotency-Key"].split(":")[-1])
        identities.append(index)
        if index == 3:
            return httpx.Response(422, json={"detail": "synthetic rejection"})
        return httpx.Response(201, json={"incident_id": f"{index:032x}"})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(respond)),
    )
    settings = Settings(reports=40, interval_ms=0)
    stack = SimpleNamespace(
        settings=settings,
        url="http://127.0.0.1:1",
        workload=trace(settings, 0),
        artifacts=tmp_path,
    )
    threshold = asyncio.Event()
    rows = await submit_trace(stack, time.monotonic(), threshold)
    assert len(rows) == 40 and len(set(identities)) == 40
    assert threshold.is_set()
    assert rows[3]["http_status"] == 422
    assert sum(r["acknowledged_incident_id"] is not None for r in rows) == 39
    assert all(
        r["request_seconds"] >= 0 and r["scheduling_drift_seconds"] >= 0 for r in rows
    )


async def test_service_sampler_awaits_real_queue_query_off_the_scheduler(tmp_path):
    from experiments.services import sample_loop

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    async def queues():
        await asyncio.sleep(0)
        return [
            {
                "name": "dispatch.incoming",
                "messages_ready": 3,
                "messages_unacknowledged": 1,
                "consumers": 1,
            }
        ]

    def resources():
        loop.call_soon_threadsafe(stop.set)
        return [{"CPUPerc": "1%"}]

    stack = SimpleNamespace(
        settings=Settings(), artifacts=tmp_path, queues=queues, resource_stats=resources
    )
    await sample_loop(stack, stop, time.monotonic(), services=True)
    sample = json.loads((tmp_path / "service_samples.jsonl").read_text())
    assert sample["queues"][0]["messages_ready"] == 3
    assert sample["queues"][0]["messages_unacknowledged"] == 1
    assert sample["resources"] == [{"CPUPerc": "1%"}]
    assert not any(key.endswith("_error") for key in sample)


def test_artifact_analysis_renders_and_does_not_turn_failed_cleanup_into_success(
    tmp_path, monkeypatch
):
    from experiments.analyze import main as analyze

    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib-cache"))
    project = "beacon-it-" + "a" * 32
    directory = tmp_path / "raw" / project
    settings = Settings(reports=40)
    spec = trials(settings, calibration=True)[0]
    result = evidence(
        directory, policy=spec["policy"], settings=settings, seed=spec["seed"]
    )
    result["trial"] = spec
    result.update(summarize_trial(directory, result, settings.reports))
    result.update(
        project=project,
        cleanup=False,
        status="failed",
        evidence_sha256=evidence_hashes(directory),
    )
    manifest = {
        "status": "failed",
        "synthetic_fixture": True,
        "plan": plan(settings, calibration=True),
        "results": [result],
    }
    write_json(directory.parent / "result.json", manifest)
    output = tmp_path / "analysis"
    assert analyze([str(directory.parent), "--output", str(output)]) == 1
    summary = json.loads((output / "summary.json").read_text())
    assert summary["not_run"] == 3 and not summary["complete_trial_collection"]
    assert summary["trials"][0]["status"] == "failed"
    assert (output / "class_ecdf.png").stat().st_size > 1000
    assert "SYNTHETIC OFFLINE FIXTURE" in (output / "class_ecdf.svg").read_text()
