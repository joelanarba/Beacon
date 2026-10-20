# Authentication checkpoint and remaining security work

Reviewed on 2026-10-20 after the passing real-service baseline at
`7894efd93fe003d157d8beed28d705fedac17729`. That baseline passed 17 service
scenarios, 30 harness checks, and 79 nested regressions. It does not validate the
subsequent changes in this checkpoint. The later run at
`cd9c753d6ef8587fd7b9f15e3710c41c86cc7a22` passed the same 17 service scenarios
and 30 harness checks, plus all 100 nested regressions, including the 21 new
authentication cases; its evidence is recorded below. This is a focused review,
not a complete security assessment or approval for public emergency-service
operation.

## Findings and corrections

| Finding | Severity | Evidence before correction | Correction and acceptance check |
|---|---|---|---|
| Concurrent reuse of a refresh token | High | `auth/router.py:refresh` read the token without a lock. Two independent PostgreSQL transactions both issued a fresh token pair from one presented token. | Refresh and logout lock the same token row and refresh its ORM state. The deterministic concurrency test now permits one rotation and rejects the other with 401; exactly one replacement remains active. |
| Dispatcher WebSocket lacked a role gate | Medium | `realtime/hub.py:ws_dispatch` accepted locally signed access tokens with missing, empty, or unrelated roles in TestClient. | Require `DISPATCHER` or `ADMIN` before accepting. Unauthorized roles close with 4403; invalid credentials close with 4401. Both permitted roles still connect. |
| Token identity and expiration were optional | Medium | `auth/security.py:decode_token` accepted signed tokens missing `sub`, `jti`, `iat`, or `exp`. An HTTP probe with no expiration reached routing instead of receiving 401. | Require those claims, a UUID token ID, a nonempty subject, and a recognized access/refresh type. Malformed numeric claims also produce authentication errors. |
| Refresh lookup ignored stored expiry and owner | Medium | Route tests accepted a token whose stored row was expired, and a locally signed token whose subject differed from that row's user. | Bind the locked lookup to both token ID and subject; check the stored expiration before issuing or revoking anything. |
| Vulnerable form-parser dependency pin | High component advisory; application exposure established by inspection | `python-multipart==0.0.9`; public callback routes use `request.form()`. The maintainer identifies the same URL-encoded parser path as affected. No denial-of-service attack was run. | Pin 0.0.32 and verify existing application and provider-form regressions locally. App-level body/field limits remain outstanding. |
| JWT dependency pin predates security fixes | Medium for this checkpoint; application exploitability not demonstrated | `python-jose==3.3.0`. The upstream 3.4.0 release fixes algorithm-confusion and JWE-decompression issues. Beacon's configured HS256 JWT path is not evidence that the OpenSSH/JWE attack paths are reachable. | Pin 3.5.0, preserving the configured algorithm allowlist; verify token and login/rotation tests. |

Missing-claim, role, and owner tests use locally signed synthetic tokens. They
demonstrate validation policy; they do not demonstrate signature forgery or
access to another user's signing key. Existing Beacon-issued tokens already
contain the required identity and expiration claims. No schema migration is
needed for these changes.

Primary dependency evidence:

- [python-jose release notes](https://github.com/mpdavis/python-jose/releases):
  3.4.0 fixes CVE-2024-33663 and CVE-2024-33664; 3.5.0 is the selected pin.
- [URL-encoded parser advisory](https://github.com/Kludex/python-multipart/security/advisories/GHSA-5rvq-cxj2-64vf):
  versions below 0.0.30 are affected by quadratic parsing of semicolon-separated
  input; the advisory explicitly includes Starlette's `request.form()` path.
- [python-multipart changelog](https://github.com/Kludex/python-multipart/blob/main/CHANGELOG.md):
  0.0.32 includes subsequent multipart parsing improvements.

## Verification and isolation

The initial new boundary suite reproduced **13 failures and 2 passes** before
application corrections. In particular, the concurrency test holds a token row
in a third transaction until both rotation requests are waiting inside their
transactions; it does not depend on an arbitrary sleep to create a race.

After corrections and installation of the two new pins in the temporary Python
3.11.17 environment, the final command was:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:BEACON_TEST_DATABASE_URL = 'postgresql+asyncpg://beacon_test@127.0.0.1:51451/beacon_test_validation'
python -m pytest -c pyproject.toml app/tests integration_tests/test_harness.py -q -ra -p no:cacheprovider -p pytest_asyncio.plugin --junitxml=artifacts/integration/security-final-20261020/junit.xml
```

Here `python` was
`C:\Users\Dell\AppData\Local\Temp\beacon-validation-venv\Scripts\python.exe`.
The final saved JUnit contains **130 cases: 122 passed, 8 skipped, zero failures,
and zero errors**, in 51.54 seconds. This includes 21 authentication-boundary
cases, 30 harness checks, and the remaining application regressions. The eight
skips require Redis or RabbitMQ, which are unavailable on this Windows host.

PostgreSQL 16.15 ran only on loopback using the previously created disposable
test cluster. Database fixtures migrated fresh random schemas and removed only
their own schemas. Development `.env` and `.venv` files were not modified. The
test cluster was stopped after validation; reports and cluster data were retained.

Ruff passed for `app`, `integration_tests`, and the runner. Black checks passed;
`pip check` reported no broken requirements. Those checks establish formatting
and dependency compatibility, not the absence of vulnerabilities. The final run
emitted multipart-import and Starlette/AnyIO deprecation warnings.

The Codespaces runs below exercised the rebuilt image and real services,
including the new authentication regressions. The first failed during broker
startup; the subsequent run passed after the scoped health-check correction.
Remote CI execution has not been verified.

## Codespaces run: authentication passed, broker startup failed

Tested revision: `70e49aa35dbd86d6906b5dc20d15ef72523a39bf`, confirmed by the
saved command log. The user supplied terminal output and read-only summaries of
the saved manifest, RabbitMQ service log/container state, nested JUnit report,
and cleanup checks. Evidence remains in Codespaces at:

```text
/workspaces/Beacon/artifacts/integration/20261020T034819Z-92d35c9df44b4089bddb750b56210fb5
```

The user ran the pull, dependency-install, runner, and exit-status commands in
the validation gate below. CPython was 3.11.13. The manifest records start
`2026-10-20T03:48:19.212528+00:00`, finish
`2026-10-20T03:56:32.315489+00:00`, `status=failed`, and exit code 1. Pytest
reported 492.55 seconds.

| Scope | Passed | Failed | Errors | Skipped | Not run |
|---|---:|---:|---:|---:|---:|
| Real-service scenarios | 16 | 1 | 0 | 0 | 0 |
| Harness checks | 30 | 0 | 0 | 0 | 0 |
| All top-level cases | 46 | 1 | 0 | 0 | 0 |
| Nested application regressions | 100 | 0 | 0 | 0 | 0 |
| Authentication-boundary subset of nested regressions | 21 | 0 | 0 | 0 | 0 |

The failed case was
`test_initial_broker_outage_and_worker_restart_preserve_accepted_work[stack0]`.
It accepted a report with the broker absent and restarted the app, then failed
in `Stack.recover("rabbitmq")`: the newly started broker exited before recovery
assertions could run. RabbitMQ reported
`Error when reading /var/lib/rabbitmq/.erlang.cookie: eacces` during prelaunch.
Docker recorded `ExitCode=1` and `OOMKilled=false`. The first health probe began
at `03:49:17.978897386Z`; the broker logged the cookie error at approximately
`03:49:19.308Z`. This is a confirmed test-stack/environment startup failure,
not a failed authentication assertion. It still makes the full run a failure.

The integration health check invoked `rabbitmq-diagnostics` directly under the
image's default root user. A root probe can create a restrictive root-owned cookie
after the image entrypoint's ownership adjustment but before the broker creates
its own cookie. This mechanism is an **inference** supported by the configuration,
timing, and [upstream image discussion](https://github.com/docker-library/rabbitmq/discussions/696).
The failed run did not capture file ownership; do not claim that it did.

The scoped correction makes the health check wait for an existing nonempty cookie,
then invokes diagnostics through `docker-entrypoint.sh`, which drops privileges
for RabbitMQ commands. The [upstream entrypoint](https://github.com/docker-library/rabbitmq/blob/master/docker-entrypoint.sh)
documents this dispatch. Cookie permissions, authentication, wait limits, and
scenario assertions are unchanged. The broker and app images are not upgraded.
No existing volume is deleted or manually repaired by the correction.

Local follow-up checks passed: `python -m pytest -c pyproject.toml
integration_tests/test_harness.py -q -p no:cacheprovider -p pytest_asyncio.plugin`
reported **30 passed** in 3.65 seconds, with the multipart-import warning. The
official standalone Compose v2.39.4 executable normalized the updated file with
`--env-file <empty.env> -f docker-compose.integration.yml -p <generated-project>
config --format json`; inspection confirmed the guarded health command and
unchanged interval, timeout, and retry count. Diff checks passed. This offline
configuration check requires no Docker daemon and is not runtime validation.

All **18 scoped teardown commands succeeded**, and all 18 projects passed their
subsequent empty-resource checks. Follow-up ownership-label listings contained no
containers, volumes, or networks. No cleanup leak was observed. The trapped
bcrypt version warning and the `crypt`/multipart import warnings are distinct
from the broker's fatal cookie-permission error.

This failed run did not validate the corrected health check. Its subsequent
runtime validation is the separate Codespaces rerun below, not a local Windows
Docker run.

## Passing Codespaces rerun after the health-check correction

Tested revision: `cd9c753d6ef8587fd7b9f15e3710c41c86cc7a22`, confirmed by the
saved `git rev-parse HEAD` output. The user supplied the runner terminal output
and read-only summaries of the saved manifest, both JUnit files, and scoped
cleanup checks. The complete artifacts remain in Codespaces at:

```text
/workspaces/Beacon/artifacts/integration/20261020T041411Z-2d56efdef10745068e92faef78dd44cf
```

The execution commands were:

```bash
git pull --ff-only origin main
.venv-integration/bin/python scripts/run_integration.py
echo "runner_exit=$?"
```

The manifest records CPython 3.11.13, start
`2026-10-20T04:14:11.753453+00:00`, finish
`2026-10-20T04:21:52.854524+00:00`, `status=passed`, and
`pytest_exit_code=0`. Pytest reported **47 passed in 460.54 seconds**; the shell
reported `runner_exit=0`.

| Scope | Passed | Failed | Errors | Skipped | Not run |
|---|---:|---:|---:|---:|---:|
| Real-service scenarios | 17 | 0 | 0 | 0 | 0 |
| Harness checks | 30 | 0 | 0 | 0 | 0 |
| All top-level cases | 47 | 0 | 0 | 0 | 0 |
| Nested application regressions | 100 | 0 | 0 | 0 | 0 |
| Authentication-boundary subset of nested regressions | 21 | 0 | 0 | 0 | 0 |

The nested report is
`beacon-it-02b444e36e29433fb78a67dc6800dde9/regression.xml`. Its authentication
cases are part of the 100 regressions; those regressions run inside one of the
17 service scenarios and are not additional top-level scenarios.

All **18 scoped teardown commands succeeded**, and all 18 projects passed the
subsequent empty-resource checks. Follow-up integration-label listings contained
no containers, volumes, or networks. No owned data-resource leak was observed;
images and build cache remain intentionally retained.

The formerly failing broker-startup scenario passed in this run. There is no
actionable failure or further application fix justified by these results. One
passing run does not establish that an intermittent race cannot recur, and the
earlier cookie-ownership mechanism remains an inference because ownership was
not captured. Pytest reported `crypt` and multipart-import deprecation warnings;
neither failed a test. This is disposable-service correctness evidence for the
recorded revision, not live SMS, deployment, capacity, performance, or a complete
security assessment. Preserve both failed and passing evidence directories.

## Codespaces validation gate

Update the host virtual environment as well as the checkout. Rebuilding the app
image alone does not update the Python environment that controls the tests.

```bash
git pull --ff-only origin main
.venv-integration/bin/python -m pip install -r app/requirements.txt
.venv-integration/bin/python scripts/run_integration.py
echo "runner_exit=$?"
```

Preserve the new run's `result.json`, `junit.xml`, nested `regression.xml`, command
log, and service snapshots. The expected collection is still 47 top-level cases
(30 harness checks and 17 service scenarios); the nested application suite now
collects 100 cases. These counts were observed for the passing revision above;
future changes require their own run and review of failures and cleanup.

## Remaining work, in order

| Priority | Work and acceptance criteria | Relevant paths |
|---|---|---|
| P0 before public ingress | Bound callback/request sizes and validate provider identifiers and field lengths before database writes. Reject malformed or excessive input without consuming unbounded memory or returning a database error. Define provider authentication independently of reporter login. | `ingestion/api.py`, `models/schemas.py`, `channels/ussd_channel.py`, `config.py` |
| P0 before public ingress | Complete framework and transitive-dependency advisory review, then apply compatible, tested fixes. This checkpoint reviewed only the two dependency pins above; it is not a full vulnerability scan. | `requirements.txt`, container build, dependency inventory |
| P1 | Define access-token and active-socket revocation requirements. HTTP authorization and socket admission currently trust signed claims; logout revokes the presented refresh token, not already issued access tokens or an entire token family. Document expiry behavior and test any stronger policy before changing it. | `auth/`, `realtime/hub.py` |
| P1 for location claims | Replace or explicitly constrain the city-centroid fallback. Unknown or absent locations currently resolve to the Accra centroid by inspection. The proposed experiment uses explicit GPS and makes no claim about arbitrary SMS geocoding. | `dispatch/geo.py`, `channels/sms_channel.py` |
| P1 for notification claims | Specify recipient routing, offline behavior, and reconnect replay. The current dispatcher feed and simulated SMS path do not establish durable receipt by absent clients. Keep the first experiment single-instance. | `realtime/hub.py`, `notification/notifier.py` |
| P1 for research | Rerun calibration after the readiness correction in [RESEARCH_RUNNING.md](RESEARCH_RUNNING.md), then execute the bounded study in [RESEARCH_PLAN.md](RESEARCH_PLAN.md). Save raw results and report unfavorable outcomes too. | First calibration invalid; corrected calibration and measured study not yet run |
| P2 | Verify the clean-checkout and remote CI workflow, pin/record the resolved dependency and image environment, and add retention/replay procedures before long-lived operation. | `.github/workflows/ci.yml`, outbox/dead-letter tooling |

AWS deployment, live SMS, horizontal notification delivery, and large feature
expansion are not prerequisites for the first controlled research evaluation.
