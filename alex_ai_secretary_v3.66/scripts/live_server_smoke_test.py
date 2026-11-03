"""Loopback-only live-server smoke test for Alex; no external services contacted."""
import json, os, socket, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def request(url, timeout=1):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.status, response.headers, response.read()

def main():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    env = os.environ.copy(); env["PYTHONPATH"] = str(ROOT)
    # Keep all optional persistence in a temporary directory for this smoke run.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="alex-v347-smoke-") as temp:
        env.update({"ALEX_MISSION_STORE_PATH": str(Path(temp)/"missions.sqlite"),
                    "ALEX_AUDIT_LOG_PATH": str(Path(temp)/"audit.jsonl"),
                    "ALEX_APPROVAL_STORE_PATH": str(Path(temp)/"approvals.sqlite",
                    )})
        proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "error"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        base = f"http://127.0.0.1:{port}"
        try:
            last_error = None
            for _ in range(60):
                if proc.poll() is not None:
                    raise RuntimeError("Uvicorn exited during startup: " + proc.stderr.read().decode(errors="replace")[-2000:])
                try:
                    status, _, body = request(base + "/health")
                    break
                except Exception as exc:
                    last_error = exc; time.sleep(.25)
            else:
                raise RuntimeError(f"Server did not become ready: {last_error}")
            health = json.loads(body)
            assert status == 200 and health.get("status") == "ok" and health.get("version") == "3.66", health
            print("PASS /health: status=ok, version=3.66")
            status, _, provider_body = request(base + "/api/v1/system/ai-provider-status")
            provider = json.loads(provider_body)
            assert status == 200 and provider.get("state") == "not_configured" and provider.get("connection_tested") is False, provider
            print("PASS AI provider status: missing configuration reported without contacting provider")
            status, headers, body = request(base + "/")
            assert status == 200 and b"dashboard" in body.lower() and "text/html" in headers.get("Content-Type", ""), (status, headers.get("Content-Type"))
            print("PASS /: dashboard HTML served")
            for asset in ("/dashboard-assets/dashboard.js", "/dashboard-assets/dashboard.css"):
                status, headers, body = request(base + asset)
                assert status == 200 and len(body) > 100, (asset, status, len(body))
                print(f"PASS {asset}: HTTP 200 ({len(body)} bytes)")
            status, _, body = request(base + "/api/v1/system/readiness")
            assert status == 200, (status, body[:500])
            readiness = json.loads(body)
            assert isinstance(readiness, dict), readiness
            print("PASS /api/v1/system/readiness: reachable in isolated local-development mode")
            print("LIMIT: local smoke test only; no external integrations tested")
        finally:
            proc.terminate()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=5)
    return 0

if __name__ == "__main__":
    try: raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL live-server smoke test: {exc}", file=sys.stderr); raise SystemExit(1)
