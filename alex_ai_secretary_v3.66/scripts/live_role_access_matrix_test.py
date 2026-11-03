"""Live HTTP least-privilege checks for admin, operator, and reader API keys.

Starts an isolated loopback-only Alex server with disposable stores and test-only
keys. It verifies representative allowed/denied routes; no external providers or
consequential actions are invoked.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(base, path, key=None, method="GET", payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=4) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw)
        except Exception:
            body = {"raw": raw.decode(errors="replace")}
        return exc.code, body


def expect(status, expected, label, body=None):
    if status != expected:
        raise AssertionError(f"{label}: expected HTTP {expected}, got {status}: {body}")
    print(f"PASS {label} (HTTP {status})")


def main():
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="alex-v356-role-matrix-") as temp:
        temp = Path(temp)
        keys = [
            {"key": "test-admin-key", "key_id": "matrix-admin", "actor_id": "matrix-admin-actor", "role": "admin"},
            {"key": "test-operator-key", "key_id": "matrix-operator", "actor_id": "matrix-operator-actor", "role": "operator"},
            {"key": "test-reader-key", "key_id": "matrix-reader", "actor_id": "matrix-reader-actor", "role": "reader"},
        ]
        env = os.environ.copy()
        env.update({
            "PYTHONPATH": str(ROOT),
            "ALEX_API_KEYS_JSON": json.dumps(keys),
            "ALEX_MISSION_STORE_PATH": str(temp / "missions.sqlite"),
            "ALEX_AUDIT_LOG_PATH": str(temp / "audit.jsonl"),
            "ALEX_APPROVAL_STORE_PATH": str(temp / "approvals.sqlite"),
            "ALEX_EXECUTION_JOURNAL_PATH": str(temp / "execution.sqlite"),
            "ALEX_ORCHESTRATION_STATE_PATH": str(temp / "orchestration.json"),
            "ALEX_REAL_EMAIL_ENABLED": "false",
            "ALEX_GOOGLE_OAUTH_ENABLED": "false",
            "ALEX_GMAIL_SENDER_ENABLED": "false",
        })
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "error"],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        try:
            health = None
            for _ in range(80):
                if proc.poll() is not None:
                    raise RuntimeError("Alex server exited: " + proc.stderr.read().decode(errors="replace")[-2500:])
                try:
                    status, health = request(base, "/health")
                    if status == 200:
                        break
                except Exception:
                    time.sleep(0.15)
            else:
                raise RuntimeError("Timed out waiting for local Alex server")
            assert health.get("version") == "3.66", health
            print("PASS isolated local server reports v3.66")

            status, body = request(base, "/api/v1/missions")
            expect(status, 401, "missing API key is rejected", body)
            status, body = request(base, "/api/v1/missions", "not-a-real-key")
            expect(status, 401, "invalid API key is rejected", body)

            # All three roles can use explicitly read-only inventories.
            for role, key in (("reader", "test-reader-key"), ("operator", "test-operator-key"), ("admin", "test-admin-key")):
                for path, label in (
                    ("/api/v1/workforce/agents", "agent inventory"),
                    ("/api/v1/missions", "mission list"),
                    ("/api/v1/missions/recovery-queue", "read-only recovery queue"),
                    ("/api/v1/connectors", "connector inventory"),
                ):
                    status, body = request(base, path, key)
                    expect(status, 200, f"{role} can read {label}", body)

            status, body = request(base, "/api/v1/missions/recovery-audit/pending", "test-reader-key")
            expect(status, 403, "reader cannot inspect operational pending-audit queue", body)
            status, body = request(base, "/api/v1/approvals", "test-reader-key")
            expect(status, 403, "reader cannot list approvals", body)
            status, body = request(base, "/api/v1/system/readiness", "test-reader-key")
            expect(status, 403, "reader cannot access administrator readiness", body)
            for role, key in (("reader", "test-reader-key"), ("operator", "test-operator-key")):
                status, body = request(base, "/api/v1/system/ai-provider-test", key, "POST", {})
                expect(status, 403, f"{role} cannot run administrator AI provider connection test", body)
            status, body = request(base, "/api/v1/system/ai-provider-test", "test-admin-key", "POST", {})
            expect(status, 503, "admin connection test safely reports provider not configured", body)

            mission_payload = {
                "mission_id": "v356-role-matrix-operator-mission",
                "objective": "Verify operator write permission without starting work",
                "budget": 0, "scope": ["draft"], "verification_required": True,
                "success_conditions": [], "allowed_tools": [], "allowed_data": [], "forbidden_actions": [],
            }
            status, body = request(base, "/api/v1/missions", "test-reader-key", "POST", {**mission_payload, "mission_id": "v356-reader-write-denied"})
            expect(status, 403, "reader cannot create missions", body)
            status, body = request(base, "/api/v1/missions", "test-operator-key", "POST", mission_payload)
            expect(status, 200, "operator can create a draft mission", body)
            assert body.get("status") == "draft", body
            assert not body.get("results"), body
            print("PASS operator-created mission remains draft; no execution started")

            status, body = request(base, "/api/v1/missions/recovery-audit/pending", "test-operator-key")
            expect(status, 200, "operator can inspect pending recovery-audit queue", body)
            for path, label in (
                ("/api/v1/approvals", "approval inventory"),
                ("/api/v1/system/readiness", "administrator readiness"),
                ("/api/v1/email/providers", "email-provider configuration"),
            ):
                status, body = request(base, path, "test-operator-key")
                expect(status, 403, f"operator cannot access {label}", body)

            for path, label in (
                ("/api/v1/approvals", "approval inventory"),
                ("/api/v1/system/readiness", "administrator readiness"),
                ("/api/v1/email/providers", "email-provider configuration"),
            ):
                status, body = request(base, path, "test-admin-key")
                expect(status, 200, f"admin can access {label}", body)

            # These requests must be rejected by middleware before endpoint logic
            # can inspect identifiers, mutate state, or dispatch connector actions.
            for role, key in (("reader", "test-reader-key"), ("operator", "test-operator-key")):
                for path, method, payload, label in (
                    ("/api/v1/approvals/test-approval/decision", "POST", {}, "approval decision"),
                    ("/api/v1/connectors/gmail.email/uncertain-sends/test-attempt/reconcile", "POST", {}, "Gmail reconciliation mutation"),
                ):
                    status, body = request(base, path, key, method, payload)
                    expect(status, 403, f"{role} denied sensitive {label}", body)
            status, body = request(base, "/api/v1/connectors/test-connector/execute", "test-reader-key", "POST", {})
            expect(status, 403, "reader cannot invoke connector execution", body)

            print("LIMIT: local HTTP authorization checks only; sensitive requests use disposable identifiers and are denied before endpoint logic. No browser-to-server, TLS/proxy, production, or external-provider test was performed")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=6)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=6)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL live role access matrix: {exc}", file=sys.stderr)
        raise SystemExit(1)
