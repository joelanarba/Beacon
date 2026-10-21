# Scheduling study results: finite bursts in Codespaces

Reviewed on 2026-10-21. The first forty-trial measured batch and its saved-evidence
checks passed. Severity scheduling lowered CRITICAL record-to-assignment durations
in both tested conditions. During broker outages, STANDARD durations increased,
and mean overall recovery was descriptively slower. The healthy STANDARD
comparisons were inconclusive. These findings apply to the bounded setup below.

## Evidence and provenance

Tested commit: `4304151015775f6baa3c962f86af074cfe432bdf`. Its hashed study inputs
match the corrected calibration at `3e946b11cdcba3b7c20f4b55756014ee94c5d15a`;
the intervening commit changed documentation only. Complete artifacts remain in
the Codespaces workspace:

```text
/workspaces/Beacon/artifacts/study/calibration-20261021T020432Z
/workspaces/Beacon/artifacts/study/batch-20261021T023128Z/frozen.json
/workspaces/Beacon/artifacts/study/batch-20261021T023128Z/measured
/workspaces/Beacon/artifacts/study/batch-20261021T023128Z/measured-analysis
```

This report uses exported terminal output and a read-only artifact-check summary. The
measured runner and analysis each exited zero. The measured manifest spans
2026-10-21 02:31:34.151124-03:30:25.078598 UTC, or 3530.927474 seconds, including
setup, warmups, measurements, evidence capture, and teardown. CPython was 3.11.13.
The local Windows review did not execute Docker or directly read the remote raw
projects. No remote artifact or original figure was modified during review.

The exported `RAW_AND_FROZEN_GATE PASS` records checks that:

- Revalidated the frozen calibration, source fingerprint, settings, and complete
  forty-trial plan; measured image IDs, host information, and controller-package
  fingerprint matched the frozen values.
- Verified hashes and paired traces for all forty warmups and forty measured
  projects, then recomputed each report summary from its saved evidence.
- Matched analyzed trials to the measured manifest and the manifest hash, and
  recomputed all eight paired interval summaries.
- Confirmed cleanup flags and absence of background errors for all eighty
  projects. Separate command-log checks found all eighty teardowns successful
  and all eighty subsequent container/volume/network checks empty.

The results are based on exported real-service evidence summaries. Independent
reproduction and visual review of the original remote figures remain unverified. The earlier calibration at `calibration-20261020T101144Z` remains
invalid and contributes no observations to these results.

## Design and environment

The [study plan](RESEARCH_PLAN.md) declares the hypotheses and controls; the
[execution guide](RESEARCH_RUNNING.md) records setup and commands. Ten paired
seeds were run under each of two conditions, with both policies per seed:
forty measured trials and forty separate eight-report warmups. Policy order
varied using saved order seed 20261020. Calibration used a different seed, zero.

Each measured trial offered 160 synthetic APP/FIRE reports with explicit GPS:
40 CRITICAL and 120 STANDARD. The policies differed in both persisted outbox
priority and RabbitMQ delivery priority; the equal baseline set both to zero.
Severity labels, durability, confirmations, retry handling, and assignment
invariants were preserved. This evaluates the combined pipeline scheduling policy.

Frozen settings were nominal 10 ms trace spacing, concurrency sixteen, prefetch
one, zero artificial dispatch delay, a 120-second post-input drain, and a
three-second stopped hold for broker-outage trials. Database and service sample
intervals were 0.25 seconds and two seconds respectively, plus query/command time.
The broker stop was requested after twenty successful HTTP acceptances; the app
stayed running. Recovery health required the RabbitMQ application to be running.

The controller reported two CPUs. Docker reported two CPUs, 8,327,036,928 bytes
of memory, and the `overlayfs` storage driver. The platform was
`Linux-6.8.0-1064-azure-x86_64-with-glibc2.36`. Per-service CPU/memory caps were
app 1 / 768 MiB, PostgreSQL 1 / 512 MiB, Redis 0.5 / 256 MiB, and RabbitMQ
1 / 768 MiB. These caps do not reserve exclusive host resources.

## Completion, correctness, and isolation

| Scope | Passed | Invalid | Failed | Skipped | Not run | Reports submitted / acknowledged / completed |
|---|---:|---:|---:|---:|---:|---|
| Healthy measured trials | 20 | 0 | 0 | 0 | 0 | 3200 / 3200 / 3200 |
| Broker-outage measured trials | 20 | 0 | 0 | 0 | 0 | 3200 / 3200 / 3200 |
| Separate warmups | 40 | 0 | 0 | 0 | 0 | 320 / 320 / 320 |

All 6400 measured reports completed: 1600 CRITICAL and 4800 STANDARD. None was
censored, unresolved, or dead-lettered. Mixed-class backlog was observed in all
forty trials. The saved gates passed identity/severity reconciliation, assignment
uniqueness, persisted/delivered priorities, and recorded assignment/resource
invariants. Warmups likewise completed all 320 reports without unresolved work
or dead letters; they are excluded from the performance comparisons.

Healthy trials recorded no queue/resource sampling error samples. Outage trials
recorded 28 under equal scheduling and 29 under severity scheduling. All 57
were expected queue failures overlapping the injected outage window; a resource
error or queue error outside that window would have invalidated the trial.
No actionable application, test, or environment failure was identified in this
batch. Host contention and timing variation remain limitations, not failed trials.

All **80 / 80 / 80** owned projects had teardown, successful teardown, and empty
post-teardown checks respectively. Follow-up ownership-label listings contained
no containers, volumes, or networks. No owned data-resource leak was observed.
Images, build cache, and saved evidence are intentionally retained. The measured
batch reused frozen images, so these eighty projects comprise forty warmups and
forty trials, with no additional build project.

## Assignment latency and uncertainty

Durations are PostgreSQL `assignment.assigned_at - incident.created_at`, using
transaction timestamps. The endpoint is database assignment. This is not an
HTTP-response, exact queue-wait, recipient-delivery, or clinical-response metric.

Each value below is the arithmetic mean of ten per-trial medians or p95 values,
not a pooled incident quantile. Differences are severity minus equal, paired by
seed within condition. Negative values indicate shorter durations under severity
scheduling. Intervals are the saved exploratory 95% percentile bootstrap
intervals for the mean paired difference, using 10,000 deterministic resamples
of ten seed pairs. Individual incidents are not independent repetitions.
The eight intervals have no multiple-comparison adjustment.

| Condition | Class | Trial statistic | Equal mean (s) | Severity mean (s) | Mean paired difference (s) | Exploratory 95% interval (s) |
|---|---|---|---:|---:|---:|---|
| Healthy | CRITICAL | Median | 8.903 | 2.363 | -6.540 | [-8.728, -4.891] |
| Healthy | CRITICAL | p95 | 19.406 | 3.036 | -16.370 | [-21.274, -13.493] |
| Healthy | STANDARD | Median | 8.886 | 10.033 | +1.147 | [-0.180, +2.027] |
| Healthy | STANDARD | p95 | 19.152 | 17.606 | -1.546 | [-5.836, +1.106] |
| Broker outage | CRITICAL | Median | 26.350 | 22.326 | -4.024 | [-6.047, -1.891] |
| Broker outage | CRITICAL | p95 | 35.209 | 24.033 | -11.176 | [-13.162, -9.370] |
| Broker outage | STANDARD | Median | 26.308 | 30.333 | +4.025 | [+1.724, +5.955] |
| Broker outage | STANDARD | p95 | 34.862 | 37.487 | +2.625 | [+0.501, +4.619] |

The CRITICAL median and p95 intervals lie below zero in both conditions. This
supports the proposed CRITICAL latency benefit within this finite-burst setup.
During outages, both STANDARD intervals lie above zero, supporting a waiting-time
cost. Healthy STANDARD intervals include zero, so neither a consistent benefit
nor a consistent cost is established there. The results do not support improving
every class or inferring an isolated RabbitMQ effect from the combined policy.

## Recovery and actual timing

Recovery is controller-observed assignment completion minus controller-observed
broker application health. None of the twenty outage trials completed before
that health observation; all completed before their observation deadlines.
The following descriptive statistics were calculated locally from the ten
exported per-policy fault records. The saved analysis does not provide a
bootstrap interval for recovery, and none is claimed here.

| Outage policy | Mean recovery (s) | Observed range (s) | Mean stop-request-to-health observation (s) | Observed range (s) |
|---|---:|---|---:|---|
| Equal | 17.940 | [14.273, 20.714] | 22.416 | [21.502, 23.643] |
| Severity | 20.294 | [15.862, 21.782] | 21.801 | [20.789, 22.841] |

The descriptive mean paired recovery difference was **+2.353 seconds** under
severity scheduling. CRITICAL prioritization did not coincide with faster mean
overall recovery. Broker-health observation timing also differed between runs;
these observations do not establish a general recovery penalty caused by policy.

The actual stopped holds ranged from 3.000344 to 3.045685 seconds under equal
scheduling and 3.000691 to 3.010028 seconds under severity scheduling. The much
longer stop-request-to-health intervals include stop/start and readiness-detection
time; they are not a measurement of exactly three seconds of unavailability.

| Group | Maximum observed send drift (s) |
|---|---:|
| Healthy / equal | 5.421948 |
| Healthy / severity | 4.057554 |
| Broker outage / equal | 3.180076 |
| Broker outage / severity | 3.080664 |

The 10 ms spacing is a nominal offered trace. These results characterize finite
bursts with scheduling drift, not a sustained 100 requests/second arrival rate
or a throughput-capacity measurement. Pairing preserves intended traces, not
identical realized request timing or broker restart duration.

## Retained outputs and remaining work

The saved file listing confirms `summary.json`, `reports.csv`,
`trial_classes.csv`, `paired_differences.csv`, `class_ecdf.png`, `class_ecdf.svg`,
`paired_p95.svg`, and `recovery_timelines.svg` in `measured-analysis`. Original
figures were generated successfully but have not been visually reviewed in this
local checkout. The frozen file, manifests, command logs, package/image records,
and eighty raw project directories remain the primary evidence.

The next step is preserving that evidence and reviewing the original figures.
No application fix, retuning, or replacement batch is indicated by these results.
Raw artifacts are ignored by Git. Run the backup script in Codespaces, then
copy its archive and checksum outside the Codespace:

```bash
bash scripts/backup-evidence.sh
```

[EVIDENCE_BACKUP.md](EVIDENCE_BACKUP.md) explains the contents and restore steps.
The archive includes failed and successful integration/study runs and committed
source history. Container images are not included. Backup creation in Codespaces,
external storage, and independent reproduction remain unverified.

Generalization remains limited by one shared two-CPU Codespace, ten seed pairs
per condition, co-located load generation and services, instrumentation overhead,
variable restart/readiness times, responder-matching cost, finite workloads, one
app/worker, prefetch one, one emergency type, and synthetic reporters. Prefetch
one is a study control; Beacon's default is sixteen. This study does not establish
starvation freedom, horizontal scaling, exactly-once notification effects,
real-carrier delivery, clinical benefit, production readiness, or algorithmic
novelty. Independent environment reproduction and a literature review are
needed before broader research claims.

At report commit `afef34c`, local checks matched all eight latency-table rows
and both recovery rows to the exported numerical evidence at the displayed
precision. Link, whitespace, Bash syntax, and diff checks passed. That commit
changed documentation only; no application test or Docker batch was rerun.
Later presentation edits change source comments and labels. The measured
revision remains `4304151`, as recorded in [VALIDATION_HISTORY.md](VALIDATION_HISTORY.md).
