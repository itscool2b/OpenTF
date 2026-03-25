"""Protocol-based inter-agent message model."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MessageType(str, Enum):
    TASK_CREATED = "task_created"
    TASK_VALIDATED = "task_validated"
    TASK_REJECTED = "task_rejected"
    TASK_EXECUTING = "task_executing"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    AGENT_REQUEST = "agent_request"
    AGENT_RESPONSE = "agent_response"
    PLAN_CREATED = "plan_created"
    PLAN_STEP_STARTED = "plan_step_started"
    PLAN_STEP_COMPLETED = "plan_step_completed"
    PLAN_STEP_FAILED = "plan_step_failed"
    PLAN_COMPLETED = "plan_completed"
    TOOL_INVOKED = "tool_invoked"
    TOOL_RESULT = "tool_result"
    STREAM_DELTA = "stream_delta"
    COMPACTION_START = "compaction_start"
    COMPACTION_DONE = "compaction_done"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_DENIED = "approval_denied"


class Message(BaseModel):
    """Protocol-based message for deterministic inter-agent communication.

    Agents interpret these by type, not by parsing free-form text.
    This avoids the ambiguity problems that break multi-agent systems at scale.
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    type: MessageType
    source: str
    target: str | None = None
    task_id: str | None = None
    payload: dict[str, Any] = {}
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
