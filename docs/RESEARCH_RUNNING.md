# Running the scheduling study

Status on 2026-10-21: **corrected Docker calibration and saved-evidence gate
passed; ready to freeze settings**. All four trials and four separate warmups
passed, with nine successful, verified-empty project teardowns. The first
calibration remains invalid. The forty-run measured study has not started.
The 17 passing incident-processing scenarios in
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

The corrected calibration below has passed review. In the same Codespace, use
this block to analyze its saved artifacts, freeze its unchanged settings, and
run the measured batch. Each attempt gets a fresh output directory. The block
stops if pulling, calibration analysis, or freezing fails; it still analyzes a
failed measured batch so invalid results remain visible.

```bash
(
  set -e
  git pull --ff-only origin main
  CAL="artifacts/study/calibration-20261021T020432Z"
  BATCH="artifacts/study/batch-$(date -u +%Y%m%dT%H%M%SZ)"
  printf 'study_directory=%s\n' "$BATCH"
  .venv-integration/bin/python -m experiments.analyze "$CAL" --output "$BATCH/calibration-analysis"
  .venv-integration/bin/python -m experiments.run freeze "$CAL" --output "$BATCH/frozen.json"
  set +e
  .venv-integration/bin/python -m experiments.run run --frozen "$BATCH/frozen.json" --output "$BATCH/measured"
  study_exit=$?
  printf 'study_exit=%s\n' "$study_exit"
  .venv-integration/bin/python -m experiments.analyze "$BATCH/measured" --output "$BATCH/measured-analysis"
  analysis_exit=$?
  printf 'analysis_exit=%s\n' "$analysis_exit"
  exit "$((study_exit || analysis_exit))"
)
```

No dependency reinstall or calibration rerun is needed for this reviewed
checkpoint. Documentation-only changes do not affect the source fingerprint;
the runner checks the hashed application, study, and harness inputs. Paste the
printed directory, trial summaries, and both exit codes after completion, then
review the saved manifest, raw evidence, class results, paired comparisons, and
cleanup before interpreting the measured study.

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
owned data-resource leak was observed. Retain the complete invalid run. This
run is ineligible for freezing; the subsequent corrected calibration is recorded
below.

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
host. The subsequent Codespaces rerun is recorded below. The saved original
run remains invalid and is not eligible for freezing or performance claims.

## Corrected Codespaces calibration: passed on 2026-10-21

Tested revision: `3e946b11cdcba3b7c20f4b55756014ee94c5d15a`, confirmed by the
saved Git command. Evidence remains in the user's Codespace:

```text
/workspaces/Beacon/artifacts/study/calibration-20261021T020432Z
```

The user supplied the runner output and a read-only saved-evidence check using
`experiments.run.validate_calibration`. The runner exited zero, the manifest
reported `passed`, and the raw-evidence gate returned `PASS` under CPython
3.11.13. This review uses those supplied summaries; the remote artifacts were
not copied to or rerun on the local Windows host. The gate rechecked source and
trial plans, paired traces, raw-file hashes, priorities, completion, recorded invariants,
observed backlog, sampling boundaries, and separate warmup cleanup.

| Scope | Passed | Invalid | Failed | Skipped | Not run |
|---|---:|---:|---:|---:|---:|
| Healthy calibration trials | 2 | 0 | 0 | 0 | 0 |
| Broker-outage calibration trials | 2 | 0 | 0 | 0 | 0 |
| Separate warmups | 4 | 0 | 0 | 0 | 0 |
| Forty-run measured batch | 0 | 0 | 0 | 0 | 40 |

All four calibration trials submitted, acknowledged, and completed 160 reports
each: 40 CRITICAL and 120 STANDARD, with no censored, unresolved, or dead-lettered
reports. All four separate warmups completed eight of eight reports. Mixed-class
backlog was observed in every trial and warmup. No correctness, environment, or
unexpected sampling failure was reported by the gate.

| Condition / policy | Input finished (s) | Completion observed (s) | Drain deadline (s) | Maximum send drift (s) | Queue/resource error samples |
|---|---:|---:|---:|---:|---:|
| Healthy / equal | 3.480647 | 21.575575 | 123.480647 | 1.778550 | 0 |
| Healthy / severity | 3.605311 | 20.923872 | 123.605311 | 1.870813 | 0 |
| Broker outage / equal | 4.549300 | 41.088741 | 124.549300 | 2.747148 | 3 |
| Broker outage / severity | 4.344919 | 36.637948 | 124.344919 | 2.499051 | 2 |

Times are controller elapsed seconds from each trial's origin. The five error
samples occurred during intervals overlapping the injected outage and passed
the unchanged boundary check. Resource errors and queue errors outside that
window would invalidate a trial. The supplied fault timeline was:

| Policy | Stop requested (s) | Stopped (s) | Restart requested (s) | Application healthy observed (s) |
|---|---:|---:|---:|---:|
| Equal | 1.434434 | 4.426924 | 7.428127 | 22.491177 |
| Severity | 1.245493 | 4.173447 | 7.175022 | 22.872046 |

The settings are eligible for freezing unchanged: 160 reports, nominal 10 ms
trace spacing, concurrency 16, no artificial dispatch delay, prefetch one,
120-second post-input drain, three-second stopped hold, database sampling at
0.25 seconds, service sampling at two seconds, and order seed 20261020. These
are finite bursts with substantial observed scheduling drift, not a demonstrated
steady 100 requests/second arrival rate. Retain actual send/ack times and report
drift in the measured analysis. No setting was selected from the latency
difference between policies; the eligibility checks passed with the existing
defaults. There is only one calibration seed pair per condition, so this record
does not establish a scheduling benefit or uncertainty across repetitions.

All nine owned projects (one build, four warmups, four trials) had successful
scoped teardown and empty container/volume/network checks: **9 / 9 / 9**.
Follow-up ownership-label listings were empty. No owned data-resource leak was
observed; retained images/build cache and evidence are intentional. Artifact-only
plot generation, freezing, and the forty-run measured batch remain unrun for
this calibration at this checkpoint. The smallest justified next step is the
freeze-and-batch block above; no application or study-code fix is indicated.

This documentation checkpoint passed local diff checks, relative-link checks,
and Bash syntax validation of the freeze-and-batch block. Only four Markdown
files changed; hashed study inputs are unchanged. No application tests or Docker
trials were rerun for the documentation update.

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
