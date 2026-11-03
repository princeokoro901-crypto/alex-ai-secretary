"""Exercise mission recovery API over HTTP against an isolated local Alex server.

Uses disposable SQLite/audit files and test-only API keys. No external services are contacted.
"""
import json, os, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def request(base, path, key=None, method="GET", payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=3) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try: body = json.loads(raw)
        except Exception: body = {"raw": raw.decode(errors="replace")}
        return exc.code, body

def main():
    with tempfile.TemporaryDirectory(prefix="alex-v348-recovery-") as temp:
        temp = Path(temp)
        db, audit = temp / "missions.sqlite", temp / "audit.jsonl"
        # Create an interrupted mission before server startup. Server recovery must pause it.
        sys.path.insert(0, str(ROOT))
        from app.missions.models import MissionContract, MissionService
        service = MissionService(store_path=str(db))
        service.create(MissionContract("live-recovery-smoke", "Validate recovery through live HTTP"))
        service.assign_agent("live-recovery-smoke", "research")
        service.start("live-recovery-smoke")
        service.close()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
        keys = [
            {"key":"test-operator-key", "key_id":"test-op", "actor_id":"test-operator", "role":"operator"},
            {"key":"test-reader-key", "key_id":"test-reader", "actor_id":"test-reader", "role":"reader"},
        ]
        env = os.environ.copy()
        env.update({"PYTHONPATH": str(ROOT), "ALEX_MISSION_STORE_PATH": str(db),
                    "ALEX_AUDIT_LOG_PATH": str(audit), "ALEX_APPROVAL_STORE_PATH": str(temp/"approvals.sqlite"),
                    "ALEX_EXECUTION_JOURNAL_PATH": str(temp/"execution.sqlite"),
                    "ALEX_API_KEYS_JSON": json.dumps(keys), "ALEX_REAL_EMAIL_ENABLED":"false",
                    "ALEX_GOOGLE_OAUTH_ENABLED":"false"})
        proc = subprocess.Popen([sys.executable,"-m","uvicorn","app.main:app","--host","127.0.0.1","--port",str(port),"--log-level","error"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(60):
                if proc.poll() is not None:
                    raise RuntimeError("server exited: "+proc.stderr.read().decode(errors="replace")[-2000:])
                try:
                    code, health = request(base, "/health")
                    if code == 200: break
                except Exception: time.sleep(.2)
                time.sleep(.1)
            else: raise RuntimeError("server startup timeout")
            assert health.get("status") == "ok", health
            code, queue = request(base, "/api/v1/missions/recovery-queue", "test-operator-key")
            assert code == 200 and [m["contract"]["mission_id"] for m in queue["missions"]] == ["live-recovery-smoke"], (code,queue)
            print("PASS live HTTP recovery queue discovers restart-paused mission")
            code, denied = request(base, "/api/v1/missions/recovery-audit/pending", "test-reader-key")
            assert code == 403, (code,denied)
            print("PASS reader role denied pending-audit operational endpoint (403)")
            code, denied = request(base, "/api/v1/missions/live-recovery-smoke/recovery-decision", "test-reader-key", "POST", {"decision":"keep_paused","reason":"test reader denial"})
            assert code == 403, (code,denied)
            print("PASS reader role denied recovery decision (403)")
            code, result = request(base, "/api/v1/missions/live-recovery-smoke/recovery-decision", "test-operator-key", "POST", {"decision":"keep_paused","reason":"Live-server smoke validation; no execution replay"})
            assert code == 200, (code,result)
            assert result["status"] == "paused" and len(result["recovery_decisions"]) == 1, result
            print("PASS operator decision is persisted and mission remains paused")
            code, queue = request(base, "/api/v1/missions/recovery-queue", "test-operator-key")
            assert code == 200 and queue["missions"] == [], (code,queue)
            code, pending = request(base, "/api/v1/missions/recovery-audit/pending", "test-operator-key")
            assert code == 200 and pending["count"] == 0, (code,pending)
            print("PASS successful decision clears recovery queue and leaves no pending audit")
            code, duplicate = request(base, "/api/v1/missions/live-recovery-smoke/recovery-decision", "test-operator-key", "POST", {"decision":"stop","reason":"must not replace prior decision"})
            assert code == 409, (code,duplicate)
            print("PASS duplicate decision rejected (409); decision not repeated")
            print("LIMIT: isolated local HTTP test only; no external integrations tested")
        finally:
            proc.terminate()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=5)
    return 0

if __name__ == "__main__":
    try: raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL live recovery API test: {exc}", file=sys.stderr); raise SystemExit(1)
