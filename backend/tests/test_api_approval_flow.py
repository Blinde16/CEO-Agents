from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import app.main as main_module
import app.webhooks as webhooks_module
from app.database import db
from app.schemas import ActionStatus, ApprovalStatus, IntegrationRecord

client = TestClient(main_module.app)


def setup_function() -> None:
    db.clear_all()


def create_client(client_id: str = "c1") -> None:
    response = client.post(
        "/clients",
        json={
            "client_id": client_id,
            "display_name": "Acme Executive Office",
            "timezone": "America/Denver",
            "working_hours": "08:00-17:00",
            "scheduling_preferences": {},
            "approval_rules": {},
            "priority_contacts": ["Sarah"],
            "voice_examples": [],
            "learned_preferences": [],
            "focus_blocks": [],
        },
    )
    assert response.status_code == 200


def connect_google(client_id: str = "c1") -> None:
    db.save_integration(
        IntegrationRecord(
            client_id=client_id,
            provider="google",
            status="connected",
            connected_account=f"{client_id}@example.com",
            scopes=["gmail.readonly", "calendar.readonly"],
        ),
        tokens={"access_token": "token", "expires_at": "2099-01-01T00:00:00+00:00"},
    )


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
    assert action["status"] == ActionStatus.queued.value
    assert action["approval_status"] == ApprovalStatus.pending.value

    approvals = client.get("/approvals")
    assert approvals.status_code == 200
    approval_id = approvals.json()[0]["approval_id"]

    approve_response = client.post(
        "/approvals/decision",
        json={
            "approval_id": approval_id,
            "reviewer_id": "reviewer-1",
            "decision": ApprovalStatus.approved.value,
        },
    )
    assert approve_response.status_code == 200

    updated_action = db.get_action(action["action_id"])
    assert updated_action is not None
    assert updated_action.status == ActionStatus.executed
    assert updated_action.approval_status == ApprovalStatus.approved
    assert updated_action.result is not None
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
            "decision": ApprovalStatus.rejected.value,
        },
    )
    assert first_decision.status_code == 200

    second_decision = client.post(
        "/approvals/decision",
        json={
            "approval_id": approval_id,
            "reviewer_id": "reviewer-2",
            "decision": ApprovalStatus.rejected.value,
        },
    )
    assert second_decision.status_code == 200
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
            "decision": ApprovalStatus.rejected.value,
        },
    )
    assert first_decision.status_code == 200

    conflicting_decision = client.post(
        "/approvals/decision",
        json={
            "approval_id": approval_id,
            "reviewer_id": "reviewer-2",
            "decision": ApprovalStatus.approved.value,
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


def test_assistant_extracts_email_recipient_and_topic_from_natural_request(monkeypatch) -> None:
    async def no_llm(*args, **kwargs):
        return None

    monkeypatch.setattr(main_module, "generate_structured_response", no_llm)

    create_client()
    response = client.post(
        "/assistant/respond",
        json={
            "client_id": "c1",
            "user_id": "u1",
            "message": "Reply to Sarah and let her know Thursday afternoon works.",
            "context": None,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "draft_ready"
    assert body["proposal"]["title"] == "Draft reply to Sarah"
    assert body["proposal"]["payload"]["topic"] == "Thursday afternoon works"


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


def test_rejection_feedback_stores_preference_rule(monkeypatch) -> None:
    async def fake_extract_preference(*args, **kwargs) -> str:
        return "Use casual, direct language for email replies"

    monkeypatch.setattr(main_module, "extract_preference_from_feedback", fake_extract_preference)

    create_client()
    draft_response = client.post(
        "/assistant/respond",
        json={
            "client_id": "c1",
            "user_id": "u1",
            "message": "Reply to John about the contract renewal",
            "context": None,
        },
    )
    assert draft_response.status_code == 200
    proposal = draft_response.json()["proposal"]

    action_response = client.post(
        "/actions",
        json={
            "client_id": "c1",
            "user_id": "u1",
            "action_type": proposal["action_type"],
            "payload": proposal["payload"],
        },
    )
    assert action_response.status_code == 200
    approval_id = client.get("/approvals").json()[0]["approval_id"]

    reject_response = client.post(
        "/approvals/decision",
        json={
            "approval_id": approval_id,
            "reviewer_id": "reviewer-1",
            "decision": ApprovalStatus.rejected.value,
            "feedback": "too formal",
        },
    )
    assert reject_response.status_code == 200

    stored_client = db.get_client("c1")
    assert stored_client is not None
    assert stored_client.learned_preferences[-1].rule == "Use casual, direct language for email replies"


def test_rejected_feedback_changes_next_fallback_email_draft(monkeypatch) -> None:
    async def fake_structured_response(*args, **kwargs):
        return None

    async def fake_extract_preference(*args, **kwargs) -> str:
        return "Use casual, direct language for email replies"

    async def fake_generate_email_draft(client, recipient_name, topic, thread_messages, user_instruction):
        greeting = "Hi" if client.learned_preferences else "Hello"
        return {
            "subject": f"Re: {topic.title()}",
            "draft_body": (
                f"{greeting} {recipient_name},\n\nFollowing up on {topic}.\n\nBest,\nAcme Executive Office"
            ),
            "confidence": 0.8,
        }

    monkeypatch.setattr(main_module, "generate_structured_response", fake_structured_response)
    monkeypatch.setattr(main_module, "extract_preference_from_feedback", fake_extract_preference)
    monkeypatch.setattr(main_module, "generate_email_draft", fake_generate_email_draft)

    create_client()
    initial_response = client.post(
        "/assistant/respond",
        json={
            "client_id": "c1",
            "user_id": "u1",
            "message": "Reply to John about the contract renewal",
            "context": None,
        },
    )
    assert initial_response.status_code == 200
    initial_proposal = initial_response.json()["proposal"]
    initial_draft = next(
        detail["value"] for detail in initial_proposal["details"] if detail["label"] == "Draft"
    )
    assert initial_draft.startswith("Hello John,")

    action_response = client.post(
        "/actions",
        json={
            "client_id": "c1",
            "user_id": "u1",
            "action_type": initial_proposal["action_type"],
            "payload": initial_proposal["payload"],
        },
    )
    assert action_response.status_code == 200
    approval_id = client.get("/approvals").json()[0]["approval_id"]

    reject_response = client.post(
        "/approvals/decision",
        json={
            "approval_id": approval_id,
            "reviewer_id": "reviewer-1",
            "decision": ApprovalStatus.rejected.value,
            "feedback": "too formal",
        },
    )
    assert reject_response.status_code == 200

    revised_response = client.post(
        "/assistant/respond",
        json={
            "client_id": "c1",
            "user_id": "u1",
            "message": "Reply to John about the contract renewal",
            "context": None,
        },
    )
    assert revised_response.status_code == 200
    revised_proposal = revised_response.json()["proposal"]
    revised_draft = next(
        detail["value"] for detail in revised_proposal["details"] if detail["label"] == "Draft"
    )
    assert revised_draft.startswith("Hi John,")


def test_morning_briefing_requires_secret() -> None:
    response = client.post("/webhooks/n8n/morning-briefing")
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid webhook secret"


def test_morning_briefing_returns_payload_for_connected_client(monkeypatch) -> None:
    create_client()
    connect_google()

    async def fake_generate_briefing(*args, **kwargs) -> dict:
        return {
            "relationship_context": "Met last week about pipeline.",
            "open_items": ["Budget approval"],
            "suggested_talking_points": ["Confirm launch date"],
        }

    monkeypatch.setattr(webhooks_module.calendar, "list_events", lambda client_id: {
        "events": [
            {
                "id": "evt-1",
                "title": "Partner Sync",
                "start": "2099-01-01T15:00:00+00:00",
                "attendees": ["partner@example.com"],
            }
        ]
    })
    monkeypatch.setattr(webhooks_module.email, "find_message_for_contact", lambda *args, **kwargs: {
        "from": "partner@example.com",
        "subject": "Agenda",
        "snippet": "Here is the agenda",
    })
    monkeypatch.setattr(webhooks_module, "generate_briefing", fake_generate_briefing)

    response = client.post(
        "/webhooks/n8n/morning-briefing",
        headers={"X-N8N-Secret": "test-secret"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["briefings"][0]["event_title"] == "Partner Sync"
    assert body["briefings"][0]["open_items"] == ["Budget approval"]


def test_pre_meeting_returns_matching_upcoming_events(monkeypatch) -> None:
    create_client()
    connect_google()

    now = datetime.now(timezone.utc)
    soon = (now + timedelta(minutes=30)).isoformat()

    async def fake_generate_briefing(*args, **kwargs) -> dict:
        return {
            "relationship_context": "Warm relationship.",
            "open_items": [],
            "suggested_talking_points": ["Ask for decision"],
        }

    monkeypatch.setattr(webhooks_module.calendar, "list_events", lambda client_id: {
        "events": [
            {
                "id": "evt-2",
                "title": "Investor Call",
                "start": soon,
                "attendees": ["investor@example.com"],
            }
        ]
    })
    monkeypatch.setattr(webhooks_module.email, "find_message_for_contact", lambda *args, **kwargs: None)
    monkeypatch.setattr(webhooks_module, "generate_briefing", fake_generate_briefing)

    response = client.post(
        "/webhooks/n8n/pre-meeting",
        headers={"X-N8N-Secret": "test-secret"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["briefings"][0]["event_title"] == "Investor Call"


def test_inbox_triage_returns_ranked_items(monkeypatch) -> None:
    create_client()
    connect_google()

    async def fake_triage_inbox(*args, **kwargs):
        from app.schemas import EmailTriageResult

        return [
            EmailTriageResult(
                message_id="m1",
                subject="Need approval today",
                sender="ops@example.com",
                date="Wed, 17 Mar 2026 10:00:00 +0000",
                category="action_required",
                urgency_score=4,
                summary="Need a fast response.",
                action_items=["Approve budget"],
                requires_reply=True,
            )
        ]

    monkeypatch.setattr(webhooks_module.email, "list_messages", lambda client_id: {
        "messages": [
            {
                "id": "m1",
                "from": "ops@example.com",
                "subject": "Need approval today",
                "snippet": "Need your response",
            }
        ]
    })
    monkeypatch.setattr(webhooks_module, "triage_inbox", fake_triage_inbox)

    response = client.post(
        "/webhooks/n8n/inbox-triage",
        headers={"X-N8N-Secret": "test-secret"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["client_count"] == 1
    assert body["results"][0]["urgent_count"] == 1
    assert body["results"][0]["items"][0]["subject"] == "Need approval today"
