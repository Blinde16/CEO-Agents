from __future__ import annotations

from app.schemas import ParsedIntent, RiskLevel


INTENT_KEYWORDS = {
    "reschedule_event": ["move", "reschedule"],
    "cancel_event": ["cancel"],
    "create_event": ["schedule", "book"],
    "draft_email_reply": ["reply", "email"],
    "create_task": ["task", "todo"],
    "set_reminder": ["remind", "reminder"],
    "generate_daily_briefing": ["daily briefing", "briefing"],
}


def parse_intent(text: str) -> ParsedIntent:
    lower = text.lower()
    detected_intent = "unknown"

    for intent, words in INTENT_KEYWORDS.items():
        if any(word in lower for word in words):
            detected_intent = intent
            break

    risk = RiskLevel.low
    if detected_intent in {"reschedule_event", "cancel_event", "draft_email_reply"}:
        risk = RiskLevel.medium
    if detected_intent == "cancel_event" and "priority" in lower:
        risk = RiskLevel.high

    entities = {"raw_text": text}
    if "next week" in lower:
        entities["date_range"] = "next_week"
    if "tomorrow" in lower:
        entities["date_range"] = "tomorrow"

    return ParsedIntent(intent=detected_intent, entities=entities, risk_level=risk)
