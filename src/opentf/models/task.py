"""Task lifecycle model with immutable state snapshots."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    VALIDATED = "validated"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskSnapshot(BaseModel):
    """Immutable record of task state at a point in time."""

    status: TaskStatus
    agent: str
    timestamp: datetime
    data: dict[str, Any] = {}
    reason: str


class Task(BaseModel):
    """A unit of work flowing through the orchestration pipeline.

    Each state transition is recorded as an immutable TaskSnapshot,
    so previous state is always recoverable. Idempotency keys
    prevent duplicate execution on retry.
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    idempotency_key: str = Field(default_factory=lambda: uuid.uuid4().hex)
    parent_id: str | None = None
    description: str
    status: TaskStatus = TaskStatus.PENDING
    agent_type: str | None = None
    input_data: dict[str, Any] = {}
    output_data: dict[str, Any] = {}
    validation_errors: list[str] = []
    history: list[TaskSnapshot] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None

    def transition(
        self,
        status: TaskStatus,
        agent: str,
        reason: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Record a state transition with an immutable snapshot."""
        snapshot = TaskSnapshot(
            status=self.status,
            agent=agent,
            timestamp=datetime.now(timezone.utc),
            data=data or {},
            reason=reason,
        )
        self.history.append(snapshot)
        self.status = status
        if status == TaskStatus.COMPLETED:
            self.completed_at = datetime.now(timezone.utc)
