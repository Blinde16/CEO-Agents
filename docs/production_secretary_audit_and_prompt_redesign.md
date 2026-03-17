# Production Audit and Redesign for Executive Secretary Agent

## Assumptions
- `CURRENT_SYSTEM_PROMPT` is represented by the assistant intent prompt logic in `backend/app/llm.py`.
- `TOOL_DEFINITIONS` are represented by actionable operation surfaces in `backend/app/action_engine.py`, provider integration modules, and API schemas in `backend/app/schemas.py`.
- `SAMPLE_CONVERSATIONS` are approximated from behavior validated in `backend/tests/test_api_approval_flow.py`.
- No standalone `DELEGATION_RULES` document was provided; current policy is inferred from approval routing behavior and README scope.

---

## SECTION 1: ROOT CAUSE DIAGNOSIS

1. **No enforced single-action commitment per turn**
   - The current response contract allows broad `assistant_message` output with a soft state label, but does not require a hard operational decision object (`EXECUTE`, `CLARIFY`, `CONFIRM`, `REFUSE`).
   - Result: model drifts into polite assistant phrasing and "offers" rather than committing.
   - Failure patterns: **polite fallback syndrome**, **action hesitation**.

2. **Intent detection and action mapping are shallow and keyword-bound**
   - Core intent parsing uses first-match keyword logic and collapses nuanced requests into one detected intent.
   - Multi-part requests (e.g., "reschedule and draft email") are likely reduced to one dominant label.
   - Failure patterns: **single-intent collapse**, inconsistent behavior across semantically similar phrasing.

3. **Clarification policy is underspecified and not machine-checkable**
   - Prompt says "ask only ONE follow-up question" but lacks a strict missing-variable strategy (e.g., choose highest-blocking field, no optional asks before required fields).
   - Result: broad follow-ups or asking for already derivable information.
   - Failure patterns: **over-clarification**, execution delay.

4. **Tool usage is weakly enforced and partially hidden behind free-form language generation**
   - Assistant can generate natural-language acknowledgements without producing deterministic tool call plans.
   - There is no hard rule such as "if action type is executable and required fields are present, must emit tool_call now."
   - Failure patterns: **tool avoidance**, inconsistent action rates.

5. **State model is minimal and not durable enough for high-reliability workflows**
   - Context tracks limited fields but lacks explicit subgoal stack, dependency graph, execution checkpoints, and recovered tool outputs keyed by turn/task IDs.
   - Result: weak continuity, dropped secondary tasks, and missed use of known data.
   - Failure patterns: **context blindness**, **single-intent collapse**.

---

## SECTION 2: PROMPT DEFICIENCY ANALYSIS

1. **Missing constraints**
   - No hard prohibition of non-committal language patterns ("Would you like me to...", "I can help with...").
   - No forced decision-mode token that downstream systems can validate.

2. **Ambiguous instruction hierarchy**
   - Mixed goals: be conversational, gather details, and act. Prompt does not prioritize action completion over conversational nicety when sufficient data exists.

3. **Lack of enforcement mechanisms**
   - JSON-only behavior is requested in some places, but parse tolerance explicitly permits surrounding text, weakening contract discipline.
   - No schema validation gate that rejects outputs missing required operational keys.

4. **Missing schemas for operational control**
   - No first-class schema for:
     - action plan with ordered steps,
     - tool call arguments,
     - required-vs-optional missing fields,
     - confidence tied to evidence,
     - delegation decision rationale.

5. **Unclear priorities under uncertainty**
   - Prompt does not specify deterministic tie-breakers for competing interpretations (e.g., choose calendar operation over email when both present, or split into multi-step plan).

---

## SECTION 3: TOOL + ACTION GAP ANALYSIS

1. **Current tools are partially usable but semantically coarse**
   - Action engine supports key operations (`draft_email_reply`, calendar mutations, tasks), but "read" actions and retrieval context are not consistently represented as deterministic tool phases before proposing edits.

2. **Action abstractions are not atomic enough for robust orchestration**
   - Missing explicit atomic stages such as:
     - `resolve_contact_identity`
     - `resolve_event_target`
     - `fetch_calendar_window`
     - `fetch_thread_context`
     - `compose_candidate`
     - `submit_for_approval`

3. **Insufficient disambiguation layer for overlapping tool intents**
   - Calendar create/reschedule/cancel depend on robust event resolution; current approach risks passing underspecified payloads.

4. **Missing typed failure channels**
   - No standardized model-visible tool error taxonomy (`NOT_FOUND`, `AMBIGUOUS_MATCH`, `AUTH_REQUIRED`, `RATE_LIMIT`, `POLICY_BLOCKED`) to drive deterministic next action.

5. **Missing multi-intent action graph**
   - Tools are called as single discrete actions without guaranteed preservation of secondary intents in same utterance.

---

## SECTION 4: REQUIRED SYSTEM ARCHITECTURE CHANGES

1. **Planner/Executor separation (mandatory)**
   - Introduce a **Planner LLM** that outputs strict plan JSON only (no user prose).
   - Introduce an **Executor runtime** that:
     - validates plan schema,
     - executes tool calls,
     - handles retries,
     - returns normalized observations.
   - User-facing message is generated only after execution state is known.

2. **Persistent task state store (mandatory)**
   - Store by `task_id`:
     - active intents (ordered),
     - subgoals with status,
     - required fields and provenance,
     - tool outputs snapshots,
     - approval checkpoints,
     - unresolved ambiguities.
   - Prevent turn-level stateless resets.

3. **Policy engine (mandatory)**
   - Externalize delegation/approval policy from prompt into deterministic rules engine:
     - `AUTO_EXECUTE`
     - `REQUIRE_CONFIRMATION`
     - `REQUIRE_APPROVAL`
     - `REFUSE`
   - Include risk tiering, actor trust, operation sensitivity, and business hours constraints.

4. **Validation and guardrail layer (mandatory)**
   - Enforce JSON schema and reject invalid planner outputs.
   - Enforce banned phrases and conversational drift checks before outbound reply.
   - Enforce "tool-required" assertions: when executable with complete fields, absence of tool call is invalid.

5. **Observation normalization layer (mandatory)**
   - Convert raw tool responses into canonical objects with confidence and ambiguity markers.
   - Planner consumes only normalized observations to reduce provider-specific variance.

---

## SECTION 5: REWRITTEN PRODUCTION PROMPT

```text
SYSTEM ROLE: EXECUTIVE_SECRETARY_PLANNER_V2

You are a deterministic planning engine for executive email and calendar operations.
You are NOT a chat assistant.
You must output exactly one JSON object matching OUTPUT_SCHEMA.
No markdown. No prose outside JSON.

OBJECTIVE
Given user input + prior state + tool observations + policy rules, choose exactly one DECISION_MODE and produce executable next steps.

DECISION MODES (choose exactly one)
1) EXECUTE_NOW
2) ASK_MINIMUM_CLARIFICATION
3) REQUEST_CONFIRMATION
4) REFUSE

GLOBAL RULES
- Action-first: if a safe executable action is possible with available required fields, choose EXECUTE_NOW.
- Never use polite fallback language.
- Never ask broad questions.
- Never ask for data already present in state or tool observations.
- Preserve all user intents in `intent_queue`; do not drop secondary intents.
- If multiple intents exist, execute the highest-priority executable intent and keep remaining intents queued.
- If policy requires approval/confirmation, do not execute mutating tools before required gate.
- For missing data, ask exactly one blocking variable only.

BANNED LANGUAGE PATTERNS
- "Would you like me to"
- "I can help"
- "Let me know if"
- "Just to confirm" (unless DECISION_MODE is REQUEST_CONFIRMATION)
- Any apology/filler unless REFUSE with policy/legal reason

TOOL USAGE ENFORCEMENT
- For DECISION_MODE=EXECUTE_NOW, provide at least one `tool_calls` entry.
- For DECISION_MODE=ASK_MINIMUM_CLARIFICATION, provide zero `tool_calls` and exactly one `clarification_question`.
- For DECISION_MODE=REQUEST_CONFIRMATION, provide `confirmation_request` with exact operation summary and risk reason.
- For DECISION_MODE=REFUSE, provide `refusal_reason_code` and concise policy-grounded message.

MISSING-FIELD POLICY
- Required fields are operation-specific and immutable:
  - draft_email_reply: recipient_identity, message_goal
  - create_event: title, start_time, attendees_or_none
  - reschedule_event: target_event_id_or_unique_match, new_time
  - cancel_event: target_event_id_or_unique_match
  - read_email/read_calendar/check_availability: query_scope
- If required field is missing, ask for only the highest-blocking missing field.
- Do not ask optional preference questions during blocking clarification.

MULTI-INTENT PRESERVATION
- Parse all intents into `intent_queue` with statuses: pending|ready|executing|blocked|done.
- Never output fewer intents than detected unless intent is invalid and documented in `dropped_intents`.

POLICY INPUT CONTRACT
- `policy.delegation_decision` is authoritative when present.
- If policy says REQUIRE_APPROVAL or REQUIRE_CONFIRMATION, set DECISION_MODE accordingly.
- If policy says AUTO_EXECUTE and required fields are complete, DECISION_MODE must be EXECUTE_NOW.

OUTPUT_SCHEMA (strict)
{
  "decision_mode": "EXECUTE_NOW | ASK_MINIMUM_CLARIFICATION | REQUEST_CONFIRMATION | REFUSE",
  "primary_intent": "string",
  "intent_queue": [
    {
      "intent": "string",
      "status": "pending | ready | executing | blocked | done",
      "blocking_field": "string|null"
    }
  ],
  "required_fields": {
    "<field_name>": {"value": "any|null", "source": "user|state|tool|inferred", "required": true|false}
  },
  "tool_calls": [
    {
      "tool_name": "string",
      "arguments": {},
      "purpose": "string"
    }
  ],
  "clarification_question": "string|null",
  "confirmation_request": {
    "summary": "string",
    "risk_reason": "string",
    "approval_path": "string"
  } | null,
  "refusal_reason_code": "POLICY_BLOCKED | OUT_OF_SCOPE | AUTH_REQUIRED | SAFETY_CONSTRAINT | NONE",
  "user_message": "string",
  "dropped_intents": [
    {"intent": "string", "reason": "string"}
  ],
  "execution_notes": ["string"],
  "confidence": 0.0
}

QUALITY CHECKS BEFORE FINALIZING JSON
1) Exactly one decision_mode selected.
2) decision_mode and tool_calls cardinality rules satisfied.
3) No banned language in user_message.
4) If a required field is missing, clarification_question asks exactly one variable.
5) Multi-intent queue preserved.
6) confidence reflects evidence quality, not politeness.
```

---

## SECTION 6: TEST SCENARIOS

1. **Ambiguous person reference + calendar move**
   - User input: "Move my 3pm with Alex to Friday morning."
   - Expected action type: `ASK_MINIMUM_CLARIFICATION`.
   - Expected behavior: Ask only which Alex/event if multiple matches; do not ask extra questions.

2. **Multi-intent command with dependency ordering**
   - User input: "Reschedule the board prep to next Tuesday and email the team the new time."
   - Expected action type: `EXECUTE_NOW` (for resolvable step) + preserved secondary intent.
   - Expected behavior: Resolve/reschedule first, keep email draft intent queued with updated meeting details.

3. **Missing recipient for email reply**
   - User input: "Reply that Thursday 2pm works."
   - Expected action type: `ASK_MINIMUM_CLARIFICATION`.
   - Expected behavior: Ask only recipient identity/thread target.

4. **Conflicting calendar constraints**
   - User input: "Book 30 minutes tomorrow before 9am with finance."
   - Expected action type: `REQUEST_CONFIRMATION` or `ASK_MINIMUM_CLARIFICATION` depending policy.
   - Expected behavior: Detect conflict with protected hours; request explicit override if policy requires.

5. **Partial reschedule with multiple candidate events**
   - User input: "Push my investor call by one hour."
   - Expected action type: `ASK_MINIMUM_CLARIFICATION`.
   - Expected behavior: If multiple investor calls exist, ask exactly which event instance.

6. **Interrupt-driven voice command**
   - User input: "Actually cancel that—no, keep it—just send them a note saying I’m late."
   - Expected action type: `EXECUTE_NOW` for latest valid intent after revision handling.
   - Expected behavior: Apply last-intent-wins for cancellation conflict; produce draft/send-note workflow.

7. **Tool-required retrieval vs hallucination risk**
   - User input: "What’s my first meeting Monday and draft a prep email to Dana."
   - Expected action type: `EXECUTE_NOW` with read-calendar tool first.
   - Expected behavior: Must call retrieval tool; no invented meeting details; preserve draft-email subgoal.

8. **Approval-gated high-risk cancellation**
   - User input: "Cancel the quarterly review with the board."
   - Expected action type: `REQUEST_CONFIRMATION` or policy `REQUIRE_APPROVAL` handoff.
   - Expected behavior: No direct cancellation tool mutation before approval gate.

9. **Authentication missing**
   - User input: "Check my inbox for urgent items and summarize."
   - Expected action type: `REFUSE` (AUTH_REQUIRED path) or policy-driven connect flow.
   - Expected behavior: Deterministic auth-required response, not generic apology.

10. **Compound, under-specified scheduling request**
    - User input: "Find 45 mins with Priya next week, avoid mornings, and if nothing works propose alternatives."
    - Expected action type: `EXECUTE_NOW` (availability search) then `ASK_MINIMUM_CLARIFICATION` only if hard blocker remains.
    - Expected behavior: Run availability tools first; return concrete alternatives; no broad follow-up.
