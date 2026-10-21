# Delivery integrity and verification

Beacon remains a prototype. This document describes the first integrity/recovery
implementation and separates its design from verified integration evidence.

## Processing contract

- Acceptance commits the incident, acceptance audit entry, and `incident.reported`
  outbox record in one PostgreSQL transaction. RabbitMQ is not on the HTTP
  acceptance path. Database failure does not produce a successful acknowledgement.
- A supervised worker retries initial broker setup, declares consumers, and relays
  due outbox records. Publication uses persistent messages, mandatory routing,
  publisher confirms, and a bounded wait. Only confirmed publication marks an
  outbox row published. A broker outage leaves the row pending.
- Publication is at least once under the assumptions that PostgreSQL retains its
  committed data, the worker/dependencies recover, and pending records are retained.
  A crash between confirmation and the outbox commit can republish the event.
- Dispatch locks the incident before claiming resources. A non-REPORTED incident
  is not dispatched again. Unique constraints permit one assignment per incident
  and one unreleased assignment per responder. Reassignment of the same incident
  is not implemented.
- ASSIGNED can advance to EN_ROUTE or RESOLVED; EN_ROUTE can advance to RESOLVED.
  Repeating the current target is a no-op. REPORTED cannot bypass assignment, and
  RESOLVED cannot reopen. Resolution marks the assignment released in the same
  transaction as responder release and the locked hospital capacity increment.
- Hospital capacity must satisfy `0 <= available_capacity <= total_capacity`.
  A medical report without a claimable TRAUMA bed stays reported for retry.
- A consumer acknowledges an exception only after committing a delayed retry or
  a dead letter. If PostgreSQL cannot store that handoff, the broker message is
  negatively acknowledged for requeue after a delay.
- Five delayed consumer retries are configured by default, in addition to the
  initial attempt. Exhausted and malformed deliveries are retained in the
  `dead_letters` database table. This is a database dead-letter store, not a
  RabbitMQ dead-letter exchange. There is no automatic administrative replay yet.
- Retry publication and dead-letter insertion are deduplicated by stable event
  identity and delivery attempt. Legacy broker messages without an ID use a hash
  of their routing key and body.

These mechanisms do not provide exactly-once external delivery. In particular,
SMS or WebSocket effects can repeat if a consumer crashes after sending and before
acknowledging. The SMS implementation is a Redis simulator, not a live carrier.
Successful socket sends do not establish recipient receipt. No-client cases are
not durably retried yet. App responder routing, reconnect replay, and multi-instance
WebSocket fan-out remain incomplete; use one app instance for evaluation.

`/health` reports liveness. `/ready` returns 200 only after broker/consumer setup,
a readable outbox table, and Redis connectivity; otherwise it returns 503. Compose
still uses liveness while migrations are applied explicitly. Readiness is not an
end-to-end incident smoke test.

Worker settings are environment variables with these defaults:

| Variable | Default |
|---|---:|
| `BROKER_TIMEOUT_SECONDS` | 5 |
| `OUTBOX_POLL_SECONDS` | 0.5 |
| `OUTBOX_BATCH_SIZE` | 32 |
| `DELIVERY_MAX_RETRIES` | 5 |
| `DELIVERY_RETRY_BASE_SECONDS` | 1 |
| `DELIVERY_RETRY_MAX_SECONDS` | 60 |

## Ingestion identities

- App clients can supply `Idempotency-Key` with 1-128 characters.
- SMS callbacks can supply the same header or a provider `id` / `messageId` form
  field. The header takes precedence.
- USSD uses its session ID. A completed callback can replay its acknowledgement
  after Redis session cleanup. Reusing the session with a different phone or
  report produces a conflict.
- Keys are scoped by ingress channel and stored as hashes. Reusing a key for
  different normalized report content returns HTTP 409.
- Requests without a stable app/SMS identity create separate incidents, even when
  their text is identical. Content alone cannot distinguish a retry from a new
  emergency. Production provider identity/authentication policy remains work to do.
- A replay returns the original acceptance reference, not a fresh status query.

## Migration

Apply versioned migrations with `alembic upgrade head`; do not generate an initial
migration during setup. Revision `20261019_0002` adds integrity constraints,
idempotency fields, outbox records, and dead-letter storage. It marks assignments
for already-resolved incidents as historical and queues existing REPORTED incidents
for dispatch. It does not repair legacy capacity counts or collapse duplicates.

If existing rows violate capacity or assignment constraints, migration fails.
Inspect and repair those rows explicitly in a backed-up, controlled environment.
Do not delete data to make migration pass. Downgrading removes delivery history
and pending work; it is not a normal recovery operation.

Published outbox and dead-letter rows are retained. There is no retention job or
storage quota yet. Monitor their growth before long-running use. To inspect
delivery metadata without printing incident content:

```sql
SELECT event_id, routing_key, delivery_attempt, publish_attempts, available_at,
       published_at, last_error
FROM event_outbox
WHERE published_at IS NULL
ORDER BY available_at;

SELECT id, consumer, event_id, delivery_attempt, error_type, created_at
FROM dead_letters
ORDER BY created_at;
```

## Safe test configuration

Use Python 3.11 and `app/requirements.txt`. Tests override development service
settings before importing the application. No test-service URL means the relevant
integration tests skip; a green unit-only run is not full integration evidence.

For database tests, set `BEACON_TEST_DATABASE_URL` to a disposable PostgreSQL
database whose name starts with `beacon_test`. Each test migrates a new random
schema and removes only that generated schema on completion. Never point this
variable at an operational database. Concurrency tests use independent connections
and real commits inside the disposable schema.

```powershell
$env:BEACON_TEST_DATABASE_URL = 'postgresql+asyncpg://beacon:TEST_PASSWORD@127.0.0.1:5432/beacon_test'
$env:BEACON_TEST_REDIS_URL = 'redis://127.0.0.1:6379/15'
$env:BEACON_TEST_AMQP_URL = 'amqp://beacon:TEST_PASSWORD@127.0.0.1:5672/'
python -m pytest -q
```

The Redis and RabbitMQ URLs must also identify disposable test services. Redis
simulator tests use unique outbox keys rather than clearing the application's
shared sink. CI provides disposable services and sets all three variables.

Regression coverage includes concurrent duplicate ingestion/dispatch, concurrent
last-bed reservation, repeated and concurrent resolution, reassigned responders,
publication failure, retry/dead-letter handoff, and initial broker setup recovery.
Mocked broker/Redis boundaries are identified in tests; they do not establish
real broker restart behavior, live telecom delivery, or deployment correctness.

## Recorded verification

The Codespaces correctness run at `cd9c753` passed all 17 real-service scenarios,
30 harness checks, and 100 nested regressions, including 21 authentication cases.
All 18 disposable projects passed teardown and empty-resource checks. The suite
covers the complete incident path with simulated SMS, concurrent resource use,
worker kills, broker recovery, retries, and dead-letter handling.

The separate scheduling batch at `4304151` passed forty trials and forty
warmups. All 6400 measured reports completed assignment, with no unresolved work
or dead letters. All eighty batch projects were verified empty after cleanup.
[RESEARCH_RESULTS.md](RESEARCH_RESULTS.md) reports the CRITICAL latency benefit,
STANDARD outage cost, recovery observations, uncertainty, and send-time drift.
Assignment is the study's completion endpoint.

[VALIDATION_HISTORY.md](VALIDATION_HISTORY.md) records earlier failures,
corrections, test counts, evidence directories, and local-only checks. The
[integration guide](INTEGRATION_TESTING.md) contains setup and coverage. The
[security review](SECURITY_REVIEW.md) separates corrected authentication findings
from remaining work. Recorded runs validate their own revisions; later comment,
label, or documentation edits are not fresh Docker executions.
