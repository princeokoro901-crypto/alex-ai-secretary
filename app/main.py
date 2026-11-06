from dataclasses import asdict
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from app.security import authenticate_api_request, load_api_keys
from app.workforce.service import WorkforceService
from app.missions.models import MissionContract, MissionService
from app.agents.runtime import AgentRuntime
from app.handoffs.service import HandoffService
from app.routing.service import AgentRoutingService
from app.orchestrator.service import OrchestratorService, OrchestrationRequest, OrchestrationStateStore, SQLiteOrchestrationStateStore, RecoveryReviewAuditError
from app.audit import AuditLog
from app.approvals import ApprovalService, ApprovalError, ApprovalStoreError
from app.execution import ExecutionGateway
from app.execution_journal import ExecutionJournal
from app.connectors.registry import ConnectorRegistry, ConnectorManifest
from app.connectors.sandbox_email import SandboxEmailOutbox, sandbox_email_action
from app.connectors.email_provider import EmailProviderRegistry, EmailProviderError
from app.connectors.gmail_sender import GoogleGmailEmailProvider
from app.connectors.gmail_reconciliation import GmailExecutionReconciliationService
from app.connectors.google_oauth import GoogleOAuthAdapter, GoogleOAuthError
from app.connectors.oauth_security import OAuthAuthorizationService, OAuthCredentialVault
from app.reconciliation.service import LifecycleReconciliationService
from app.readiness import build_readiness_report
from app.workspace import draft_report, WorkspaceInputError
from app.reminder_workspace import ReminderStore, create_reminder
from app.ai_workspace import draft_ai_report
from app.llm_provider import LLMNotConfigured, LLMProviderError, provider_status, test_provider_connection
import os
import re
import secrets

app = FastAPI(title="Alex Core v3.66")
app.middleware("http")(authenticate_api_request)

_DASHBOARD_DIR = Path(__file__).resolve().parent / "static"
app.mount("/dashboard-assets", StaticFiles(directory=_DASHBOARD_DIR), name="dashboard-assets")
workforce = WorkforceService()
mission_service = MissionService(workforce=workforce, store_path=os.getenv("ALEX_MISSION_STORE_PATH"))
agent_runtime = AgentRuntime(workforce, mission_service)
handoff_service = HandoffService(workforce, mission_service)
routing_service = AgentRoutingService(workforce, mission_service, agent_runtime.performance)
state_backend = os.getenv("ALEX_ORCHESTRATION_STATE_BACKEND", "json").lower()
state_path = os.getenv("ALEX_ORCHESTRATION_STATE_PATH")
if state_backend == "sqlite" and state_path:
    orchestration_state = SQLiteOrchestrationStateStore(state_path)
else:
    orchestration_state = OrchestrationStateStore(state_path)
audit_path = os.getenv("ALEX_AUDIT_LOG_PATH")
audit_log = AuditLog(audit_path)
approval_path = os.getenv("ALEX_APPROVAL_STORE_PATH")
approval_service = ApprovalService(approval_path)
execution_journal_path = os.getenv("ALEX_EXECUTION_JOURNAL_PATH")
execution_journal = ExecutionJournal(execution_journal_path) if execution_journal_path else None
execution_gateway = ExecutionGateway(approval_service, audit_log.record, execution_journal)
reminder_store = ReminderStore(os.getenv("ALEX_REMINDER_DB_PATH"))  # v3.67: local workspace task
connector_registry = ConnectorRegistry(execution_gateway)
# No concrete provider adapters are registered by default. Real delivery needs
# the explicit provider allowlist and global feature flag; Gmail additionally
# requires the separate sender opt-in and durable security stores below.
real_email_enabled = os.getenv("ALEX_REAL_EMAIL_ENABLED", "false").strip().lower() == "true"
allowed_email_providers = tuple(item.strip() for item in os.getenv("ALEX_EMAIL_ALLOWED_PROVIDERS", "").split(",") if item.strip())
email_provider_registry = EmailProviderRegistry(
    real_sending_enabled=real_email_enabled,
    allowed_provider_ids=allowed_email_providers,
)

# Google OAuth browser callback remains disabled unless all secrets and the exact
# redirect URI are explicitly configured by the operator.
google_oauth_adapter = None
_google_oauth_enabled = os.getenv("ALEX_GOOGLE_OAUTH_ENABLED", "false").strip().lower() == "true"
if _google_oauth_enabled:
    _google_oauth_values = {
        "ALEX_GOOGLE_OAUTH_CLIENT_ID": os.getenv("ALEX_GOOGLE_OAUTH_CLIENT_ID", "").strip(),
        "ALEX_GOOGLE_OAUTH_CLIENT_SECRET": os.getenv("ALEX_GOOGLE_OAUTH_CLIENT_SECRET", "").strip(),
        "ALEX_GOOGLE_OAUTH_ENCRYPTION_KEY": os.getenv("ALEX_GOOGLE_OAUTH_ENCRYPTION_KEY", "").strip(),
        "ALEX_GOOGLE_OAUTH_STATE_DB_PATH": os.getenv("ALEX_GOOGLE_OAUTH_STATE_DB_PATH", "").strip(),
        "ALEX_GOOGLE_OAUTH_CREDENTIAL_DB_PATH": os.getenv("ALEX_GOOGLE_OAUTH_CREDENTIAL_DB_PATH", "").strip(),
        "ALEX_GOOGLE_OAUTH_REDIRECT_URI": os.getenv("ALEX_GOOGLE_OAUTH_REDIRECT_URI", "").strip(),
    }
    _missing_google_oauth = [name for name, value in _google_oauth_values.items() if not value]
    if _missing_google_oauth:
        raise RuntimeError("Google OAuth is enabled but required configuration is missing: " + ", ".join(_missing_google_oauth))
    _oauth_redirect = _google_oauth_values["ALEX_GOOGLE_OAUTH_REDIRECT_URI"]
    _oauth_key = _google_oauth_values["ALEX_GOOGLE_OAUTH_ENCRYPTION_KEY"]
    _oauth_state_service = OAuthAuthorizationService(
        _google_oauth_values["ALEX_GOOGLE_OAUTH_STATE_DB_PATH"], _oauth_key,
        {"gmail": _oauth_redirect},
    )
    _oauth_credential_vault = OAuthCredentialVault(
        _google_oauth_values["ALEX_GOOGLE_OAUTH_CREDENTIAL_DB_PATH"], _oauth_key,
    )
    google_oauth_adapter = GoogleOAuthAdapter(
        client_id=_google_oauth_values["ALEX_GOOGLE_OAUTH_CLIENT_ID"],
        client_secret=_google_oauth_values["ALEX_GOOGLE_OAUTH_CLIENT_SECRET"],
        authorization_service=_oauth_state_service,
        credential_vault=_oauth_credential_vault,
        enabled=True,
    )

# Real Gmail delivery is a separate, explicit opt-in. It requires persistent
# approval, audit, execution-journal and send-ledger storage, plus a specific
# owner-scoped account; setting only the general email flag is insufficient.
_gmail_sender_enabled = os.getenv("ALEX_GMAIL_SENDER_ENABLED", "false").strip().lower() == "true"
gmail_email_provider = None
if _gmail_sender_enabled:
    _gmail_sender_values = {
        "ALEX_GMAIL_SENDER_ACTOR_ID": os.getenv("ALEX_GMAIL_SENDER_ACTOR_ID", "").strip(),
        "ALEX_GMAIL_SENDER_ACCOUNT_ID": os.getenv("ALEX_GMAIL_SENDER_ACCOUNT_ID", "").strip(),
        "ALEX_GMAIL_SEND_LEDGER_PATH": os.getenv("ALEX_GMAIL_SEND_LEDGER_PATH", "").strip(),
    }
    _missing_gmail_sender = [name for name, value in _gmail_sender_values.items() if not value]
    if _missing_gmail_sender:
        raise RuntimeError("Gmail sender is enabled but required configuration is missing: " + ", ".join(_missing_gmail_sender))
    if google_oauth_adapter is None:
        raise RuntimeError("Gmail sender requires ALEX_GOOGLE_OAUTH_ENABLED=true and valid Google OAuth configuration")
    if not real_email_enabled or "gmail" not in email_provider_registry.allowed_provider_ids:
        raise RuntimeError("Gmail sender requires ALEX_REAL_EMAIL_ENABLED=true and gmail in ALEX_EMAIL_ALLOWED_PROVIDERS")
    if not approval_service.path or not audit_log.path or execution_journal is None:
        raise RuntimeError("Gmail sender requires persistent ALEX_APPROVAL_STORE_PATH, ALEX_AUDIT_LOG_PATH, and ALEX_EXECUTION_JOURNAL_PATH")
    gmail_email_provider = GoogleGmailEmailProvider(
        google_oauth_adapter,
        owner_actor_id=_gmail_sender_values["ALEX_GMAIL_SENDER_ACTOR_ID"],
        account_id=_gmail_sender_values["ALEX_GMAIL_SENDER_ACCOUNT_ID"],
        ledger_path=_gmail_sender_values["ALEX_GMAIL_SEND_LEDGER_PATH"],
    )
    email_provider_registry.register("gmail", gmail_email_provider)

sandbox_email_outbox = SandboxEmailOutbox(os.getenv("ALEX_SANDBOX_EMAIL_DB_PATH"))
connector_registry.register(
    ConnectorManifest(
        connector_id="sandbox.email",
        name="Sandbox Email",
        description="Validates and records a simulated email; never sends messages externally.",
        action_type="email.send_sandbox",
        input_fields={"to": "string", "subject": "string", "body": "string", "idempotency_key": "string"},
        required_fields=("to", "subject", "body"),
        allowed_roles=("operator", "admin"),
    ),
    sandbox_email_outbox.queue_simulated,
    action_builder=sandbox_email_action,
)
if gmail_email_provider is not None:
    connector_registry.register(
        ConnectorManifest(
            connector_id="gmail.email",
            name="Gmail Email",
            description="Sends one approved email through the configured owner-scoped Google account; uncertain outcomes are never automatically retried.",
            action_type="email.send_gmail",
            input_fields={"to": "string", "subject": "string", "body": "string", "idempotency_key": "string"},
            required_fields=("to", "subject", "body", "idempotency_key"),
            allowed_roles=("admin",),
        ),
        lambda payload, approved_action, context: email_provider_registry.send(
            "gmail", payload, approved_action, execution_context=context),
        action_builder=lambda payload: email_provider_registry.build_action("gmail", payload),
        action_aware_handler=True,
        execution_context_handler=True,
    )
orchestrator_service = OrchestratorService(workforce, mission_service, routing_service, agent_runtime, orchestration_state, audit_log)
reconciliation_service = LifecycleReconciliationService()
startup_reconciliation = reconciliation_service.reconcile(orchestrator_service.runs, audit_log.events)

@app.get("/", include_in_schema=False)
def dashboard():
    """Serve the read-only browser console; API operations remain separately authenticated."""
    return FileResponse(
        _DASHBOARD_DIR / "dashboard.html",
        media_type="text/html",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
        },
    )


@app.post("/api/v1/workspace/draft-report")
def workspace_draft_report(payload: dict, request: Request):
    """Create a reviewable Markdown report from supplied notes; no external calls."""
    try:
        result = draft_report(payload)
    except WorkspaceInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    actor = getattr(request.state, "actor", {"actor_id": "local:owner"})
    audit_log.record(
        "workspace_report_drafted", actor.get("actor_id", "unknown"),
        result["task_id"], "draft_report_from_notes",
        {"source_line_count": result["source_line_count"],
         "output_sha256": result["output_sha256"],
         "external_actions_performed": False},
    )
    return result


@app.post("/api/v1/workspace/create-reminder")
def workspace_create_reminder(payload: dict, request: Request):
    """Create a local reminder; no external calls, nothing sent anywhere."""
    try:
        result = create_reminder(payload, reminder_store)
    except WorkspaceInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    actor = getattr(request.state, "actor", {"actor_id": "local:owner"})
    audit_log.record(
        "workspace_reminder_created", actor.get("actor_id", "unknown"),
        result["task_id"], "create_reminder",
        {"has_due_at": result["due_at"] is not None, "external_actions_performed": False},
    )
    return result


@app.get("/api/v1/workspace/reminders")
def workspace_list_reminders(request: Request):
    """Reminders Alex has created (read-only)."""
    return {"reminders": [r.__dict__ for r in reminder_store.list()], "persistent": reminder_store.persistent}


@app.post("/api/v1/workspace/ai-draft-report")
def workspace_ai_draft_report(payload: dict, request: Request):
    """Draft a report with an explicitly configured server-side AI provider."""
    try:
        result = draft_ai_report(payload)
    except WorkspaceInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except LLMNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except LLMProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    actor = getattr(request.state, "actor", {"actor_id": "unknown"})
    audit_log.record(
        "workspace_ai_report_drafted", actor.get("actor_id", "unknown"),
        result["task_id"], "ai_draft_report_from_notes",
        {"source_line_count": result["source_line_count"],
         "output_sha256": result["output_sha256"],
         "model_label": result["model_label"],
         "external_actions_performed": False},
    )
    return result


@app.get("/api/v1/system/ai-provider-status")
def system_ai_provider_status():
    """Report configuration state only; never contacts provider or exposes secrets."""
    return provider_status()


@app.post("/api/v1/system/ai-provider-test")
def system_ai_provider_test(request: Request):
    """Administrator-only explicit live test; sends only a short probe, never user notes."""
    actor = request.state.actor
    if actor.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Administrator role required")
    try:
        result = test_provider_connection()
    except LLMNotConfigured:
        audit_log.record("ai_provider_connection_test", actor.get("actor_id", "unknown"),
                         "system", "ai_provider_test", {"outcome": "not_configured"})
        raise HTTPException(status_code=503, detail="AI provider is not configured or its configuration is invalid.") from None
    except LLMProviderError as exc:
        audit_log.record("ai_provider_connection_test", actor.get("actor_id", "unknown"),
                         "system", "ai_provider_test", {"outcome": "failed"})
        raise HTTPException(status_code=502, detail=str(exc)) from None
    audit_log.record("ai_provider_connection_test", actor.get("actor_id", "unknown"),
                     "system", "ai_provider_test", {"outcome": "connected", "model_label": result["model_label"]})
    return result


@app.get("/health")
def health():
    return {"status": "ok", "version": "3.66", "orchestration_persistence": bool(orchestration_state.path), "orchestration_backend": state_backend, "audit_persistence": bool(audit_log.path), "audit_events": len(audit_log.events), "audit_integrity": audit_log.verify(), "audit_recovery": bool(audit_log.recovery_path), "approval_persistence": bool(approval_service.path), "pending_approvals": len(approval_service.list("pending"))}

@app.get("/api/v1/system/readiness")
def system_readiness(request: Request):
    """Administrator-only, read-only deployment diagnostics; never returns secrets or paths."""
    actor = request.state.actor
    if actor.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Administrator role required")
    try:
        configured_keys = load_api_keys()
        key_config_error = False
    except RuntimeError:
        configured_keys = []
        key_config_error = True
    report = build_readiness_report(
        api_keys=configured_keys,
        api_key_config_error=key_config_error,
        orchestration_state=orchestration_state,
        audit_log=audit_log,
        approval_service=approval_service,
        execution_journal=execution_journal,
        google_oauth_adapter=google_oauth_adapter,
        gmail_email_provider=gmail_email_provider,
        real_email_enabled=real_email_enabled,
        allowed_email_providers=allowed_email_providers,
    )
    audit_log.record("system_readiness_inspected", actor["actor_id"], "system", "readiness",
                     {"key_id": actor.get("key_id"), "ready_for_live_gmail": report["ready_for_live_gmail"],
                      "blocker_count": report["blocker_count"], "warning_count": report["warning_count"]})
    return report


@app.get("/api/v1/workforce/agents")
def list_workforce_agents():
    """List registered agents and capabilities; read-only, no assignment or execution."""
    return {"agents": [agent.model_dump() for agent in workforce.agents.values()]}

@app.get("/api/v1/workforce/teams")
def teams():
    return {"teams": [t.model_dump() for t in workforce.list_teams()]}

@app.post("/api/v1/workforce/teams")
def create_team(payload: dict):
    try:
        return workforce.create_team(payload).model_dump()
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.post("/api/v1/workforce/delegate")
def delegate(payload: dict):
    try:
        return workforce.delegate(payload).model_dump()
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.get("/api/v1/missions")
def list_missions():
    """List missions in the current MissionService store; read-only."""
    return {"missions": [asdict(mission) for mission in mission_service.list()]}

@app.post("/api/v1/missions")
def create_mission(payload: dict):
    try:
        contract = MissionContract(**payload)
        return asdict(mission_service.create(contract))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.get("/api/v1/missions/recovery-queue")
def mission_recovery_queue():
    """List restart-recovered missions still awaiting human review."""
    items = []
    for mission in mission_service.list():
        awaiting = (mission.status.value == "paused"
                    and any(c.get("name") == "recovered_after_restart_manual_review_required" for c in mission.checkpoints)
                    and not mission.recovery_decisions)
        if awaiting:
            items.append(asdict(mission))
    return {"missions": items}

@app.get("/api/v1/missions/recovery-audit/pending")
def pending_mission_recovery_audits():
    """List recovery decisions whose audit delivery has not been confirmed."""
    pending = []
    for mission in mission_service.list():
        for record in mission.recovery_decisions:
            if record.get("audit_status", "pending") != "recorded":
                pending.append({
                    "mission_id": mission.contract.mission_id,
                    "objective": mission.contract.objective,
                    "mission_status": mission.status.value,
                    "decision_id": record.get("decision_id"),
                    "decision": record.get("decision"),
                    "actor_id": record.get("actor_id"),
                    "recorded_at": record.get("recorded_at"),
                    "audit_status": "pending",
                })
    pending.sort(key=lambda item: (item.get("recorded_at") or "", item["mission_id"]))
    return {"pending": pending, "count": len(pending)}


@app.post("/api/v1/missions/{mission_id}/recovery-decision")
def mission_recovery_decision(mission_id: str, payload: dict, request: Request):
    """Record an authorized human decision; never resume mission execution."""
    actor = request.state.actor
    decision = payload.get("decision")
    reason = payload.get("reason")
    try:
        mission = mission_service.record_recovery_decision(
            mission_id, decision, actor.get("actor_id", ""), reason, actor.get("key_id"), actor.get("role")
        )
    except ValueError as exc:
        message = str(exc)
        status = 404 if message == "Mission not found" else (409 if "already recorded" in message or "awaiting restart recovery review" in message or "paused mission" in message else 400)
        raise HTTPException(status_code=status, detail=message)
    decision_record = mission.recovery_decisions[-1]
    try:
        _record_recovery_audit(mission_id, decision_record)
        mission_service.mark_recovery_audit_status(mission_id, decision_record["decision_id"], "recorded")
    except Exception:
        # Decision remains durable and explicitly marked pending for an idempotent retry.
        raise HTTPException(status_code=503, detail="Decision was saved, but audit persistence is pending. Retry the recovery-audit endpoint; do not repeat the decision.")
    return asdict(mission_service.get(mission_id))

def _record_recovery_audit(mission_id: str, record: dict):
    """Write one idempotent audit event for a persisted recovery decision."""
    return audit_log.record(
        "mission_recovery_decision_recorded", record["actor_id"], "mission", mission_id,
        {"decision": record["decision"], "reason": record["reason"], "key_id": record.get("key_id"), "role": record.get("role")},
        event_id=record["decision_id"],
    )

@app.post("/api/v1/missions/{mission_id}/recovery-audit/retry")
def retry_mission_recovery_audit(mission_id: str, request: Request):
    """Retry audit delivery for a saved decision without changing mission state."""
    actor = request.state.actor
    try:
        mission = mission_service.get(mission_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if not mission.recovery_decisions:
        raise HTTPException(status_code=409, detail="No recovery decision is available for audit retry")
    record = mission.recovery_decisions[-1]
    if record.get("audit_status", "pending") == "recorded":
        return {"mission_id": mission_id, "audit_status": "recorded", "already_recorded": True}
    try:
        _record_recovery_audit(mission_id, record)
        mission_service.mark_recovery_audit_status(mission_id, record["decision_id"], "recorded")
    except Exception:
        raise HTTPException(status_code=503, detail="Audit retry failed; decision remains saved and pending")
    return {"mission_id": mission_id, "audit_status": "recorded", "already_recorded": False}

@app.get("/api/v1/missions/{mission_id}")
def get_mission(mission_id: str):
    try:
        return asdict(mission_service.get(mission_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

@app.post("/api/v1/missions/{mission_id}/agents")
def assign_agent(mission_id: str, payload: dict):
    try:
        return asdict(mission_service.assign_agent(mission_id, payload["agent_id"]))
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.post("/api/v1/missions/{mission_id}/start")
def start_mission(mission_id: str):
    try:
        return asdict(mission_service.start(mission_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.post("/api/v1/missions/{mission_id}/agents/{agent_id}/memory")
def remember_agent_memory(mission_id: str, agent_id: str, payload: dict):
    try:
        mission_service.get(mission_id)
        item = agent_runtime.remember(agent_id, payload["key"], payload.get("value"), payload.get("scope", "assigned_task_context"), payload.get("sensitivity", "normal"), float(payload.get("importance", 0.5)))
        return item.__dict__
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.post("/api/v1/missions/{mission_id}/agents/{agent_id}/execute")
def execute_agent(mission_id: str, agent_id: str, payload: dict):
    try:
        return agent_runtime.submit(mission_id, agent_id, payload["objective"], payload["tool"], payload.get("input", {}), float(payload.get("budget", 0)), payload.get("idempotency_key")).__dict__
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.post("/api/v1/missions/{mission_id}/handoffs")
def create_handoff(mission_id: str, payload: dict):
    try:
        return handoff_service.create(mission_id, payload["from_agent"], payload["to_agent"], payload["task_id"], payload.get("findings"), payload.get("evidence"), payload.get("unresolved_questions"), payload.get("verification")).model_dump()
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/v1/workforce/agents/{agent_id}/performance")
def agent_performance(agent_id: str):
    if agent_id not in workforce.agents:
        raise HTTPException(status_code=404, detail="Unknown agent")
    return agent_runtime.performance.profile(agent_id).__dict__

@app.post("/api/v1/workforce/recommend")
def recommend_agent(payload: dict):
    try:
        tool = payload["tool"]
        candidates = payload.get("agent_ids")
        if candidates is None:
            pool = list(workforce.agents.values())
        else:
            unknown = [aid for aid in candidates if aid not in workforce.agents]
            if unknown:
                raise ValueError("Unknown candidate agent")
            pool = [workforce.agents[aid] for aid in candidates]
        return {"tool": tool, "recommendations": agent_runtime.performance.recommend(pool, tool), "authority_note": "Performance ranking does not grant permissions."}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.post("/api/v1/workforce/route")
def route_agent(payload: dict):
    try:
        candidates = routing_service.route(
            payload["task_type"],
            payload["required_tools"],
            payload.get("mission_id"),
            payload.get("candidate_agent_ids"),
        )
        return {"task_type": payload["task_type"], "recommendations": [c.__dict__ for c in candidates], "authority_note": "Routing selects only already-authorized agents; performance never grants authority."}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.post("/api/v1/missions/{mission_id}/actions")
def propose_action(mission_id: str, payload: dict, request: Request):
    actor = request.state.actor
    try:
        mission_service.validate_action(mission_id, payload["action"])
        approval = approval_service.create(mission_id, payload["action"], actor)
        mission_service.propose_action(mission_id, payload["action"])
        audit_log.record(
            "approval_requested", actor["actor_id"], "approval", approval.approval_id,
            {"actor_id": actor["actor_id"], "key_id": actor["key_id"], "role": actor["role"],
             "mission_id": mission_id, "status": "pending", "action_length": len(approval.action)},
        )
        return {"approval": asdict(approval), "mission_status": mission_service.get(mission_id).status.value,
                "note": "This creates a pending approval record; it does not execute the action."}
    except (KeyError, ValueError) as exc:
        try:
            audit_log.record("approval_request_rejected", actor["actor_id"], "mission", mission_id,
                             {"actor_id": actor["actor_id"], "key_id": actor["key_id"], "role": actor["role"], "outcome": "rejected"})
        except OSError:
            pass
        raise HTTPException(status_code=400, detail=str(exc))
    except ApprovalStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.get("/api/v1/orchestrations")
def list_orchestrations(limit: int = 25):
    """Return recent orchestration results without triggering any work."""
    safe_limit = max(1, min(int(limit), 100))
    runs = list(orchestrator_service.runs.values())
    return {"orchestrations": [result.__dict__ for result in reversed(runs[-safe_limit:])], "count": min(len(runs), safe_limit), "total": len(runs), "limit": safe_limit}


@app.get("/api/v1/orchestrations/{orchestration_id}")
def get_orchestration(orchestration_id: str):
    result = orchestrator_service.runs.get(orchestration_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Orchestration not found")
    return result.__dict__


@app.post("/api/v1/orchestrations/{orchestration_id}/recovery-review")
def review_orchestration_recovery(orchestration_id: str, payload: dict, request: Request):
    """Record a one-time human disposition for an uncertain run; never retries it."""
    actor = request.state.actor
    disposition = payload.get("disposition") if isinstance(payload, dict) else None
    allowed = {"acknowledged_no_retry", "escalated_for_manual_investigation"}
    if disposition not in allowed:
        raise HTTPException(400, detail="Disposition must be acknowledged_no_retry or escalated_for_manual_investigation")
    try:
        record = orchestrator_service.record_recovery_review(orchestration_id, disposition, actor)
        return record.__dict__
    except KeyError:
        raise HTTPException(404, detail="Orchestration not found")
    except ValueError as exc:
        raise HTTPException(409, detail=str(exc))
    except RecoveryReviewAuditError as exc:
        raise HTTPException(503, detail=str(exc))
    except OSError:
        raise HTTPException(503, detail="Could not persist the recovery review. Inspect the run before retrying this request.")


@app.post("/api/v1/orchestrate")
def orchestrate(payload: dict, request: Request):
    actor = request.state.actor
    try:
        request = OrchestrationRequest(
            objective=payload["objective"],
            task_type=payload["task_type"],
            required_tools=payload["required_tools"],
            tool=payload["tool"],
            budget=float(payload.get("budget", 0)),
            input=payload.get("input", {}),
            mission_id=payload.get("mission_id"),
            candidate_agent_ids=payload.get("candidate_agent_ids"),
            verification_required=bool(payload.get("verification_required", True)),
            idempotency_key=payload.get("idempotency_key"),
            initiated_by=actor["actor_id"],
            initiated_by_key_id=actor["key_id"],
            initiated_by_role=actor["role"],
        )
        return orchestrator_service.run(request).__dict__
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.get("/api/v1/approvals")
def list_approvals(status: str | None = None):
    try:
        return {"approvals": [asdict(record) for record in approval_service.list(status)]}
    except ApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.get("/api/v1/approvals/{approval_id}")
def get_approval(approval_id: str):
    try:
        return asdict(approval_service.get(approval_id))
    except ApprovalError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

@app.post("/api/v1/approvals/{approval_id}/decision")
def decide_approval(approval_id: str, payload: dict, request: Request):
    actor = request.state.actor
    try:
        decision = payload.get("decision")
        record = approval_service.decide(approval_id, decision, actor, payload.get("reason"))
        audit_log.record(
            "approval_decided", actor["actor_id"], "approval", approval_id,
            {"actor_id": actor["actor_id"], "key_id": actor["key_id"], "role": actor["role"],
             "requester_id": record.requested_by, "requester_key_id": record.requester_key_id,
             "decision": record.status, "reason_provided": bool(record.decision_reason)},
        )
        return asdict(record)
    except ApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ApprovalStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

@app.post("/api/v1/approvals/{approval_id}/execution-permit")
def issue_execution_permit(approval_id: str, payload: dict, request: Request):
    actor = request.state.actor
    try:
        record = approval_service.issue_execution_permit(
            approval_id, payload.get("mission_id"), payload.get("action"), actor,
        )
        audit_log.record(
            "execution_permit_issued", actor["actor_id"], "approval", approval_id,
            {"actor_id": actor["actor_id"], "key_id": actor["key_id"], "role": actor["role"],
             "mission_id": record.mission_id, "action_digest": record.action_digest,
             "permit_id": record.execution_permit_id},
        )
        return {"approval_id": approval_id, "mission_id": record.mission_id,
                "action_digest": record.action_digest, "execution_permit_id": record.execution_permit_id,
                "issued_at": record.execution_permit_issued_at,
                "note": "Permit issued only. No external action has been executed. A connector must consume this permit immediately before its side effect."}
    except ApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ApprovalStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

@app.get("/api/v1/audit")
def audit_events(resource_type: str | None = None, resource_id: str | None = None):
    return {"events": [e.__dict__ for e in audit_log.list(resource_type, resource_id)]}

@app.get("/api/v1/consistency")
def consistency():
    return orchestrator_service.consistency_report(audit_log.events)

@app.get("/api/v1/reconciliation")
def reconciliation():
    return reconciliation_service.reconcile(orchestrator_service.runs, audit_log.events)


@app.get("/api/v1/connectors")
def list_connectors():
    return {"connectors": connector_registry.list(),
            "note": "Only connectors registered by trusted application code are available. sandbox.email is simulation-only; no external connectors are enabled unless the explicit Gmail real-sending and durable-storage settings are valid."}


@app.get("/api/v1/email/providers")
def list_email_provider_status():
    """Admin-only, redacted readiness inventory; this endpoint cannot send email."""
    return email_provider_registry.status()


_OAUTH_BINDING_COOKIE = "alex_oauth_binding"
_OAUTH_BINDING_RE = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
_OAUTH_COMPLETE_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="referrer" content="no-referrer"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Alex account authorization</title></head><body><main><h1>Alex account authorization</h1><p>The authorization callback has finished. You may close this page and return to Alex.</p><p>This page does not confirm authorization success or email delivery.</p></main></body></html>"""


def _oauth_completion_redirect() -> RedirectResponse:
    return RedirectResponse(
        url="/oauth/google/complete", status_code=303,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"},
    )


@app.post("/api/v1/email/google/oauth/start")
def start_google_oauth(request: Request, response: Response):
    """Admin-authenticated OAuth start; browser binding is HttpOnly and short-lived."""
    if google_oauth_adapter is None:
        raise HTTPException(status_code=503, detail="Google OAuth is disabled or not configured")
    actor = request.state.actor
    binding = request.cookies.get(_OAUTH_BINDING_COOKIE, "")
    if not _OAUTH_BINDING_RE.fullmatch(binding):
        binding = secrets.token_urlsafe(32)
    try:
        started = google_oauth_adapter.begin(
            actor["actor_id"], actor["key_id"], browser_session_binding=binding,
        )
    except (GoogleOAuthError, ValueError):
        raise HTTPException(status_code=503, detail="Google OAuth could not be started") from None
    redirect_uri = google_oauth_adapter.authorization_service.redirect_uris["gmail"]
    response.set_cookie(
        key=_OAUTH_BINDING_COOKIE,
        value=binding,
        max_age=600,
        httponly=True,
        secure=redirect_uri.startswith("https://"),
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return {
        "provider_id": "gmail",
        "authorization_url": started["authorization_url"],
        "expires_at": started["expires_at"],
        "note": "Open the authorization URL in this browser. Callback completion requires the short-lived HttpOnly browser-binding cookie.",
    }


@app.get("/oauth/google/callback")
def google_oauth_callback(request: Request):
    """Public provider callback protected by one-time state plus browser binding."""
    if google_oauth_adapter is None:
        return HTMLResponse("Google OAuth is disabled.", status_code=503, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    # Reject ambiguous duplicate query parameters rather than accepting a parser's
    # first/last-value behavior for security-sensitive callback fields.
    for field in ("state", "code", "error"):
        if len(request.query_params.getlist(field)) > 1:
            audit_log.record("google_oauth_callback_rejected", "unknown", "oauth", "gmail", {"reason": "duplicate_query_parameter"})
            return _oauth_completion_redirect()

    state = request.query_params.get("state")
    code = request.query_params.get("code")
    provider_error = request.query_params.get("error")
    binding = request.cookies.get(_OAUTH_BINDING_COOKIE, "")
    if not state or not _OAUTH_BINDING_RE.fullmatch(binding):
        audit_log.record("google_oauth_callback_rejected", "unknown", "oauth", "gmail", {"reason": "missing_state_or_browser_binding"})
        return _oauth_completion_redirect()

    if provider_error:
        try:
            context = google_oauth_adapter.cancel_browser(state=state, browser_session_binding=binding)
            audit_log.record("google_oauth_authorization_cancelled", context["actor_id"], "oauth", "gmail",
                             {"key_id": context["key_id"], "provider_error_received": True})
        except GoogleOAuthError:
            audit_log.record("google_oauth_callback_rejected", "unknown", "oauth", "gmail", {"reason": "invalid_error_callback"})
        return _oauth_completion_redirect()

    if not code:
        audit_log.record("google_oauth_callback_rejected", "unknown", "oauth", "gmail", {"reason": "missing_code"})
        return _oauth_completion_redirect()
    try:
        result = google_oauth_adapter.complete_browser(
            code=code, state=state, browser_session_binding=binding,
        )
        actor_id = result.pop("_audit_actor_id", "unknown")
        key_id = result.pop("_audit_key_id", "unknown")
        audit_log.record("google_oauth_authorized", actor_id, "oauth", "gmail",
                         {"key_id": key_id, "account_label": result["account_label"],
                          "scope_count": len(result["scopes"]), "credential_tokens_exposed": False})
    except GoogleOAuthError:
        audit_log.record("google_oauth_callback_failed", "unknown", "oauth", "gmail",
                         {"reason": "state_validation_or_provider_exchange_failed"})
    return _oauth_completion_redirect()


@app.get("/api/v1/email/google/accounts")
def list_google_accounts(request: Request):
    """List only the connected Google accounts owned by the authenticated actor."""
    if google_oauth_adapter is None:
        raise HTTPException(status_code=503, detail="Google OAuth is disabled or not configured")
    actor = request.state.actor
    try:
        accounts = google_oauth_adapter.list_accounts(actor["actor_id"])
    except GoogleOAuthError:
        raise HTTPException(status_code=503, detail="Google account status is unavailable") from None
    return {"accounts": accounts, "tokens_exposed": False}


@app.post("/api/v1/email/google/accounts/{account_id}/refresh")
def refresh_google_account(account_id: str, request: Request):
    """Refresh an expiring Google access token; the response never contains tokens."""
    if google_oauth_adapter is None:
        raise HTTPException(status_code=503, detail="Google OAuth is disabled or not configured")
    actor = request.state.actor
    try:
        result = google_oauth_adapter.refresh_account_tokens(account_id, actor["actor_id"])
        audit_log.record("google_oauth_token_refresh", actor["actor_id"], "oauth_account", account_id,
                         {"key_id": actor["key_id"], "outcome": "refreshed" if result.get("refresh_performed") else "not_needed",
                          "token_values_exposed": False})
        return result
    except GoogleOAuthError:
        audit_log.record("google_oauth_token_refresh_failed", actor["actor_id"], "oauth_account", account_id,
                         {"key_id": actor["key_id"], "reason": "refresh_unavailable_or_failed"})
        raise HTTPException(status_code=409, detail="Google account could not be refreshed; inspect account status or reconnect") from None


@app.post("/api/v1/email/google/accounts/{account_id}/disconnect")
def disconnect_google_account(account_id: str, request: Request):
    """Disable local credentials and attempt Google-side token revocation."""
    if google_oauth_adapter is None:
        raise HTTPException(status_code=503, detail="Google OAuth is disabled or not configured")
    actor = request.state.actor
    try:
        result = google_oauth_adapter.disconnect_account(account_id, actor["actor_id"])
        audit_log.record("google_oauth_account_disconnected", actor["actor_id"], "oauth_account", account_id,
                         {"key_id": actor["key_id"], "local_disconnected": result["local_disconnected"],
                          "provider_revocation": result["provider_revocation"], "token_values_exposed": False})
        return result
    except GoogleOAuthError:
        audit_log.record("google_oauth_account_disconnect_failed", actor["actor_id"], "oauth_account", account_id,
                         {"key_id": actor["key_id"], "reason": "local_disconnect_failed"})
        raise HTTPException(status_code=404, detail="Google account is unavailable for this actor") from None


@app.get("/oauth/google/complete")
def google_oauth_complete_page():
    """Static landing page; never reflects OAuth query values or credential data."""
    return HTMLResponse(
        _OAUTH_COMPLETE_HTML,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer",
                 "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"},
    )


@app.post("/api/v1/connectors/sandbox.email/action-preview")
def preview_sandbox_email_action(payload: dict):
    """Generate the exact action string that must be approved for this email payload."""
    try:
        action = sandbox_email_action(payload)
        return {"action": action, "action_type": "email.send_sandbox",
                "note": "Approve this exact action, then issue a single-use execution permit. Previewing does not queue or send an email."}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/v1/connectors/gmail.email/uncertain-sends")
def list_uncertain_gmail_sends(request: Request, limit: int = 100):
    """Admin-only inventory of unresolved Gmail ledger entries; does not retry or reconcile them."""
    actor = request.state.actor
    if actor.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Administrator role required")
    if gmail_email_provider is None:
        raise HTTPException(status_code=503, detail="Real Gmail sending is not configured")
    try:
        sends = gmail_email_provider.ledger.unresolved(gmail_email_provider.account_id, limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    audit_log.record("gmail_uncertain_sends_listed", actor["actor_id"], "gmail_send_ledger",
                     gmail_email_provider.account_id,
                     {"key_id": actor.get("key_id"), "count": len(sends), "message_content_exposed": False})
    return {"sends": sends,
            "note": "This is a metadata-only investigation queue. It does not prove provider delivery, reconcile execution records, or authorize a retry."}


def _persist_reconciliation_audit(record):
    """Idempotently deliver the audit event, then mark its durable outbox row."""
    event_id = record.get("audit_event_id") or f"execution-reconciliation-{record['reconciliation_id']}"
    audit_log.record(
        "execution_reconciliation_recorded", record["actor_id"], "execution",
        record["execution_id"],
        {"key_id": record.get("key_id"), "outcome": record["outcome"],
         "reason_provided": True,
         "external_reference_provided": bool(record.get("external_reference"))},
        event_id=event_id,
    )
    execution_journal.mark_reconciliation_audit_status(record["reconciliation_id"], "recorded")
    return event_id


@app.get("/api/v1/executions/reconciliation-audit/pending")
def pending_execution_reconciliation_audits(request: Request, limit: int = 100):
    if execution_journal is None:
        raise HTTPException(status_code=503, detail="Execution journal is not configured")
    actor = request.state.actor
    if actor.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Administrator role required")
    try:
        pending = execution_journal.pending_reconciliation_audits(limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"pending": pending, "count": len(pending),
            "note": "Retrying audit delivery never repeats the underlying action or reconciliation decision."}


@app.post("/api/v1/executions/reconciliations/{reconciliation_id}/audit/retry")
def retry_execution_reconciliation_audit(reconciliation_id: int, request: Request):
    if execution_journal is None:
        raise HTTPException(status_code=503, detail="Execution journal is not configured")
    actor = request.state.actor
    if actor.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Administrator role required")
    record = next(iter(execution_journal.reconciliations_for_execution_reconciliation_id(reconciliation_id)), None)
    if record is None:
        raise HTTPException(status_code=404, detail="Reconciliation record not found")
    if record["audit_status"] == "recorded":
        return {"reconciliation_id": reconciliation_id, "audit_status": "recorded", "already_recorded": True}
    try:
        _persist_reconciliation_audit(record)
    except Exception:
        raise HTTPException(status_code=503, detail="Reconciliation is saved, but audit delivery remains pending; retry this audit endpoint") from None
    return {"reconciliation_id": reconciliation_id, "audit_status": "recorded", "already_recorded": False}


@app.get("/api/v1/connectors/gmail.email/reconciliation")
def inspect_gmail_execution_reconciliation(request: Request, limit: int = 100):
    """Cross-reference uncertain Gmail attempts with exact execution-journal IDs."""
    actor = request.state.actor
    if actor.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Administrator role required")
    if gmail_email_provider is None:
        raise HTTPException(status_code=503, detail="Real Gmail sending is not configured")
    if execution_journal is None:
        raise HTTPException(status_code=503, detail="Execution journal is not configured")
    try:
        report = GmailExecutionReconciliationService(
            gmail_email_provider.ledger, execution_journal
        ).inspect(gmail_email_provider.account_id, limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    audit_log.record("gmail_execution_reconciliation_inspected", actor["actor_id"],
                     "gmail_send_ledger", gmail_email_provider.account_id,
                     {"key_id": actor.get("key_id"), "count": len(report["attempts"]),
                      "counts": report["counts"], "mutated_state": False})
    return report


@app.post("/api/v1/connectors/gmail.email/uncertain-sends/{attempt_id}/reconcile")
def reconcile_gmail_send_attempt(attempt_id: str, payload: dict, request: Request):
    """Record an administrator's evidence-based decision without changing send state."""
    actor = request.state.actor
    if actor.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Administrator role required")
    if gmail_email_provider is None:
        raise HTTPException(status_code=503, detail="Real Gmail sending is not configured")
    if execution_journal is None:
        raise HTTPException(status_code=503, detail="Execution journal is not configured")
    allowed_fields = {"outcome", "reason", "external_reference"}
    if not isinstance(payload, dict) or set(payload) - allowed_fields:
        raise HTTPException(status_code=400, detail="Only outcome, reason, and external_reference are accepted")
    try:
        service = GmailExecutionReconciliationService(gmail_email_provider.ledger, execution_journal)
        result = service.record_decision(
            gmail_email_provider.account_id, attempt_id, payload.get("outcome"),
            payload.get("reason"), actor, payload.get("external_reference"),
        )
        record = result["reconciliation"]
        _persist_reconciliation_audit(record)
        return result
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except Exception:
        raise HTTPException(status_code=503, detail="Reconciliation is saved, but audit delivery remains pending; retry the execution reconciliation audit endpoint") from None


@app.post("/api/v1/connectors/gmail.email/action-preview")
def preview_gmail_email_action(payload: dict):
    """Prepare the exact provider/account/content-bound action; does not send."""
    if gmail_email_provider is None:
        raise HTTPException(status_code=503, detail="Real Gmail sending is not configured")
    try:
        action = email_provider_registry.build_action("gmail", payload)
        return {"action": action, "action_type": "email.send_gmail",
                "note": "Preview only. Create an approval for this exact action, then issue a single-use execution permit before execution."}
    except (ValueError, EmailProviderError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/v1/connectors/{connector_id}/execute")
def execute_connector(connector_id: str, payload: dict, request: Request):
    actor = request.state.actor
    try:
        result = connector_registry.execute(
            connector_id=connector_id,
            permit_id=payload["permit_id"],
            mission_id=payload["mission_id"],
            action=payload["action"],
            payload=payload["input"],
            actor=actor,
        )
        audit_log.record("connector_execution_completed", actor["actor_id"], "execution", result.execution_id,
                         {"actor_id": actor["actor_id"], "key_id": actor["key_id"], "role": actor["role"],
                          "connector_id": connector_id, "status": result.status,
                          "action_digest": result.action_digest})
        note = ("sandbox.email only records a simulation; it does not send an external email."
                if connector_id == "sandbox.email" else
                "Gmail execution uses the configured provider and approval gateway; an uncertain result must be reconciled before any new attempt.")
        return {"execution": asdict(result), "note": note}
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Missing request field: {exc.args[0]}")
    except Exception as exc:
        from app.execution import ExecutionError
        if isinstance(exc, ExecutionError):
            raise HTTPException(status_code=400, detail=str(exc))
        raise


@app.get("/api/v1/sandbox-email/outbox")
def list_sandbox_email_outbox(limit: int = 100):
    try:
        return {"messages": sandbox_email_outbox.list_messages(limit),
                "note": "These are simulated messages only; none were sent externally."}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/v1/executions/uncertain")
def list_uncertain_executions():
    if execution_journal is None:
        raise HTTPException(status_code=503, detail="Execution journal is not configured")
    return {"executions": execution_journal.uncertain(),
            "note": "Uncertain outcomes must be reconciled before any manual retry; Alex never retries them automatically."}

@app.get("/api/v1/executions/{execution_id}/reconciliations")
def list_execution_reconciliations(execution_id: str):
    if execution_journal is None:
        raise HTTPException(status_code=503, detail="Execution journal is not configured")
    if execution_journal.get(execution_id) is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    return {"execution_id": execution_id,
            "reconciliations": execution_journal.reconciliations_for(execution_id)}

@app.post("/api/v1/executions/{execution_id}/reconcile")
def reconcile_execution(execution_id: str, payload: dict, request: Request):
    if execution_journal is None:
        raise HTTPException(status_code=503, detail="Execution journal is not configured")
    actor = request.state.actor
    try:
        record = execution_journal.record_reconciliation(
            execution_id, payload.get("outcome"), payload.get("reason"), actor,
            payload.get("external_reference"),
        )
        _persist_reconciliation_audit(record)
        return {"reconciliation": record,
                "execution_status": execution_journal.get(execution_id)["status"],
                "note": "Reconciliation is an audit decision only; it does not rerun the action or make retries safe."}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=503, detail="Reconciliation is saved, but audit delivery remains pending; retry the execution reconciliation audit endpoint") from None


@app.get("/api/v1/executions")
def list_execution_history(limit: int = 100, status: str | None = None):
    """Admin-only bounded execution history from the durable journal."""
    if execution_journal is None:
        raise HTTPException(status_code=503, detail="Execution journal is not configured")
    try:
        return {"executions": execution_journal.list(limit=limit, status=status),
                "limit": limit, "status_filter": status,
                "note": "Durable history is available only when ALEX_EXECUTION_JOURNAL_PATH is configured."}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
