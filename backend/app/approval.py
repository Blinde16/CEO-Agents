from __future__ import annotations

from app.schemas import ApprovalStatus, ClientConfig, RiskLevel

DEFAULT_APPROVAL_ACTIONS = {
    "draft_email_reply",
    "cancel_event",
}


def requires_approval(
    action_type: str,
    risk_level: RiskLevel,
    priority_contact: bool = False,
    client: ClientConfig | None = None,
) -> bool:
    """
    Determine if an action requires human approval before execution.

    Checks (in priority order):
    1. Client-configured approval_rules (overrides defaults if present)
    2. Priority contact flag — always requires approval
    3. Hardcoded defaults for high-risk action types
    """
    # 1. Client-configured rules take precedence
    if client and client.approval_rules:
        rules = client.approval_rules
        # e.g. approval_rules: {"require_approval_for": ["create_event", "draft_email_reply"]}
        required_for = rules.get("require_approval_for", [])
        if isinstance(required_for, list) and action_type in required_for:
            return True

        # e.g. approval_rules: {"skip_approval_for": ["create_event"]}
        skip_for = rules.get("skip_approval_for", [])
        if isinstance(skip_for, list) and action_type in skip_for:
            return False

        # e.g. approval_rules: {"require_approval_above_risk": "low"}
        risk_threshold = rules.get("require_approval_above_risk")
        if risk_threshold:
            threshold_map = {RiskLevel.low: 0, RiskLevel.medium: 1, RiskLevel.high: 2}
            current_risk_val = threshold_map.get(risk_level, 0)
            threshold_val = threshold_map.get(RiskLevel(risk_threshold), 1)
            if current_risk_val >= threshold_val:
                return True

    # 2. Priority contacts always need approval regardless of action type
    if priority_contact:
        return True

    # 3. Hardcoded defaults
    if action_type in DEFAULT_APPROVAL_ACTIONS:
        return True
    if action_type == "reschedule_event" and risk_level in {RiskLevel.medium, RiskLevel.high}:
        return True

    return False


def approval_status_for_action(
    action_type: str,
    risk_level: RiskLevel,
    priority_contact: bool = False,
    client: ClientConfig | None = None,
) -> ApprovalStatus:
    return (
        ApprovalStatus.pending
        if requires_approval(action_type, risk_level, priority_contact, client)
        else ApprovalStatus.not_required
    )
