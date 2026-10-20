# Real-service tests in Codespaces

The Codespaces rerun on 2026-10-20 passed **all 17 real-service scenarios and
30 harness checks**, with zero failures, errors, skips, or scenarios left unrun.
The nested regression suite passed **79 tests with no skips**. All 18 disposable
projects passed cleanup checks. The passing evidence below applies to commit
`7894efd93fe003d157d8beed28d705fedac17729`; the preceding failed run and earlier
70 passed / 8 skipped reliability result remain separate records.

The subsequent [authentication checkpoint](SECURITY_REVIEW.md) changes token
validation and two dependency pins. It requires a fresh Codespaces run: update
`.venv-integration` with `pip install -r app/requirements.txt` before invoking
the runner. The passing result below applies to its recorded revision only.

## Run

Create a Codespace, or rebuild an existing one, using this repository's
`.devcontainer/devcontainer.json`. It selects Python 3.11 and Docker-in-Docker;
its Dockerfile removes the unused inherited Yarn APT source before Features
install. The creation command installs `app/requirements.txt` in `.venv-integration`.
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

## Recovery mode: inherited Yarn repository key

The reported Codespaces creation log on 2026-10-20 failed at 01:41:32 UTC while
installing Docker-in-Docker 2.17.0. The base image downloaded successfully; the
feature's `apt-get update` then rejected `https://dl.yarnpkg.com/debian` with
`NO_PUBKEY 62D54FD4003F6525` and exited 100. Codespaces started its Alpine recovery
container. Docker installation and the Python dependency setup did not complete.

This is a confirmed environment-build failure. The integration runner did not
start: **0 scenarios passed, 0 failed, 0 skipped, and 17 were not run**. There are
no integration-run artifacts to diagnose, and the harness created no test stack
whose teardown could be verified.

The scoped correction is `.devcontainer/Dockerfile`, based on the same Python
image, which removes `/etc/apt/sources.list.d/yarn.list` before Features run.
Beacon has no Yarn dependency. APT signature verification remains enabled for
the remaining sources. A similar inherited-source failure and Dockerfile
workaround are recorded in the [upstream issue](https://github.com/devcontainers/features/issues/1543).
Application code, service configuration, and scenario assertions are unchanged.

To retry from the existing recovery terminal, first preserve any local edits,
then update the checkout:

```bash
git pull --ff-only origin main
```

Use **Codespaces: Rebuild Container** from the Command Palette. Removing the
source in the recovery container itself does not change the image used for the
real build. After rebuilding and completing dependency installation, run the
three commands in the Run section. If creation fails again, inspect the first
error in **Codespaces: View Creation Log** before changing another component.

JSON, Dockerfile-path, and diff checks passed locally. The matching devcontainers
CLI 0.83.3 could not finish `read-configuration` because it requires a Docker
executable, which is absent on the local host. Subsequent Codespaces terminal
output confirmed a working Docker client and daemon (29.9.1-1), Compose v2.40.3,
and the integration Python environment. The environment blocker was cleared;
service results are recorded below.

## First completed Codespaces run and focused corrections

Command: `.venv-integration/bin/python scripts/run_integration.py`, followed by
`echo "runner_exit=$?"`. Evidence directory:
`/workspaces/Beacon/artifacts/integration/20261020T021326Z-ca7a47316bf546c79edb020c825bde8a`.
The user supplied the terminal report and selected saved command, queue,
database, and service-log artifacts for review on 2026-10-20. The complete
artifact directory remains in Codespaces; it has not been copied to this checkout.

| Scope | Passed | Failed | Skipped | Not run |
|---|---:|---:|---:|---:|
| Real-service scenarios | 13 | 4 | 0 | 0 |
| Local harness checks | 22 | 0 | 0 | 0 |
| All top-level cases | 35 | 4 | 0 | 0 |

Pytest finished in 850.37 seconds; the runner exited 1. The regression-wrapper
scenario passed, but its nested JUnit case counts have not been independently
reviewed. These totals describe the failed run, not the corrected implementation.

Two distinct causes account for the four observed failures:

- **Application defect: consumer restoration after a broker restart.**
  `test_broker_reconnection_with_an_inflight_delivery` timed out assigning an
  incident. Its snapshot contained two `REPORTED` incidents, no assignments,
  both report outbox rows published, two ready dispatch messages, and zero
  consumers on both worker queues. RabbitMQ accepted the app's connection after
  restarting. In pinned aio-pika 9.4.1, the robust channel stores queues in weak
  sets. Beacon discarded the queue objects returned during consumer startup,
  so the channel had no queues or consumer registrations to restore. A local
  test using that library's actual queue registry and restore path reproduced
  the loss with broker I/O replaced. `EventBus` now retains declared queues until
  close. This does not change the retry schedule or delivery guarantees.
- **Test defect: controller connections to stale published ports.**
  The Redis dispatch-retry and notification-retry cases timed out reading
  simulated SMS with `ConnectionError`. Their assignment snapshots succeeded,
  retries were published, both queues drained with one consumer each, and no
  dead letters were recorded. Saved bindings showed Redis ports changing from
  `32802` to `32806` and `32810` to `32813`. The PostgreSQL retry-handoff case
  timed out with `ConnectionRefusedError`; its PostgreSQL binding changed from
  `32817` to `32820`, and Redis from `32816` to `32821`. Recovery refreshed app
  and broker ports but left PostgreSQL and Redis controller ports unchanged.
  Recovery now discovers those ports again, including when HTTP readiness is
  deferred. Application peers continue to use internal service DNS. The failed
  probes do not establish missing SMS or failed assignment inside the app.

The pinned queue behavior is visible in the
[aio-pika 9.4.1 source](https://github.com/mosquito/aio-pika/blob/9.4.1/aio_pika/robust_channel.py).
The smallest corrections retain those queue objects and refresh controller
bindings; they add no fixed host ports, dependency upgrades, or longer timeouts.
All seventeen service scenarios and their assertions remain in place.

Cleanup evidence lists **18 successful scoped `down --volumes --remove-orphans`
commands** (one build project and seventeen scenario projects). Follow-up Docker
listings for the integration ownership label found no containers, volumes, or
networks. No leaked owned data resources were observed. Images and build cache
are intentionally retained. Stale controller ports prevented database/SMS
snapshots in affected cases; preserve those capture errors with the original run.

Local regression checks on CPython 3.11.17 initially reproduced three failures:
PostgreSQL port refresh, Redis port refresh, and queue restoration. After the
corrections, this command passed **32 checks** (30 harness checks, queue
restoration, and existing initial-worker-setup recovery):

```text
python -m pytest -c pyproject.toml integration_tests/test_harness.py app/tests/test_bus_priority.py::test_consumers_remain_registered_for_robust_channel_restore app/tests/test_delivery.py::test_worker_retries_failed_initial_broker_setup -q -p no:cacheprovider -p pytest_asyncio.plugin
```

Ruff, Black, and diff checks passed for the focused changes. The queue test checks
restoration after caller references disappear and release on bus close. Port
tests cover all four services with readiness enabled and deferred. Docker is
unavailable on this Windows host, so **none of the corrected service scenarios
has been rerun here**. In Codespaces, preserve the old evidence directory, update
the checkout, and run the complete suite again:

```bash
git pull --ff-only origin main
.venv-integration/bin/python scripts/run_integration.py
echo "runner_exit=$?"
```

No dev-container rebuild or dependency reinstall is required for these Python
changes; the runner builds the app image from the updated checkout. The harness
collects 47 top-level cases: 30 harness checks and the same 17 service scenarios.

## Passing Codespaces rerun on 2026-10-20

The rerun tested commit `7894efd93fe003d157d8beed28d705fedac17729`, confirmed
by the saved `git rev-parse HEAD` command. The user supplied the terminal output
and read-only summaries of the saved manifest, both JUnit files, all seventeen
service cases, and cleanup commands. These artifacts remain in Codespaces under:

```text
/workspaces/Beacon/artifacts/integration/20261020T024338Z-586204026d9c47eabf7f310a17ddfd00
```

The exact user commands were:

```bash
git pull --ff-only origin main
.venv-integration/bin/python scripts/run_integration.py
echo "runner_exit=$?"
```

The runner recorded CPython 3.11.13, start time `2026-10-20T02:43:38.548103+00:00`,
finish time `2026-10-20T02:51:39.136301+00:00`, `status=passed`, and
`pytest_exit_code=0`. Pytest reported **47 passed in 479.69 seconds**; the shell
reported `runner_exit=0`.

| Scope | Passed | Failed | Errors | Skipped | Not run |
|---|---:|---:|---:|---:|---:|
| Real-service scenarios | 17 | 0 | 0 | 0 | 0 |
| Local harness checks | 30 | 0 | 0 | 0 | 0 |
| All top-level cases (`junit.xml`) | 47 | 0 | 0 | 0 | 0 |
| Nested existing regressions (`regression.xml`) | 79 | 0 | 0 | 0 | 0 |

The nested report is
`beacon-it-89764044d3a54384b0138c5478594269/regression.xml`. Its tests run inside
one of the seventeen scenarios; the 79 are not additional top-level scenarios.
Individual case review confirmed all nine fault cases, seven pipeline cases,
and the regression wrapper passed, including the four cases that failed before
the queue-retention and controller-port corrections.

Cleanup review found **18 scoped teardown commands, all successful**, with all
18 projects passing their subsequent empty-container, empty-volume, and
empty-network checks. Follow-up Docker listings for `com.beacon.integration.run`
also showed no containers, volumes, or networks. No cleanup or data-isolation
failure was observed; images and build cache remain intentionally retained.

There is no actionable failed scenario in this run and no further application
fix justified by these results. Pytest reported one passlib `crypt` deprecation
warning; it did not fail a test. This records disposable PostgreSQL, Redis, and
RabbitMQ correctness coverage for the tested revision. Live SMS, production/AWS
operation, performance/capacity, and broader audit findings remain unverified by
this suite. Preserve both failed and passing evidence directories for comparison.

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
