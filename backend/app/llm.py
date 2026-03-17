from __future__ import annotations

import json
from typing import Any

import httpx

from app.schemas import ClientConfig, ConversationContext, EmailTriageResult
from app.settings import get_settings

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _openai_chat(model: str, system: str, user: str, api_key: str) -> str | None:
    """Call the OpenAI chat completions endpoint and return the first message text."""
    async with httpx.AsyncClient(timeout=45.0) as http_client:
        response = await http_client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.3,
            },
        )
        response.raise_for_status()
        body = response.json()

    choices = body.get("choices", [])
    if not choices:
        return None
    return choices[0].get("message", {}).get("content", "").strip() or None


def _parse_json_response(raw: str) -> dict | None:
    """Extract a JSON object from a model response, tolerating surrounding text."""
    if not raw:
        return None
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(raw[start: end + 1])
            except json.JSONDecodeError:
                pass
    return None


# ---------------------------------------------------------------------------
# 1. Conversational turn — used by /assistant/respond (mini model)
# ---------------------------------------------------------------------------

async def generate_structured_response(
    client: ClientConfig,
    context: ConversationContext | None,
    message: str,
) -> dict | None:
    settings = get_settings()
    if not settings.openai_api_key:
        return None

    learned_prefs = ""
    if client.learned_preferences:
        rules = [p.rule for p in client.learned_preferences[-10:]]
        learned_prefs = "\n".join(f"- {r}" for r in rules)

    system_prompt = f"""You are an executive assistant for {client.display_name or client.client_id}.
You help with two categories only:
1. Drafting an email reply
2. Handling a calendar request (create, reschedule, cancel)

Return valid JSON only — no markdown, no explanation — with this exact shape:
{{
  "action_type": "draft_email_reply" | "create_event" | "reschedule_event" | "cancel_event" | null,
  "assistant_message": string,
  "collected_fields": {{
    "source_text": string?,
    "recipient_name": string?,
    "recipient_email": string?,
    "topic": string?,
    "draft_body": string?,
    "contact_name": string?,
    "requested_time": string?,
    "title": string?,
    "attendees": string[]?
  }},
  "missing_fields": string[],
  "state": "needs_direction" | "needs_clarification" | "draft_ready",
  "confidence": number
}}

Rules:
- Ask only ONE focused follow-up question when information is missing.
- Keep assistant_message short and client-facing (no jargon).
- If drafting email, include a polished "draft_body" in the executive's voice.
- confidence is a float 0.0–1.0 reflecting how complete and accurate the response is.
- Never mention JSON, payloads, APIs, or internal system names.
- Apply these learned preferences from prior feedback:
{learned_prefs or "(none yet)"}

Client profile:
- Timezone: {client.timezone}
- Working hours: {client.working_hours}
- Priority contacts: {", ".join(client.priority_contacts) or "none"}
- Focus blocks: {", ".join(client.focus_blocks) or "none"}""".strip()

    user_payload = {
        "message": message,
        "context": context.model_dump() if context else None,
        "approval_rules": client.approval_rules,
    }

    raw = await _openai_chat(
        model=settings.openai_model,  # gpt-4.1-mini — fast, cheap
        system=system_prompt,
        user=json.dumps(user_payload),
        api_key=settings.openai_api_key,
    )
    return _parse_json_response(raw or "")


# ---------------------------------------------------------------------------
# 2. Email triage — batch triage N emails in one call (mini model)
# ---------------------------------------------------------------------------

async def triage_inbox(
    client: ClientConfig,
    messages: list[dict[str, Any]],
) -> list[EmailTriageResult]:
    """
    Triage a list of email messages in a single LLM call.
    Each message dict should contain: id, from, subject, date, snippet, body (optional).
    Returns a list of EmailTriageResult sorted by urgency descending.
    """
    settings = get_settings()
    if not settings.openai_api_key or not messages:
        return []

    system_prompt = f"""You are an executive assistant triage system for {client.display_name or client.client_id}.
Analyze the provided inbox messages and return a JSON array — one object per email.
Return ONLY the JSON array, no other text.

Each object must have:
{{
  "message_id": string,
  "subject": string,
  "sender": string,
  "date": string,
  "category": "urgent" | "action_required" | "meeting_request" | "fyi" | "newsletter",
  "urgency_score": integer 1-5 (5 = drop everything),
  "summary": string (1-2 sentences, what this email is actually about),
  "action_items": string[] (specific actions the executive needs to take),
  "proposed_meeting_time": string | null (if the email proposes a meeting time),
  "proposed_meeting_attendees": string[] (people mentioned for the proposed meeting),
  "requires_reply": boolean,
  "reply_deadline": string | null (ISO date or human-readable deadline if mentioned)
}}

Priority contacts (treat with elevated urgency): {", ".join(client.priority_contacts) or "none"}
Executive timezone: {client.timezone}""".strip()

    # Format messages compactly for the prompt
    messages_payload = [
        {
            "id": m.get("id"),
            "from": m.get("from", ""),
            "subject": m.get("subject", ""),
            "date": m.get("date", ""),
            "body": (m.get("body") or m.get("snippet", ""))[:800],
        }
        for m in messages
    ]

    raw = await _openai_chat(
        model=settings.openai_model,  # gpt-4.1-mini — cheap, fast for triage
        system=system_prompt,
        user=json.dumps(messages_payload),
        api_key=settings.openai_api_key,
    )
    if not raw:
        return []

    raw = raw.strip()
    # Extract JSON array
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end != -1:
            try:
                parsed = json.loads(raw[start: end + 1])
            except json.JSONDecodeError:
                return []
        else:
            return []

    if not isinstance(parsed, list):
        return []

    results = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        try:
            results.append(EmailTriageResult(
                message_id=str(item.get("message_id", "")),
                subject=str(item.get("subject", "")),
                sender=str(item.get("sender", "")),
                date=str(item.get("date", "")),
                category=str(item.get("category", "fyi")),
                urgency_score=int(item.get("urgency_score", 1)),
                summary=str(item.get("summary", "")),
                action_items=[str(a) for a in item.get("action_items", []) if a],
                proposed_meeting_time=item.get("proposed_meeting_time") or None,
                proposed_meeting_attendees=[str(a) for a in item.get("proposed_meeting_attendees", []) if a],
                requires_reply=bool(item.get("requires_reply", False)),
                reply_deadline=item.get("reply_deadline") or None,
            ))
        except Exception:
            continue

    return sorted(results, key=lambda r: r.urgency_score, reverse=True)


# ---------------------------------------------------------------------------
# 3. Email draft generation — thread-aware, voice-matched (heavy model)
# ---------------------------------------------------------------------------

async def generate_email_draft(
    client: ClientConfig,
    recipient_name: str,
    topic: str,
    thread_messages: list[dict[str, str]],
    user_instruction: str,
) -> dict[str, Any]:
    """
    Generate a polished, thread-aware email draft in the executive's voice.
    Uses gpt-4.1 for quality. Returns {draft_body, subject, confidence}.
    """
    settings = get_settings()
    if not settings.openai_api_key:
        return {"draft_body": "", "subject": f"Re: {topic}", "confidence": 0.5}

    voice_section = ""
    if client.voice_examples:
        examples = "\n\n---\n".join(client.voice_examples[:3])
        voice_section = f"\nHere are example emails written by the executive to match their voice:\n{examples}\n"

    learned_prefs = ""
    if client.learned_preferences:
        rules = [p.rule for p in client.learned_preferences if p.action_type == "draft_email_reply"]
        if rules:
            learned_prefs = "Apply these learned preferences:\n" + "\n".join(f"- {r}" for r in rules[-5:])

    thread_text = ""
    if thread_messages:
        parts = []
        for msg in thread_messages:
            parts.append(f"From: {msg.get('from', '')}\nDate: {msg.get('date', '')}\n{msg.get('body', '')[:600]}")
        thread_text = "\n\n---\n".join(parts)

    system_prompt = f"""You are drafting an email on behalf of {client.display_name or "the executive"}.
{voice_section}
{learned_prefs}

Return JSON only with this shape:
{{
  "subject": string,
  "draft_body": string,
  "confidence": number (0.0-1.0)
}}

Rules:
- Match the executive's tone from the voice examples.
- Keep it concise. Executives don't ramble.
- Do not mention AI, drafts, or that you are an assistant.
- Reference the email thread context naturally if relevant.
- confidence reflects how well you matched intent and tone.""".strip()

    user_payload = {
        "recipient": recipient_name,
        "topic": topic,
        "instruction": user_instruction,
        "email_thread": thread_text or "(no thread — this is a new email)",
        "client_name": client.display_name or client.client_id,
    }

    raw = await _openai_chat(
        model=settings.openai_model_heavy,  # gpt-4.1 — quality matters for drafts
        system=system_prompt,
        user=json.dumps(user_payload),
        api_key=settings.openai_api_key,
    )
    result = _parse_json_response(raw or "")
    if not result:
        return {"draft_body": "", "subject": f"Re: {topic}", "confidence": 0.5}
    return result


# ---------------------------------------------------------------------------
# 4. Pre-meeting briefing (heavy model)
# ---------------------------------------------------------------------------

async def generate_briefing(
    client: ClientConfig,
    event: dict[str, Any],
    recent_emails: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Generate a pre-meeting briefing for an upcoming calendar event.
    Returns {relationship_context, open_items, suggested_talking_points}.
    """
    settings = get_settings()
    if not settings.openai_api_key:
        return {
            "relationship_context": "No LLM configured.",
            "open_items": [],
            "suggested_talking_points": [],
        }

    attendees = ", ".join(event.get("attendees", [])) or "unknown"
    email_context = "\n\n".join(
        f"From: {e.get('from', '')}\nSubject: {e.get('subject', '')}\n{(e.get('body') or e.get('snippet', ''))[:400]}"
        for e in recent_emails[:5]
    )

    system_prompt = f"""You are preparing a pre-meeting briefing for {client.display_name or "the executive"}.
Return JSON only:
{{
  "relationship_context": string (1-2 sentences on history/relationship with attendees),
  "open_items": string[] (unresolved topics from recent email threads),
  "suggested_talking_points": string[] (3-5 specific points to raise),
  "confidence": number (0.0-1.0)
}}""".strip()

    user_payload = {
        "meeting_title": event.get("title", "Untitled"),
        "meeting_time": event.get("start", ""),
        "attendees": attendees,
        "recent_email_threads": email_context or "(no recent emails found)",
        "executive_name": client.display_name or client.client_id,
    }

    raw = await _openai_chat(
        model=settings.openai_model_heavy,  # gpt-4.1 — briefings need quality
        system=system_prompt,
        user=json.dumps(user_payload),
        api_key=settings.openai_api_key,
    )
    result = _parse_json_response(raw or "")
    if not result:
        return {
            "relationship_context": "Could not generate briefing.",
            "open_items": [],
            "suggested_talking_points": [],
        }
    return result


# ---------------------------------------------------------------------------
# 5. Preference extraction from rejection feedback (mini model)
# ---------------------------------------------------------------------------

async def extract_preference_from_feedback(
    client: ClientConfig,
    action_type: str,
    rejected_draft: dict[str, Any],
    feedback: str,
) -> str | None:
    """
    When the executive rejects a draft with feedback, extract a concise, reusable preference rule.
    Returns a single preference string or None if not extractable.
    """
    settings = get_settings()
    if not settings.openai_api_key or not feedback.strip():
        return None

    system_prompt = """Extract a concise, reusable preference rule from the executive's feedback about a rejected draft.
Return a single JSON object: {"rule": string} — one clear sentence that can guide future drafts.
If the feedback is too vague to extract a rule, return {"rule": null}.
Examples:
- Feedback "too formal" → {"rule": "Use casual, direct language for email replies"}
- Feedback "don't schedule before 10am" → {"rule": "Never schedule meetings before 10:00 AM"}
- Feedback "wrong person" → {"rule": null}""".strip()

    user_payload = {
        "action_type": action_type,
        "rejected_draft_summary": str(rejected_draft)[:300],
        "executive_feedback": feedback,
    }

    raw = await _openai_chat(
        model=settings.openai_model,  # mini is fine for this simple extraction
        system=system_prompt,
        user=json.dumps(user_payload),
        api_key=settings.openai_api_key,
    )
    result = _parse_json_response(raw or "")
    if not result:
        return None
    rule = result.get("rule")
    return str(rule) if rule else None
