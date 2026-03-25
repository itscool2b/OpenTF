"""Tests for MessageBus audit log lifecycle management."""

import pytest
from unittest.mock import AsyncMock

from opentf.core.bus import MessageBus
from opentf.models.message import Message, MessageType


def _msg(source: str = "test") -> Message:
    return Message(type=MessageType.AGENT_REQUEST, source=source, payload={})


@pytest.mark.asyncio
async def test_audit_log_capped() -> None:
    bus = MessageBus(max_audit_log=100)
    for i in range(200):
        await bus.publish(_msg(f"agent-{i}"))
    log = bus.audit_log
    assert len(log) == 100


@pytest.mark.asyncio
async def test_audit_log_preserves_recent() -> None:
    bus = MessageBus(max_audit_log=10)
    for i in range(20):
        await bus.publish(_msg(f"agent-{i}"))
    log = bus.audit_log
    # Most recent messages preserved
    assert log[-1].source == "agent-19"
    assert log[0].source == "agent-10"


@pytest.mark.asyncio
async def test_audit_log_default_cap() -> None:
    bus = MessageBus()
    # Default max is 10_000 — just verify it works
    for i in range(50):
        await bus.publish(_msg())
    assert len(bus.audit_log) == 50


@pytest.mark.asyncio
async def test_new_messages_after_cap() -> None:
    bus = MessageBus(max_audit_log=5)
    for i in range(5):
        await bus.publish(_msg(f"old-{i}"))
    await bus.publish(_msg("new"))
    log = bus.audit_log
    assert len(log) == 5
    assert log[-1].source == "new"


@pytest.mark.asyncio
async def test_clear_resets_log() -> None:
    bus = MessageBus(max_audit_log=100)
    for i in range(10):
        await bus.publish(_msg())
    bus.clear()
    assert len(bus.audit_log) == 0
    # Verify cap still works after clear
    for i in range(200):
        await bus.publish(_msg())
    assert len(bus.audit_log) == 100
