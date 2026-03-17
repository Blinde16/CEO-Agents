from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.action_engine import UnsupportedActionError, execute_action
from app.approval import approval_status_for_action
from app.integrations.google import (
    GOOGLE_SCOPES,
    GoogleIntegrationConfigError,
    build_auth_url,
    exchange_code_for_tokens,
    fetch_user_info,
)
from app.integrations import calendar, email
from app.integrations.store import clear_tokens, set_tokens
from app.intent_parser import parse_intent
from app.llm import (
    extract_preference_from_feedback,
    generate_briefing,
    generate_email_draft,
    generate_structured_response,
    triage_inbox,
)
from app.schemas import (
    ActionLog,
    ActionRecord,
    ActionRequest,
    ActionStatus,
    ApprovalDecision,
    ApprovalRecord,
    ApprovalStatus,
    ClientConfig,
    ConversationContext,
    ConversationRequest,
    ConversationResponse,
    DraftProposal,
    EmailTriageResult,
    IntegrationAuthStartResponse,
    IntegrationRecord,
    IntentRequest,
    LearnedPreference,
    MeetingBriefing,
)
from app.settings import get_settings

app = FastAPI(title="CEO-Agents API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CLIENTS: dict[str, ClientConfig] = {}
ACTIONS: dict[str, ActionRecord] = {}
ACTION_LOGS: list[ActionLog] = []
APPROVAL_QUEUE: dict[str, ApprovalRecord] = {}
INTEGRATIONS: dict[str, dict[str, IntegrationRecord]] = {}
GOOGLE_OAUTH_STATES: dict[str, str] = {}

FINAL_APPROVAL_STATES = {
    ApprovalStatus.approved,
    ApprovalStatus.rejected,
    ApprovalStatus.expired,
}


# ---------------------------------------------------------------------------
# Health & client management
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/clients", response_model=ClientConfig)
def upsert_client(config: ClientConfig) -> ClientConfig:
    # Preserve learned preferences across upserts so they survive resets from the frontend
    existing = CLIENTS.get(config.client_id)
    if existing and not config.learned_preferences and existing.learned_preferences:
        config = config.model_copy(update={"learned_preferences": existing.learned_preferences})
    CLIENTS[config.client_id] = config
    return config


@app.get("/clients", response_model=list[ClientConfig])
def list_clients() -> list[ClientConfig]:
    return list(CLIENTS.values())


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


# ---------------------------------------------------------------------------
# Main conversational endpoint
# ---------------------------------------------------------------------------

@app.post("/assistant/respond", response_model=ConversationResponse)
async def assistant_respond(request: ConversationRequest) -> ConversationResponse:
    client = CLIENTS.get(request.client_id)
    if not client:
        raise HTTPException(status_code=404, detail="client not found")

    context = request.context or ConversationContext()

    # --- Short-circuit read-only requests ---
    if _is_calendar_read_request(request.message):
        return _calendar_read_response(request.client_id, request.message)
    if _is_availability_request(request.message):
        return _availability_response(request.client_id, request.message, client)
    if _is_email_read_request(request.message):
        return await _email_triage_response(request.client_id, request.message, client)

    # --- LLM conversational turn (mini model) ---
    llm_response = None
    try:
        llm_response = await generate_structured_response(client, context, request.message)
    except Exception:
        llm_response = None

    if llm_response:
        return await _response_from_llm_payload(client, context, request.message, llm_response)

    # --- Deterministic fallback ---
    parsed = parse_intent(request.message)
    action_type = _resolve_action_type(context, parsed.intent)
    collected_fields = {**context.collected_fields}
    collected_fields.update(_extract_fields(action_type, request.message, client))
    missing_fields = _missing_fields_for_action(action_type, collected_fields)

    if action_type is None:
        next_context = ConversationContext(
            intent="unknown",
            action_type=None,
            collected_fields=collected_fields,
            missing_fields=[],
        )
        return ConversationResponse(
            state="needs_direction",
            assistant_message=(
                "I can help with email replies or calendar coordination. "
                "Tell me who the email is for, or what meeting you want me to schedule."
            ),
            context=next_context,
        )

    next_context = ConversationContext(
        intent=action_type,
        action_type=action_type,
        collected_fields=collected_fields,
        missing_fields=missing_fields,
    )

    if missing_fields:
        return ConversationResponse(
            state="needs_clarification",
            assistant_message=_clarification_prompt(action_type, missing_fields),
            context=next_context,
        )

    proposal = await _build_proposal(action_type, collected_fields, client)
    return ConversationResponse(
        state="draft_ready",
        assistant_message="I drafted this for you. Does this look good to you?",
        context=next_context,
        proposal=proposal,
    )


# ---------------------------------------------------------------------------
# Briefing endpoint
# ---------------------------------------------------------------------------

@app.get("/briefing", response_model=MeetingBriefing)
async def get_meeting_briefing(client_id: str, event_id: str) -> MeetingBriefing:
    client = CLIENTS.get(client_id)
    if not client:
        raise HTTPException(status_code=404, detail="client not found")

    if not INTEGRATIONS.get(client_id, {}).get("google"):
        raise HTTPException(status_code=400, detail="Google not connected")

    # Get the event from calendar
    events_result = calendar.list_events(client_id)
    event = next((e for e in events_result.get("events", []) if e.get("id") == event_id), None)
    if not event:
        raise HTTPException(status_code=404, detail="event not found")

    attendee_emails = [a for a in event.get("attendees", []) if a]

    # Fetch recent emails related to attendees
    recent_emails: list[dict] = []
    for attendee_email in attendee_emails[:3]:
        name_hint = attendee_email.split("@")[0].replace(".", " ")
        msg = email.find_message_for_contact(client_id, name_hint)
        if msg:
            recent_emails.append(msg)

    briefing_data = await generate_briefing(client, event, recent_emails)

    return MeetingBriefing(
        event_id=event_id,
        event_title=event.get("title", "Meeting"),
        start_time=str(event.get("start", "")),
        attendees=attendee_emails,
        relationship_context=briefing_data.get("relationship_context", ""),
        open_items=briefing_data.get("open_items", []),
        suggested_talking_points=briefing_data.get("suggested_talking_points", []),
        recent_emails=[
            {"from": e.get("from", ""), "subject": e.get("subject", ""), "snippet": e.get("snippet", "")}
            for e in recent_emails
        ],
    )


# ---------------------------------------------------------------------------
# Integrations
# ---------------------------------------------------------------------------

@app.get("/integrations", response_model=list[IntegrationRecord])
def list_integrations(client_id: str) -> list[IntegrationRecord]:
    return list(INTEGRATIONS.get(client_id, {}).values())


@app.get("/integrations/google/start", response_model=IntegrationAuthStartResponse)
def start_google_auth(client_id: str) -> IntegrationAuthStartResponse:
    if client_id not in CLIENTS:
        raise HTTPException(status_code=404, detail="client not found")

    state = str(uuid4())
    GOOGLE_OAUTH_STATES[state] = client_id
    try:
        auth_url = build_auth_url(client_id, state)
    except GoogleIntegrationConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return IntegrationAuthStartResponse(provider="google", auth_url=auth_url)


@app.get("/integrations/google/callback")
async def google_auth_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        app_base_url = get_settings().app_base_url.rstrip("/")
        return RedirectResponse(url=f"{app_base_url}?integration=google&status=error&message={error}")

    if not code or not state or state not in GOOGLE_OAUTH_STATES:
        raise HTTPException(status_code=400, detail="invalid oauth callback")

    client_id = GOOGLE_OAUTH_STATES.pop(state)
    try:
        token_payload = await exchange_code_for_tokens(code)
        user_info = await fetch_user_info(token_payload["access_token"])
    except GoogleIntegrationConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"google oauth failed: {exc}") from exc

    token_payload["expires_at"] = (
        datetime.now(timezone.utc) + timedelta(seconds=int(token_payload.get("expires_in", 3600)))
    ).isoformat()
    set_tokens(client_id, "google", token_payload)
    INTEGRATIONS.setdefault(client_id, {})["google"] = IntegrationRecord(
        client_id=client_id,
        provider="google",
        status="connected",
        connected_account=user_info.get("email"),
        scopes=GOOGLE_SCOPES,
    )
    app_base_url = get_settings().app_base_url.rstrip("/")
    return RedirectResponse(url=f"{app_base_url}?integration=google&status=connected")


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

@app.post("/actions", response_model=ActionRecord)
def queue_or_execute_action(request: ActionRequest) -> ActionRecord:
    client = CLIENTS.get(request.client_id)
    if not client:
        raise HTTPException(status_code=404, detail="client not found")

    parsed = parse_intent(request.payload.get("source_text", request.action_type))
    contact = str(
        request.payload.get("recipient_name")
        or request.payload.get("contact_name")
        or request.payload.get("recipient_email")
        or ""
    ).lower()
    priority_contact = any(priority.lower() == contact for priority in client.priority_contacts)
    approval_status = approval_status_for_action(
        request.action_type, parsed.risk_level, priority_contact, client
    )

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
        APPROVAL_QUEUE[approval_id] = ApprovalRecord(
            approval_id=approval_id,
            action_id=action_id,
            client_id=request.client_id,
            status=ApprovalStatus.pending,
        )
        execution_error = None
    else:
        execution_error = _execute(action)

    _log_action(action, execution_error)
    return ACTIONS[action_id]


@app.get("/actions", response_model=list[ActionRecord])
def list_actions(client_id: str | None = None) -> list[ActionRecord]:
    actions = list(ACTIONS.values())
    if client_id:
        actions = [action for action in actions if action.client_id == client_id]
    return sorted(actions, key=lambda action: action.created_at, reverse=True)


@app.get("/approvals", response_model=list[ApprovalRecord])
def list_approvals(client_id: str | None = None) -> list[ApprovalRecord]:
    approvals = list(APPROVAL_QUEUE.values())
    if client_id:
        approvals = [approval for approval in approvals if approval.client_id == client_id]
    return sorted(
        approvals,
        key=lambda approval: approval.decision_time or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )


@app.post("/approvals/decision", response_model=ApprovalRecord)
async def decide_approval(decision: ApprovalDecision) -> ApprovalRecord:
    approval = APPROVAL_QUEUE.get(decision.approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="approval not found")

    if decision.decision not in FINAL_APPROVAL_STATES:
        raise HTTPException(status_code=400, detail="invalid decision")

    current_status = approval.status

    # Idempotent replay
    if current_status in FINAL_APPROVAL_STATES and current_status == decision.decision:
        return approval

    if current_status in FINAL_APPROVAL_STATES and current_status != decision.decision:
        raise HTTPException(status_code=409, detail="approval already finalized")

    approval.status = decision.decision
    approval.reviewer_id = decision.reviewer_id
    approval.decision_time = datetime.now(timezone.utc)

    action = ACTIONS[approval.action_id]
    action.approval_status = decision.decision
    action.reviewer_id = decision.reviewer_id
    action.decision_time = approval.decision_time

    if decision.decision == ApprovalStatus.approved:
        execution_error = _execute(action)
    else:
        execution_error = "action not approved"
        # Preference learning: if feedback provided on rejection, extract and store a rule
        if decision.feedback and decision.feedback.strip():
            client = CLIENTS.get(action.client_id)
            if client:
                try:
                    rule = await extract_preference_from_feedback(
                        client,
                        action.action_type,
                        action.payload,
                        decision.feedback,
                    )
                    if rule:
                        updated_prefs = list(client.learned_preferences) + [
                            LearnedPreference(action_type=action.action_type, rule=rule)
                        ]
                        CLIENTS[client.client_id] = client.model_copy(
                            update={"learned_preferences": updated_prefs[-20:]}  # keep last 20
                        )
                except Exception:
                    pass

    _log_action(action, execution_error)
    return approval


@app.get("/logs", response_model=list[ActionLog])
def list_logs(client_id: str | None = None) -> list[ActionLog]:
    logs = ACTION_LOGS
    if client_id:
        logs = [log for log in logs if log.client_id == client_id]
    return sorted(logs, key=lambda log: log.timestamp, reverse=True)


@app.post("/demo/reset")
def reset_demo() -> dict[str, str]:
    CLIENTS.clear()
    ACTIONS.clear()
    ACTION_LOGS.clear()
    APPROVAL_QUEUE.clear()
    INTEGRATIONS.clear()
    GOOGLE_OAUTH_STATES.clear()
    clear_tokens()
    return {"status": "reset"}


# ---------------------------------------------------------------------------
# Private helpers — intent routing
# ---------------------------------------------------------------------------

def _resolve_action_type(context: ConversationContext, parsed_intent: str) -> str | None:
    if parsed_intent in {"draft_email_reply", "create_event", "reschedule_event", "cancel_event"}:
        return parsed_intent
    if context.action_type in {"draft_email_reply", "create_event", "reschedule_event", "cancel_event"}:
        return context.action_type
    return None


def _is_calendar_read_request(message: str) -> bool:
    lower = message.lower()
    read_phrases = [
        "what's on my calendar", "what is on my calendar", "show my calendar", "show calendar",
        "list my calendar", "list calendar", "what do i have", "what meetings do i have",
        "what events do i have", "show my events", "show my schedule", "what is my schedule",
        "what's my schedule",
    ]
    return any(phrase in lower for phrase in read_phrases)


def _is_availability_request(message: str) -> bool:
    lower = message.lower()
    availability_phrases = [
        "when am i free", "when do i have time", "find time", "find a time",
        "best time", "open slot", "available slot", "availability", "when can i meet",
    ]
    return any(phrase in lower for phrase in availability_phrases)


def _is_email_read_request(message: str) -> bool:
    lower = message.lower()
    read_phrases = [
        "review my inbox", "review inbox", "show my inbox", "show inbox",
        "what emails do i have", "what's in my inbox", "what is in my inbox",
        "summarize my inbox", "categorize my inbox", "triage my inbox",
        "check my email", "check my inbox", "what email",
    ]
    return any(phrase in lower for phrase in read_phrases)


# ---------------------------------------------------------------------------
# Private helpers — read-only response builders
# ---------------------------------------------------------------------------

def _calendar_read_response(client_id: str, message: str) -> ConversationResponse:
    if INTEGRATIONS.get(client_id, {}).get("google") is None:
        return ConversationResponse(
            state="calendar_read",
            assistant_message="Connect Google first and I can read your real calendar before making suggestions.",
            context=ConversationContext(intent="calendar_read", action_type=None, collected_fields={}, missing_fields=[]),
        )

    events_result = calendar.list_events(client_id)
    events = events_result.get("events", [])

    if not events:
        return ConversationResponse(
            state="calendar_read",
            assistant_message="I don't see any upcoming calendar events in the connected range.",
            context=ConversationContext(intent="calendar_read", action_type=None, collected_fields={}, missing_fields=[]),
        )

    normalized_message = message.lower()
    filtered_events = events
    if "tomorrow" in normalized_message:
        filtered_events = [event for event in events if "tomorrow" in _relative_day_label(event.get("start"))]
    elif "today" in normalized_message:
        filtered_events = [event for event in events if "today" in _relative_day_label(event.get("start"))]

    if not filtered_events:
        return ConversationResponse(
            state="calendar_read",
            assistant_message="I checked the connected calendar and didn't find events for that timeframe.",
            context=ConversationContext(intent="calendar_read", action_type=None, collected_fields={}, missing_fields=[]),
        )

    summary_lines = []
    for event in filtered_events[:5]:
        attendees = event.get("attendees", [])
        attendee_str = f" with {', '.join(attendees[:2])}" if attendees else ""
        summary_lines.append(
            f"- {event.get('title')}{attendee_str}: {_humanize_event_time(event.get('start'))}"
        )

    return ConversationResponse(
        state="calendar_read",
        assistant_message="Here's what I see on your calendar:\n" + "\n".join(summary_lines),
        context=ConversationContext(intent="calendar_read", action_type=None, collected_fields={}, missing_fields=[]),
    )


def _availability_response(client_id: str, message: str, client: ClientConfig) -> ConversationResponse:
    if INTEGRATIONS.get(client_id, {}).get("google") is None:
        return ConversationResponse(
            state="availability_read",
            assistant_message="Connect Google first and I can suggest open slots from your real calendar.",
            context=ConversationContext(intent="availability_read", action_type=None, collected_fields={}, missing_fields=[]),
        )

    events_result = calendar.list_events(client_id)
    events = events_result.get("events", [])
    slots = _compute_open_slots(events, client.working_hours, client.focus_blocks)

    if not slots:
        return ConversationResponse(
            state="availability_read",
            assistant_message="I checked your calendar and didn't find a clean open slot in the next three days.",
            context=ConversationContext(intent="availability_read", action_type=None, collected_fields={}, missing_fields=[]),
        )

    lines = [f"- {slot}" for slot in slots[:4]]
    return ConversationResponse(
        state="availability_read",
        assistant_message="Here are your best open windows:\n" + "\n".join(lines),
        context=ConversationContext(intent="availability_read", action_type=None, collected_fields={}, missing_fields=[]),
    )


async def _email_triage_response(client_id: str, message: str, client: ClientConfig) -> ConversationResponse:
    """Fetch inbox, run LLM batch triage, and return a rich structured response."""
    if INTEGRATIONS.get(client_id, {}).get("google") is None:
        return ConversationResponse(
            state="email_read",
            assistant_message="Connect Google first and I can review and triage your real inbox.",
            context=ConversationContext(intent="email_read", action_type=None, collected_fields={}, missing_fields=[]),
        )

    messages_result = email.list_messages(client_id)
    messages = messages_result.get("messages", [])
    if not messages:
        return ConversationResponse(
            state="email_read",
            assistant_message="I checked your inbox and didn't find recent messages to review.",
            context=ConversationContext(intent="email_read", action_type=None, collected_fields={}, missing_fields=[]),
        )

    # Run LLM triage on all messages in one batch call (mini model)
    triage_results = await triage_inbox(client, messages)

    if not triage_results:
        # Fallback to simple listing if LLM triage fails
        lines = []
        for m in messages[:6]:
            lines.append(f"- {m.get('subject')} from {m.get('from')}: {m.get('snippet', '')[:80]}")
        return ConversationResponse(
            state="email_read",
            assistant_message="Here are recent inbox items:\n" + "\n".join(lines),
            context=ConversationContext(intent="email_read", action_type=None, collected_fields={}, missing_fields=[]),
        )

    # Build human-readable summary
    urgent = [r for r in triage_results if r.urgency_score >= 4]
    action_required = [r for r in triage_results if r.requires_reply and r.urgency_score < 4]
    meeting_requests = [r for r in triage_results if r.category == "meeting_request" and r.proposed_meeting_time]

    summary_parts = []
    if urgent:
        summary_parts.append(f"**{len(urgent)} urgent item(s)** need your attention now.")
    if action_required:
        summary_parts.append(f"**{len(action_required)} email(s)** require a reply.")
    if meeting_requests:
        summary_parts.append(f"**{len(meeting_requests)} meeting request(s)** with proposed times.")

    # List the top 6 by urgency
    lines = []
    for r in triage_results[:6]:
        urgency_label = "🔴" if r.urgency_score >= 4 else ("🟡" if r.urgency_score >= 3 else "⚪")
        lines.append(
            f"{urgency_label} [{r.category}] **{r.subject}** — {r.summary}"
            + (f"\n  → Actions: {'; '.join(r.action_items[:2])}" if r.action_items else "")
            + (f"\n  → Meeting proposed: {r.proposed_meeting_time}" if r.proposed_meeting_time else "")
        )

    intro = " ".join(summary_parts) or f"Here's your inbox — {len(triage_results)} messages reviewed."
    full_message = intro + "\n\n" + "\n".join(lines)

    return ConversationResponse(
        state="email_read",
        assistant_message=full_message,
        context=ConversationContext(intent="email_read", action_type=None, collected_fields={}, missing_fields=[]),
        triage_results=triage_results,
    )


# ---------------------------------------------------------------------------
# Private helpers — field extraction & proposal building
# ---------------------------------------------------------------------------

def _extract_fields(action_type: str | None, message: str, client: ClientConfig) -> dict:
    if action_type is None:
        return {}

    text = " ".join(message.strip().split())
    lower = text.lower()
    fields: dict = {"source_text": text}

    for prefix in ("to ", "with "):
        if prefix in lower:
            start = lower.index(prefix) + len(prefix)
            remainder = text[start:]
            stop_tokens = [" about ", " for ", " tomorrow", " next week", " on ", " at ", " regarding "]
            stop_positions = [remainder.lower().find(token) for token in stop_tokens if remainder.lower().find(token) != -1]
            stop = min(stop_positions) if stop_positions else len(remainder)
            contact = remainder[:stop].strip(" .,")
            if contact:
                key = "recipient_name" if action_type == "draft_email_reply" else "contact_name"
                fields[key] = contact
            break

    if "tomorrow" in lower:
        if "afternoon" in lower:
            fields["requested_time"] = "Tomorrow afternoon"
        elif "morning" in lower:
            fields["requested_time"] = "Tomorrow morning"
        else:
            fields["requested_time"] = "Tomorrow"
    elif "next week" in lower:
        fields["requested_time"] = "Next week"
    elif "this afternoon" in lower:
        fields["requested_time"] = "This afternoon"
    elif "this morning" in lower:
        fields["requested_time"] = "This morning"

    if action_type == "draft_email_reply":
        if "about " in lower:
            fields["topic"] = text[lower.index("about ") + len("about "):].strip(" .")
        elif "regarding " in lower:
            fields["topic"] = text[lower.index("regarding ") + len("regarding "):].strip(" .")

        recipient = fields.get("recipient_name")
        if isinstance(recipient, str) and recipient:
            # Try Google Contacts first for real email resolution
            resolved_email = email.resolve_contact_email(client.client_id, recipient)
            if resolved_email:
                fields["recipient_email"] = resolved_email
            else:
                fields["recipient_email"] = f"{recipient.lower().replace(' ', '.')}@example.com"

            inbox_message = email.find_message_for_contact(client.client_id, recipient)
            if inbox_message:
                if not resolved_email:
                    real_email = _extract_email_address(str(inbox_message.get("from", "")))
                    if real_email:
                        fields["recipient_email"] = real_email
                fields.setdefault("topic", str(inbox_message.get("subject", "follow-up")).removeprefix("Re: ").strip())
                fields["email_body"] = str(inbox_message.get("body") or inbox_message.get("snippet", ""))
                fields["email_snippet"] = str(inbox_message.get("snippet", ""))
                fields["source_message_from"] = str(inbox_message.get("from", ""))
                fields["source_message_subject"] = str(inbox_message.get("subject", ""))
                fields["thread_id"] = str(inbox_message.get("thread_id", ""))

    if action_type in {"create_event", "reschedule_event", "cancel_event"}:
        if "about " in lower:
            fields["title"] = text[lower.index("about ") + len("about "):].strip(" .").title()
        elif "for " in lower:
            tail = text[lower.index("for ") + len("for "):].strip(" .")
            if tail:
                fields["title"] = tail.title()

        contact = fields.get("contact_name")
        if isinstance(contact, str) and contact:
            # Try to resolve attendee email from Contacts
            resolved = email.resolve_contact_email(client.client_id, contact)
            attendee_entry = resolved or contact
            fields["attendees"] = [attendee_entry, client.display_name or "Executive"]

    return fields


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


def _clarification_prompt(action_type: str, missing_fields: list[str]) -> str:
    prompts = {
        "recipient_name": "Who should this email go to?",
        "topic": "What should the email say or be about?",
        "contact_name": "Who is this meeting with?",
        "requested_time": "What timing should I propose?",
        "title": "What should I call the meeting?",
    }
    first_missing = missing_fields[0]
    prefix = "I need one detail before I draft this. "
    if action_type == "draft_email_reply":
        prefix = "I can draft that. "
    if action_type in {"create_event", "reschedule_event", "cancel_event"}:
        prefix = "I can handle the calendar request. "
    return prefix + prompts.get(first_missing, "What detail should I fill in?")


async def _response_from_llm_payload(
    client: ClientConfig,
    context: ConversationContext,
    message: str,
    llm_payload: dict,
) -> ConversationResponse:
    action_type = _resolve_action_type(context, str(llm_payload.get("action_type") or ""))
    collected_fields = {**context.collected_fields}
    llm_fields = llm_payload.get("collected_fields", {})
    if isinstance(llm_fields, dict):
        collected_fields.update(llm_fields)
    collected_fields.setdefault("source_text", message)

    # If the LLM gave us a recipient/contact, try to resolve their real email
    if action_type == "draft_email_reply":
        recipient = str(collected_fields.get("recipient_name", ""))
        if recipient and ("@" not in str(collected_fields.get("recipient_email", ""))
                         or "example.com" in str(collected_fields.get("recipient_email", ""))):
            resolved = email.resolve_contact_email(client.client_id, recipient)
            if resolved:
                collected_fields["recipient_email"] = resolved
            inbox_msg = email.find_message_for_contact(client.client_id, recipient)
            if inbox_msg:
                if not resolved:
                    real_email = _extract_email_address(str(inbox_msg.get("from", "")))
                    if real_email:
                        collected_fields["recipient_email"] = real_email
                collected_fields["email_body"] = str(inbox_msg.get("body") or inbox_msg.get("snippet", ""))
                collected_fields.setdefault("topic", str(inbox_msg.get("subject", "")).removeprefix("Re: ").strip())
                collected_fields["thread_id"] = str(inbox_msg.get("thread_id", ""))
                collected_fields["source_message_from"] = str(inbox_msg.get("from", ""))
                collected_fields["source_message_subject"] = str(inbox_msg.get("subject", ""))

    missing_fields = [
        field for field in llm_payload.get("missing_fields", []) if isinstance(field, str)
    ] or _missing_fields_for_action(action_type, collected_fields)

    next_context = ConversationContext(
        intent=action_type or "unknown",
        action_type=action_type,
        collected_fields=collected_fields,
        missing_fields=missing_fields,
    )

    if action_type is None:
        return ConversationResponse(
            state="needs_direction",
            assistant_message=str(
                llm_payload.get("assistant_message") or "I can help with email replies or calendar coordination."
            ),
            context=next_context,
        )

    if missing_fields:
        return ConversationResponse(
            state="needs_clarification",
            assistant_message=str(
                llm_payload.get("assistant_message") or _clarification_prompt(action_type, missing_fields)
            ),
            context=next_context,
        )

    proposal = await _build_proposal(action_type, collected_fields, client)
    return ConversationResponse(
        state="draft_ready",
        assistant_message=str(llm_payload.get("assistant_message") or "I drafted this for you. Does this look good?"),
        context=next_context,
        proposal=proposal,
    )


async def _build_proposal(action_type: str, fields: dict, client: ClientConfig) -> DraftProposal:
    google_connected = "google" in INTEGRATIONS.get(client.client_id, {})

    if action_type == "draft_email_reply":
        recipient_name = str(fields.get("recipient_name", "Recipient"))
        recipient_email_addr = str(fields.get("recipient_email", f"{recipient_name.lower().replace(' ', '.')}@example.com"))
        topic = str(fields.get("topic", "follow-up"))
        source_text = str(fields.get("source_text", ""))
        thread_id = str(fields.get("thread_id", ""))

        # Fetch thread messages for context-aware reply
        thread_messages: list[dict] = []
        if thread_id and google_connected:
            thread_messages = email.get_thread_for_message(client.client_id, thread_id)

        # Generate polished draft using heavy model
        draft_result = await generate_email_draft(
            client=client,
            recipient_name=recipient_name,
            topic=topic,
            thread_messages=thread_messages,
            user_instruction=source_text,
        )
        draft_body = str(fields.get("draft_body") or draft_result.get("draft_body") or (
            f"Hi {recipient_name},\n\n"
            f"I wanted to follow up about {topic}.\n\n"
            f"Best,\n{client.display_name or 'Executive Office'}"
        ))
        draft_subject = draft_result.get("subject") or f"Re: {topic.title()}"
        confidence_score = float(draft_result.get("confidence", 0.8))

        warnings: list[str] = []
        if "@" not in recipient_email_addr or "example.com" in recipient_email_addr:
            warnings.append("Recipient email is a placeholder — confirm the address before sending.")
        if not google_connected:
            warnings.append("Google is not connected — this will remain a simulated draft.")
        if fields.get("source_message_subject"):
            warnings.append(
                f"Draft grounded on: \"{fields.get('source_message_subject')}\" from {fields.get('source_message_from')}."
            )

        payload = {
            "source_text": source_text,
            "recipient_name": recipient_name,
            "recipient_email": recipient_email_addr,
            "topic": topic,
            "subject": draft_subject,
            "draft_body": draft_body,
            "thread_id": thread_id,
        }

        return DraftProposal(
            kind="email",
            title=f"Draft reply to {recipient_name}",
            summary="A polished, context-aware email draft is ready for your approval.",
            details=[
                {"label": "To", "value": f"{recipient_name} <{recipient_email_addr}>"},
                {"label": "Subject", "value": draft_subject},
                *(
                    [{"label": "Thread", "value": f"{len(thread_messages)} prior message(s) reviewed"}]
                    if thread_messages else []
                ),
                *(
                    [{"label": "Based on", "value": str(fields.get("source_message_subject"))}]
                    if fields.get("source_message_subject") else []
                ),
                {"label": "Draft", "value": draft_body},
            ],
            warnings=warnings,
            source="grounded" if google_connected else "demo",
            confidence_label="ready for review" if google_connected else "demo preview",
            confidence_score=confidence_score,
            action_type=action_type,
            approval_required=True,
            payload=payload,
        )

    # --- Calendar proposal ---
    contact_name = str(fields.get("contact_name", "Attendee"))
    requested_time = str(fields.get("requested_time", "TBD"))
    title = str(fields.get("title", f"Meeting with {contact_name}"))
    attendees = fields.get("attendees", [contact_name, client.display_name or "Executive"])
    if not isinstance(attendees, list):
        attendees = [str(attendees)]
    source_text = str(fields.get("source_text", ""))

    # Smart duration inference
    from app.integrations.calendar import infer_duration_minutes, check_conflicts, check_focus_block_conflict
    duration_minutes = infer_duration_minutes(title, source_text)

    warnings: list[str] = []

    # Conflict detection
    if google_connected and requested_time != "TBD":
        conflicts = check_conflicts(client.client_id, requested_time, duration_minutes)
        for conflict in conflicts:
            warnings.append(
                f"Conflict: \"{conflict.get('title')}\" at {_humanize_event_time(conflict.get('start'))} overlaps this slot."
            )

    # Focus block protection
    if requested_time != "TBD" and client.focus_blocks:
        focus_warning = check_focus_block_conflict(requested_time, client.focus_blocks)
        if focus_warning:
            warnings.append(focus_warning)

    if not google_connected:
        warnings.append("Google is not connected — calendar changes will stay in demo mode.")
    if action_type in {"reschedule_event", "cancel_event"}:
        warnings.append("Reschedule/cancel uses best-match lookup — include a distinctive meeting title for accuracy.")

    has_conflicts = len(warnings) > 0 and any("Conflict:" in w for w in warnings)
    confidence_score = 0.9 if google_connected and not has_conflicts else (0.7 if not google_connected else 0.65)

    payload = {
        "source_text": source_text,
        "contact_name": contact_name,
        "title": title,
        "requested_time": requested_time,
        "attendees": attendees,
        "duration_minutes": duration_minutes,
    }

    summary_map = {
        "create_event": "A calendar hold is ready to place.",
        "reschedule_event": "A revised meeting time is ready to send.",
        "cancel_event": "This cancellation is ready for approval.",
    }

    return DraftProposal(
        kind="calendar",
        title=title,
        summary=summary_map.get(action_type, "A calendar update is ready."),
        details=[
            {"label": "With", "value": contact_name},
            {"label": "When", "value": requested_time},
            {"label": "Duration", "value": f"{duration_minutes} minutes"},
            {"label": "Attendees", "value": ", ".join(str(a) for a in attendees)},
        ],
        warnings=warnings,
        source="grounded" if google_connected else "demo",
        confidence_label="grounded in connected calendar" if google_connected else "demo preview",
        confidence_score=confidence_score,
        action_type=action_type,
        approval_required=action_type in {"reschedule_event", "cancel_event"},
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _humanize_event_time(start_value: object) -> str:
    if not start_value:
        return "time unavailable"
    raw = str(start_value)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        local = parsed.astimezone()
        return local.strftime("%A %b %d at %I:%M %p")
    except ValueError:
        return raw


def _relative_day_label(start_value: object) -> str:
    if not start_value:
        return ""
    raw = str(start_value)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        local = parsed.astimezone().date()
        today = datetime.now().astimezone().date()
        if local == today:
            return "today"
        if local == today + timedelta(days=1):
            return "tomorrow"
        return ""
    except ValueError:
        return ""


def _compute_open_slots(events: list[dict], working_hours: str, focus_blocks: list[str] | None = None) -> list[str]:
    start_hour, end_hour = _parse_working_hours(working_hours)
    now = datetime.now().astimezone()
    windows: list[str] = []

    normalized_events = []
    for event in events:
        start = _parse_event_dt(event.get("start"))
        end = _parse_event_dt(event.get("end"))
        if start and end:
            normalized_events.append((start, end))

    # Also block focus blocks as busy time
    if focus_blocks:
        for day_offset in range(0, 3):
            day = (now + timedelta(days=day_offset)).date()
            for block in focus_blocks:
                try:
                    start_raw, end_raw = block.split("-", 1)
                    s_h = int(start_raw.split(":")[0])
                    s_m = int(start_raw.split(":")[1]) if ":" in start_raw else 0
                    e_h = int(end_raw.split(":")[0])
                    e_m = int(end_raw.split(":")[1]) if ":" in end_raw else 0
                    block_start = datetime.combine(day, datetime.min.time()).astimezone().replace(
                        hour=s_h, minute=s_m, second=0, microsecond=0
                    )
                    block_end = datetime.combine(day, datetime.min.time()).astimezone().replace(
                        hour=e_h, minute=e_m, second=0, microsecond=0
                    )
                    normalized_events.append((block_start, block_end))
                except Exception:
                    continue

    for day_offset in range(0, 3):
        day = (now + timedelta(days=day_offset)).date()
        work_start = datetime.combine(day, datetime.min.time()).astimezone().replace(
            hour=start_hour, minute=0, second=0, microsecond=0
        )
        work_end = datetime.combine(day, datetime.min.time()).astimezone().replace(
            hour=end_hour, minute=0, second=0, microsecond=0
        )
        day_events = sorted(
            [
                (max(start, work_start), min(end, work_end))
                for start, end in normalized_events
                if start.date() == day and end > work_start and start < work_end
            ],
            key=lambda item: item[0],
        )

        cursor = max(work_start, now if day_offset == 0 else work_start)
        for event_start, event_end in day_events:
            if (event_start - cursor) >= timedelta(minutes=45):
                windows.append(_format_slot(cursor, event_start))
            cursor = max(cursor, event_end)
        if (work_end - cursor) >= timedelta(minutes=45):
            windows.append(_format_slot(cursor, work_end))

    return windows


def _parse_working_hours(working_hours: str) -> tuple[int, int]:
    try:
        start_raw, end_raw = working_hours.split("-", 1)
        return int(start_raw.split(":")[0]), int(end_raw.split(":")[0])
    except Exception:
        return (8, 17)


def _parse_event_dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone()
    except ValueError:
        return None


def _format_slot(start: datetime, end: datetime) -> str:
    return f"{start.strftime('%A %b %d, %I:%M %p')} to {end.strftime('%I:%M %p')}"


def _extract_email_address(value: str) -> str | None:
    if "<" in value and ">" in value:
        start = value.find("<") + 1
        end = value.find(">", start)
        if end > start:
            return value[start:end].strip()
    if "@" in value:
        return value.strip()
    return None


def _execute(action: ActionRecord) -> str | None:
    try:
        action.result = execute_action(action.client_id, action.action_type, action.payload)
        action.status = ActionStatus.executed
        return None
    except UnsupportedActionError as exc:
        action.status = ActionStatus.failed
        action.result = {"error": str(exc)}
        return str(exc)
    except Exception as exc:
        action.status = ActionStatus.failed
        action.result = {"error": str(exc)}
        return str(exc)


def _log_action(action: ActionRecord, error: str | None) -> None:
    ACTION_LOGS.append(
        ActionLog(
            action_id=action.action_id,
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
