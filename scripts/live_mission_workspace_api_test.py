"""Live local HTTP test for mission create/list/inspect/agent assignment.
Uses isolated temporary stores and local-development authentication only.
No external integrations or consequential actions are invoked.
"""
import json, os, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]

def request(base, path, method="GET", payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type":"application/json", "Accept":"application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as exc:
        try: body = json.loads(exc.read())
        except Exception: body = {}
        return exc.code, body

def main():
    port = free_port(); base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="alex-v349-mission-api-") as temp:
        env = os.environ.copy(); env["PYTHONPATH"] = str(ROOT)
        env.update({"ALEX_MISSION_STORE_PATH":str(Path(temp)/"missions.sqlite"), "ALEX_AUDIT_LOG_PATH":str(Path(temp)/"audit.jsonl"), "ALEX_APPROVAL_STORE_PATH":str(Path(temp)/"approvals.sqlite"), "ALEX_ORCHESTRATION_STATE_PATH":str(Path(temp)/"orchestration.json"), "ALEX_API_KEYS_JSON":""})
        proc = subprocess.Popen([sys.executable,"-m","uvicorn","app.main:app","--host","127.0.0.1","--port",str(port),"--log-level","error"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            last=None
            for _ in range(60):
                if proc.poll() is not None: raise RuntimeError("Server exited: "+proc.stderr.read().decode(errors="replace")[-1500:])
                try:
                    status, health = request(base,"/health")
                    if status==200: break
                except Exception as exc: last=exc; time.sleep(.25)
            else: raise RuntimeError(f"Server did not start: {last}")
            assert health.get("version")=="3.66", health
            status, agents = request(base,"/api/v1/workforce/agents")
            assert status==200 and agents.get("agents"), (status,agents)
            agent_id = agents["agents"][0].get("agent_id") or agents["agents"][0].get("id")
            assert agent_id, agents["agents"][0]
            print(f"PASS live agent inventory: {len(agents['agents'])} agents; selected {agent_id}")

            objective="v3.66 API test: prepare an aerospace project outline"
            payload={"mission_id":"mission-v349-workspace-test","objective":objective,"budget":0,"scope":["draft"],"verification_required":True,"success_conditions":[],"allowed_tools":[],"allowed_data":[],"forbidden_actions":[]}
            status, created=request(base,"/api/v1/missions","POST",payload)
            assert status==200 and created["contract"]["objective"]==objective and created["status"]=="draft", (status,created)
            print("PASS create mission: persisted response is draft; no execution started")

            status, listing=request(base,"/api/v1/missions")
            assert status==200 and any(m.get("contract",{}).get("mission_id")==payload["mission_id"] for m in listing.get("missions",[])), (status,listing)
            print("PASS mission list contains newly created mission")

            status, inspected=request(base,"/api/v1/missions/"+payload["mission_id"])
            assert status==200 and inspected["contract"]["objective"]==objective, (status,inspected)
            print("PASS inspect mission returns expected objective")

            status, assigned=request(base,"/api/v1/missions/"+payload["mission_id"]+"/agents","POST",{"agent_id":agent_id})
            assert status==200 and agent_id in assigned.get("assigned_agents",[]) and assigned["status"]=="draft", (status,assigned)
            assert not assigned.get("results"), assigned.get("results")
            print("PASS agent assignment persists while mission remains draft with no results")

            status, invalid=request(base,"/api/v1/missions","POST",{**payload,"mission_id":"mission-v349-invalid-budget","budget":-1})
            assert status==400, (status,invalid)
            print("PASS invalid negative budget rejected (HTTP 400)")
            print("LIMIT: live local API test; browser interaction and external integrations not tested")
        finally:
            proc.terminate()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=5)
if __name__=="__main__":
    try: main()
    except Exception as exc: print(f"FAIL live mission workspace API test: {exc}",file=sys.stderr); raise SystemExit(1)
