"""
NATS JetStream adapter for MoleculerPy Channels.

Implements production-ready pub/sub messaging with NATS JetStream, providing:
- Persistent message storage (JetStream streams)
- Consumer groups for horizontal scaling (deliver_group)
- Automatic retry via NAK
- Dead Letter Queue for failed messages
- Context propagation for distributed tracing
- Metrics collection (7 core metrics)

Compatible with moleculer-channels NATS adapter patterns.

Example:
    >>> from moleculerpy_channels.adapters import NatsAdapter
    >>> adapter = NatsAdapter(url="nats://localhost:4222")
    >>> await adapter.connect()
"""

# Standard library
import asyncio
import time
from typing import Any

# Third-party (optional)
try:
    from nats.aio.client import Client as NATS
    from nats.js import JetStreamContext
    from nats.js.api import ConsumerConfig, StreamConfig
    from nats.js.errors import BadRequestError
except ImportError:
    NATS = None
    JetStreamContext = None
    ConsumerConfig = None
    StreamConfig = None
    BadRequestError = None

# Local imports
from ..channel import Channel
from ..constants import HEADER_ORIGINAL_CHANNEL, HEADER_ORIGINAL_GROUP
from ..errors import (
    MessagePublishError,
    NatsConnectionError,
    NatsConsumerError,
    NatsStreamError,
)
from ..metrics import ChannelMetrics
from .base import BaseAdapter
from .protocols import JetStreamMessage, SanitizedStreamName


class NatsAdapter(BaseAdapter):
    """
    NATS JetStream adapter for persistent pub/sub messaging.

    Provides Redis Streams-like functionality using NATS JetStream:
    - Streams (similar to Redis Streams)
    - Consumers with deliver groups (similar to consumer groups)
    - Manual ACK/NAK (similar to XACK/NACK)
    - Durable subscriptions (persisted across restarts)

    Key differences from Redis:
    - Retry: NAK instead of XAUTOCLAIM (simpler, no cursor needed)
    - Naming: Stream names cannot contain '.', '>', '*' → replaced with '_'
    - Headers: Native NATS headers instead of Redis Hash

    Args:
        url: NATS server URL (e.g., "nats://localhost:4222")
        stream_config: Default JetStream stream configuration
        consumer_config: Default JetStream consumer configuration
        max_reconnect_attempts: Max connection retry attempts (-1 = infinite)
        reconnect_time_wait: Wait time between reconnects (seconds)

    Example:
        >>> adapter = NatsAdapter(
        ...     url="nats://localhost:4222",
        ...     stream_config={"max_msgs": 10000},
        ... )
        >>> await adapter.connect()
    """

    def __init__(
        self,
        url: str = "nats://localhost:4222",
        stream_config: dict[str, Any] | None = None,
        consumer_config: dict[str, Any] | None = None,
        max_reconnect_attempts: int = -1,
        reconnect_time_wait: int = 2,
    ):
        """
        Initialize NATS JetStream adapter.

        Args:
            url: NATS server URL (can be comma-separated for cluster)
            stream_config: Default stream configuration
            consumer_config: Default consumer configuration
            max_reconnect_attempts: Max reconnection attempts
            reconnect_time_wait: Wait time between reconnects (seconds)
        """
        super().__init__()

        if NATS is None:
            raise ImportError(
                "nats-py package not installed. "
                "Install with: pip install nats-py"
            )

        self.url = url
        self.stream_config = stream_config or {}
        self.consumer_config = consumer_config or {}
        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_time_wait = reconnect_time_wait

        # NATS connection objects
        self.nc: NATS | None = None  # NATS connection
        self.js: JetStreamContext | None = None  # JetStream context

        # Subscriptions registry
        self.subscriptions: dict[str, Any] = {}  # channel_id -> subscription

        # Connection state
        self._connected = False
        self._stopping = False

        # Metrics collector (initialized in init())
        self.metrics: ChannelMetrics | None = None

    def init(self, broker: Any, logger: Any) -> None:
        """
        Initialize adapter with broker, logger, and metrics.

        Args:
            broker: ServiceBroker instance
            logger: Logger instance
        """
        super().init(broker, logger)
        self.metrics = ChannelMetrics(broker=broker, logger=logger, enabled=True)

    async def connect(self) -> None:
        """
        Connect to NATS server and initialize JetStream.

        Establishes connection with automatic reconnection handling.
        Creates JetStream context for pub/sub operations.

        Raises:
            NatsConnectionError: If connection fails after max retries
        """
        if self._connected:
            return

        try:
            self.logger.info(f"Connecting to NATS at {self.url}")

            # Parse servers (support comma-separated URLs)
            servers = [s.strip() for s in self.url.split(",")]

            # Create NATS client
            self.nc = NATS()

            # Connect to NATS server
            await self.nc.connect(
                servers=servers,
                max_reconnect_attempts=self.max_reconnect_attempts,
                reconnect_time_wait=self.reconnect_time_wait,
            )

            # Create JetStream context
            self.js = self.nc.jetstream()

            self._connected = True
            self.logger.info("Connected to NATS JetStream")

        except Exception as e:
            self.logger.error(f"Failed to connect to NATS: {e}")
            raise NatsConnectionError(f"NATS connection failed: {e}") from e

    async def disconnect(self) -> None:
        """
        Disconnect from NATS server.

        Performs graceful shutdown:
        1. Drain subscriptions (wait for in-flight messages)
        2. Close connection
        """
        self._stopping = True

        try:
            if self.nc and not self.nc.is_closed:
                self.logger.info("Closing NATS connection...")

                # Drain connection (wait for in-flight messages) with timeout
                try:
                    await asyncio.wait_for(self.nc.drain(), timeout=10.0)
                    self.logger.info("NATS connection closed")
                except asyncio.TimeoutError:
                    self.logger.warning("Drain timeout (10s), forcing close")
                    await self.nc.close()

        except Exception as e:
            self.logger.error(f"Error closing NATS connection: {e}")

        finally:
            self._connected = False

    def _sanitize_stream_name(self, name: str) -> SanitizedStreamName:
        """
        Sanitize stream name for NATS compatibility.

        NATS stream names cannot contain: '.', '>', '*'
        More info: https://docs.nats.io/jetstream/administration/naming

        Args:
            name: Original stream/channel name

        Returns:
            Sanitized name with forbidden chars replaced by '_' (branded type)

        Note:
            Returns SanitizedStreamName (NewType) to guarantee compile-time
            safety — prevents accidental use of raw stream names.
        """
        sanitized = name.replace(".", "_").replace(">", "_").replace("*", "_")
        return SanitizedStreamName(sanitized)

    async def _create_stream(
        self,
        stream_name: SanitizedStreamName,
        subjects: list[str],
        config: dict[str, Any] | None = None,
    ) -> None:
        """
        Create JetStream stream (idempotent).

        Args:
            stream_name: Stream name (must be sanitized via _sanitize_stream_name)
            subjects: List of subjects for this stream
            config: Optional stream configuration

        Note:
            Idempotent — ignores "stream name already in use" error
            Accepts SanitizedStreamName to guarantee NATS naming compliance
        """
        if not self.js:
            raise NatsStreamError("JetStream context not initialized")

        # Merge with default config
        stream_config = {**self.stream_config, **(config or {})}
        stream_config["name"] = stream_name
        stream_config["subjects"] = subjects

        try:
            stream = StreamConfig(**stream_config)
            await self.js.add_stream(stream)
            self.logger.debug(f"Created NATS stream: {stream_name}")

        except BadRequestError as e:
            if "stream name already in use" in str(e).lower():
                self.logger.debug(f"Stream {stream_name} already exists (idempotent)")
            else:
                self.logger.error(f"Failed to create stream {stream_name}: {e}")
                raise

        except Exception as e:
            self.logger.error(f"Stream creation error: {e}")
            raise NatsStreamError(f"Failed to create stream: {e}") from e

    async def subscribe(self, channel: Channel, service: Any) -> None:
        """
        Subscribe to a channel with JetStream consumer.

        Creates:
        1. Stream for channel messages
        2. Stream for DLQ (if enabled)
        3. Durable consumer with deliver group

        Args:
            channel: Channel configuration
            service: Service instance (unused for NATS)
        """
        if not self._connected or not self.js:
            raise NatsConsumerError("Adapter not connected")

        self.logger.debug(
            f"Subscribing to '{channel.name}' with group '{channel.group}'"
        )

        # Initialize active message tracking
        self.init_channel_active_messages(channel.id)

        # 1. Create stream for channel
        stream_name = self._sanitize_stream_name(channel.name)
        await self._create_stream(stream_name, [channel.name])

        # 2. Create DLQ stream (if enabled)
        if channel.dead_lettering and channel.dead_lettering.enabled:
            dlq_name = channel.dead_lettering.queue_name
            dlq_stream = self._sanitize_stream_name(dlq_name)
            await self._create_stream(dlq_stream, [dlq_name])

        # 3. Configure consumer WITH deliver_group in config
        consumer_config = {
            **self.consumer_config,
            "durable_name": self._sanitize_stream_name(channel.group),
            "deliver_group": stream_name,  # ✅ Set in config for queue subscription
            "ack_policy": "explicit",  # Manual ACK
            "deliver_policy": "new",  # Only new messages
            "max_ack_pending": channel.max_in_flight or 100,
        }

        # 4. Create subscription with consumer
        try:
            config = ConsumerConfig(**consumer_config)

            # Create message handler
            async def message_handler(msg: JetStreamMessage) -> None:
                await self._process_message(channel, msg)

            # Subscribe WITHOUT queue parameter (deliver_group in config instead)
            # nats-py requires deliver_group in ConsumerConfig, not as queue parameter
            sub = await self.js.subscribe(
                subject=channel.name,
                # queue parameter NOT used - deliver_group in config instead
                cb=message_handler,
                config=config,
                manual_ack=True,
            )

            self.subscriptions[channel.id] = sub
            self.logger.info(f"Subscribed to '{channel.name}' (stream: {stream_name})")

        except Exception as e:
            self.logger.error(f"Subscription failed for '{channel.name}': {e}")
            raise NatsConsumerError(f"Failed to subscribe: {e}") from e

    async def _process_message(self, channel: Channel, msg: JetStreamMessage) -> None:
        """
        Process a single JetStream message.

        Flow:
        1. Mark message as "in progress" (prevents redelivery)
        2. Deserialize payload
        3. Call handler
        4. ACK on success / NAK on error (with retry limit check)
        5. Move to DLQ if max retries exceeded

        Args:
            channel: Channel configuration
            msg: JetStream message
        """
        # Get message sequence (for tracking)
        msg_seq = msg.metadata.sequence.stream

        # Track active message
        await self.add_channel_active_messages(channel.id, [str(msg_seq)])

        # Prevent redelivery while processing
        await msg.in_progress()

        start_time = time.time()

        try:
            # Deserialize payload
            payload = self.serializer.deserialize(msg.data)

            # Call handler
            await channel.handler(payload, msg)

            # ACK on success
            await msg.ack()

            # Metrics: successful processing
            if self.metrics:
                duration_ms = (time.time() - start_time) * 1000
                self.metrics.increment_total(channel.name, channel.group)
                self.metrics.observe_time(channel.name, channel.group, duration_ms)

        except Exception as e:
            self.logger.warning(f"Handler error in '{channel.name}': {e}")

            # Metrics: error
            if self.metrics:
                self.metrics.increment_errors(channel.name, channel.group)

            # Check retry limit
            metadata = msg.metadata
            num_delivered = metadata.num_delivered

            if num_delivered >= channel.max_retries:
                # Max retries exceeded → DLQ
                self.logger.info(
                    f"Max retries ({channel.max_retries}) exceeded for message {msg_seq}, moving to DLQ"
                )

                if channel.dead_lettering and channel.dead_lettering.enabled:
                    await self._move_to_dlq(channel, msg, e)

                # ACK to prevent further redelivery
                await msg.ack()

            else:
                # NAK for retry
                await msg.nak()

                # Metrics: retry
                if self.metrics:
                    self.metrics.increment_retries(channel.name, channel.group)

        finally:
            # Remove from active tracking
            await self.remove_channel_active_messages(channel.id, [str(msg_seq)])

            # Metrics: update active gauge
            if self.metrics:
                active_count = await self.get_number_of_channel_active_messages(channel.id)
                self.metrics.set_active(channel.name, channel.group, active_count)

    async def _move_to_dlq(
        self, channel: Channel, msg: JetStreamMessage, error: Exception
    ) -> None:
        """
        Move failed message to Dead Letter Queue.

        Publishes original message data to DLQ stream with error headers.

        Args:
            channel: Channel configuration
            msg: Failed JetStream message
            error: Exception that caused failure
        """
        if not channel.dead_lettering or not channel.dead_lettering.enabled:
            return

        try:
            # Build error headers (compatible with Redis adapter)
            from ..utils import default_transform_error_to_headers

            error_headers = default_transform_error_to_headers(error)

            # Add original channel metadata
            headers = {
                HEADER_ORIGINAL_CHANNEL: channel.name,
                HEADER_ORIGINAL_GROUP: channel.group,
                **error_headers,
            }

            # Publish to DLQ
            dlq_name = channel.dead_lettering.queue_name
            await self.publish(dlq_name, msg.data, {"raw": True, "headers": headers})

            # Metrics: DLQ move
            if self.metrics:
                self.metrics.increment_dlq(channel.name, channel.group)

            self.logger.info(f"Moved message {msg.metadata.sequence.stream} to DLQ '{dlq_name}'")

        except Exception as e:
            self.logger.error(f"Failed to move message to DLQ: {e}")

    async def unsubscribe(self, channel: Channel) -> None:
        """
        Unsubscribe from channel (graceful shutdown).

        Waits for active messages to complete before unsubscribing.

        Args:
            channel: Channel configuration
        """
        if channel.unsubscribing:
            return

        channel.unsubscribing = True

        sub = self.subscriptions.get(channel.id)
        if not sub:
            return

        # Wait for active messages to complete (with timeout)
        timeout = 30.0  # 30 seconds
        start = time.time()

        while await self.get_number_of_channel_active_messages(channel.id) > 0:
            if time.time() - start > timeout:
                active_count = await self.get_number_of_channel_active_messages(channel.id)
                self.logger.warning(
                    f"Unsubscribe timeout (30s) for '{channel.name}' "
                    f"({active_count} messages still active)"
                )
                break

            active = await self.get_number_of_channel_active_messages(channel.id)
            self.logger.info(
                f"Waiting for {active} active messages on '{channel.name}'..."
            )
            await asyncio.sleep(1.0)

        # Drain and unsubscribe (drain already does unsubscribe)
        try:
            await sub.drain()  # Drains messages and unsubscribes
            # No need for explicit unsubscribe() - drain() does it

            # Stop tracking
            self.stop_channel_active_messages(channel.id)

            # Remove from registry
            del self.subscriptions[channel.id]

            self.logger.debug(f"Unsubscribed from '{channel.name}'")

        except Exception as e:
            self.logger.error(f"Error unsubscribing from '{channel.name}': {e}")

    async def publish(
        self, channel_name: str, payload: Any, opts: dict[str, Any]
    ) -> str:
        """
        Publish message to NATS JetStream.

        Args:
            channel_name: Channel/subject name
            payload: Message payload (will be serialized unless raw=True)
            opts: Publishing options:
                - raw: bool (skip serialization)
                - headers: dict (message headers)

        Returns:
            Message sequence number

        Raises:
            MessagePublishError: If publishing fails
        """
        if self._stopping:
            raise MessagePublishError("Adapter is stopping")

        if not self._connected or not self.js:
            raise MessagePublishError("Adapter not connected")

        try:
            # Prepare data
            if opts.get("raw"):
                data = payload  # Already bytes
            else:
                data = self.serializer.serialize(payload)

            # Prepare headers
            headers = opts.get("headers")

            # Publish to JetStream
            ack = await self.js.publish(
                subject=channel_name,
                payload=data,
                headers=headers,
            )

            seq = ack.seq
            self.logger.debug(f"Published message {seq} to '{channel_name}'")

            return str(seq)

        except Exception as e:
            raise MessagePublishError(f"Failed to publish to '{channel_name}': {e}") from e

    def parse_message_headers(
        self, raw_message: JetStreamMessage
    ) -> dict[str, str] | None:
        """
        Parse headers from NATS JetStream message.

        Args:
            raw_message: JetStream message object

        Returns:
            Dictionary of headers or None
        """
        if hasattr(raw_message, "headers") and raw_message.headers:
            # NATS headers is a dict-like object
            headers = {}
            for key in raw_message.headers:
                value = raw_message.headers.get(key)
                if value:
                    # Headers can be string or list of strings
                    if isinstance(value, list):
                        headers[key] = value[0] if value else ""
                    else:
                        headers[key] = value  # Already a string
            return headers

        return None
