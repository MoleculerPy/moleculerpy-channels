"""
Base adapter abstract class for channel backends.

Defines the interface that all adapters (Redis, Kafka, NATS, Fake) must implement.
Provides common functionality for active message tracking and graceful shutdown.
"""

import asyncio
import json
from abc import ABC, abstractmethod
from typing import Any

from ..channel import Channel
from ..errors import AdapterError


class JSONSerializer:
    """Simple JSON serializer for adapters."""

    @staticmethod
    def serialize(data: Any) -> bytes:
        """Serialize data to JSON bytes."""
        return json.dumps(data).encode("utf-8")

    @staticmethod
    def deserialize(data: bytes) -> Any:
        """Deserialize JSON bytes to data."""
        return json.loads(data.decode("utf-8"))


class BaseAdapter(ABC):
    """
    Abstract base class for channel adapters.

    Adapters handle the underlying message broker integration (Redis Streams,
    Kafka, NATS, etc.) and implement pub/sub semantics with persistence.

    Key responsibilities:
    - Connect/disconnect to message broker
    - Subscribe/unsubscribe to channels
    - Publish messages with headers
    - Track active messages for graceful shutdown
    - Parse message headers for context propagation
    """

    def __init__(self) -> None:
        """Initialize adapter with empty state."""
        self.broker: Any = None
        self.logger: Any = None
        self.serializer: Any = None
        self._active_messages: dict[str, set[str]] = {}  # channel_id -> set of message IDs
        self._lock = asyncio.Lock()

    def init(self, broker: Any, logger: Any) -> None:
        """
        Initialize adapter with broker and logger.

        Called by middleware in created() hook.

        Args:
            broker: ServiceBroker instance
            logger: Logger instance
        """
        self.broker = broker
        self.logger = logger
        # Create a simple JSON serializer if broker.serializer is just a string
        if hasattr(broker, "serializer") and isinstance(broker.serializer, str):
            self.serializer = JSONSerializer()
        else:
            self.serializer = broker.serializer

    @abstractmethod
    async def connect(self) -> None:
        """
        Connect to the message broker.

        Called by middleware in starting() hook.
        Should establish connections, create queues/streams if needed.
        """
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """
        Disconnect from the message broker.

        Called by middleware in stopped() hook.
        Should close connections and clean up resources.
        """
        pass

    @abstractmethod
    async def subscribe(self, channel: Channel, service: Any) -> None:
        """
        Subscribe to a channel.

        Start consuming messages from the channel and call the handler for each message.

        Args:
            channel: Channel configuration
            service: Service instance that owns the handler
        """
        pass

    @abstractmethod
    async def unsubscribe(self, channel: Channel) -> None:
        """
        Unsubscribe from a channel.

        Stop consuming messages. Should wait for active messages to finish
        before returning (graceful shutdown).

        Args:
            channel: Channel to unsubscribe from
        """
        pass

    @abstractmethod
    async def publish(self, channel_name: str, payload: Any, opts: dict[str, Any]) -> str | None:
        """
        Publish message to a channel.

        Args:
            channel_name: Name of the channel
            payload: Message payload (will be serialized)
            opts: Publishing options (headers, etc.)

        Returns:
            Backend-specific message identifier when available.

        Raises:
            MessagePublishError: If publishing fails
        """
        pass

    @abstractmethod
    def parse_message_headers(self, raw_message: Any) -> dict[str, str] | None:
        """
        Parse headers from raw message.

        Adapter-specific method to extract headers from the broker's message format.

        Args:
            raw_message: Raw message object from the broker

        Returns:
            Dictionary of headers or None if no headers
        """
        pass

    def add_prefix_topic(self, topic: str) -> str:
        """
        Add broker-specific prefix to topic name.

        Can be overridden by adapters to add namespace prefixes.

        Args:
            topic: Original topic name

        Returns:
            Prefixed topic name
        """
        return topic

    # ── Active Message Tracking ──────────────────────────────────────────────

    def init_channel_active_messages(self, channel_id: str, to_throw: bool = True) -> None:
        """
        Initialize active messages tracking for a channel.

        Should be called when subscribing to a channel.

        Args:
            channel_id: Channel identifier
            to_throw: Whether to raise error if already tracking (default: True)

        Raises:
            AdapterError: If already tracking and to_throw=True
        """
        if channel_id in self._active_messages:
            if to_throw:
                raise AdapterError(f"Already tracking active messages of channel {channel_id}")
        self._active_messages[channel_id] = set()

    def stop_channel_active_messages(self, channel_id: str) -> None:
        """
        Stop tracking active messages for a channel.

        Should be called after unsubscribing and waiting for active messages.
        Validates that no messages are still being processed.

        Args:
            channel_id: Channel identifier

        Raises:
            AdapterError: If active messages remain (not if already stopped)
        """
        if channel_id not in self._active_messages:
            # Already stopped or never initialized - OK
            return

        active_count = len(self._active_messages[channel_id])
        if active_count != 0:
            raise AdapterError(
                f"Can't stop tracking active messages of channel {channel_id}. "
                f"There are {active_count} messages still active."
            )

        del self._active_messages[channel_id]

    async def add_channel_active_messages(self, channel_id: str, message_ids: list[str]) -> None:
        """
        Track active messages for a channel.

        Used for graceful shutdown to wait for in-flight messages.

        Args:
            channel_id: Channel identifier
            message_ids: List of message IDs being processed
        """
        async with self._lock:
            if channel_id not in self._active_messages:
                self._active_messages[channel_id] = set()
            self._active_messages[channel_id].update(message_ids)

    async def remove_channel_active_messages(self, channel_id: str, message_ids: list[str]) -> None:
        """
        Remove messages from active tracking.

        Called after messages are successfully processed or failed.

        Args:
            channel_id: Channel identifier
            message_ids: List of message IDs to remove
        """
        async with self._lock:
            if channel_id in self._active_messages:
                self._active_messages[channel_id].difference_update(message_ids)
                if not self._active_messages[channel_id]:
                    del self._active_messages[channel_id]

    async def get_number_of_channel_active_messages(self, channel_id: str) -> int:
        """
        Get count of active messages for a channel (with lock protection).

        Used during unsubscribe to wait for completion.
        Made async to prevent race conditions with add/remove operations.

        Args:
            channel_id: Channel identifier

        Returns:
            Number of active messages
        """
        async with self._lock:
            return len(self._active_messages.get(channel_id, set()))

    async def wait_for_channel_active_messages(
        self, channel_id: str, timeout: float = 30.0
    ) -> None:
        """
        Wait for all active messages to complete.

        Polls every 100ms until no active messages remain or timeout is reached.

        Args:
            channel_id: Channel identifier
            timeout: Maximum wait time in seconds

        Raises:
            AdapterError: If timeout is reached with active messages remaining
        """
        elapsed = 0.0
        interval = 0.1  # 100ms

        while await self.get_number_of_channel_active_messages(channel_id) > 0:
            if elapsed >= timeout:
                remaining = await self.get_number_of_channel_active_messages(channel_id)
                raise AdapterError(
                    f"Timeout waiting for {remaining} active messages on channel {channel_id}"
                )
            await asyncio.sleep(interval)
            elapsed += interval

    def transform_error_to_headers(self, error: Exception) -> dict[str, str]:
        """
        Convert exception to error headers.

        Can be overridden by adapters for custom error serialization.
        Default implementation imports and uses utils.default_transform_error_to_headers.

        Args:
            error: Exception to serialize

        Returns:
            Dictionary of error headers
        """
        from ..utils import default_transform_error_to_headers

        return default_transform_error_to_headers(error)

    def transform_headers_to_error_data(self, headers: dict[str, str]) -> dict[str, Any]:
        """
        Parse error information from headers.

        Can be overridden by adapters for custom error deserialization.
        Default implementation imports and uses utils.default_transform_headers_to_error_data.

        Args:
            headers: Dictionary of headers

        Returns:
            Dictionary containing parsed error data
        """
        from ..utils import default_transform_headers_to_error_data

        return default_transform_headers_to_error_data(headers)
