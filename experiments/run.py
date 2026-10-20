"""Plan, calibrate, freeze, then run forty disposable scheduling trials."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import os
import platform
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from experiments.protocol import (
    Settings,
    digest,
    fingerprint,
    plan,
    trace,
    verify_evidence,
    write_json,
)


def save_manifest(directory, data):
    temporary = directory / "result.next.json"
    write_json(temporary, data)
    temporary.replace(directory / "result.json")


def validate_calibration(directory):
    from experiments.analyze import summarize_trial

    record = json.loads((directory / "result.json").read_text())
    if record["status"] != "passed" or record["plan"]["phase"] != "calibration":
        raise ValueError("A passing real-service calibration is required")
    settings = Settings(**record["plan"]["settings"])
    if record["plan"] != plan(settings, calibration=True):
        raise ValueError("Calibration source, parameters, or trial plan changed")
    expected = record["plan"]["trials"]
    results = record.get("results", [])
    warmups = record.get("warmups", [])
    if [r["trial"] for r in results] != expected or [
        r["trial"] for r in warmups
    ] != expected:
        raise ValueError(
            "Calibration must include all four trials and separate warmups"
        )
    for result in [*warmups, *results]:
        if (
            result["status"] != "passed"
            or not result.get("cleanup")
            or result.get("background_errors")
        ):
            raise ValueError("Calibration correctness/cleanup gate failed")
        directory_name = result["project"]
        if not re.fullmatch(r"beacon-it-[a-f0-9]{32}", directory_name):
            raise ValueError("Invalid calibration project")
        verify_evidence(directory / directory_name, result)
        saved_trace = json.loads(
            (directory / directory_name / "trace.json").read_text()
        )
        if saved_trace != trace(
            settings, result["trial"]["seed"], warmup=result["phase"] == "warmup"
        ):
            raise ValueError(
                "Calibration trace does not match its paired seed/settings"
            )
        checked = summarize_trial(
            directory / directory_name,
            result,
            8 if result["phase"] == "warmup" else settings.reports,
            warmup=result["phase"] == "warmup",
        )
        if checked["status"] != "passed":
            raise ValueError(
                f"Raw calibration evidence does not pass: {checked['invalid_reasons']}"
            )
    return record


def freeze(directory, output):
    record = validate_calibration(directory)
    payload = {
        "schema": 1,
        "calibration": str(directory.resolve()),
        "calibration_sha256": digest(record),
        "settings": record["plan"]["settings"],
        "source": record["plan"]["source"],
        "images": record["images"],
        "hardware": record["hardware"],
        "controller_packages_sha256": record["controller_packages_sha256"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, {"payload": payload, "sha256": digest(payload)})


def read_frozen(path):
    record = json.loads(path.read_text())
    payload = record["payload"]
    if record["sha256"] != digest(payload) or payload["source"] != fingerprint():
        raise ValueError("Frozen parameters or source changed; repeat calibration")
    Settings(**payload["settings"])
    if set(payload["images"]) != {
        "app",
        "migrate",
        "postgres",
        "redis",
        "rabbitmq",
    } or any(
        not re.fullmatch(r"sha256:[a-f0-9]{64}", value)
        for value in payload["images"].values()
    ):
        raise ValueError("Freeze must reference exact local Docker image IDs")
    source = Path(payload["calibration"])
    calibration = validate_calibration(source)
    if digest(calibration) != payload["calibration_sha256"]:
        raise ValueError("Calibration evidence changed after freezing")
    return payload


def hardware(run):
    data = run.run(
        [
            "docker",
            "info",
            "--format",
            "{{json .NCPU}} {{json .MemTotal}} {{json .Driver}}",
        ]
    ).stdout.strip()
    return {
        "docker_cpu_memory_driver": data,
        "controller_cpus": os.cpu_count(),
        "codespace": os.environ.get("CODESPACE_NAME"),
        "platform": platform.platform(),
    }


async def execute(directory, manifest, frozen=None):
    from integration_tests.stack import DockerRun

    from experiments.services import trial

    settings = Settings(**manifest["plan"]["settings"])
    run = DockerRun(directory)
    run.preflight()
    if run.run(["git", "status", "--porcelain"]).stdout.strip():
        raise RuntimeError(
            "Calibration/trials require a clean checkout. Preserve local changes first."
        )
    manifest["hardware"] = hardware(run)
    packages = sorted(
        [
            {"name": d.metadata["Name"], "version": d.version}
            for d in importlib.metadata.distributions()
        ],
        key=lambda d: d["name"].lower(),
    )
    write_json(directory / "controller_packages.json", packages)
    manifest["controller_packages_sha256"] = digest(packages)
    if frozen:
        if frozen["hardware"] != manifest["hardware"]:
            raise RuntimeError(
                "Controller/daemon environment differs from calibration; recalibrate"
            )
        if frozen["controller_packages_sha256"] != digest(packages):
            raise RuntimeError(
                "Controller packages differ from calibration; recalibrate"
            )
        images = frozen["images"]
        for identity in set(images.values()):
            run.run(["docker", "image", "inspect", identity])
        run.image = images["app"]
    else:
        run.build()
        images = {
            "app": json.loads(
                run.run(
                    [
                        "docker",
                        "image",
                        "inspect",
                        run.image,
                        "--format",
                        "{{json .Id}}",
                    ]
                ).stdout
            )
        }
        images["migrate"] = images["app"]
        for service, tag in (
            ("postgres", "postgres:16-alpine"),
            ("redis", "redis:7.2-alpine"),
            ("rabbitmq", "rabbitmq:3.13-management-alpine"),
        ):
            run.run(["docker", "pull", tag], timeout=300)
            images[service] = json.loads(
                run.run(
                    ["docker", "image", "inspect", tag, "--format", "{{json .Id}}"]
                ).stdout
            )
        for identity in set(images.values()):
            run.run(["docker", "image", "inspect", identity])
    manifest["images"] = images
    save_manifest(directory, manifest)
    for spec in manifest["plan"]["trials"]:
        if manifest["plan"]["source"] != fingerprint():
            raise RuntimeError(
                "Source changed during the batch; stopping and preserving evidence"
            )
        print(
            f"{spec['trial']}: separate warmup, then fresh measured project", flush=True
        )
        warmup = await trial(run, settings, spec, images, warmup=True)
        manifest["warmups"].append(warmup)
        save_manifest(directory, manifest)
        if warmup["status"] != "passed":
            print(
                json.dumps(
                    {
                        "warmup": warmup["project"],
                        "error": warmup.get("error"),
                        "reasons": warmup.get("invalid_reasons"),
                    }
                ),
                flush=True,
            )
        if warmup["status"] != "passed" or not warmup["cleanup"]:
            raise RuntimeError(
                "Warmup failed; review retained evidence before continuing"
            )
        result = await trial(run, settings, spec, images)
        manifest["results"].append(result)
        save_manifest(directory, manifest)
        print(
            f"  {result['status']}; cleanup={result['cleanup']}; "
            f"project={result['project']}",
            flush=True,
        )
        if result["status"] != "passed":
            print(
                json.dumps(
                    {
                        "error": result.get("error"),
                        "reasons": result.get("invalid_reasons"),
                        "cleanup_error": result.get("cleanup_error"),
                    }
                ),
                flush=True,
            )
        if not result["cleanup"]:
            raise RuntimeError(
                "Cleanup failed; stopping before starting another project"
            )
    manifest["status"] = (
        "passed"
        if all(r["status"] == "passed" for r in manifest["results"])
        else "invalid"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("dry-run", "calibrate", "run"):
        command = sub.add_parser(name)
        command.add_argument("--output", type=Path, required=True)
        if name == "run":
            command.add_argument("--frozen", type=Path, required=True)
        else:
            command.add_argument("--reports", type=int, default=160)
            command.add_argument("--interval-ms", type=float, default=10)
            command.add_argument("--dispatch-delay-ms", type=float, default=0)
    command = sub.add_parser("freeze")
    command.add_argument("calibration", type=Path)
    command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if sys.version_info[:2] != (3, 11):
            raise ValueError("Use the Codespaces Python 3.11 environment")
        if sys.flags.optimize:
            raise ValueError(
                "Run without optimization; ownership assertions must execute"
            )
        if args.command == "freeze":
            freeze(args.calibration, args.output)
            print(f"Frozen parameters: {args.output}")
            return 0
        frozen = read_frozen(args.frozen) if args.command == "run" else None
        settings = (
            Settings(**frozen["settings"])
            if frozen
            else Settings(
                reports=args.reports,
                interval_ms=args.interval_ms,
                dispatch_delay_ms=args.dispatch_delay_ms,
            )
        )
        directory = args.output.resolve()
        directory.mkdir(parents=True, exist_ok=False)
        manifest = {
            "started": datetime.now(UTC).isoformat(),
            "command": sys.argv,
            "python": platform.python_version(),
            "status": "planned",
            "plan": plan(settings, calibration=args.command == "calibrate"),
            "results": [],
            "warmups": [],
        }
        save_manifest(directory, manifest)
        for seed in [0] if args.command == "calibrate" else range(1, 11):
            write_json(directory / f"trace-{seed}.json", trace(settings, seed))
        if args.command == "dry-run":
            print(
                f"Plan only: {directory}; 40 trials; "
                "no services started and no research results"
            )
            return 0
        manifest["status"] = "running"
        save_manifest(directory, manifest)
        try:
            if not shutil.which("docker"):
                raise RuntimeError("Docker unavailable; run calibration in Codespaces")
            asyncio.run(execute(directory, manifest, frozen))
        except (Exception, KeyboardInterrupt) as exc:
            manifest["status"] = (
                "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            )
            manifest["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            manifest["finished"] = datetime.now(UTC).isoformat()
            manifest["not_run"] = len(manifest["plan"]["trials"]) - len(
                manifest["results"]
            )
            save_manifest(directory, manifest)
        print(f"Study {manifest['status']}; evidence: {directory}")
        if manifest.get("error"):
            print(manifest["error"])
        return 0 if manifest["status"] == "passed" else 1
    except (ValueError, KeyError, OSError) as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
