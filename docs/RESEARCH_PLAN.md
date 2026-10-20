# Focused study: severity scheduling during backlog and broker recovery

Status on 2026-10-20: **runner and artifact analysis implemented; Docker study
not yet run**. The isolated baseline, seeded workload, calibration gate, and
analysis have local control tests; these are not performance results or a novelty
claim. Setup and execution are in [RESEARCH_RUNNING.md](RESEARCH_RUNNING.md).
The real-service correctness gate, including authentication, passed at
`cd9c753d6ef8587fd7b9f15e3710c41c86cc7a22`; see
[INTEGRATION_TESTING.md](INTEGRATION_TESTING.md). Remaining public-ingress
restrictions are in [SECURITY_REVIEW.md](SECURITY_REVIEW.md).

## Question and hypotheses

Does severity-aware scheduling across Beacon's database outbox and RabbitMQ
reduce critical-incident assignment latency during backlog and broker recovery,
while preserving accepted-work completion and assignment invariants?

This is an engineering evaluation of scheduling and durable processing under
failure. It does not establish a novel algorithm, clinical benefit, or production
emergency-service suitability. A literature review is required before any novelty
claim. The broader systems question is the latency/fairness tradeoff when durable
work passes through multiple scheduling stages and accumulates during an outage.

Hypotheses to test, rather than assumed outcomes:

1. With a measured backlog, severity-aware scheduling lowers critical-incident
   assignment latency relative to equal-priority scheduling.
2. The same ordering may increase standard-incident waiting time. Quantify that
   cost instead of claiming that priority improves every class.
3. After the same finite broker outage, both configurations complete accepted
   work within a declared observation window without duplicate assignments or
   inconsistent resource state. A failure of either configuration is a result.

## One comparison, two conditions

| Factor | Values |
|---|---|
| Scheduling | Equal priority; existing severity priority |
| Condition | Healthy burst; same burst with a three-second RabbitMQ stop/start |
| Primary outcomes | Critical and standard record-to-assignment durations; accepted-work completion and recovery |
| Controlled values | Same request trace and seed, responders, GPS, app/broker settings, prefetch, resource limits, image, dependencies, and observation window |

Two stages currently prioritize work: `events/outbox.py:relay_one` selects higher
outbox priorities first, then RabbitMQ orders waiting deliveries by priority.
**Setting only the AMQP header to zero is not an equal-priority baseline.** The
isolated baseline must persist every experiment event/retry with priority zero
and publish it with zero priority, while retaining the actual severity in its
payload. Both configurations must otherwise use identical durable outbox,
confirmation, retry, and idempotency mechanisms. The comparison therefore measures
the pipeline's scheduling policy, not the isolated effect of RabbitMQ priority.

Keep this baseline override and event instrumentation outside the production
image, following the existing integration-entry pattern. Do not add a lossy
publication mode or weaken correctness to create a comparison.

## Workload and bounded environment

Use one app/embedded worker, PostgreSQL, Redis, and RabbitMQ in a disposable
Compose project with the integration ownership and cleanup checks. No operational
service URL, production data, public port forwarding, AWS, or live SMS is involved.
Record the Codespaces machine, service CPU/memory limits, revision, image digests,
resolved package versions, and configuration for each run.

Use synthetic APP reports with valid explicit GPS, one emergency type (`FIRE`),
and unique idempotency keys. Assign 25% CRITICAL and 75% STANDARD severities using
a saved seeded trace; preserve severity labels in both configurations. Using one
responder discipline avoids mixing scheduling effects with hospital requirements
or channel parsing. Provision enough available synthetic responders for the whole
finite run so resource exhaustion does not silently become the studied factor.
Report the resulting matching-query cost as a limitation.

Start calibration with 160 measured reports per run and a short separate warm-up
followed by a fresh measured project. Set prefetch to one in both experimental
configurations through the isolated launcher. This is a controlled study setting,
not a claim about Beacon's default prefetch of sixteen. Calibrate the offered
trace and, only if necessary, one fixed test-only dispatch delay to produce a
measurable backlog. Freeze those settings before collecting comparison results.
Record any artificial delay and do not present it as measured application work.

Reject a scheduling trial with no observed backlog as insufficient evidence of
priority benefit; retain its raw output and rejection reason. No throughput or
capacity number is assumed in advance. Do not tune the workload separately for
the two scheduling configurations after observing their outcomes.

For the fault condition, stop RabbitMQ after the controller has observed twenty
successful acceptances; record actual stop and restart timestamps and continue
the identical scheduled request trace. Record scheduling drift if the controller
cannot sustain the trace. Resume the existing app rather than restarting it to
hide a failed reconnection.

## Measurements and correctness checks

Preserve one row per submitted report, keyed by run and client request identity:
send/ack times, HTTP outcome, returned incident ID, severity, incident creation,
assignment timestamp, and final durable state. IDs belong in raw artifacts, not
Prometheus label values. Retain rejected and timed-out requests as well as 201s.

The primary duration is `assignment.assigned_at - incident.created_at`, using
timestamps from the same PostgreSQL clock. Label it **record-to-assignment**:
these are transaction timestamps, not exact HTTP-commit, queue-wait, or recipient
receipt times. Measure client request duration with the controller's monotonic
clock. If queue-wait timing is added, instrument actual enqueue/consumer entry
boundaries and document its clock and overhead; do not infer it from the existing
dispatch histogram, which has no severity label.

Record live queue ready/unacknowledged counts, consumer counts, pending outbox
rows, retry attempts, dead letters, and service-resource samples throughout the
run. Define recovery as time from the recorded healthy broker to completion of
all accepted reports in the run. Censor recovery at a fixed 120-second post-input
drain deadline if completion is not observed.

At the deadline reconcile every accepted incident with its assignment and pending
or failed durable state. **Unfinished at a deadline is not proof of lost work.**
Report unresolved/pending, dead-lettered, and completed counts separately. Verify
one assignment per incident, at most one unreleased assignment per responder,
and capacity bounds. Any violation or cleanup failure invalidates a latency-only
success claim and must be reported prominently.

Do not use the bounded Redis SMS list as a complete delivery ledger: it retains
only 200 entries, and duplicate side effects can evict earlier entries. This
first study's completion endpoint is database assignment. Simulated SMS behavior
remains correctness evidence from the integration suite, not a recipient-latency
or exactly-once outcome in this experiment.

## Procedure and analysis

1. Pass the current full integration gate, including authentication regressions.
2. Implement and test the isolated priority override, recorder, workload driver,
   and artifact-only analysis. Check that the baseline neutralizes both stages
   without changing payloads, identity, or retry semantics.
3. Perform calibration separately and freeze all measured-run parameters.
4. Use ten paired trace seeds for each condition: two schedules times two
   conditions times ten repetitions gives forty measured disposable runs. Vary
   schedule order within each pair using a saved randomization seed. Preserve
   every run; retain and explain invalid trials rather than replacing them silently.
5. Export raw tables, settings, event/resource samples, commands, package/image
   identity, and correctness summaries before scoped teardown. Check for leftover
   owned resources and retain failed-run evidence.
6. Produce per-run/class counts, median and p95 durations, standard-class waiting
   cost, completion/censoring, and recovery durations. Report paired differences
   and uncertainty across runs; do not treat correlated incidents as independent
   repetitions. Avoid p99 claims at this sample size. Keep censored runs visible
   rather than silently computing percentiles only over completed incidents.
7. Generate ECDFs or class-latency plots, paired comparison plots, and a recovery
   timeline from the raw artifacts. Report negative and inconclusive results.

The [execution guide](RESEARCH_RUNNING.md) provides the runner and artifact-only
analysis commands, local validation evidence, and the unrun Docker gate. The
runner rejects invalid configuration and reused output directories, returns
nonzero on correctness/cleanup failure or an unobserved mixed-class backlog,
and preserves invalid trials. Offline dry runs generate plans only. Four real
calibration trials, each with a separate warmup, must pass before parameters
can be frozen and the forty-run batch started.

## Limits and completion criteria

Shared Codespaces resources, client/server co-location, instrumentation overhead,
small finite workloads, responder-matching cost, synthetic delays, and prefetch
selection limit generalization. Finite bursts do not prove starvation freedom
under a sustained overload. A single app cannot establish horizontal scaling or
WebSocket fan-out. A broker stop/start does not model network partitions, database
data loss, machine failure, or every retry failure boundary.

The research checkpoint is complete only when another developer can reproduce
the pinned setup, execute the declared trials, regenerate the figures, and trace
every reported number to retained raw observations and correctness checks. The
report must explain the mechanism, fairness tradeoff, uncertainty, and limitations.
Until then Beacon has a tested systems implementation and locally tested study
tooling, not measured research results.
