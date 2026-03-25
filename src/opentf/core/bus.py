"""In-memory async event bus with audit logging.

Agents communicate through protocol-based messages published on this bus.
Subscribers receive messages by type. All messages are logged for
observability. Swappable for Redis/NATS later without changing agent code.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from typing import Awaitable, Callable

from opentf.models.message import Message, MessageType

log = logging.getLogger(__name__)

Handler = Callable[[Message], Awaitable[None]]


class MessageBus:
    """Async pub/sub message bus."""

    def __init__(self, max_audit_log: int = 10_000) -> None:
        self._subscribers: dict[MessageType, list[Handler]] = defaultdict(list)
        self._global_subscribers: list[Handler] = []
        self._audit_log: deque[Message] = deque(maxlen=max_audit_log)

    def subscribe(self, msg_type: MessageType, handler: Handler) -> None:
        """Subscribe a handler to a specific message type."""
        self._subscribers[msg_type].append(handler)

    def unsubscribe(self, msg_type: MessageType, handler: Handler) -> None:
        """Remove a handler from a message type."""
        try:
            self._subscribers[msg_type].remove(handler)
        except ValueError:
            pass

    def subscribe_all(self, handler: Handler) -> None:
        """Subscribe a handler to all message types (for logging/monitoring)."""
        self._global_subscribers.append(handler)

    async def publish(self, message: Message) -> None:
        """Publish a message to all matching subscribers."""
        self._audit_log.append(message)
        log.debug(
            "Bus: %s from=%s target=%s task=%s",
            message.type.value,
            message.source,
            message.target or "*",
            message.task_id or "-",
        )

        handlers = [
            *self._global_subscribers,
            *self._subscribers.get(message.type, []),
        ]
        if handlers:
            await asyncio.gather(*(h(message) for h in handlers))

    @property
    def audit_log(self) -> list[Message]:
        """Return the full audit trail of messages."""
        return list(self._audit_log)

    def clear(self) -> None:
        """Clear all subscribers and audit log."""
        self._subscribers.clear()
        self._global_subscribers.clear()
        self._audit_log.clear()
