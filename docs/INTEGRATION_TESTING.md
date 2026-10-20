# Real-service tests in Codespaces

The integration infrastructure is implemented. **Docker service scenarios are
pending execution**; local harness checks are not service-integration evidence.
The earlier reliability result of 70 passed / 8 skipped is separate evidence.

## Run

Create a Codespace, or rebuild an existing one, using this repository's
`.devcontainer/devcontainer.json`. It selects Python 3.11 and Docker-in-Docker;
the creation command installs `app/requirements.txt` in `.venv-integration`.
From the repository root, run:

```bash
docker version
docker compose version
.venv-integration/bin/python scripts/run_integration.py
```

For an existing Codespace on `main`, first preserve any local changes and update
with `git pull --ff-only origin main`, then rebuild using the repository's dev
container configuration. A new Codespace should be created from the updated
`main` branch. If the environment creation command did not finish, use the explicit
Python setup below before running the suite.

For an existing Linux environment with Python 3.11, Docker Engine, and Compose v2,
the explicit setup is:

```bash
python3.11 -m venv .venv-integration
.venv-integration/bin/python -m pip install -r app/requirements.txt
.venv-integration/bin/python scripts/run_integration.py
```

No `.env`, AWS credentials, development database, or SMS account is needed. The
runner requires a local Docker socket and checks daemon/Compose availability.
Tests intentionally fail if the runtime is unavailable; they do not silently
skip. Every scenario uses a fresh project and can take time to start PostgreSQL,
Redis, and RabbitMQ. The default retry-exhaustion test includes delays of 1, 2, 4,
8, and 16 seconds. This is a correctness suite, not a performance benchmark.

## Coverage

Seventeen real-service cases cover:

- Empty PostgreSQL database, versioned CLI migration to `20261019_0002`, repeated
  upgrade, documented seed command twice, Redis GEO seed verification, liveness,
  readiness, and login.
- Real HTTP acceptance through PostgreSQL outbox, RabbitMQ, dispatch, hospital
  reservation, dispatcher WebSocket, simulated SMS, and lifecycle notifications.
- Concurrent app idempotency, provider SMS IDs, complete USSD navigation and
  terminal replay, conflicts, and intentionally distinct unkeyed reports.
- Duplicate AMQP reports before assignment and after resolution. Repeated assigned
  messages demonstrate permitted duplicate SMS effects, not exactly-once delivery.
- Last-bed contention, concurrent repeated resolution, distinct releases sharing
  a hospital, and an old resolution after responder reassignment.
- Worker restart with pending acceptance, initial broker unavailability, broker
  interruption/reconnection, kill after assignment before acknowledgment, and
  kill after publish confirmation before the outbox commit.
- Actual Redis interruption on both dispatch and notification, PostgreSQL failure
  during retry handoff, default retry exhaustion, and malformed/schema/header
  poison messages retained in the database dead-letter store.
- The existing regression suite with all three real services and no skips, using
  a separate database from E2E scenarios. Its JUnit file records individual cases.

The tests use the application's own lifespan and consumers. A test-only launcher
holds named processing, publish, failure-storage, and acknowledgment boundaries
until the controller releases them or kills the app. Its control server and code
exist only in the integration Compose configuration, outside the production image.
The app and embedded worker restart together; no worker deployment refactor is
part of this implementation.

## Isolation and cleanup

`docker-compose.integration.yml` is standalone. The controller supplies an empty
env file and a generated `beacon-it-<uuid>` project, removing inherited Compose
overrides. It never invokes development Compose. Ports bind to ephemeral loopback
addresses, and the dev container disables automatic port forwarding. All data is
synthetic and all notifications use the Redis SMS simulator.

Each case owns its PostgreSQL/Redis/RabbitMQ volumes and network. Faults target
only those containers. The host controller validates project/resource ownership
labels before fault injection and teardown; containers do not receive a Docker
socket. Assertion connections use the same disposable E2E database as the app.
Existing ASGI fixtures use another database with their usual per-test schemas.

Evidence is captured before `finally` cleanup, on success and failure. Cleanup
removes only the generated project's containers, volumes, and network and checks
that none remain. Docker images/build cache are retained for reuse and inspection.
An externally killed controller or terminated Codespace can bypass `finally`;
inspect project names and labels in the command log before any manual cleanup.
Do not use global Docker prune to clean up these tests.

## Results to inspect

Each invocation prints a new directory under `artifacts/integration/`, which is
ignored by Git. Preserve the entire directory when reporting a failure:

| File | Evidence |
|---|---|
| `result.json` | Runner command, Python version, UTC start/end, test/error/skip counts, and pass/fail status. |
| `junit.xml` | Top-level cases, including failures and harness checks. |
| `commands.jsonl` | Exact Docker/Git/regression commands, exit codes, outputs, image IDs/digests, package versions, fault timeline, and test-to-project mapping. |
| `<project>/regression.xml` | Existing suite's individual real-service results. |
| `<project>/services.log` | Timestamped app/dependency logs before teardown. |
| `<project>/*.json` | Incident/assignment/capacity/outbox/dead-letter metadata, live queue counts, and simulated SMS snapshots. |

Command/service logs redact JWTs and connection passwords; JUnit files redact
JWTs. Only synthetic report content is used. Queue drain assertions query live
RabbitMQ queue processes rather than cached management statistics. A zero exit
requires no skips and all seventeen real-service cases to have run. A failure,
cleanup error, or unexpected skip is incomplete validation, not a passing run.

## Local validation of the implementation

On the Windows implementation host, CPython 3.11.17 was used for:

```powershell
python -m pytest -c pyproject.toml integration_tests/test_harness.py -q -p no:cacheprovider
python -m pytest -c pyproject.toml integration_tests --collect-only -q -p no:cacheprovider
python -m ruff check --no-cache integration_tests scripts/run_integration.py
python -m black --check --workers 1 integration_tests scripts/run_integration.py
```

The final local harness run passed **22 checks** in 5.20 seconds, including the
ordering of all four fault barriers. Collection covers 39 top-level cases (22
harness checks and 17 real-service cases). Ruff and Black checks passed. All 70
Python files parsed with Python 3.11 grammar; YAML, JSON, and TOML parsing passed.
The runner's missing-Docker preflight correctly refused to start.

The official standalone Compose v2.39.4 executable was downloaded to a temporary
directory and verified against its published SHA-256. The following offline
configuration check passed using an empty env file, a generated test project, and
`BEACON_IT_IMAGE=beacon-integration:offline-validation`:

```text
docker-compose --env-file <empty.env> -f docker-compose.integration.yml -p beacon-it-00000000000000000000000000000000 config --quiet
```

That check requires no Docker daemon. No PostgreSQL/Redis/RabbitMQ Docker stack, image
build, Codespaces launch, broker fault, or real-service success is claimed from
these local checks. Windows has no Docker Engine; WSL cannot start because host
virtualization is unavailable. The broader authentication, location, WebSocket
replay/fan-out, dependency-security, and live telecom limitations remain open.

Checkpoint review on 2026-10-20 repeated the harness suite (**22 passed**, 3.16
seconds), collected all **17 integration-marked cases** (22 harness cases
deselected), and passed Ruff, Black, Python/configuration parsing, and diff checks.
The normalized Compose configuration also passed checks for all five services,
ownership labels, loopback bindings, private resources, and a shared app/migration
image. Docker remains unavailable on the review host; a fresh WSL startup attempt
returned `HCS_E_HYPERV_NOT_INSTALLED`. No Docker scenario or Docker teardown was
executed by this review.

References for runtime conventions: [Compose CLI](https://docs.docker.com/reference/cli/docker/compose/),
[Codespaces dev containers](https://docs.github.com/en/codespaces/setting-up-your-project-for-codespaces/adding-a-dev-container-configuration/introduction-to-dev-containers),
[Docker-in-Docker feature](https://github.com/devcontainers/features/tree/main/src/docker-in-docker),
and [RabbitMQ queue inspection](https://www.rabbitmq.com/docs/3.13/man/rabbitmqctl.8).
