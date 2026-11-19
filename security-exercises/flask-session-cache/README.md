# Flask session cache security exercise

This directory is an intentionally vulnerable, isolated training fixture. It is not part of the bookmark service's default runtime or Compose test service. Do not deploy it, expose it to a network, or use real credentials.

The sample page uses a Flask session to select a response. A security regression test checks that session-dependent responses include `Vary: Cookie`. On the baseline the test is expected to fail.

## Reproduce in a disposable environment

From this directory, with Python 3.9 or later:

```sh
python -m venv .venv
# Linux/macOS:
. .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m unittest test_session_cache -v
```

Baseline: the signed-in response is returned but the cookie cache-variation assertion fails. The example uses Flask's in-process test client; it does not start a server or proxy and does not demonstrate an actual data leak.

## Security context

The fixture deliberately pins Flask 3.1.2, affected by [CVE-2026-27205 / GHSA-68rp-wp8r-4726](https://github.com/pallets/flask/security/advisories/GHSA-68rp-wp8r-4726). Exploitation depends on deployment conditions including proxy caching behavior and cache-control settings. The vendor lists Flask 3.1.3 as the first patched version. This fixture is for testing an agent's investigation, remediation, regression test, and PR workflow; do not use it as production code.