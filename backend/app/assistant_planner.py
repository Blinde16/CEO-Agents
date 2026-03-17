from __future__ import annotations

from typing import Any, Callable

from app.intent_parser import parse_intent
from app.llm import generate_conversation_plan
from app.schemas import AssistantPlan, ClientConfig, ConversationContext

_READ_TOOLS = {
    "read_calendar": "calendar.list_events",
    "check_availability": "calendar.find_availability",
    "read_email": "gmail.list_messages",
}

_WRITE_TOOLS = {
    "draft_email_reply": "gmail.create_draft",
    "create_event": "calendar.create_event",
    "reschedule_event": "calendar.update_event",
    "cancel_event": "calendar.cancel_event",
}

_READ_INTENTS = set(_READ_TOOLS)
_WRITE_INTENTS = set(_WRITE_TOOLS)


def _is_email_capability_request(message: str) -> bool:
    lower = message.lower()
    return (
        "email" in lower or "emails" in lower or "inbox" in lower or "gmail" in lower
    ) and any(
        phrase in lower
        for phrase in [
            "what can you do",
            "what can it do",
            "what can you help with",
            "what are you able",
            "how can you help",
            "what do you do",
            "what can you do with",
        ]
    )


def _is_calendar_capability_request(message: str) -> bool:
    lower = message.lower()
    return (
        "calendar" in lower or "schedule" in lower or "meetings" in lower
    ) and any(
        phrase in lower
        for phrase in [
            "what can you do",
            "what can it do",
            "what can you help with",
            "what are you able",
            "how can you help",
            "what do you do",
            "what can you do with",
        ]
    )


def _is_calendar_read_request(message: str) -> bool:
    lower = message.lower()
    return any(
        phrase in lower
        for phrase in [
            "what's on my calendar",
            "what is on my calendar",
            "show my calendar",
            "show calendar",
            "list my calendar",
            "list calendar",
            "what do i have",
            "what meetings do i have",
            "what events do i have",
            "show my events",
            "show my schedule",
            "what is my schedule",
            "what's my schedule",
            "what's on my schedule",
            "my calendar",
            "my schedule",
            "what do i have today",
            "what do i have tomorrow",
            "what do i have this week",
            "what's happening",
            "what's coming up",
            "what meetings",
            "my meetings",
            "am i busy",
            "do i have anything",
            "do i have a meeting",
        ]
    )


def _is_availability_request(message: str) -> bool:
    lower = message.lower()
    return any(
        phrase in lower
        for phrase in [
            "when am i free",
            "when do i have time",
            "find time",
            "find a time",
            "best time",
            "open slot",
            "available slot",
            "availability",
            "when can i meet",
            "free time",
            "free slot",
            "when are you free",
            "when would work",
            "open window",
            "open time",
            "gap in my calendar",
        ]
    )


def _is_email_read_request(message: str) -> bool:
    lower = message.lower()
    return any(
        phrase in lower
        for phrase in [
            "review my inbox",
            "review inbox",
            "show my inbox",
            "show inbox",
            "what emails do i have",
            "what's in my inbox",
            "what is in my inbox",
            "summarize my inbox",
            "categorize my inbox",
            "triage my inbox",
            "check my email",
            "check my inbox",
            "what email",
            "my inbox",
            "any emails",
            "new emails",
            "unread emails",
            "important emails",
            "what messages",
            "check messages",
        ]
    )


def _resolve_action_type(context: ConversationContext, parsed_intent: str) -> str | None:
    if parsed_intent in _READ_INTENTS | _WRITE_INTENTS:
        return parsed_intent
    if context.action_type in _WRITE_INTENTS:
        return context.action_type
    return None


def _missing_fields_for_action(action_type: str | None, collected_fields: dict) -> list[str]:
    if action_type is None:
        return []
    requirements = {
        "draft_email_reply": ["recipient_name", "topic"],
        "create_event": ["contact_name", "requested_time", "title"],
        "reschedule_event": ["contact_name", "requested_time"],
        "cancel_event": ["contact_name"],
    }
    return [field for field in requirements.get(action_type, []) if not collected_fields.get(field)]


def _fallback_plan(
    client: ClientConfig,
    context: ConversationContext,
    message: str,
    extract_fields: Callable[[str | None, str, ClientConfig], dict[str, Any]],
) -> AssistantPlan:
    if _is_email_capability_request(message):
        return AssistantPlan(
            mode="capability",
            capability_scope="email",
            tool_name=None,
            requires_approval=False,
            needs_google=True,
            assistant_message=(
                "With your email connected, I can review recent inbox messages, summarize what needs attention, "
                "pull thread context for a contact, and draft a reply for your approval before anything is sent."
            ),
            confidence=0.95,
        )

    if _is_calendar_capability_request(message):
        return AssistantPlan(
            mode="capability",
            capability_scope="calendar",
            tool_name=None,
            requires_approval=False,
            needs_google=True,
            assistant_message=(
                "With your calendar connected, I can read upcoming events, find open time, prepare meeting briefs, "
                "and draft calendar changes for your approval before placing them."
            ),
            confidence=0.95,
        )

    if _is_calendar_read_request(message):
        return AssistantPlan(
            mode="read",
            action_type="read_calendar",
            tool_name=_READ_TOOLS["read_calendar"],
            requires_approval=False,
            needs_google=True,
            assistant_message="Let me check your calendar.",
            confidence=0.92,
        )

    if _is_availability_request(message):
        return AssistantPlan(
            mode="read",
            action_type="check_availability",
            tool_name=_READ_TOOLS["check_availability"],
            requires_approval=False,
            needs_google=True,
            assistant_message="Let me check your open time.",
            confidence=0.92,
        )

    if _is_email_read_request(message):
        return AssistantPlan(
            mode="read",
            action_type="read_email",
            tool_name=_READ_TOOLS["read_email"],
            requires_approval=False,
            needs_google=True,
            assistant_message="Let me review your inbox.",
            confidence=0.92,
        )

    parsed = parse_intent(message)
    action_type = _resolve_action_type(context, parsed.intent)

    if action_type is None:
        return AssistantPlan(
            mode="unknown",
            action_type=None,
            assistant_message=(
                "I can review inbox messages, draft email replies, read your calendar, find free time, "
                "or prepare a calendar change for approval."
            ),
            confidence=0.35,
        )

    if action_type in _READ_INTENTS:
        return AssistantPlan(
            mode="read",
            action_type=action_type,
            tool_name=_READ_TOOLS[action_type],
            requires_approval=False,
            needs_google=True,
            assistant_message="Let me pull the connected data.",
            confidence=0.8,
        )

    collected_fields = {**context.collected_fields}
    collected_fields.update(extract_fields(action_type, message, client))
    missing_fields = _missing_fields_for_action(action_type, collected_fields)

    return AssistantPlan(
        mode="clarify" if missing_fields else "write",
        action_type=action_type,
        tool_name=_WRITE_TOOLS[action_type],
        collected_fields=collected_fields,
        missing_fields=missing_fields,
        requires_approval=True,
        needs_google=action_type in {"draft_email_reply", "create_event", "reschedule_event", "cancel_event"},
        assistant_message="",
        confidence=0.78 if not missing_fields else 0.62,
    )


async def build_assistant_plan(
    client: ClientConfig,
    context: ConversationContext,
    message: str,
    extract_fields: Callable[[str | None, str, ClientConfig], dict[str, Any]],
) -> AssistantPlan:
    llm_plan = None
    try:
        llm_plan = await generate_conversation_plan(client, context, message)
    except Exception:
        llm_plan = None

    fallback = _fallback_plan(client, context, message, extract_fields)
    if not llm_plan:
        return fallback

    action_type = llm_plan.action_type or fallback.action_type
    collected_fields = {**context.collected_fields}
    if isinstance(llm_plan.collected_fields, dict):
        collected_fields.update(llm_plan.collected_fields)
    if action_type in _WRITE_INTENTS:
        collected_fields.update(extract_fields(action_type, message, client))
    if message and "source_text" not in collected_fields:
        collected_fields["source_text"] = " ".join(message.strip().split())

    missing_fields = [
        field for field in llm_plan.missing_fields if isinstance(field, str)
    ] or _missing_fields_for_action(action_type, collected_fields)

    mode = llm_plan.mode
    if action_type in _READ_INTENTS:
        mode = "read"
    elif action_type in _WRITE_INTENTS and missing_fields:
        mode = "clarify"
    elif action_type in _WRITE_INTENTS:
        mode = "write"
    elif llm_plan.capability_scope:
        mode = "capability"

    tool_name = llm_plan.tool_name
    if not tool_name and action_type in _READ_TOOLS:
        tool_name = _READ_TOOLS[action_type]
    if not tool_name and action_type in _WRITE_TOOLS:
        tool_name = _WRITE_TOOLS[action_type]

    return AssistantPlan(
        mode=mode or fallback.mode,
        action_type=action_type,
        tool_name=tool_name,
        capability_scope=llm_plan.capability_scope or fallback.capability_scope,
        collected_fields=collected_fields,
        missing_fields=missing_fields,
        requires_approval=action_type in _WRITE_INTENTS,
        needs_google=llm_plan.needs_google or fallback.needs_google,
        assistant_message=llm_plan.assistant_message or fallback.assistant_message,
        confidence=llm_plan.confidence or fallback.confidence,
    )
