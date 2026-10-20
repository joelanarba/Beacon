# Running the scheduling study

Status on 2026-10-20: **first Docker calibration invalid; corrected broker
readiness probe requires a new calibration**. Both healthy trials passed; both
outage trials were invalidated by a premature recovery timestamp. The forty-run
measured study has not started. The 17 passing incident-processing scenarios in
[INTEGRATION_TESTING.md](INTEGRATION_TESTING.md) validate the earlier correctness
checkpoint, not this study launcher. The question, hypotheses, and limitations
are in [RESEARCH_PLAN.md](RESEARCH_PLAN.md).

## Codespaces setup and first calibration

Use the existing Docker-enabled Python 3.11 Codespace. Preserve local changes
before pulling. The controller requires a clean checkout, an unoptimized Python
interpreter, and the local Docker socket. Install the separate host plotting
requirements; the production image and its requirements are unchanged.

```bash
git pull --ff-only origin main
.venv-integration/bin/python -m pip install -r experiments/requirements.txt
docker version
docker compose version
STUDY_ROOT="artifacts/study/$(date -u +%Y%m%dT%H%M%SZ)"
.venv-integration/bin/python -m experiments.run dry-run --output "$STUDY_ROOT/plan"
.venv-integration/bin/python -m experiments.run calibrate --output "$STUDY_ROOT/calibration"
echo "calibration_exit=$?"
.venv-integration/bin/python -m experiments.analyze "$STUDY_ROOT/calibration" --output "$STUDY_ROOT/calibration-analysis"
echo "analysis_exit=$?"
```

The dry run records the forty-trial plan and ten paired traces without service
access. Its status is `planned`, never an integration or experiment success.
Reusing any output directory is an error; use a new timestamp for each attempt.

Calibration uses seed zero, two policies, and two conditions: four trials. Each
has an eight-report warmup in a separate project, followed by a fresh project
for 160 reports (40 CRITICAL, 120 STANDARD). The default trace interval is 10 ms,
client concurrency is sixteen, and artificial dispatch delay is zero. This is
an offered trace; actual send times and scheduling drift are retained.

**Review calibration results and cleanup before freezing or starting the batch.**
Paste the printed run/project summaries and exit codes for review; retain the
entire evidence directory. A failure or missing backlog is useful evidence and
must not be silently replaced. If calibration needs adjustment, choose one
common setting for both policies, save a new calibration directory, and rerun all
four trials, for example:

```bash
.venv-integration/bin/python -m experiments.run calibrate --output artifacts/study/calibration-delay-20ms --dispatch-delay-ms 20
```

Do not select settings from the measured comparison outcomes. An artificial delay
is a declared experimental control, not application processing work.

## Freeze and measured batch, after calibration review

These commands are the next stage, not part of initial calibration:

```bash
.venv-integration/bin/python -m experiments.run freeze "$STUDY_ROOT/calibration" --output "$STUDY_ROOT/frozen.json"
.venv-integration/bin/python -m experiments.run run --frozen "$STUDY_ROOT/frozen.json" --output "$STUDY_ROOT/measured"
echo "study_exit=$?"
.venv-integration/bin/python -m experiments.analyze "$STUDY_ROOT/measured" --output "$STUDY_ROOT/measured-analysis"
echo "analysis_exit=$?"
```

The freeze step rechecks all four calibration trials, separate warmups, raw-file
hashes, priorities, completion, observed backlog, invariants, and cleanup. It
locks source hashes, settings, exact local image IDs, controller packages, and
the recorded host/daemon environment. Keep the calibration artifacts and images
in the same Codespace. A changed checkout, environment, package inventory, image
availability, or raw calibration evidence requires a new calibration. Freeze
files cannot be edited to change parameters without rejection.

The measured batch uses seeds one through ten, paired within each condition:
forty trials and forty separate warmups. Policy order varies deterministically
within each pair using saved seed 20261020. All trials are retained; invalid
performance trials continue to be collected, but cleanup failure stops creation
of further projects. Root manifests distinguish attempted results from trials
not run. Setup/fault failures remain failures with their saved error and logs.

## Controls and observations

- The generated standalone Compose file retains private volumes/networks,
  ownership labels, synthetic credentials, simulated SMS, and ephemeral loopback
  ports. It uses the existing ownership verification and scoped cleanup. No
  operational service URL, AWS account, live carrier, or host socket enters an
  app container. Images/build cache are intentionally retained.
- Only the mounted study launcher changes scheduling: equal mode persists and
  publishes every event/retry with priority zero, preserving severity, payloads,
  IDs, transactions, confirmations, and retry rules. Severity mode keeps existing
  priorities. Both use prefetch one; runtime state and actual consumed priorities
  are checked against the persisted outbox.
- Each run seeds enough synthetic FIRE responders at the same explicit GPS for
  its entire finite workload. There is no hospital demand. Responder IDs and
  trace content match within each pair; matching cost remains a study limitation.
- Applied limits are verified from container state: app 1 CPU / 768 MiB,
  PostgreSQL 1 CPU / 512 MiB, Redis 0.5 CPU / 256 MiB, RabbitMQ 1 CPU / 768 MiB;
  the one-shot migration container is configured for 1 CPU / 512 MiB. These caps
  do not reserve host capacity or eliminate shared-Codespaces noise.
- Broker interruption is requested after twenty successful HTTP acceptances.
  The broker is held stopped for three seconds, then restarted; ownership checks,
  stop/start, and health detection add time. Actual timestamps are recorded; do
  not describe this as exactly three seconds of unavailability. Blocking Docker
  operations run off the request scheduler. The app stays running.
  The generated study health probe now uses `check_running` through the image
  entrypoint, with the existing cookie guard, to require the RabbitMQ application.
  Runtime-only `ping` is insufficient for this recovery boundary.
- Database snapshots are sampled at 0.25-second intervals plus query time; live
  `rabbitmqctl` queue counts and Docker resource samples at two-second intervals
  plus command time. Sample durations/errors are retained. Queue failures that
  overlap the intentional outage are expected; other sample failures invalidate
  the trial. Instrumentation overhead affects both policies.
- An eligible trial must sample at least one backlog containing both CRITICAL
  and STANDARD unassigned incidents. Absence of that observation invalidates a
  priority-benefit claim rather than proving priority ineffective.
- Assignment completion is polled with the controller's monotonic clock for up
  to 120 seconds after the last request finishes. Only assignments observed
  before that deadline contribute durations. Later durable completion remains
  visible without erasing censoring. Recovery uses the controller's observation
  of a healthy broker, not an exact server startup timestamp; completion before
  that observation is flagged and recorded as zero additional recovery time.

## Evidence and artifact-only analysis

The root `result.json` records the plan, source/environment/image identities,
warmups, trial results, failures, and not-run count. `commands.jsonl` records
exact Docker/Git commands, image inspection, package inventory, and cleanup.
Each owned project keeps:

| Files | Evidence |
|---|---|
| `trace.json`, `requests.jsonl`, `reports.json` | One row per submission, intended/send/ack times, drift, HTTP outcome, returned ID, durable state, and deadline-observation flag. |
| `outbox_full.json`, `launcher.json` | Persisted priorities, payload severity, retries/publications, prefetch, and consumed delivery priorities. |
| `database_samples.jsonl`, `service_samples.jsonl`, `faults.jsonl` | Backlog/completion, queue/consumer counts, resource observations, sampling duration/errors, and actual fault timeline. |
| `compose.yml`, service logs/snapshots, `result.json` | Exact private configuration, failure context, final state, invariants, cleanup, and SHA-256 hashes of retained project files. |

Analysis reads those files without starting services. It verifies raw-file hashes
and recomputes per-trial metrics; original failure/cleanup status cannot become
success during analysis. It exports `summary.json`, `reports.csv`,
`trial_classes.csv`, `paired_differences.csv`, class ECDFs (SVG/PNG), paired p95
differences (SVG), and recovery timelines (SVG). Invalid and censored trials stay
visible and are excluded from paired contrasts; incomplete collections return
nonzero. Class percentiles are suppressed if any class member is censored.

The latency label is **record-to-assignment**, from PostgreSQL transaction
timestamps, not exact commit time, queue wait, or recipient receipt. Quantiles
use linear interpolation of sorted observations. Uncertainty is an exploratory
95% percentile bootstrap interval for the mean paired difference, using 10,000
resamples of seed pairs; individual incidents are not independent repetitions.
Calibration has one pair per condition and therefore no uncertainty interval.
No p99, starvation-freedom, clinical, throughput-capacity, or novelty claim is made.
The bounded SMS list is not a complete delivery ledger.

Abruptly killing the controller or Codespace can bypass cleanup. Inspect retained
project names and ownership-label listings before any manual cleanup; never use
global prune. Preserve failed evidence and synthetic analysis fixtures.

## First Codespaces calibration: invalid recovery measurement

Tested revision: `ecf72adf67e4fe1512ba0ffe18af4d3b837b81ac`, confirmed by the
saved Git command. The user supplied terminal output and read-only summaries of
the root manifest, warmup/trial statuses, fault timestamps, service samples,
failed commands, and cleanup checks. Complete artifacts remain in Codespaces:

```text
/workspaces/Beacon/artifacts/study/calibration-20261020T101144Z
```

The user pulled `main`, installed `experiments/requirements.txt`, and ran:

```bash
.venv-integration/bin/python -m experiments.run calibrate --output "artifacts/study/calibration-$(date -u +%Y%m%dT%H%M%SZ)"
echo "calibration_exit=$?"
```

CPython was 3.11.13; the manifest status was `invalid` and the runner exited one.

| Scope | Passed | Invalid | Failed | Skipped | Not run |
|---|---:|---:|---:|---:|---:|
| Healthy calibration trials | 2 | 0 | 0 | 0 | 0 |
| Broker-outage calibration trials | 0 | 2 | 0 | 0 | 0 |
| Separate warmups | 4 | 0 | 0 | 0 | 0 |
| Forty-run measured batch | 0 | 0 | 0 | 0 | 40 |

Both invalid trials reported only `Live queue sampling failed outside the
injected outage`. This is a confirmed study readiness/measurement defect:

| Policy | Recorded broker-healthy time (UTC) | First subsequent failed queue command (UTC) | Evidence |
|---|---|---|---|
| Equal | 10:15:10.470322 | 10:15:12.786842–10:15:15.332262 | `rabbitmqctl list_queues` exited 64 because the `rabbit` application was not running. |
| Severity | 10:16:36.092259 | 10:16:38.645058–10:16:41.029627 | Same command, exit code, and startup error. |

The inherited health probe used `rabbitmq-diagnostics ping`, which establishes
runtime reachability and CLI authentication, not RabbitMQ application readiness.
The [RabbitMQ 3.13 diagnostics manual](https://www.rabbitmq.com/docs/3.13/man/rabbitmq-diagnostics.8)
distinguishes that check from `check_running`. Therefore the runner closed its
intentional-outage window too early and sampled legitimate broker startup as an
unexpected outage. The old recovery durations are not valid measurements.

The scoped correction changes only the generated study health command to
`check_running`. It retains the cookie guard, entrypoint privilege drop, probe
timings, workload, scheduling policies, observation deadline, and rejection of
queue failures outside the outage. Production code and the existing integration
Compose file are unchanged. There is no arbitrary grace interval or reclassification
of the saved run as passing.

Later samples show both worker consumers restored and both queues drained.
Assignment completion was observed at controller elapsed times 42.685403 s
(equal) and 41.520228 s (severity), before their 124.770007 s and 124.435299 s
drain deadlines. These observations do not rescue the invalid timing boundary
or establish a scheduling improvement. No application defect was established by
these failures.

All **nine** owned projects (one build, four warmups, four calibration trials)
completed scoped teardown and subsequent empty-resource checks. Follow-up
ownership-label listings contained no containers, volumes, or networks; no
owned data-resource leak was observed. Retain the complete invalid run and use
a new output directory for all four corrected calibration trials. Do not freeze
this run or start the forty-trial batch yet.

Local correction validation on CPython 3.11.17 reported **71 passed**, with zero
failures, errors, or skips, in 47.44 seconds: 41 study checks plus the existing
30 harness checks. JUnit is retained at
`artifacts/study/broker-readiness-correction-20261020/junit.xml`. The generated
shell probe was exercised against scripted CLI responses for both policies:
missing cookie, runtime up/application down, and application running. The old
probe falsely passed both application-down cases before correction. Separate
boundary cases verify that queue errors before or after the recorded outage
remain invalid; the analysis gate was not weakened.

Ruff, Black, Python 3.11 syntax parsing, diff checks, and offline normalization
of both generated Compose configurations passed. Probe interval, timeout, and
retry count match the inherited values. These local checks use scripted CLI
responses, not a live RabbitMQ restart; Docker remains unavailable on the local
host. Corrected Codespaces calibration has not run. The saved original run
remains invalid and is not eligible for freezing or performance claims.

## Local verification and unrun work

Local checks use CPython 3.11.17 in the separate validation environment. Study
control tests and the existing harness suite reported **61 passed, zero failures,
errors, or skips, in 14.06 seconds**: 31 study checks and 30 existing harness
checks. JUnit is retained at
`artifacts/study/final-queue-sampling-20261020/junit.xml`. The run emitted one
multipart-import deprecation warning. Ruff, Black, Python 3.11 syntax parsing of
all eight study Python files, and dependency compatibility (`pip check`) passed.
The service-sampler regression verifies that the asynchronous live queue query
is awaited off the request scheduler and yields structured queue rows; a string
or coroutine reference cannot satisfy the analysis coverage check.

The generated equal/severity Compose files both normalized successfully with
the official standalone Compose v2.39.4 executable and an empty env file. Review
checked all five services, loopback bindings, local image IDs, pull policy,
configuration paths, and configured resource limits. This requires no daemon;
it does not verify that Docker applies the limits at runtime.

The explicit CLI dry run returned zero with `status=planned`, forty scheduled
trials, and no service results. The missing-Docker calibration path returned one
with `status=failed`, no trial results, and all four calibration trials **not
run**. These artifacts remain under `artifacts/study/plan-local-20261020` and
`artifacts/study/missing-docker-local-20261020`; they are control-path checks,
not four application failures. Existing output-directory rejection is tested.

Tests use synthetic data and replaced service boundaries. Artifact-only analysis
generated its tables and figures from a fixture labeled **SYNTHETIC OFFLINE
FIXTURE**; the PNG was visually inspected. No Docker study stack, calibration
backlog, fault timing, resource limit at runtime, forty-run result, or performance
improvement is claimed from these local checks. The production application and
existing integration suite were not changed or rerun for this implementation.

Commands for the local control suite:

```bash
python -m pytest -c pyproject.toml experiments/tests integration_tests/test_harness.py -q -ra -p no:cacheprovider -p pytest_asyncio.plugin
python -m ruff check --no-cache experiments
python -m black --check --workers 1 experiments
```

Runtime conventions: [Compose service limits](https://docs.docker.com/reference/compose-file/services/)
and [Matplotlib 3.10.8](https://pypi.org/project/matplotlib/3.10.8/) for the separate
host-only plotting pin. Resolved transitive package versions and actual image
digests are recorded during calibration; this is not a full dependency-security
assessment.
