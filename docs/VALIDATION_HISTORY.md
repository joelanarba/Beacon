# Validation history

These results belong to their recorded revisions. Later edits do not count as
new executions. Codespaces results were reviewed through exported terminal,
manifest, JUnit, command-log, and state summaries. Raw directories stay outside
Git and should be retained using [EVIDENCE_BACKUP.md](EVIDENCE_BACKUP.md).

## Reliability and integration

| Revision or event | Result | Environment and limits |
|---|---|---|
| `2420672` reliability pass, 2026-10-19 | 70 passed, 8 skipped; targeted migration/integrity suite 20 passed | Python 3.11.17, disposable PostgreSQL 16.15. Redis/RabbitMQ tests skipped. No Docker or deployment validation. |
| Codespaces creation, 2026-10-20 | Container build failed before tests; all 17 service scenarios not run | Inherited Yarn APT source failed signature verification. The dev-container Dockerfile removes the unused source before Docker Features install. |
| `fd1a4e3`, first completed integration run | 13 service scenarios passed, 4 failed; 22 harness checks passed; runner exit 1 | One application reconnection defect and three stale controller-port failures. |
| `7894efd`, integration rerun | 17 service scenarios, 30 harness checks, and 79 nested regressions passed; no skips; runner exit 0 | Python 3.11.13, real disposable PostgreSQL/Redis/RabbitMQ. All 18 project teardowns and empty checks passed. |
| Authentication changes, local run | 122 passed, 8 skipped in 51.54 s, including 21 authentication cases and 30 harness checks | Python 3.11.17 and disposable PostgreSQL. Redis/RabbitMQ unavailable locally. |
| `70e49aa`, authentication integration run | 16 service scenarios passed, 1 failed; 30 harness checks and 100 nested regressions passed | Broker startup failed with a cookie-permission error. All 18 projects cleaned up. |
| `cd9c753`, integration rerun | 17 service scenarios, 30 harness checks, and 100 nested regressions passed, including 21 authentication cases; no failures/errors/skips; runner exit 0 | All 18 project teardowns and empty checks passed. Pytest reported 47 top-level cases in 460.54 s. |

The nested regression suite runs inside one service scenario. Its cases are not
additional top-level scenarios. The latest integration evidence is:

```text
/workspaces/Beacon/artifacts/integration/20261020T041411Z-2d56efdef10745068e92faef78dd44cf
```

Earlier completed runs are retained at:

```text
/workspaces/Beacon/artifacts/integration/20261020T021326Z-ca7a47316bf546c79edb020c825bde8a
/workspaces/Beacon/artifacts/integration/20261020T024338Z-586204026d9c47eabf7f310a17ddfd00
/workspaces/Beacon/artifacts/integration/20261020T034819Z-92d35c9df44b4089bddb750b56210fb5
```

### Broker restoration and controller ports

The first completed run left two report messages ready with no consumers after
a broker restart. `aio-pika` 9.4.1 keeps queues in weak registries. Beacon now
retains the queue objects so the library can restore their bindings and consumers.
The affected case passed in the subsequent run. See the pinned
[queue registry implementation](https://github.com/mosquito/aio-pika/blob/9.4.1/aio_pika/robust_channel.py).

Three other failures came from controller connections using old published Redis
or PostgreSQL ports after restart. Saved assignments, outbox rows, queues, and
container bindings distinguished those test failures from lost application work.
The controller now discovers the ports again after recovery.

### RabbitMQ cookie permissions

The run at `70e49aa` failed with
`Error when reading /var/lib/rabbitmq/.erlang.cookie: eacces`; Docker recorded
exit code 1 and no OOM kill. A root health probe creating a cookie before the
broker completed startup is an inferred mechanism. File ownership was not
captured in that failed run.

The integration probe now waits for a nonempty cookie and runs diagnostics
through the image entrypoint's privilege drop. The subsequent `cd9c753` run
passed. The inference follows the
[upstream image discussion](https://github.com/docker-library/rabbitmq/discussions/696)
and [entrypoint](https://github.com/docker-library/rabbitmq/blob/master/docker-entrypoint.sh).

## Scheduling study

| Revision | Result | Evidence directory under `/workspaces/Beacon/artifacts/study/` |
|---|---|---|
| `ecf72ad` | Four warmups and two healthy calibration trials passed; both outage trials invalid; exit 1; all nine project cleanups passed | `calibration-20261020T101144Z` |
| `3e946b1` | Four calibration trials and four warmups passed; raw-evidence gate passed; exit 0; all nine projects verified empty | `calibration-20261021T020432Z` |
| `4304151` | Forty measured trials and forty warmups passed; 6400 measured and 320 warmup reports completed; all eighty projects verified empty; study and analysis exit 0 | `batch-20261021T023128Z` |

The first calibration's recovery timestamp was premature: `ping` reported a
reachable Erlang runtime while `rabbitmqctl list_queues` still reported that the
RabbitMQ application was not running. The study health probe changed to
`check_running`, retaining the cookie guard and strict rejection of queue errors
outside the injected outage. The original calibration stays invalid.
The [RabbitMQ diagnostics manual](https://www.rabbitmq.com/docs/3.13/man/rabbitmq-diagnostics.8)
defines the two checks.

Local correction checks passed 71 tests in 47.44 s: 41 study checks and 30 harness
checks. They included scripted health-probe responses and sampling-boundary
tests, not a live local broker restart. The full measured results, uncertainty,
drift, and limitations are in [RESEARCH_RESULTS.md](RESEARCH_RESULTS.md).

The later presentation cleanup changes comments, labels, and documentation.
Recorded Docker results still apply to the revisions above. Frozen source hashes
are exact, so analysis or replay of a past run should use its recorded checkout.
Independent reproduction, original-figure visual review, AWS operation, live SMS,
and remote CI execution remain unverified.

## Presentation and backup checks, 2026-10-21

The cleanup passed 82 local tests with no failures or skips in 27.57 s: 41 study
checks, 30 harness checks, and 11 channel/health checks. JUnit is retained at
`artifacts/cleanup-review/20261021T040800Z/junit.xml`. There was one multipart
import deprecation warning. Ruff passed; Black found all 79 Python files
unchanged. Python 3.11 syntax, simulator JavaScript syntax, configuration parsing,
documentation links, shell syntax, and diff checks passed.

Executable Python syntax trees matched the preceding commit in all 21 edited
Python files after excluding docstrings. The edits remove punctuation and
shorten comments/docstrings. Simulator and Grafana changes are labels only.
All files in the public working-tree inventory were ASCII text, with no
emojis or em dashes. Local instruction files, credentials, results, backups,
environments, and infrastructure state remain ignored. The obsolete integration
plan was retired after its relevant evidence was retained here.

The backup script was exercised locally. Archive comparison and SHA-256 checks
passed. Its Git bundle contained normal branch refs and HEAD, without private
tooling refs. Cloning the bundle into a separate restore directory recovered the
recorded measured-study commit. The archive contained the expected evidence and
no root `.env`, environments, or nested earlier backups. This local test does not
establish that the Codespaces evidence has been downloaded or stored elsewhere.
