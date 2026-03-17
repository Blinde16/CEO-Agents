from fastapi.testclient import TestClient

from app.main import ACTIONS, ACTION_LOGS, APPROVAL_QUEUE, CLIENTS, app
from app.schemas import ActionStatus, ApprovalStatus

client = TestClient(app)


def setup_function() -> None:
    CLIENTS.clear()
    ACTIONS.clear()
    APPROVAL_QUEUE.clear()
    ACTION_LOGS.clear()


def create_client() -> None:
    response = client.post(
        "/clients",
        json={
            "client_id": "c1",
            "display_name": "Acme Executive Office",
            "timezone": "America/Denver",
            "working_hours": "08:00-17:00",
            "scheduling_preferences": {},
            "approval_rules": {},
            "priority_contacts": ["Sarah"],
        },
    )
    assert response.status_code == 200


def test_actions_requiring_approval_are_queued_then_executed_after_approval() -> None:
    create_client()
    response = client.post(
        "/actions",
        json={
            "client_id": "c1",
            "user_id": "u1",
            "action_type": "draft_email_reply",
            "payload": {"source_text": "reply to this email"},
        },
    )
    assert response.status_code == 200
    action = response.json()
    assert action["status"] == ActionStatus.queued
    assert action["approval_status"] == ApprovalStatus.pending

    approvals = client.get("/approvals")
    assert approvals.status_code == 200
    approval_id = approvals.json()[0]["approval_id"]

    approve_response = client.post(
        "/approvals/decision",
        json={
            "approval_id": approval_id,
            "reviewer_id": "reviewer-1",
            "decision": ApprovalStatus.approved,
        },
    )
    assert approve_response.status_code == 200

    updated_action = ACTIONS[action["action_id"]]
    assert updated_action.status == ActionStatus.executed
    assert updated_action.approval_status == ApprovalStatus.approved
    assert updated_action.result["provider"] == "email"


def test_approval_decision_is_idempotent_for_same_final_state() -> None:
    create_client()
    create_response = client.post(
        "/actions",
        json={
            "client_id": "c1",
            "user_id": "u1",
            "action_type": "cancel_event",
            "payload": {"source_text": "cancel this meeting"},
        },
    )
    assert create_response.status_code == 200

    approval_id = client.get("/approvals").json()[0]["approval_id"]

    first_decision = client.post(
        "/approvals/decision",
        json={
            "approval_id": approval_id,
            "reviewer_id": "reviewer-1",
            "decision": ApprovalStatus.rejected,
        },
    )
    assert first_decision.status_code == 200

    second_decision = client.post(
        "/approvals/decision",
        json={
            "approval_id": approval_id,
            "reviewer_id": "reviewer-2",
            "decision": ApprovalStatus.rejected,
        },
    )
    assert second_decision.status_code == 200

    # Replay does not mutate finalized metadata.
    assert second_decision.json()["reviewer_id"] == "reviewer-1"


def test_approval_decision_rejects_conflicting_final_state_transition() -> None:
    create_client()
    create_response = client.post(
        "/actions",
        json={
            "client_id": "c1",
            "user_id": "u1",
            "action_type": "cancel_event",
            "payload": {"source_text": "cancel this meeting"},
        },
    )
    assert create_response.status_code == 200

    approval_id = client.get("/approvals").json()[0]["approval_id"]

    first_decision = client.post(
        "/approvals/decision",
        json={
            "approval_id": approval_id,
            "reviewer_id": "reviewer-1",
            "decision": ApprovalStatus.rejected,
        },
    )
    assert first_decision.status_code == 200

    conflicting_decision = client.post(
        "/approvals/decision",
        json={
            "approval_id": approval_id,
            "reviewer_id": "reviewer-2",
            "decision": ApprovalStatus.approved,
        },
    )
    assert conflicting_decision.status_code == 409
    assert conflicting_decision.json()["detail"] == "approval already finalized"


def test_action_creation_requires_existing_client() -> None:
    response = client.post(
        "/actions",
        json={
            "client_id": "missing",
            "user_id": "u1",
            "action_type": "create_event",
            "payload": {"source_text": "schedule lunch tomorrow"},
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "client not found"


def test_assistant_requests_clarification_for_missing_email_recipient() -> None:
    create_client()
    response = client.post(
        "/assistant/respond",
        json={
            "client_id": "c1",
            "user_id": "u1",
            "message": "Reply and let them know Thursday afternoon works.",
            "context": None,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "needs_clarification"
    assert "Who should this email go to?" in body["assistant_message"]


def test_assistant_builds_email_draft_after_follow_up() -> None:
    create_client()
    first_response = client.post(
        "/assistant/respond",
        json={
            "client_id": "c1",
            "user_id": "u1",
            "message": "Reply about Thursday afternoon working well.",
            "context": None,
        },
    )
    assert first_response.status_code == 200

    second_response = client.post(
        "/assistant/respond",
        json={
            "client_id": "c1",
            "user_id": "u1",
            "message": "Send it to Sarah.",
            "context": first_response.json()["context"],
        },
    )
    assert second_response.status_code == 200
    body = second_response.json()
    assert body["state"] == "draft_ready"
    assert body["proposal"]["kind"] == "email"
    assert body["proposal"]["title"] == "Draft reply to Sarah"


def test_calendar_read_request_requires_connected_google() -> None:
    create_client()
    response = client.post(
        "/assistant/respond",
        json={
            "client_id": "c1",
            "user_id": "u1",
            "message": "What's on my calendar tomorrow?",
            "context": None,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "calendar_read"
    assert "Connect Google first" in body["assistant_message"]


def test_availability_request_requires_connected_google() -> None:
    create_client()
    response = client.post(
        "/assistant/respond",
        json={
            "client_id": "c1",
            "user_id": "u1",
            "message": "When am I free this week?",
            "context": None,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "availability_read"
    assert "Connect Google first" in body["assistant_message"]


def test_email_read_request_requires_connected_google() -> None:
    create_client()
    response = client.post(
        "/assistant/respond",
        json={
            "client_id": "c1",
            "user_id": "u1",
            "message": "Review my inbox",
            "context": None,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "email_read"
    assert "Connect Google first" in body["assistant_message"]
