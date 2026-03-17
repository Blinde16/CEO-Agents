from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class ApprovalStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    expired = "expired"
    not_required = "not_required"


class ActionStatus(str, Enum):
    queued = "queued"
    executed = "executed"
    failed = "failed"


class IntentRequest(BaseModel):
    client_id: str
    user_id: str
    text: str


class ParsedIntent(BaseModel):
    intent: str
    entities: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel


class ActionRequest(BaseModel):
    client_id: str
    user_id: str
    action_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ActionRecord(BaseModel):
    action_id: str
    client_id: str
    user_id: str
    action_type: str
    payload: dict[str, Any]
    status: ActionStatus
    approval_status: ApprovalStatus
    created_at: datetime
    result: dict[str, Any] | None = None
    reviewer_id: str | None = None
    decision_time: datetime | None = None


class ApprovalDecision(BaseModel):
    approval_id: str
    reviewer_id: str
    decision: ApprovalStatus
    # Optional feedback provided when rejecting — used for preference learning
    feedback: str | None = None


# Learned preference stored after executive gives feedback on a rejected draft
class LearnedPreference(BaseModel):
    action_type: str
    rule: str  # e.g. "never schedule before 10am with investors"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ClientConfig(BaseModel):
    client_id: str
    display_name: str | None = None
    timezone: str
    working_hours: str
    scheduling_preferences: dict[str, Any] = Field(default_factory=dict)
    approval_rules: dict[str, Any] = Field(default_factory=dict)
    priority_contacts: list[str] = Field(default_factory=list)
    # Voice matching: 2-5 example emails the executive has written previously
    voice_examples: list[str] = Field(default_factory=list)
    # Preferences learned from rejected drafts
    learned_preferences: list[LearnedPreference] = Field(default_factory=list)
    # Focus blocks the AI should protect when scheduling (e.g. "09:00-11:00")
    focus_blocks: list[str] = Field(default_factory=list)


class ActionLog(BaseModel):
    action_id: str
    client_id: str
    user_id: str
    timestamp: datetime
    action_type: str
    action_status: ActionStatus
    error_message: str | None
    executed_by: str
    approval_status: ApprovalStatus


class ApprovalRecord(BaseModel):
    approval_id: str
    action_id: str
    client_id: str
    status: ApprovalStatus
    reviewer_id: str | None = None
    decision_time: datetime | None = None


class ConversationContext(BaseModel):
    intent: str = "unknown"
    action_type: str | None = None
    collected_fields: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)


class AssistantPlan(BaseModel):
    mode: str  # capability / read / write / clarify / unknown
    action_type: str | None = None
    tool_name: str | None = None
    capability_scope: str | None = None
    collected_fields: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    needs_google: bool = False
    assistant_message: str = ""
    confidence: float = 0.0


# Per-email triage result produced by LLM batch triage
class EmailTriageResult(BaseModel):
    message_id: str
    subject: str
    sender: str
    date: str
    category: str  # urgent / action_required / meeting_request / fyi / newsletter
    urgency_score: int  # 1-5
    summary: str  # 1-2 sentence plain-language summary
    action_items: list[str] = Field(default_factory=list)
    # If the email contains a meeting proposal, this is the proposed time
    proposed_meeting_time: str | None = None
    proposed_meeting_attendees: list[str] = Field(default_factory=list)
    requires_reply: bool = False
    reply_deadline: str | None = None


# Pre-meeting briefing
class MeetingBriefing(BaseModel):
    event_id: str
    event_title: str
    start_time: str
    attendees: list[str]
    relationship_context: str  # history, recent interactions
    open_items: list[str]  # unresolved topics from email threads
    suggested_talking_points: list[str]
    recent_emails: list[dict[str, str]] = Field(default_factory=list)


class DraftProposal(BaseModel):
    kind: str
    title: str
    summary: str
    details: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source: str = "generated"
    confidence_label: str = "draft"
    confidence_score: float = 0.8  # 0.0–1.0, shown in UI
    action_type: str
    approval_required: bool
    payload: dict[str, Any] = Field(default_factory=dict)
    # If a meeting request was detected in an email, surface it here
    detected_meeting_request: dict[str, Any] | None = None


class ConversationRequest(BaseModel):
    client_id: str
    user_id: str
    message: str
    context: ConversationContext | None = None


class ConversationResponse(BaseModel):
    state: str
    assistant_message: str
    context: ConversationContext
    plan: AssistantPlan | None = None
    proposal: DraftProposal | None = None
    # Triage results when the user requests inbox review
    triage_results: list[EmailTriageResult] = Field(default_factory=list)


class IntegrationRecord(BaseModel):
    client_id: str
    provider: str
    status: str
    connected_account: str | None = None
    scopes: list[str] = Field(default_factory=list)


class IntegrationAuthStartResponse(BaseModel):
    provider: str
    auth_url: str
