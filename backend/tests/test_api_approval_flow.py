from fastapi.testclient import TestClient

from app.main import ACTIONS, ACTION_LOGS, APPROVAL_QUEUE, CLIENTS, app
from app.schemas import ActionStatus, ApprovalStatus

client = TestClient(app)


def setup_function() -> None:
    CLIENTS.clear()
    ACTIONS.clear()
    APPROVAL_QUEUE.clear()
    ACTION_LOGS.clear()


def test_actions_requiring_approval_are_queued_then_executed_after_approval() -> None:
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


def test_approval_decision_is_idempotent_for_same_final_state() -> None:
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
