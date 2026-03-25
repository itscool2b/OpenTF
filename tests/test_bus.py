"""Tests for the MessageBus pub/sub system."""

import pytest

from opentf.core.bus import MessageBus
from opentf.models.message import Message, MessageType


@pytest.fixture
def bus() -> MessageBus:
    return MessageBus()


async def test_subscribe_and_publish(bus: MessageBus) -> None:
    received = []

    async def handler(msg: Message) -> None:
        received.append(msg)

    bus.subscribe(MessageType.TASK_COMPLETED, handler)
    await bus.publish(Message(
        type=MessageType.TASK_COMPLETED,
        source="test",
        payload={"ok": True},
    ))

    assert len(received) == 1
    assert received[0].source == "test"


async def test_subscribe_filters_by_type(bus: MessageBus) -> None:
    received = []

    async def handler(msg: Message) -> None:
        received.append(msg)

    bus.subscribe(MessageType.TASK_COMPLETED, handler)
    await bus.publish(Message(type=MessageType.TASK_FAILED, source="test"))

    assert len(received) == 0


async def test_global_subscriber_receives_all(bus: MessageBus) -> None:
    received = []

    async def handler(msg: Message) -> None:
        received.append(msg)

    bus.subscribe_all(handler)
    await bus.publish(Message(type=MessageType.TASK_COMPLETED, source="a"))
    await bus.publish(Message(type=MessageType.TASK_FAILED, source="b"))
    await bus.publish(Message(type=MessageType.TOOL_INVOKED, source="c"))

    assert len(received) == 3


async def test_unsubscribe(bus: MessageBus) -> None:
    received = []

    async def handler(msg: Message) -> None:
        received.append(msg)

    bus.subscribe(MessageType.TASK_COMPLETED, handler)
    await bus.publish(Message(type=MessageType.TASK_COMPLETED, source="test"))
    assert len(received) == 1

    bus.unsubscribe(MessageType.TASK_COMPLETED, handler)
    await bus.publish(Message(type=MessageType.TASK_COMPLETED, source="test2"))
    assert len(received) == 1  # Still 1, handler was removed


async def test_audit_log(bus: MessageBus) -> None:
    await bus.publish(Message(type=MessageType.TOOL_INVOKED, source="agent"))
    await bus.publish(Message(type=MessageType.TOOL_RESULT, source="agent"))

    assert len(bus.audit_log) == 2
    assert bus.audit_log[0].type == MessageType.TOOL_INVOKED
    assert bus.audit_log[1].type == MessageType.TOOL_RESULT


async def test_clear(bus: MessageBus) -> None:
    received = []

    async def handler(msg: Message) -> None:
        received.append(msg)

    bus.subscribe(MessageType.TASK_COMPLETED, handler)
    bus.clear()

    await bus.publish(Message(type=MessageType.TASK_COMPLETED, source="test"))
    assert len(received) == 0
    assert len(bus.audit_log) == 1  # Audit still records after clear
