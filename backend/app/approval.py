from __future__ import annotations

from app.schemas import ApprovalStatus, RiskLevel


DEFAULT_APPROVAL_ACTIONS = {
    "draft_email_reply",
    "cancel_event",
}


def requires_approval(action_type: str, risk_level: RiskLevel, priority_contact: bool = False) -> bool:
    if action_type in DEFAULT_APPROVAL_ACTIONS:
        return True
    if action_type == "reschedule_event" and (risk_level in {RiskLevel.medium, RiskLevel.high}):
        return True
    if priority_contact:
        return True
    return False


def approval_status_for_action(action_type: str, risk_level: RiskLevel, priority_contact: bool = False) -> ApprovalStatus:
    return ApprovalStatus.pending if requires_approval(action_type, risk_level, priority_contact) else ApprovalStatus.not_required
