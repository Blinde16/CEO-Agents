from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException

from app.action_engine import UnsupportedActionError, execute_action
from app.approval import approval_status_for_action
from app.intent_parser import parse_intent
from app.schemas import (
    ActionLog,
    ActionRecord,
    ActionRequest,
    ActionStatus,
    ApprovalDecision,
    ApprovalStatus,
    ClientConfig,
    IntentRequest,
)

app = FastAPI(title="CEO-Agents API", version="1.0.0")

CLIENTS: dict[str, ClientConfig] = {}
ACTIONS: dict[str, ActionRecord] = {}
ACTION_LOGS: list[ActionLog] = []
APPROVAL_QUEUE: dict[str, dict] = {}

FINAL_APPROVAL_STATES = {
    ApprovalStatus.approved,
    ApprovalStatus.rejected,
    ApprovalStatus.expired,
}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/clients", response_model=ClientConfig)
def upsert_client(config: ClientConfig) -> ClientConfig:
    CLIENTS[config.client_id] = config
    return config


@app.get("/clients/{client_id}", response_model=ClientConfig)
def get_client(client_id: str) -> ClientConfig:
    client = CLIENTS.get(client_id)
    if not client:
        raise HTTPException(status_code=404, detail="client not found")
    return client


@app.post("/intent/parse")
def parse_user_intent(request: IntentRequest) -> dict:
    parsed = parse_intent(request.text)
    return parsed.model_dump()


@app.post("/actions", response_model=ActionRecord)
def queue_or_execute_action(request: ActionRequest) -> ActionRecord:
    parsed = parse_intent(request.payload.get("source_text", request.action_type))
    approval_status = approval_status_for_action(request.action_type, parsed.risk_level)

    action_id = str(uuid4())
    action = ActionRecord(
        action_id=action_id,
        client_id=request.client_id,
        user_id=request.user_id,
        action_type=request.action_type,
        payload=request.payload,
        status=ActionStatus.queued,
        approval_status=approval_status,
        created_at=datetime.now(timezone.utc),
    )
    ACTIONS[action_id] = action

    if approval_status == ApprovalStatus.pending:
        approval_id = str(uuid4())
        APPROVAL_QUEUE[approval_id] = {
            "approval_id": approval_id,
            "action_id": action_id,
            "client_id": request.client_id,
            "status": ApprovalStatus.pending,
            "reviewer_id": None,
            "decision_time": None,
        }
    else:
        _execute(action)

    _log_action(action, None)
    return ACTIONS[action_id]


@app.get("/approvals")
def list_approvals() -> list[dict]:
    return list(APPROVAL_QUEUE.values())


@app.post("/approvals/decision")
def decide_approval(decision: ApprovalDecision) -> dict:
    approval = APPROVAL_QUEUE.get(decision.approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="approval not found")

    if decision.decision not in FINAL_APPROVAL_STATES:
        raise HTTPException(status_code=400, detail="invalid decision")

    current_status = approval["status"]

    # Idempotent replay: return existing finalized state unchanged.
    if current_status in FINAL_APPROVAL_STATES and current_status == decision.decision:
        return approval

    if current_status in FINAL_APPROVAL_STATES and current_status != decision.decision:
        raise HTTPException(status_code=409, detail="approval already finalized")

    approval["status"] = decision.decision
    approval["reviewer_id"] = decision.reviewer_id
    approval["decision_time"] = datetime.now(timezone.utc).isoformat()

    action = ACTIONS[approval["action_id"]]
    action.approval_status = decision.decision

    if decision.decision == ApprovalStatus.approved:
        _execute(action)

    _log_action(action, None if decision.decision == ApprovalStatus.approved else "action not approved")
    return approval


@app.get("/logs", response_model=list[ActionLog])
def list_logs() -> list[ActionLog]:
    return ACTION_LOGS


def _execute(action: ActionRecord) -> None:
    try:
        execute_action(action.action_type, action.payload)
        action.status = ActionStatus.executed
    except UnsupportedActionError as exc:
        action.status = ActionStatus.failed
        _log_action(action, str(exc))


def _log_action(action: ActionRecord, error: str | None) -> None:
    ACTION_LOGS.append(
        ActionLog(
            client_id=action.client_id,
            user_id=action.user_id,
            timestamp=datetime.now(timezone.utc),
            action_type=action.action_type,
            action_status=action.status,
            error_message=error,
            executed_by="action_engine",
            approval_status=action.approval_status,
        )
    )
