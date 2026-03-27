"""
In-memory fake adapter for testing.

Provides a simple in-memory pub/sub implementation without external dependencies.
Useful for unit tests and development.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from ..channel import Channel
from ..errors import MessagePublishError
from .base import BaseAdapter


@dataclass
class FakeMessage:
    """
    In-memory message representation.

    Attributes:
        payload: Message payload (already serialized)
        headers: Message headers
    """

    payload: Any
    headers: dict[str, str] = field(default_factory=dict)


class FakeAdapter(BaseAdapter):
    """
    In-memory adapter for testing.

    Stores messages in memory and delivers them synchronously to subscribers.
    Does NOT implement persistence, retry logic, or DLQ (those are tested via Redis adapter).

    Use cases:
    - Unit tests
    - Local development
    - Integration tests without external dependencies
    """

    def __init__(self) -> None:
        """Initialize fake adapter with empty state."""
        super().__init__()
        self._connected = False
        self._subscriptions: dict[str, list[tuple[Channel, Any]]] = {}  # channel_name -> handlers
        self._messages: dict[str, list[FakeMessage]] = {}  # channel_name -> messages

    async def connect(self) -> None:
        """Mark adapter as connected."""
        if self._connected:
            return
        if self.logger:
            self.logger.info("FakeAdapter: Connecting...")
        self._connected = True
        if self.logger:
            self.logger.debug("FakeAdapter: Connected")

    async def disconnect(self) -> None:
        """Mark adapter as disconnected and clear state."""
        if not self._connected:
            return
        if self.logger:
            self.logger.info("FakeAdapter: Disconnecting...")
        self._connected = False
        self._subscriptions.clear()
        self._messages.clear()
        if self.logger:
            self.logger.debug("FakeAdapter: Disconnected")

    async def subscribe(self, channel: Channel, service: Any) -> None:
        """
        Subscribe to a channel.

        Registers the handler to receive messages when they are published.

        Args:
            channel: Channel configuration with handler
            service: Service instance (unused in FakeAdapter)
        """
        if not self._connected:
            raise RuntimeError("Adapter not connected")

        channel_name = channel.name
        if channel_name not in self._subscriptions:
            self._subscriptions[channel_name] = []

        self._subscriptions[channel_name].append((channel, service))
        if self.logger:
            self.logger.debug(
                f"FakeAdapter: Subscribed to '{channel_name}' (group: {channel.group})"
            )

    async def unsubscribe(self, channel: Channel) -> None:
        """
        Unsubscribe from a channel.

        Waits for active messages to complete before removing subscription.

        Args:
            channel: Channel to unsubscribe from
        """
        channel.unsubscribing = True

        # Wait for active messages
        await self.wait_for_channel_active_messages(channel.id)

        # Remove subscription
        channel_name = channel.name
        if channel_name in self._subscriptions:
            self._subscriptions[channel_name] = [
                (ch, svc)
                for ch, svc in self._subscriptions[channel_name]
                if ch.id != channel.id
            ]
            if not self._subscriptions[channel_name]:
                del self._subscriptions[channel_name]

        if self.logger:
            self.logger.debug(f"FakeAdapter: Unsubscribed from '{channel_name}'")

    async def publish(self, channel_name: str, payload: Any, opts: dict[str, Any]) -> None:
        """
        Publish message to a channel.

        Immediately delivers message to all subscribers (synchronous delivery).

        Args:
            channel_name: Name of the channel
            payload: Message payload (will be serialized)
            opts: Publishing options (headers, etc.)

        Raises:
            MessagePublishError: If no subscribers or delivery fails
        """
        if not self._connected:
            raise MessagePublishError("Adapter not connected")

        # Serialize payload
        try:
            serialized_payload = self.serializer.serialize(payload)
        except Exception as e:
            raise MessagePublishError(f"Failed to serialize payload: {e}") from e

        # Extract headers
        headers = opts.get("headers", {})

        # Create message
        message = FakeMessage(payload=serialized_payload, headers=headers)

        # Store message (for debugging)
        if channel_name not in self._messages:
            self._messages[channel_name] = []
        self._messages[channel_name].append(message)

        # Deliver to subscribers
        if channel_name not in self._subscriptions:
            if self.logger:
                self.logger.warning(f"FakeAdapter: No subscribers for '{channel_name}'")
            return

        tasks = []
        for channel, service in self._subscriptions[channel_name]:
            if channel.unsubscribing:
                continue
            tasks.append(self._deliver_message(channel, message))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _deliver_message(self, channel: Channel, message: FakeMessage) -> None:
        """
        Deliver message to a channel handler.

        Args:
            channel: Channel with handler
            message: Message to deliver
        """
        # Deserialize payload
        try:
            payload = self.serializer.deserialize(message.payload)
        except Exception as e:
            if self.logger:
                self.logger.error(f"FakeAdapter: Failed to deserialize message: {e}")
            return

        # Track active message
        message_id = str(id(message))  # Unique ID for this delivery
        await self.add_channel_active_messages(channel.id, [message_id])

        try:
            # Call handler
            await channel.handler(payload, message)
        except Exception as e:
            if self.logger:
                self.logger.error(
                    f"FakeAdapter: Handler error on '{channel.name}' (group: {channel.group}): {e}"
                )
        finally:
            # Remove from active tracking
            await self.remove_channel_active_messages(channel.id, [message_id])

    def parse_message_headers(self, raw_message: Any) -> dict[str, str] | None:
        """
        Parse headers from raw message.

        Args:
            raw_message: FakeMessage instance

        Returns:
            Dictionary of headers or None
        """
        if isinstance(raw_message, FakeMessage):
            return raw_message.headers if raw_message.headers else None
        return None

    def get_message_history(self, channel_name: str) -> list[FakeMessage]:
        """
        Get all messages published to a channel (for testing).

        Args:
            channel_name: Channel name

        Returns:
            List of messages
        """
        return self._messages.get(channel_name, [])

    def clear_message_history(self, channel_name: str | None = None) -> None:
        """
        Clear message history (for testing).

        Args:
            channel_name: Channel to clear (None = clear all)
        """
        if channel_name:
            self._messages.pop(channel_name, None)
        else:
            self._messages.clear()
