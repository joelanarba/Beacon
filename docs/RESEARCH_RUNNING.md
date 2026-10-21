# Run the scheduling study

The first batch is complete: forty measured trials and forty warmups passed,
all 6400 measured reports completed, and all eighty projects were verified empty
after cleanup. See [RESEARCH_RESULTS.md](RESEARCH_RESULTS.md) for the results and
[VALIDATION_HISTORY.md](VALIDATION_HISTORY.md) for failed attempts and corrections.
A replacement batch is not needed for the completed evaluation.

## Recorded evidence

The passing calibration used `3e946b1`; the measured batch used `4304151`.
Its source inputs were unchanged between those two revisions.

```text
/workspaces/Beacon/artifacts/study/calibration-20261021T020432Z
/workspaces/Beacon/artifacts/study/batch-20261021T023128Z/frozen.json
/workspaces/Beacon/artifacts/study/batch-20261021T023128Z/measured
/workspaces/Beacon/artifacts/study/batch-20261021T023128Z/measured-analysis
```

The earlier `calibration-20261020T101144Z` remains invalid. Do not replace its
status or include it in paired performance comparisons. Back up both successful
and failed evidence using [EVIDENCE_BACKUP.md](EVIDENCE_BACKUP.md).

Later presentation edits changed source-file bytes. Frozen fingerprints are
exact, including comments and docstrings. Use the recorded checkout for old
source/frozen-evidence checks. A new measured batch needs a calibration for its
own checkout, packages, images, and environment; do not edit a frozen file to
bypass that requirement.

## Setup for a new evaluation

Use Python 3.11, Docker Engine, and Compose v2 in the Codespace. The controller
requires a clean checkout, an unoptimized Python interpreter, and a local Docker
socket. The host plotting requirements are separate from the production image:

```bash
.venv-integration/bin/python -m pip install -r experiments/requirements.txt
docker version
docker compose version
```

## Plan and calibrate

Run from the repository root with a new output directory:

```bash
STUDY_ROOT="artifacts/study/$(date -u +%Y%m%dT%H%M%SZ)"
.venv-integration/bin/python -m experiments.run dry-run --output "$STUDY_ROOT/plan"
.venv-integration/bin/python -m experiments.run calibrate --output "$STUDY_ROOT/calibration"
echo "calibration_exit=$?"
.venv-integration/bin/python -m experiments.analyze "$STUDY_ROOT/calibration" --output "$STUDY_ROOT/calibration-analysis"
echo "analysis_exit=$?"
```

The dry run writes a plan and traces without starting services. Calibration uses
seed zero, two policies, and two conditions: four trials, each with a separate
8-report warmup and a fresh 160-report project. Defaults are nominal 10 ms trace
spacing, concurrency sixteen, and no artificial dispatch delay.

Review all four trials, raw hashes, observed mixed-class backlog, completion,
sampling boundaries, priorities, and cleanup before freezing. Preserve invalid
trials. If a change is needed, apply one common setting to both policies and
rerun all four calibration trials in a new directory. Do not tune settings from
measured comparison outcomes. An artificial delay is a declared control, not
application processing time.

## Freeze and collect

After calibration review, use the same environment and retained images:

```bash
(
  set -e
  .venv-integration/bin/python -m experiments.run freeze "$STUDY_ROOT/calibration" --output "$STUDY_ROOT/frozen.json"
  set +e
  .venv-integration/bin/python -m experiments.run run --frozen "$STUDY_ROOT/frozen.json" --output "$STUDY_ROOT/measured"
  study_exit=$?
  printf 'study_exit=%s\n' "$study_exit"
  .venv-integration/bin/python -m experiments.analyze "$STUDY_ROOT/measured" --output "$STUDY_ROOT/measured-analysis"
  analysis_exit=$?
  printf 'analysis_exit=%s\n' "$analysis_exit"
  exit "$((study_exit || analysis_exit))"
)
```

Freezing rechecks calibration and locks source hashes, settings, local image IDs,
controller packages, and host/daemon information. The measured batch uses seeds
one through ten in each condition: forty trials and forty separate warmups.
Policy order varies deterministically using order seed 20261020. Cleanup failure
stops new projects. Invalid trials remain recorded and are excluded from paired
contrasts; the manifest reports trials not run.

## Controls

- Equal mode persists and publishes priority zero, preserving payload severity,
  event identity, confirmations, and retry behavior. Severity mode retains the
  existing priorities. Both use prefetch one, compared with the application
  default of sixteen. Runtime state and delivered priorities are checked.
- Each run seeds enough synthetic FIRE responders at explicit GPS coordinates
  for the finite workload. Reports are 25% CRITICAL and 75% STANDARD. There is
  no hospital demand in this study.
- Applied running-service caps are app 1 CPU / 768 MiB, PostgreSQL 1 / 512 MiB,
  Redis 0.5 CPU / 256 MiB, and RabbitMQ 1 CPU / 768 MiB. The migration container
  is configured for 1 CPU / 512 MiB. Caps do not reserve host capacity.
- Broker interruption is requested after twenty successful HTTP acceptances.
  The broker is held stopped for three seconds, then restarted. Stop/start and
  health detection add time; actual timestamps are saved. The app stays running.
  The study health probe uses `check_running` through the image entrypoint with
  a cookie guard. Runtime-only `ping` is insufficient for queue readiness.
- Database samples run at 0.25-second intervals plus query time. Queue counts
  and Docker resource samples run at two-second intervals plus command time.
  Queue errors overlapping the injected outage are expected. Resource errors
  or queue errors outside that window invalidate a trial.
- An eligible trial must observe both severity classes waiting in a backlog.
  Actual send times and scheduling drift are retained. Nominal trace spacing
  is not proof of an achieved steady arrival rate.
- Assignment completion is polled for 120 seconds after the last request
  finishes. Only observations before the deadline contribute durations. Later
  durable completion does not erase censoring. Completion before observed broker
  health is flagged and contributes zero additional recovery time.

## Saved files and analysis

The root `result.json` contains the plan, source/environment/image identities,
warmups, results, failures, and not-run count. `commands.jsonl` contains commands,
outputs, package inventories, and cleanup. Each project stores:

| Files | Contents |
|---|---|
| `trace.json`, `requests.jsonl`, `reports.json` | Intended/send/ack timing, drift, HTTP results, incident identity, durable state, and deadline flags |
| `outbox_full.json`, `launcher.json` | Persisted priorities, severity, publication/retry state, prefetch, and actual delivery priorities |
| `database_samples.jsonl`, `service_samples.jsonl`, `faults.jsonl` | Backlog, completion, queues, resources, errors, sample duration, and fault timing |
| `compose.yml`, logs, snapshots, `result.json` | Configuration, final state, invariants, cleanup, and evidence hashes |

Analysis starts no services. It verifies raw hashes and recomputes per-trial
metrics. Original failure or cleanup status cannot become success. Outputs are
`summary.json`, `reports.csv`, `trial_classes.csv`, `paired_differences.csv`,
class ECDFs in SVG/PNG, paired p95 differences in SVG, and recovery timelines in
SVG. Incomplete or invalid collections return nonzero. Censored classes have no
median/p95 estimate; invalid trials stay visible and are excluded from contrasts.

The duration is record-to-assignment, using PostgreSQL transaction timestamps.
It is not exact commit time, queue wait, or recipient receipt. Quantiles use
linear interpolation. Exploratory 95% intervals use 10,000 bootstrap resamples
of paired seeds; incidents are not independent repetitions. Calibration has one
pair per condition and no uncertainty interval. No p99 or capacity claim is made.

Projects use private resources, synthetic credentials, simulated SMS, and
loopback ports. Images and raw evidence are retained. Abrupt controller or
Codespace termination can bypass cleanup; inspect recorded ownership labels
before manual cleanup and do not use global prune. Original-figure visual review
and independent environment reproduction remain unverified.
