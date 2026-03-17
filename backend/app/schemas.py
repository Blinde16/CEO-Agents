from __future__ import annotations

from datetime import datetime
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


class ApprovalDecision(BaseModel):
    approval_id: str
    reviewer_id: str
    decision: ApprovalStatus


class ClientConfig(BaseModel):
    client_id: str
    timezone: str
    working_hours: str
    scheduling_preferences: dict[str, Any] = Field(default_factory=dict)
    approval_rules: dict[str, Any] = Field(default_factory=dict)
    priority_contacts: list[str] = Field(default_factory=list)


class ActionLog(BaseModel):
    client_id: str
    user_id: str
    timestamp: datetime
    action_type: str
    action_status: ActionStatus
    error_message: str | None
    executed_by: str
    approval_status: ApprovalStatus
