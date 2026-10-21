# Real-service integration tests

The recorded run at `cd9c753d6ef8587fd7b9f15e3710c41c86cc7a22` passed 17
real-service scenarios and 30 harness checks. Its nested application suite
passed 100 tests, including 21 authentication cases. There were no failures,
errors, skips, or unrun scenarios. All 18 disposable projects passed teardown
and empty-resource checks. Python was 3.11.13; the runner exited zero and pytest
reported 47 top-level cases in 460.54 seconds.

Evidence directory:

```text
/workspaces/Beacon/artifacts/integration/20261020T041411Z-2d56efdef10745068e92faef78dd44cf
```

These counts belong to that revision. Earlier failures and corrections are in
[VALIDATION_HISTORY.md](VALIDATION_HISTORY.md). The scheduling experiment has
its own results in [RESEARCH_RESULTS.md](RESEARCH_RESULTS.md).

## Setup and run

Create a Codespace using `.devcontainer/devcontainer.json`. It provides Python
3.11 and Docker-in-Docker and installs `app/requirements.txt` into
`.venv-integration`. After setup finishes, run from the repository root:

```bash
docker version
docker compose version
.venv-integration/bin/python scripts/run_integration.py
echo "runner_exit=$?"
```

For another Linux environment with Python 3.11, Docker Engine, and Compose v2:

```bash
python3.11 -m venv .venv-integration
.venv-integration/bin/python -m pip install -r app/requirements.txt
.venv-integration/bin/python scripts/run_integration.py
```

No development `.env`, SMS account, or AWS credentials are needed. Missing Docker
or an unavailable daemon causes a failure, not a skipped integration run.
Update the virtual environment when dependency pins change. Rebuilding the app
image alone does not update the host test controller.

If Codespaces starts in recovery mode, inspect **Codespaces: View Creation Log**.
The dev-container Dockerfile removes an unused inherited Yarn APT source before
Features install. After changing that configuration, use **Codespaces: Rebuild
Container**. Historical build failures are recorded in the validation history.

## Coverage

The seventeen service scenarios cover:

- Startup from an empty PostgreSQL database, CLI migration to `20261019_0002`, a
  repeated upgrade, repeated documented seeding, Redis GEO data, readiness, and login.
- Real HTTP acceptance through the PostgreSQL outbox, RabbitMQ dispatch,
  hospital reservation, dispatcher WebSocket, simulated SMS, and lifecycle updates.
- Sequential and concurrent app/SMS/USSD replay, identity conflicts, and distinct
  reports without idempotency keys.
- Duplicate AMQP delivery before assignment and after resolution, plus permitted
  duplicate notification effects.
- Last-bed contention, repeated and concurrent resolution, distinct releases
  sharing a hospital, and old resolution after responder reassignment.
- Initial broker outage, broker interruption, worker restart, kill after
  assignment before acknowledgment, and kill after publish confirmation before
  the outbox commit.
- Real Redis interruption during dispatch and notification, PostgreSQL failure
  during retry handoff, default retry exhaustion, and poison messages.
- The existing regression suite against separate disposable services and a
  separate database, with its individual cases saved in nested JUnit.

A test-only launcher holds processing, publication, handoff, and acknowledgment
boundaries for crash tests. It is outside the production image. The app and its
embedded worker restart together. These tests do not establish live-carrier
receipt, exactly-once notifications, or multi-instance WebSocket delivery.

## Isolation and cleanup

`docker-compose.integration.yml` is standalone. The controller provides an empty
env file and a generated `beacon-it-<uuid>` project. It removes inherited Compose
overrides and never invokes the development stack. Ports use ephemeral loopback
bindings. The dev container disables automatic port forwarding.

Each case owns its data-service volumes and network. Ownership labels are
checked before faults and teardown. Containers do not receive the host Docker
socket. The running app and its assertions share the disposable E2E database;
existing ASGI fixtures use a separate regression database and per-test schemas.

Evidence is captured before cleanup on both success and failure. Only the owned
project's containers, volumes, and network are removed. Images and build cache
are retained. An externally killed controller can bypass cleanup. Use the
recorded project names and labels to inspect leftovers; do not use global prune.

## Saved results

The runner prints a new directory under the ignored `artifacts/integration/`.

| File | Contents |
|---|---|
| `result.json` | Command, revision context, Python version, timestamps, counts, and status |
| `junit.xml` | Top-level scenario and harness results |
| `commands.jsonl` | Docker/Git commands, outputs, image/package identities, fault timeline, and project mapping |
| `<project>/regression.xml` | Individual nested application regressions |
| `<project>/services.log` | App and dependency logs before teardown |
| `<project>/*.json` | Incident, assignment, capacity, outbox, dead-letter, queue, and simulator snapshots |

Command logs redact connection passwords and JWTs; JUnit redacts JWTs. Queue
assertions query live RabbitMQ processes. A zero exit requires all seventeen
service scenarios and no skips. Preserve failures and successful runs with
[EVIDENCE_BACKUP.md](EVIDENCE_BACKUP.md).

## Local control checks

Without Docker, the harness can be checked separately. This does not run the
seventeen service scenarios:

```bash
python -m pytest -c pyproject.toml integration_tests/test_harness.py -q -p no:cacheprovider -p pytest_asyncio.plugin
python -m ruff check --no-cache integration_tests scripts/run_integration.py
python -m black --check --workers 1 integration_tests scripts/run_integration.py
```
