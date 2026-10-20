"""Run the study through the previously verified disposable integration harness."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from datetime import UTC, datetime

import httpx
import yaml
from integration_tests.stack import COMPOSE, DockerRun, Stack, check_project

from experiments.protocol import (
    LIMITS,
    ROOT,
    Settings,
    append_json,
    evidence_hashes,
    trace,
    write_json,
)


def compose_config(policy: str, settings: Settings, images: dict) -> dict:
    config = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    config["services"]["app"]["build"]["context"] = str(ROOT / "app")
    config["configs"]["integration-entry"]["file"] = str(
        ROOT / "experiments/worker_entry.py"
    )
    for service, limits in LIMITS.items():
        config["services"][service].update(limits)
        config["services"][service]["image"] = images[service]
        config["services"][service]["pull_policy"] = "never"
    config["services"]["app"]["environment"].update(
        BEACON_STUDY_POLICY=policy,
        BEACON_STUDY_DELAY_MS=str(settings.dispatch_delay_ms),
    )
    # Recovery timing requires the RabbitMQ application, not only its Erlang VM.
    # Keep the cookie guard and image entrypoint's privilege drop intact.
    config["services"]["rabbitmq"]["healthcheck"]["test"] = [
        "CMD-SHELL",
        "test -s /var/lib/rabbitmq/.erlang.cookie && exec "
        "docker-entrypoint.sh rabbitmq-diagnostics -q check_running",
    ]
    return config


class StudyStack(Stack):
    def __init__(self, run, settings, trial, images, *, warmup=False):
        super().__init__(run)
        self.settings, self.trial = settings, trial
        self.workload = trace(settings, trial["seed"], warmup=warmup)
        self.compose_file = self.artifacts / "compose.yml"
        self.compose_file.write_text(
            yaml.safe_dump(compose_config(trial["policy"], settings, images)),
            encoding="utf-8",
        )

    def compose(self, *args, check=True, timeout=30):
        check_project(self.project)
        return self.run.run(
            [
                "docker",
                "compose",
                "--ansi",
                "never",
                "--env-file",
                str(self.run.empty_env),
                "-f",
                str(self.compose_file),
                "-p",
                self.project,
                *args,
            ],
            env=self.env,
            check=check,
            timeout=timeout,
        )

    async def seed(self):
        ids = [
            uuid.uuid5(uuid.NAMESPACE_URL, f"beacon-study:{self.trial['seed']}:{i}")
            for i in range(len(self.workload))
        ]
        await self.sql(
            """INSERT INTO responders
            (id,name,type,status,contact,reachable_channel)
            SELECT id,'Synthetic FIRE responder','FIRE','AVAILABLE',
                   'synthetic-'||id::text,'SMS' FROM unnest($1::uuid[]) AS id""",
            ids,
        )
        async with self.redis() as client:
            values = [value for rid in ids for value in (-0.1969, 5.555, str(rid))]
            await client.geoadd("responders:live", values)
        self.responders = [str(rid) for rid in ids]

    def check_limits(self):
        ids = self.compose("ps", "-q").stdout.split()
        data = json.loads(self.run.run(["docker", "container", "inspect", *ids]).stdout)
        found = set()
        for item in data:
            service = item["Config"]["Labels"]["com.docker.compose.service"]
            limits = LIMITS[service]
            host = item["HostConfig"]
            if host["NanoCpus"] != int(limits["cpus"] * 1e9):
                raise RuntimeError(f"CPU limit was not applied: {service}")
            if host["Memory"] != int(limits["mem_limit"][:-1]) * 1024**2:
                raise RuntimeError(f"Memory limit was not applied: {service}")
            found.add(service)
        if found != {"app", "postgres", "redis", "rabbitmq"}:
            raise RuntimeError("Cannot verify every running service limit")

    def resource_stats(self):
        ids = self.compose("ps", "-q").stdout.split()
        if not ids:
            raise RuntimeError("No owned services to sample")
        output = self.run.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}", *ids],
            timeout=15,
        ).stdout
        return [json.loads(line) for line in output.splitlines() if line.strip()]


SAMPLE_SQL = """SELECT
    (SELECT count(*) FROM incidents) AS durable_incidents,
    (SELECT count(*) FROM incidents WHERE status='REPORTED'
       AND severity='CRITICAL') AS reported_critical,
    (SELECT count(*) FROM incidents WHERE status='REPORTED'
       AND severity='STANDARD') AS reported_standard,
    (SELECT count(*) FROM assignments) AS assignments,
    (SELECT count(*) FROM event_outbox WHERE published_at IS NULL) AS pending_outbox,
    (SELECT count(*) FROM event_outbox WHERE delivery_attempt>0) AS retry_rows,
    (SELECT count(*) FROM dead_letters) AS dead_letters"""


async def sample_loop(stack, stop, origin, *, services=False):
    path = stack.artifacts / (
        "service_samples.jsonl" if services else "database_samples.jsonl"
    )
    interval = (
        stack.settings.service_sample_seconds
        if services
        else stack.settings.database_sample_seconds
    )
    while not stop.is_set():
        row = {
            "at": datetime.now(UTC).isoformat(),
            "elapsed": time.monotonic() - origin,
        }
        if services:
            for key, getter in (
                ("queues", lambda: asyncio.run(stack.queues())),
                ("resources", stack.resource_stats),
            ):
                try:
                    row[key] = await asyncio.to_thread(getter)
                    if not isinstance(row[key], list) or not all(
                        isinstance(item, dict) for item in row[key]
                    ):
                        raise ValueError("Service sample must contain structured rows")
                except Exception as exc:
                    row[key + "_error"] = str(exc)[-1000:]
        else:
            try:
                row.update((await stack.sql(SAMPLE_SQL))[0])
            except Exception as exc:
                row["error"] = type(exc).__name__
        row["sample_seconds"] = time.monotonic() - origin - row["elapsed"]
        append_json(path, row)
        try:
            await asyncio.wait_for(stop.wait(), interval)
        except TimeoutError:
            pass


async def submit_trace(stack, origin, threshold):
    semaphore = asyncio.Semaphore(stack.settings.concurrency)
    accepted = 0
    rows = []
    async with httpx.AsyncClient(
        base_url=stack.url, trust_env=False, timeout=15
    ) as client:

        async def submit(item):
            nonlocal accepted
            async with semaphore:
                start = time.monotonic()
                row = dict(item)
                row.update(
                    sent_at=datetime.now(UTC).isoformat(),
                    sent_elapsed=start - origin,
                    scheduling_drift_seconds=start - origin - item["scheduled_seconds"],
                    http_status=None,
                    acknowledged_incident_id=None,
                )
                try:
                    response = await client.post(
                        "/ingest/app",
                        json=item["body"],
                        headers={"Idempotency-Key": item["identity"]},
                    )
                    row["http_status"] = response.status_code
                    if response.status_code == 201:
                        row["acknowledged_incident_id"] = str(
                            uuid.UUID(response.json()["incident_id"])
                        )
                        accepted += 1
                        if accepted >= 20:
                            threshold.set()
                    else:
                        row["response_excerpt"] = response.text[:500]
                except (
                    httpx.HTTPError,
                    ValueError,
                    KeyError,
                    TypeError,
                    AttributeError,
                ) as exc:
                    row["error"] = type(exc).__name__
                row.update(
                    ack_at=datetime.now(UTC).isoformat(),
                    request_seconds=time.monotonic() - start,
                )
                append_json(stack.artifacts / "requests.jsonl", row)
                rows.append(row)

        async with asyncio.TaskGroup() as tasks:
            for item in stack.workload:
                await asyncio.sleep(
                    max(0, origin + item["scheduled_seconds"] - time.monotonic())
                )
                tasks.create_task(submit(item))
    return sorted(rows, key=lambda row: row["index"])


async def broker_outage(stack, threshold, origin, events):
    await asyncio.wait_for(threshold.wait(), 60)

    def record(name):
        row = {
            "event": name,
            "elapsed": time.monotonic() - origin,
            "at": datetime.now(UTC).isoformat(),
        }
        events.append(row)
        append_json(stack.artifacts / "faults.jsonl", row)

    record("stop_requested")
    await asyncio.to_thread(stack.fault, "stop", "rabbitmq")
    record("stopped")
    await asyncio.sleep(stack.settings.outage_hold_seconds)
    record("restart_requested")
    # recover performs blocking Compose work: keep it off the request scheduler.
    await asyncio.to_thread(lambda: asyncio.run(stack.recover("rabbitmq", ready=False)))
    record("broker_healthy")


async def reconcile(stack, requests, observed):
    incidents = await stack.sql(
        """SELECT i.id::text,i.ingest_key,i.severity,i.status,i.created_at,
        count(a.id)::int AS assignment_count,min(a.assigned_at) AS assigned_at
        FROM incidents i LEFT JOIN assignments a ON a.incident_id=i.id GROUP BY i.id"""
    )
    by_key = {row["ingest_key"]: row for row in incidents}
    for row in requests:
        key = hashlib.sha256(f"APP\0{row['identity']}".encode()).hexdigest()
        row["durable"] = by_key.get(key)
        row["assignment_observed_by_deadline"] = (
            row["durable"] is not None and row["durable"]["id"] in observed
        )
    write_json(stack.artifacts / "reports.json", requests)
    write_json(stack.artifacts / "all_incidents.json", incidents)
    outbox = await stack.sql(
        """SELECT event_id,routing_key,priority,delivery_attempt,publish_attempts,
        created_at,available_at,published_at,last_error,
        payload->>'severity' AS payload_severity
        FROM event_outbox ORDER BY created_at,id"""
    )
    write_json(stack.artifacts / "outbox_full.json", outbox)
    return incidents, outbox


async def trial(run: DockerRun, settings: Settings, spec, images, *, warmup=False):
    from experiments.analyze import summarize_trial

    stack = StudyStack(run, settings, spec, images, warmup=warmup)
    phase = "warmup" if warmup else "measured"
    run.record({"trial": spec, "phase": phase, "project": stack.project})
    write_json(stack.artifacts / "trial.json", {**spec, "phase": phase})
    write_json(stack.artifacts / "trace.json", stack.workload)
    result = {
        "trial": spec,
        "phase": phase,
        "project": stack.project,
        "status": "failed",
        "cleanup": False,
    }
    tasks, events, requests = [], [], []
    stop = asyncio.Event()
    try:
        await stack.prepare()
        await stack.start_app()
        stack.check_limits()
        state = await stack.control("/study")
        if (state["policy"], state["prefetch"], state["delay_ms"]) != (
            spec["policy"],
            1,
            settings.dispatch_delay_ms,
        ):
            raise RuntimeError("Launcher policy/prefetch/delay verification failed")
        origin = time.monotonic()
        tasks = [
            asyncio.create_task(sample_loop(stack, stop, origin, services=value))
            for value in (False, True)
        ]
        threshold = asyncio.Event()
        fault = None
        if spec["condition"] == "broker_outage" and not warmup:
            fault = asyncio.create_task(broker_outage(stack, threshold, origin, events))
            tasks.append(fault)
        requests = await submit_trace(stack, origin, threshold)
        input_end = time.monotonic() - origin
        deadline = time.monotonic() + settings.drain_seconds
        completion = None
        observed = set()
        while time.monotonic() < deadline:
            assigned = await stack.sql("SELECT incident_id::text FROM assignments")
            if time.monotonic() > deadline:
                break
            observed = {row["incident_id"] for row in assigned}
            if len(observed) == len(stack.workload):
                completion = time.monotonic() - origin
                break
            await asyncio.sleep(0.1)
        # A short trace may complete before stop/start. Still observe the fault.
        if fault:
            await fault
        stop.set()
        await asyncio.gather(*tasks)
        await stack.invariants()
        if await stack.scalar(
            """SELECT count(*) FROM assignments a
            JOIN responders r ON r.id=a.responder_id
            WHERE a.released_at IS NULL AND r.status <> 'ASSIGNED'"""
        ):
            raise RuntimeError("Responder/assignment state mismatch")
        incidents, outbox = await reconcile(stack, requests, observed)
        state = await stack.control("/study")
        write_json(stack.artifacts / "launcher.json", state)
        result.update(
            input_end_elapsed=input_end,
            completion_elapsed=completion,
            drain_deadline_elapsed=input_end + settings.drain_seconds,
            events=events,
            durable_incidents=len(incidents),
            invariants_passed=True,
            deadletter_count=await stack.scalar("SELECT count(*) FROM dead_letters"),
        )
        result.update(
            summarize_trial(stack.artifacts, result, len(stack.workload), warmup=warmup)
        )
    except (Exception, asyncio.CancelledError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, asyncio.CancelledError):
            raise
    finally:
        stop.set()
        # Never tear down while a fault thread can still restart a service.
        for task in tasks:
            try:
                await task
            except (Exception, asyncio.CancelledError) as exc:
                result.setdefault("background_errors", []).append(type(exc).__name__)
                result["status"] = "failed"
        try:
            await stack.capture()
        finally:
            try:
                stack.cleanup()
                result["cleanup"] = True
            except Exception as exc:
                result["cleanup_error"] = f"{type(exc).__name__}: {exc}"
                result["status"] = "failed"
            result["evidence_sha256"] = evidence_hashes(stack.artifacts)
            write_json(stack.artifacts / "result.json", result)
    return result
