# Security Policy

## Supported Scope

AETUS currently targets restricted device networks, lab networks, and private infrastructure. The ingest path supports bearer-token authentication and optional HMAC-SHA256 request authentication. HMAC mode is strongly recommended whenever devices share a routed network or when bearer tokens may traverse infrastructure outside a tightly controlled segment.

Direct public-internet exposure is not considered a supported deployment posture for the current release line without an additional edge layer such as TLS termination, WAF/rate limiting, network policy, and operator authentication.

## Reporting A Vulnerability

Please do not open public GitHub issues for suspected vulnerabilities.

Report security concerns by emailing the repository maintainer listed in the package metadata, or by opening a private GitHub security advisory if available for the repository. Include:

- Affected component: firmware, ingest API, query API, frontend, Python client, Rust client, or deployment config.
- Reproduction steps or a minimal proof of concept.
- Whether credentials, device tokens, HMAC secrets, or telemetry data may be exposed.
- Suggested mitigation if you already have one.

We will acknowledge reports as quickly as practical and coordinate a fix before public disclosure.

## Credential Handling

- Do not commit real Wi-Fi credentials, device tokens, bootstrap tokens, admin tokens, JWT secrets, HMAC secrets, `.env.hil`, generated config headers, or local database files.
- Development tokens such as `devtok_test_001`, `bootstrap_shared_token`, and `aetus` database credentials are fixture values for local compose tests only.
- HIL credentials should live in an untracked `.env.hil` file or the operator shell environment.
- Browser-facing query clients must use short-lived query JWTs, not ingest device tokens or bootstrap tokens.

## Current Security Boundaries

- `POST /v1/ingest` can run in bearer-token mode or HMAC-SHA256 mode.
- `AETUS_HMAC_AUTH_REQUIRED=true` makes ingest HMAC-only.
- `/v1/time` currently uses bearer-token authentication.
- Query API uses HS256 JWTs issued through an admin-token protected endpoint.
- Query JWTs can restrict scopes, devices, streams, time range, and max points.
- Admin/control endpoints are intended for internal networks unless protected by an additional auth/reverse-proxy layer.

## Non-Goals For The Current Release

- Full public-internet hardening.
- Built-in user management or SSO.
- Immediate JWT revocation.
- Per-request nonce/replay protection for HMAC ingest.
- Firmware-side secure element integration.

