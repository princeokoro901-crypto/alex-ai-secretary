from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["version"] == "3.66"

def test_create_team_and_delegate():
    r = client.post("/api/v1/workforce/teams", json={
        "name": "Digital Marketing Team",
        "objective": "Grow the business online",
        "agent_ids": ["research", "content", "analytics"],
        "budget_limit": 100
    })
    assert r.status_code == 200
    team_id = r.json()["team_id"]
    r = client.post("/api/v1/workforce/delegate", json={
        "team_id": team_id,
        "agent_id": "research",
        "objective": "Research the target audience",
        "budget": 20
    })
    assert r.status_code == 200
    assert r.json()["status"] == "delegated"

def test_budget_guard():
    r = client.post("/api/v1/workforce/teams", json={
        "name": "Budget Team", "objective": "Test limits",
        "agent_ids": ["research"], "budget_limit": 10
    })
    team_id = r.json()["team_id"]
    r = client.post("/api/v1/workforce/delegate", json={
        "team_id": team_id, "agent_id": "research",
        "objective": "Too expensive", "budget": 11
    })
    assert r.status_code == 400


def test_read_only_agent_inventory_returns_registered_agents_and_capabilities():
    from fastapi.testclient import TestClient
    from app.main import app

    response = TestClient(app).get("/api/v1/workforce/agents")
    assert response.status_code == 200
    agents = response.json()["agents"]
    assert agents
    assert {agent["agent_id"] for agent in agents} >= {"research", "content", "analytics"}
    assert all(isinstance(agent.get("capabilities"), list) for agent in agents)


def test_agent_inventory_is_read_only_and_does_not_assign_or_execute():
    from fastapi.testclient import TestClient
    from app.main import app, mission_service
    from app.missions.models import MissionContract

    mission_id = "agent-inventory-read-only-check"
    mission_service.create(MissionContract(mission_id=mission_id, objective="Read-only check"))
    before = list(mission_service.get(mission_id).assigned_agents)
    response = TestClient(app).get("/api/v1/workforce/agents")
    after = list(mission_service.get(mission_id).assigned_agents)
    assert response.status_code == 200
    assert after == before == []
