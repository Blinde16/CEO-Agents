from app.approval import approval_status_for_action
from app.intent_parser import parse_intent
from app.schemas import ApprovalStatus, RiskLevel


def test_parse_reschedule_intent():
    parsed = parse_intent("Move lunch with Sarah to next week")
    assert parsed.intent == "reschedule_event"
    assert parsed.risk_level == RiskLevel.medium
    assert parsed.entities["date_range"] == "next_week"


def test_email_action_requires_approval():
    status = approval_status_for_action("draft_email_reply", RiskLevel.low)
    assert status == ApprovalStatus.pending
