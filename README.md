# Beacon

Beacon is an emergency-coordination backend built with FastAPI, PostgreSQL,
RabbitMQ, and Redis. It accepts app reports and simulated SMS/USSD callbacks,
assigns responders, manages hospital capacity, and publishes dispatcher updates.
The project focuses on delivery reliability, concurrent state changes, and
severity scheduling during backlog and broker recovery.

SMS and USSD use a local provider simulator. AWS templates are included, but
live telecom integration and AWS operation have not been verified.

## What has been tested

| Work | Recorded result | Evidence |
|---|---|---|
| Real-service integration | 17 scenarios and 30 harness checks passed; 100 nested application regressions passed, including 21 authentication cases | [Integration tests](docs/INTEGRATION_TESTING.md) |
| Scheduling study | 40 measured trials and 40 separate warmups passed; all 6,400 measured reports completed assignment | [Study results](docs/RESEARCH_RESULTS.md) |
| Isolation | All 18 integration projects and all 80 measured-batch projects passed teardown and empty-resource checks | [Validation history](docs/VALIDATION_HISTORY.md) |

The integration result applies to `cd9c753`; the measured study applies to
`4304151`. Later presentation changes do not turn those into fresh test runs.
The evidence records exact revisions, commands, service identities, and outputs.

The scheduling study compared equal priority with severity priority in both the
PostgreSQL outbox and RabbitMQ, using ten paired seeds per condition. The table
shows mean paired p95 differences in record-to-assignment seconds, with
exploratory 95% bootstrap intervals. Negative values mean shorter durations
under severity scheduling.

| Condition | CRITICAL p95 difference | STANDARD p95 difference |
|---|---:|---:|
| Healthy burst | -16.370 s [-21.274, -13.493] | -1.546 s [-5.836, +1.106] |
| Broker outage | -11.176 s [-13.162, -9.370] | +2.625 s [+0.501, +4.619] |

CRITICAL durations were lower in both conditions. STANDARD durations increased
during outages; the healthy STANDARD comparison was inconclusive. Mean overall
recovery was descriptively 2.353 seconds longer under severity scheduling. These
are finite-burst results from one shared Codespace, with send-time drift and
prefetch one. They do not establish sustained throughput or production capacity.

## Processing path

1. Ingestion commits the incident, audit entry, and outbox event in one
   PostgreSQL transaction.
2. The outbox relay publishes persistent events to RabbitMQ with publisher
   confirms. Broker outages leave unpublished work in PostgreSQL.
3. Dispatch locks the incident, reserves an available responder and any required
   hospital capacity, then commits the assignment and its notification event.
4. Notification consumers publish dispatcher WebSocket updates and record
   simulated SMS. Failed processing uses bounded retries and database dead letters.

Row locks and constraints guard duplicate assignments and resource release.
Publication is at least once. SMS and socket effects can repeat after a crash;
Beacon does not provide exactly-once recipient delivery. The full contract is in
[RELIABILITY.md](docs/RELIABILITY.md).

## Local setup

Install Docker Engine and Docker Compose v2, then run from the repository root:

```bash
cp .env.example .env
```

Replace `SECRET_KEY` in `.env` with a random value of at least 32 characters.
`openssl rand -hex 32` can generate one. The service passwords in the example
are for the local demo.

```bash
docker compose up -d --build
docker compose exec app alembic upgrade head
docker compose exec app python seed.py
```

After migrations and seeding:

- API documentation: [localhost:8000/docs](http://localhost:8000/docs)
- Provider simulator: [localhost:8000/sim/](http://localhost:8000/sim/)
- Readiness: [localhost:8000/ready](http://localhost:8000/ready)
- Local dispatcher: `dispatcher@example.com` / `beacon-dispatch`

The simulator accepts SMS and USSD reports and shows the dispatcher feed and
Redis SMS sink. The tracked Compose file publishes only port 8000. Grafana,
Prometheus, and the RabbitMQ console require an explicit local port override
before they can be opened from the host.

## Integration tests

The test runner starts disposable PostgreSQL, Redis, and RabbitMQ services. It
uses its own configuration, synthetic data, and randomly assigned loopback ports.
It does not use the development `.env` or database.

```bash
.venv-integration/bin/python scripts/run_integration.py
```

See [INTEGRATION_TESTING.md](docs/INTEGRATION_TESTING.md) for Codespaces setup,
coverage, evidence files, and cleanup rules. A unit-only run with service tests
skipped is not equivalent to this suite.

## Research and evidence

The completed study is documented in [RESEARCH_RESULTS.md](docs/RESEARCH_RESULTS.md).
The [study plan](docs/RESEARCH_PLAN.md) defines the question and controls;
[RESEARCH_RUNNING.md](docs/RESEARCH_RUNNING.md) contains the commands and artifact
format. Experimental code lives in `experiments/`, outside the production image.

Raw runs, failed attempts, plots, environments, local notes, and backups are
ignored by Git. Keep the raw evidence outside Codespaces as well:

```bash
bash scripts/backup-evidence.sh
```

Follow [EVIDENCE_BACKUP.md](docs/EVIDENCE_BACKUP.md) to download and check the
archive. The backup includes the committed source history and saved experiment
files, without copying the root `.env` or live service volumes.

## Limits and remaining work

- Callback authentication, request-size limits, and a broader dependency review
  are still needed before public ingress.
- Missing locations can fall back to an Accra centroid. The study uses explicit GPS.
- Offline recipient delivery, reconnect replay, app-responder routing, and
  WebSocket fan-out across app instances are incomplete.
- Outbox and dead-letter retention and administrative replay remain manual work.
- The original study figures still need visual review, and independent
  reproduction on another environment has not been verified.

See [SECURITY_REVIEW.md](docs/SECURITY_REVIEW.md) for the security backlog. The
[AWS deployment reference](docs/production/aws.md) describes unverified templates;
no cloud deployment is required to reproduce the Codespaces study.

## License

[MIT](LICENSE)
