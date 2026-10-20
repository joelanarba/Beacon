# Real-service integration test plan

Prepared on 2026-10-19 after checkpoint
`2420672a71776bc4f6e8b5f26e11e7ab353b360d` was pushed to `origin/main`.
This preserves the original plan and checkpoint environment inspection. Phase 2
has since been implemented in `docker-compose.integration.yml`,
`integration_tests/`, and `scripts/run_integration.py`. See
[INTEGRATION_TESTING.md](INTEGRATION_TESTING.md) for the implemented interface and
current validation. The real-service scenarios have not been executed locally.

## Evidence and environment

The preceding reliability pass recorded 70 passed, 8 skipped against disposable
PostgreSQL 16.15 with CPython 3.11.17. The saved JUnit report contains 78 tests,
zero errors, zero failures, and eight skips. This checkpoint review inspected that
report; it did not rerun pytest. The skips require Redis or RabbitMQ.

Checks run again during the checkpoint review:

| Command/check | Result |
|---|---|
| `git fetch origin main` | Local and fetched remote had zero commits of divergence before committing. |
| `python -m ruff check --no-cache app` | Passed using the temporary validation environment. |
| `python -m black --check --workers 1 <32 staged Python paths>` | Passed; all 32 unchanged. The earlier parallel invocation stalled and was interrupted. |
| Python AST parsing with `feature_version=(3,11)` | All 61 tracked Python files parsed. |
| YAML/JSON/TOML parsing | Five YAML, one JSON, and two TOML files parsed. |
| `git diff --check` and `git diff --cached --check` | Passed. |
| Pending-file inventory and credential-pattern scan | 37 coherent files; no deletions, local database/archive files, or credential candidates beyond identified test/demo placeholders. |
| `git show -s --format=fuller HEAD` | Author and committer both `joelanarba <anarbajoel@gmail.com>`; no attribution trailer. |
| `git push origin HEAD:main` | Succeeded, `0f6dda1..2420672`. |
| `git ls-remote --heads origin refs/heads/main` | Matched the full checkpoint hash above. |

The Python executable for the repeated checks was
`C:\Users\Dell\AppData\Local\Temp\beacon-validation-venv\Scripts\python.exe`.
The saved test report was
`C:\Users\Dell\AppData\Local\Temp\beacon-validation-pg-20261019-01a15617\pytest-final.xml`.
Black used `BLACK_CACHE_DIR=$env:TEMP\beacon-black-cache`. The file arguments came
from `git diff --cached --name-only`, filtered to `.py`. These paths are local
evidence locations, not prerequisites for a clean checkout.

Runtime inspection, performed without installing software or changing host features:

| Exact command/check | Result |
|---|---|
| `Get-Command docker -ErrorAction SilentlyContinue` | No Docker command in PATH. |
| `Test-Path` for `C:\Program Files\Docker\Docker\resources\bin\docker.exe`, `C:\Program Files\Docker\Docker\resources\docker.exe`, and `C:\ProgramData\DockerDesktop\version-bin\docker.exe` | All false. |
| `Get-Command docker, podman, redis-server, rabbitmq-server, python -ErrorAction SilentlyContinue` | Only Python returned, at `C:\Python314\python.exe`. |
| `Get-Service -Name '*docker*','*redis*','*rabbit*','*postgres*' -ErrorAction SilentlyContinue` | No matching services returned. |
| `wsl --list --verbose` outside the sandbox | Ubuntu, stopped, WSL version 2. The initial sandboxed listing was denied. |
| `wsl -d Ubuntu -- sh -lc 'for cmd in docker podman redis-server rabbitmq-server psql python3; do command -v "$cmd" \|\| true; done; if command -v docker >/dev/null 2>&1; then docker version; docker compose version; fi; if command -v podman >/dev/null 2>&1; then podman version; fi'` | Failed before the Linux command ran: virtualization unavailable, `Wsl/Service/CreateInstance/CreateVm/HCS/HCS_E_HYPERV_NOT_INSTALLED`. |

The escaped pipes in the table represent literal shell `||`, not backslashes in
the executed command. No Docker version/build/startup command, real Redis or
RabbitMQ test, or new full-stack test completed in this review. GitHub Actions
execution was not inspected. Running the plan locally requires a working Docker
Engine and Compose runtime; this host's WSL virtualization issue must be resolved
by its operator, or the tests must run on an isolated Linux Docker host/CI runner.
Image/dependency downloads will also require network access there.

## Existing paths and gaps

- `docker-compose.yml` uses fixed `beacon-*` container names, persistent named
  volumes, `.env`, and application liveness rather than readiness. An ignored
  `docker-compose.override.yml` exists locally. A different project name alone is
  insufficient isolation. Do not run these tests through the development stack.
- `app/Dockerfile` builds Python 3.11, installs `app/requirements.txt`, and starts
  `uvicorn main:app`. Its build context excludes the root `pyproject.toml`.
  Therefore plain pytest inside this image does not necessarily use the project's
  asyncio configuration. Run the test controller from the repository root with
  an explicit config and a Python 3.11 environment.
- `app/alembic/env.py` reads the application database setting. Use the real CLI
  `alembic upgrade head` against an empty disposable database, then check revision
  `20261019_0002`; do not substitute metadata creation or autogeneration.
- `main.lifespan` starts the outbox relay and both consumers in the API process.
  Restarting/killing this app also restarts/kills the worker. No separate worker
  deployment refactor is required for this test phase.
- The current httpx ASGI fixtures neither start lifespan nor exercise the worker.
  Their savepoint-backed request sessions and random schemas cannot be silently
  shared with a separate running app. New E2E tests must use real HTTP, committed
  transactions, and the exact database configured for that app.
- Existing tests cover real database invariants and isolated Redis/broker calls.
  They do not cover the complete running-service path or broker recovery.
- Outbound SMS always uses `SimulatedSmsProvider`. Its Redis sink is the observable
  destination. Dead letters live in PostgreSQL, not a RabbitMQ dead-letter exchange.

## Focused implementation

Add a standalone `docker-compose.integration.yml`, a host-side runner, and
`integration_tests/` outside `app/tests/` so its fixtures cannot inherit the
existing ASGI overrides. Reuse the app Dockerfile. Add only the test infrastructure
needed below; leave development Compose, deployment, and unrelated application
design unchanged unless a failing test establishes a necessary correction.

The test Compose file must define only PostgreSQL 16, Redis 7.2, RabbitMQ 3.13,
a migration job, and one app instance. Record resolved image digests and package
versions in each run. Set all service URLs and test settings explicitly. Supply a
generated empty Compose env file and exclude inherited `COMPOSE_*` overrides.
Do not load `.env`, mount the checkout into the app, copy secrets into its image,
use fixed container names, or attach external networks/volumes.

Each scenario group gets a generated `beacon-it-<uuid>` project and project-owned
volumes/network. Bind any needed HTTP/PostgreSQL/Redis/AMQP ports to randomly
assigned loopback ports. The controller discovers them with `compose port` and
checks project labels, database names, and service identities before mutation.
It must refuse arbitrary external URLs. No host Docker socket is mounted into
containers. The host controller issues fault commands only to its own project.

Use a fresh `beacon_test_e2e` database for each scenario group. Migration job, app,
and assertion connections share that database; the app uses ordinary committed
transactions and its public schema. Restart scenarios preserve their own volumes
until assertions finish. Separately run the existing regression suite with its
random-schema fixtures against another disposable `beacon_test_regression`
database. Never mix these request fixtures with the running app's E2E state.

Create deterministic synthetic responders, GEO positions, a TRAUMA hospital, and
a dispatcher account through a test seeder. Prefer SMS-reachable responders so
the complete assigned path reaches a measurable sink. Verify Redis GEO seeding
explicitly: `seed.py` can log and continue after Redis failure. Exercise the
documented seed command in a separate clean-start smoke check as well.

## Scenarios and pass criteria

| Scenario | Required observations |
|---|---|
| Clean startup and migration | Empty database has no application tables; migration job reaches the recorded head; a second upgrade is harmless. Start app, require `/health` 200 and bounded polling of `/ready` to 200. Run the seed smoke twice and check no duplicate seed records and valid GEO members. |
| Complete incident path | Open an authenticated dispatcher WebSocket, ingest an app report over real HTTP, and follow its ID through incident/audit/outbox, RabbitMQ consumption, one assignment, reserved capacity, dispatcher event, and Redis simulated SMS. Also exercise SMS and a complete USSD callback walk. Assert zero unexpected dead letters. |
| Duplicate ingestion | Sequential and concurrent app keys, SMS provider IDs, and replayed terminal USSD callbacks return the original reference. Assert one incident, one initial reported outbox row, and one assignment. Changed content under an existing key returns 409. Unkeyed app/SMS reports remain distinct. |
| Duplicate broker delivery | Publish copies of the same real reported event with stable identity before/after assignment and after resolution. Assert one assignment and no extra reservation or reopening. Deliver duplicate assigned events too: record notification copies as permitted at-least-once effects, rather than claiming exactly-once SMS or sockets. |
| Resource release and contention | Concurrent distinct reports contend for one bed and available responders. Capacity never leaves its bounds; active responders are unique across assignments. Concurrent repeated resolution releases each assignment once; different resolutions sharing a hospital lose no increments. Replaying an old resolution cannot release a responder subsequently assigned elsewhere. |
| Worker restart/crash | Kill and start the owning app container with its data services intact. Cover accepted work still pending publication, a real broker delivery not yet acknowledged, and confirmation before the outbox commit. Use test-only synchronization barriers for the narrow crash windows; do not depend on lucky sleeps. Assert eventual processing, stable identity, and unchanged database invariants; count repeated notifications separately. |
| Broker unavailable at boot | Start the app without waiting for a broker, ingest through HTTP, observe pending outbox work and `/ready` 503. Start the broker and require automatic consumer setup, ready 200, assignment, and simulated notification without restarting the app. |
| Broker interruption/reconnection | Stop/start RabbitMQ during queued and in-flight work while preserving this project's broker volume. Require eventual reconnection, consumers restored, pending outbox progress, no lost accepted incidents, and no duplicate assignments. |
| Retry recovery and exhaustion | Interrupt Redis so dispatch or simulated SMS raises. Verify durable delayed attempt rows before original messages disappear, increasing bounded delays, recovery after Redis returns, and exhausted retries stored once in `dead_letters`. Include temporary PostgreSQL loss during failure handoff: unrecorded failures must remain broker-owned/requeued. |
| Poison delivery | Send malformed JSON, invalid schema, and invalid attempt headers over real AMQP. Assert one terminal database dead letter per identity/consumer, retained synthetic body/error metadata, no infinite retry loop, and continued processing of a subsequent valid event. |

Use polling with deadlines and explicit barriers, not fixed sleeps as proof of
completion. Capture assignment/capacity state and ready/unacknowledged queue counts
at each fault boundary. The default maximum is five delayed retries plus the
initial attempt; verify that behavior separately from any explicitly accelerated
test timing. Exhausted resource shortages may remain REPORTED with a dead letter;
do not assert automatic administrative replay, which is not implemented.

App-responder routing, no-client WebSocket durability, reconnect replay, and
multi-instance fan-out remain known limitations. Keep one app instance for these
tests. A passing dispatcher socket or simulated SMS test cannot establish those
missing behaviors or live carrier delivery.

## Original proposed commands and evidence capture

The commands below record the proposed interface before implementation. For the
current automated runner, use INTEGRATION_TESTING.md. These proposed commands are
not a record of successful Docker execution.
Run from `C:\dev\Projects\beacon-starter` with an isolated Python 3.11 executable:

```powershell
docker version
docker compose version
& $Python311 -m pytest -c pyproject.toml integration_tests -q -p no:cacheprovider --junitxml="$RunArtifacts\junit.xml"
```

The new integration fixtures should orchestrate these exact forms, using generated
absolute `$ComposeFile`, `$EmptyEnv`, and `$Project` values owned by this run:

```powershell
$ComposeArgs = @('compose', '--env-file', $EmptyEnv, '-f', $ComposeFile, '-p', $Project)
docker @ComposeArgs config --quiet
docker @ComposeArgs build app migrate
docker @ComposeArgs up -d --wait postgres redis rabbitmq
docker @ComposeArgs run --rm --no-deps migrate alembic upgrade head
docker @ComposeArgs run --rm --no-deps migrate alembic current
docker @ComposeArgs up -d --no-deps app
docker @ComposeArgs port app 8000
docker @ComposeArgs port postgres 5432
docker @ComposeArgs port redis 6379
docker @ComposeArgs port rabbitmq 5672
docker @ComposeArgs exec -T app python seed.py
docker @ComposeArgs ps --all
```

The migrate service must use the same built application image and explicit
database settings as app. The controller must also check process exit codes,
schema contents, and actual HTTP readiness; service liveness is insufficient.
For initial-broker-outage coverage, start only PostgreSQL and Redis before app,
and start RabbitMQ after durable HTTP acceptance is established.

Fault operations, each bracketed by scenario assertions and event barriers:

```powershell
docker @ComposeArgs kill -s SIGKILL app
docker @ComposeArgs start app
docker @ComposeArgs stop rabbitmq
docker @ComposeArgs start rabbitmq
docker @ComposeArgs stop redis
docker @ComposeArgs start redis
docker @ComposeArgs stop postgres
docker @ComposeArgs start postgres
```

The existing regression suite should run separately with all three
`BEACON_TEST_*` URLs supplied by the runner from its disposable services:

```powershell
& $Python311 -m pytest -c pyproject.toml app/tests -q -p no:cacheprovider --junitxml="$RunArtifacts\regression.xml"
```

Collect before teardown, including on failure:

```powershell
docker @ComposeArgs ps --all
docker @ComposeArgs images
docker @ComposeArgs logs --no-color --timestamps
docker @ComposeArgs down --volumes --remove-orphans
```

Teardown runs in `finally` only after verifying the generated project and resource
labels; remove only resources belonging to this run. Never run global prune,
clear shared Redis keys, or tear down development Compose. Do not delete evidence
with service volumes: save logs, JUnit, command exit codes, UTC fault timeline,
revision/image/package versions, queue snapshots, database invariant queries,
and synthetic notification counts outside the stack. Redact authentication
tokens and connection credentials. Report unexpected skips as incomplete evidence.

## Next gate

Run the implemented infrastructure on the Docker-enabled Codespaces environment,
then inspect its saved command/result record. If a correctness or security
failure appears, preserve the failed run and report it before expanding scope.
Do not mark this plan verified because the earlier PostgreSQL suite passed.
No AWS deployment, host virtualization changes, live SMS, performance claims,
or unrelated refactoring are part of this phase.
