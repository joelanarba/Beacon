# Authentication and security review

The latest recorded real-service run at `cd9c753` passed all 21 authentication
cases inside 100 application regressions, alongside 17 service scenarios and
30 harness checks. This is a focused authentication review, not a complete
security assessment. See [VALIDATION_HISTORY.md](VALIDATION_HISTORY.md) for the
failed startup run, subsequent correction, and exact evidence directories.

## Corrected findings

| Finding | Severity | Evidence | Correction |
|---|---|---|---|
| Concurrent refresh-token reuse | High | Independent transactions could both rotate one token | Refresh and logout lock the same row and refresh its ORM state; the concurrency test permits one rotation and rejects the other with 401 |
| Missing dispatcher WebSocket role gate | Medium | Signed tokens with missing or unrelated roles were accepted | Require DISPATCHER or ADMIN; role failures close with 4403 and invalid credentials with 4401 |
| Optional token identity/expiry claims | Medium | Signed tokens missing required claims could pass decoding | Require `sub`, UUID `jti`, `iat`, `exp`, and a recognized token type; reject malformed numeric claims |
| Refresh row not bound to owner and stored expiry | Medium | A token could pass despite an expired stored row or different owner | Match the locked row to both token ID and subject; check stored expiration |
| Old form-parser pin | High component advisory | `python-multipart==0.0.9`; callback routes use `request.form()` | Pin 0.0.32 and verify existing form regressions; application request limits remain unfinished |
| Old JWT-library pin | Medium for this review | `python-jose==3.3.0` predates upstream fixes | Pin 3.5.0 and retain the configured algorithm allowlist; verify token and login/rotation cases |

Claim/role/owner tests use locally signed synthetic tokens. They establish
validation behavior, not signature forgery or possession of another signing key.
Existing Beacon tokens contain the required claims. No schema migration was
needed for these changes.

The initial boundary suite reproduced 13 failures and two passes before fixes.
The local corrected run passed 122 tests and skipped eight service-dependent
cases. The later Codespaces run exercised real services with no skips. These
results do not establish the absence of other vulnerabilities or validate remote CI.

Dependency evidence recorded for the selected pins:

- [python-jose release notes](https://github.com/mpdavis/python-jose/releases)
- [URL-encoded parser advisory](https://github.com/Kludex/python-multipart/security/advisories/GHSA-5rvq-cxj2-64vf)
- [python-multipart changelog](https://github.com/Kludex/python-multipart/blob/main/CHANGELOG.md)

## Remaining work

| Priority | Work | Acceptance condition |
|---|---|---|
| Before public ingress | Bound request bodies, provider IDs, and field lengths; define callback authentication | Reject excessive or malformed input before unbounded processing or database failures |
| Before public ingress | Review framework and transitive dependencies | Record the resolved inventory and advisory findings; apply compatible fixes and rerun affected tests |
| Token/session policy | Define access-token and active-socket revocation | Document the policy and test it; logout currently revokes the presented refresh token, not already issued access tokens |
| Location claims | Constrain or replace the Accra-centroid fallback | State what happens without a usable location; the completed study uses explicit GPS |
| Notification claims | Define recipient routing, offline behavior, and reconnect replay | Test delivery/replay rules before claiming durable receipt; use one app instance for the present evaluation |
| Research evidence | Back up raw files and review original figures | Preserve the frozen settings, all trials, failed attempts, source history, and analysis outputs |
| Operations | Verify CI, retention, replay, restore, and deployment procedures | Run and record each procedure in an isolated environment before claiming it works |

The first bounded study is complete; its STANDARD latency cost and other limits
are in [RESEARCH_RESULTS.md](RESEARCH_RESULTS.md). AWS deployment and live SMS
are not required for that evaluation. Their configuration files are not evidence
of successful external operation.
