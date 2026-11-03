# v3.47 — Local live-server smoke test

## Purpose

Exercise Alex's actual ASGI application through a loopback-only Uvicorn process, rather than relying only on FastAPI's in-process test client or mocked browser responses.

## Run

From the project root, install `requirements.txt`, then run:

```bash
PYTHONPATH=. python scripts/live_server_smoke_test.py
```

The script selects an available loopback port, starts Uvicorn, waits for `/health`, and checks the health version, dashboard HTML, JavaScript and CSS assets, and system readiness endpoint. It uses a temporary directory for mission, audit, and approval persistence and terminates the server at the end.

## Safety and scope

- Binds only to `127.0.0.1`; do not expose this development configuration to a public network.
- Uses local-development authentication fallback only when no API keys are configured. This is not a production-safe deployment mode.
- Does not contact Google, Gmail, WhatsApp, telephony, voice, or any other external provider.
- Does not submit or execute missions, send messages, or test a disaster restore.
- Passing proves these local HTTP routes/assets respond in this environment, not production readiness.
