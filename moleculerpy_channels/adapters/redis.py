"""
Redis Streams adapter for MoleculerPy Channels.

Implements production-ready pub/sub messaging with Redis Streams, providing:
- Persistent message storage
- Consumer groups for horizontal scaling
- Automatic retry via XAUTOCLAIM
- Dead Letter Queue for failed messages
- Context propagation for distributed tracing
"""

import asyncio
import base64
import time
import traceback
from collections.abc import Awaitable
from typing import Any

from ..metrics import ChannelMetrics

try:
    import redis.asyncio as aioredis
except ImportError:
    raise ImportError(
        "Redis support requires 'redis' package. Install with: pip install redis[hiredis]"
    )

from ..channel import Channel
from ..constants import HEADER_ORIGINAL_CHANNEL, HEADER_ORIGINAL_GROUP
from ..errors import AdapterError, MessagePublishError, SubscriptionError
from .base import BaseAdapter

type StreamFieldValue = bytes | bytearray | memoryview[int] | str | int | float


class RedisAdapter(BaseAdapter):
    """
    Redis Streams adapter for persistent pub/sub messaging.

    Uses Redis Streams for message storage with consumer groups, automatic
    retry via XAUTOCLAIM, and Dead Letter Queue for failed messages.

    Example:
        >>> from moleculerpy_channels import ChannelsMiddleware
        >>> from moleculerpy_channels.adapters import RedisAdapter
        >>>
        >>> adapter = RedisAdapter(redis_url="redis://localhost:6379/0")
        >>> middleware = ChannelsMiddleware(adapter=adapter)
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        max_connections: int = 10,
        socket_timeout: float = 5.0,
        socket_connect_timeout: float = 5.0,
    ) -> None:
        """
        Initialize Redis adapter.

        Args:
            redis_url: Redis connection URL
            max_connections: Max connections in pool
            socket_timeout: Socket timeout in seconds
            socket_connect_timeout: Socket connect timeout in seconds
        """
        super().__init__()

        self.redis_url = redis_url
        self.max_connections = max_connections
        self.socket_timeout = socket_timeout
        self.socket_connect_timeout = socket_connect_timeout

        self.redis: aioredis.Redis | None = None
        self._consumer_name: str = ""
        self._background_tasks: dict[str, list[asyncio.Task[None]]] = {}
        self._backoff_state: dict[str, float] = {}  # channel_id -> current_delay
        self._xclaim_cursors: dict[str, str] = {}  # channel_id -> cursor for XAUTOCLAIM
        self._error_metadata: dict[str, dict[str, str]] = {}  # msg_key -> error info
        self._connected = False

        # Exponential backoff configuration
        self._base_delay = 0.1  # Start with 100ms
        self._max_delay = 30.0  # Cap at 30 seconds
        self._exponential_base = 2.0  # Double each attempt
        self._jitter_factor = 0.25  # Add 0-25% jitter

        # Error metadata TTL (24 hours in seconds)
        self._error_info_ttl = 24 * 3600

        # Metrics collector (will be initialized in init())
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

    async def _await_maybe(self, result: Awaitable[Any] | Any) -> Any:
        """Await redis client methods with imperfect third-party typings."""
        if isinstance(result, Awaitable):
            return await result
        return result

    def _require_redis(self) -> aioredis.Redis:
        """Return connected Redis client or raise a typed adapter error."""
        if not self._connected or self.redis is None:
            raise AdapterError("Adapter not connected")
        return self.redis

    def _transform_error_to_metadata(self, error: Exception) -> dict[str, str]:
        """
        Transform exception to Redis Hash metadata.

        Converts error object to flat key-value pairs suitable for Redis Hash.
        Stack traces and complex data are base64-encoded to handle special
        characters (required for compatibility with NATS/Kafka headers).

        Args:
            error: Exception to transform

        Returns:
            Dictionary of error metadata (all values as strings)
        """
        metadata: dict[str, str] = {}

        # Basic error info
        if hasattr(error, "__class__"):
            metadata["x-error-name"] = error.__class__.__name__
        if hasattr(error, "message") and error.message:
            metadata["x-error-message"] = str(error.message)
        elif str(error):
            metadata["x-error-message"] = str(error)

        # Error code (if available)
        if hasattr(error, "code"):
            metadata["x-error-code"] = str(error.code)

        # Stack trace (base64 encoded to handle newlines)
        stack_trace = traceback.format_exception(type(error), error, error.__traceback__)
        if stack_trace:
            stack_str = "".join(stack_trace)
            metadata["x-error-stack"] = base64.b64encode(stack_str.encode()).decode()

        # Timestamp
        metadata["x-error-timestamp"] = str(int(time.time() * 1000))

        return metadata

    async def connect(self) -> None:
        """
        Connect to Redis and create connection pool.

        Raises:
            AdapterError: If connection fails
        """
        if self._connected:
            return

        try:
            self.logger.info(f"Connecting to Redis at {self.redis_url}")

            # Create connection pool
            self.redis = aioredis.Redis.from_url(
                self.redis_url,
                max_connections=self.max_connections,
                socket_timeout=self.socket_timeout,
                socket_connect_timeout=self.socket_connect_timeout,
                decode_responses=False,  # We handle serialization
            )

            # Test connection
            await self._await_maybe(self.redis.ping())

            # Generate consumer name (unique per broker instance)
            # MoleculerPy uses 'nodeID' (Moleculer.js compatible attribute name)
            broker_id = getattr(self.broker, "nodeID", getattr(self.broker, "node_id", "broker-1"))
            self._consumer_name = f"{broker_id}-{int(time.time() * 1000)}"

            self._connected = True
            self.logger.info(f"Connected to Redis (consumer: {self._consumer_name})")

        except Exception as e:
            raise AdapterError(f"Failed to connect to Redis: {e}") from e

    async def disconnect(self) -> None:
        """
        Disconnect from Redis and cleanup resources.

        Waits for all background tasks to complete before closing connection.
        """
        if not self._connected:
            return

        self.logger.info("Disconnecting from Redis...")

        # Cancel all background tasks
        for channel_id, tasks in self._background_tasks.items():
            self.logger.debug(f"Cancelling {len(tasks)} background tasks for {channel_id}")
            for task in tasks:
                task.cancel()

            # Wait for cancellation
            await asyncio.gather(*tasks, return_exceptions=True)

        self._background_tasks.clear()

        # Close Redis connection
        if self.redis:
            await self.redis.aclose()
            self.redis = None

        self._connected = False
        self.logger.info("Disconnected from Redis")

    async def subscribe(self, channel: Channel, service: Any) -> None:
        """
        Subscribe to a Redis Stream with consumer group.

        Creates the stream and consumer group if they don't exist, then starts
        background loops for consuming messages and handling retries.

        Args:
            channel: Channel configuration
            service: Service instance (unused)

        Raises:
            SubscriptionError: If subscription fails
        """
        if not self._connected or not self.redis:
            raise SubscriptionError("Adapter not connected")

        try:
            self.logger.info(
                f"Subscribing to '{channel.name}' (group: {channel.group}, "
                f"consumer: {self._consumer_name})"
            )

            # Create consumer group (idempotent)
            await self._create_consumer_group(channel)

            # Start background loops
            tasks = []

            # 1. XREADGROUP loop (consume new messages)
            read_task = asyncio.create_task(
                self._xreadgroup_loop(channel), name=f"xreadgroup-{channel.id}"
            )
            tasks.append(read_task)

            # 2. XAUTOCLAIM loop (retry pending messages)
            if channel.redis and channel.redis.claim_interval > 0:
                claim_task = asyncio.create_task(
                    self._xclaim_loop(channel), name=f"xclaim-{channel.id}"
                )
                tasks.append(claim_task)

            # 3. DLQ detection loop (move failed messages to DLQ)
            if channel.dead_lettering and channel.dead_lettering.enabled:
                dlq_task = asyncio.create_task(self._dlq_loop(channel), name=f"dlq-{channel.id}")
                tasks.append(dlq_task)

            # Store tasks for cleanup
            self._background_tasks[channel.id] = tasks

            self.logger.debug(f"Started {len(tasks)} background loops for '{channel.name}'")

        except Exception as e:
            raise SubscriptionError(f"Failed to subscribe to '{channel.name}': {e}") from e

    async def unsubscribe(self, channel: Channel) -> None:
        """
        Unsubscribe from a channel.

        Stops background loops and waits for active messages to complete.

        Order of operations (critical for data safety):
        1. Mark channel as unsubscribing (stops new message processing)
        2. Wait for active messages to complete (prevents data loss)
        3. Cancel background tasks (after handlers finish)

        Args:
            channel: Channel to unsubscribe from
        """
        channel.unsubscribing = True

        self.logger.info(f"Unsubscribing from '{channel.name}' (group: {channel.group})")

        # STEP 1: Wait for active messages to complete FIRST (critical!)
        # Background loops will stop naturally when they check channel.unsubscribing
        try:
            await asyncio.wait_for(self.wait_for_channel_active_messages(channel.id), timeout=30.0)
        except asyncio.TimeoutError:
            active_count = await self.get_number_of_channel_active_messages(channel.id)
            self.logger.warning(
                f"Timeout waiting for {active_count} active message(s) "
                f"on '{channel.name}' to complete"
            )

        # STEP 2: Now safe to cancel background tasks (handlers are done)
        if channel.id in self._background_tasks:
            tasks = self._background_tasks[channel.id]

            # Cancel all tasks
            for task in tasks:
                if not task.done():
                    task.cancel()

            # Wait for cancellation with timeout
            try:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=5.0)
            except asyncio.TimeoutError:
                self.logger.warning(f"Timeout waiting for background tasks on '{channel.name}'")

            del self._background_tasks[channel.id]

        # Clean up backoff state for this channel
        self._reset_backoff(channel.id)
        self._reset_backoff(f"{channel.id}-xclaim")
        self._reset_backoff(f"{channel.id}-dlq")

        # Clean up cursor state (XAUTOCLAIM cursor)
        if channel.id in self._xclaim_cursors:
            del self._xclaim_cursors[channel.id]

        self.logger.debug(f"Unsubscribed from '{channel.name}'")

    async def publish(self, channel_name: str, payload: Any, opts: dict[str, Any]) -> str | None:
        """
        Publish message to Redis Stream.

        Args:
            channel_name: Name of the stream
            payload: Message payload (will be serialized)
            opts: Publishing options (headers, xaddMaxLen, etc.)

        Returns:
            Message ID from XADD

        Raises:
            MessagePublishError: If publishing fails
        """
        try:
            redis = self._require_redis()
        except AdapterError as err:
            raise MessagePublishError(str(err)) from err

        try:
            # Serialize payload
            serialized_payload = self.serializer.serialize(payload)

            # Build fields dict
            fields: dict[StreamFieldValue, StreamFieldValue] = {b"payload": serialized_payload}

            # Add headers
            headers = opts.get("headers", {})
            for key, value in headers.items():
                if isinstance(value, str):
                    encoded_value: StreamFieldValue = value.encode()
                elif isinstance(value, (bytes, bytearray, memoryview, int, float)):
                    encoded_value = value
                else:
                    encoded_value = str(value)
                fields[key.encode()] = encoded_value

            # XADD with maxlen (capped streams)
            max_len = opts.get("xaddMaxLen")  # None if not specified

            # Build xadd kwargs dynamically
            xadd_kwargs = {
                "name": channel_name,
                "fields": fields,
            }

            if max_len is not None and max_len > 0:
                xadd_kwargs["maxlen"] = max_len
                # Use approximate trimming only for large streams (> 1000)
                # For small maxlen, approximate doesn't trigger (needs ~100+ entries)
                xadd_kwargs["approximate"] = max_len > 1000

            if max_len is not None and max_len > 0:
                message_id = await redis.xadd(
                    channel_name,
                    fields,
                    maxlen=max_len,
                    approximate=max_len > 1000,
                )
            else:
                message_id = await redis.xadd(channel_name, fields)

            message_id_str = (
                message_id.decode() if isinstance(message_id, bytes) else str(message_id)
            )
            self.logger.debug(f"Published message {message_id_str} to '{channel_name}'")

            return message_id_str

        except Exception as e:
            raise MessagePublishError(f"Failed to publish to '{channel_name}': {e}") from e

    def parse_message_headers(self, raw_message: Any) -> dict[str, str] | None:
        """
        Parse headers from Redis Stream message.

        Args:
            raw_message: Tuple of (message_id, fields_dict) from Redis

        Returns:
            Dictionary of headers or None
        """
        if not isinstance(raw_message, tuple) or len(raw_message) != 2:
            return None

        _, fields = raw_message
        if not isinstance(fields, dict):
            return None

        # Extract headers (everything except payload)
        headers: dict[str, str] = {}
        for key, value in fields.items():
            if key == b"payload":
                continue

            # Decode bytes to strings
            key_str = key.decode() if isinstance(key, bytes) else str(key)
            value_str = value.decode() if isinstance(value, bytes) else str(value)
            headers[key_str] = value_str

        return headers if headers else None

    # ── Private Methods ──────────────────────────────────────────────────────

    async def _backoff_sleep(self, channel_id: str) -> None:
        """
        Sleep with exponential backoff and jitter.

        Formula: delay = min(base * (2^attempts), max) + jitter
        Jitter: Random 0-25% of delay to prevent thundering herd

        Args:
            channel_id: Channel ID for tracking backoff state
        """
        import random

        # Get current backoff delay (or start with base_delay)
        current_delay = self._backoff_state.get(channel_id, self._base_delay)

        # Add jitter (0-25% of delay)
        jitter = current_delay * self._jitter_factor * random.random()
        sleep_time = current_delay + jitter

        self.logger.debug(f"Backoff sleep for '{channel_id}': {sleep_time:.2f}s")

        await asyncio.sleep(sleep_time)

        # Exponential increase for next time (capped at max_delay)
        next_delay = min(current_delay * self._exponential_base, self._max_delay)
        self._backoff_state[channel_id] = next_delay

    def _reset_backoff(self, channel_id: str) -> None:
        """
        Reset backoff state after successful operation.

        Args:
            channel_id: Channel ID to reset
        """
        self._backoff_state.pop(channel_id, None)

    async def _create_consumer_group(self, channel: Channel) -> None:
        """
        Create consumer group for the channel (idempotent).

        Args:
            channel: Channel configuration
        """
        redis = self._require_redis()
        try:
            await redis.xgroup_create(
                name=channel.name,
                groupname=channel.group,
                id="0",  # Start from beginning
                mkstream=True,  # Create stream if not exists
            )
            self.logger.debug(f"Created consumer group '{channel.group}' for '{channel.name}'")
        except aioredis.ResponseError as e:
            # BUSYGROUP means group already exists (expected)
            if "BUSYGROUP" not in str(e):
                raise

    async def _xreadgroup_loop(self, channel: Channel) -> None:
        """
        Background loop for consuming new messages via XREADGROUP.

        Reads new messages from the stream and processes them.

        Args:
            channel: Channel configuration
        """
        redis_opts = channel.redis
        read_timeout_ms = redis_opts.read_timeout_ms if redis_opts else 5000
        start_id = redis_opts.start_id if redis_opts else ">"

        self.logger.debug(
            f"Starting XREADGROUP loop for '{channel.name}' "
            f"(timeout: {read_timeout_ms}ms, start: {start_id})"
        )

        while not channel.unsubscribing:
            try:
                redis = self._require_redis()
                # XREADGROUP with blocking
                messages = await redis.xreadgroup(
                    groupname=channel.group,
                    consumername=self._consumer_name,
                    streams={channel.name: start_id},
                    count=10,  # Batch size
                    block=read_timeout_ms,
                )

                if not messages:
                    continue

                # Process messages in background tasks (don't block XREADGROUP loop)
                for stream_name, message_list in messages:
                    for msg_id, fields in message_list:
                        asyncio.create_task(self._process_message(channel, msg_id, fields))

                # Success - reset backoff
                self._reset_backoff(channel.id)

            except asyncio.CancelledError:
                self.logger.debug(f"XREADGROUP loop cancelled for '{channel.name}'")
                break
            except Exception as e:
                self.logger.error(f"XREADGROUP error for '{channel.name}': {e}")
                # Exponential backoff with jitter
                await self._backoff_sleep(channel.id)

    async def _xclaim_loop(self, channel: Channel) -> None:
        """
        Background loop for claiming pending messages via XAUTOCLAIM.

        Claims messages that have been idle for longer than min_idle_time.
        Uses cursor-based scanning to efficiently track position in the
        pending entries list (PEL).

        Args:
            channel: Channel configuration
        """
        redis_opts = channel.redis
        if not redis_opts:
            return

        min_idle_time = redis_opts.min_idle_time
        claim_interval_sec = redis_opts.claim_interval / 1000

        # Initialize cursor for this channel (persistent across calls)
        if channel.id not in self._xclaim_cursors:
            self._xclaim_cursors[channel.id] = "0-0"

        self.logger.debug(
            f"Starting XAUTOCLAIM loop for '{channel.name}' "
            f"(min_idle: {min_idle_time}ms, interval: {claim_interval_sec}s)"
        )

        while not channel.unsubscribing:
            try:
                await asyncio.sleep(claim_interval_sec)
                redis = self._require_redis()

                # Get current cursor for this channel
                cursor = self._xclaim_cursors[channel.id]

                # XAUTOCLAIM pending messages with cursor
                result = await redis.xautoclaim(
                    name=channel.name,
                    groupname=channel.group,
                    consumername=self._consumer_name,
                    min_idle_time=min_idle_time,
                    start_id=cursor,  # Use cursor from previous call
                    count=10,
                )

                # Format: [next_cursor, messages, deleted_ids]
                if len(result) < 2:
                    continue

                next_cursor, claimed_messages = result[0], result[1]

                # Update cursor for next iteration
                # When cursor returns to "0-0", Redis has scanned entire PEL
                # and will restart from beginning on next call (circular scan)
                self._xclaim_cursors[channel.id] = (
                    next_cursor.decode() if isinstance(next_cursor, bytes) else next_cursor
                )

                # Metrics: retry attempts (one increment per claimed message)
                if claimed_messages and self.metrics:
                    for _ in claimed_messages:
                        self.metrics.increment_retries(channel.name, channel.group)

                # Process claimed messages in background (non-blocking)
                for msg_id, fields in claimed_messages:
                    asyncio.create_task(self._process_message(channel, msg_id, fields))

                # Success - reset backoff
                self._reset_backoff(f"{channel.id}-xclaim")

            except asyncio.CancelledError:
                self.logger.debug(f"XAUTOCLAIM loop cancelled for '{channel.name}'")
                break
            except Exception as e:
                self.logger.error(f"XAUTOCLAIM error for '{channel.name}': {e}")
                # Exponential backoff with jitter
                await self._backoff_sleep(f"{channel.id}-xclaim")

    async def _dlq_loop(self, channel: Channel) -> None:
        """
        Background loop for detecting failed messages and moving to DLQ.

        Checks XPENDING for messages exceeding max_retries.

        Args:
            channel: Channel configuration
        """
        if not channel.dead_lettering or not channel.dead_lettering.enabled:
            return

        redis_opts = channel.redis
        dlq_check_interval = redis_opts.dlq_check_interval if redis_opts else 30

        self.logger.debug(
            f"Starting DLQ loop for '{channel.name}' (interval: {dlq_check_interval}s)"
        )

        while not channel.unsubscribing:
            try:
                await asyncio.sleep(dlq_check_interval)
                redis = self._require_redis()

                # XPENDING to get message delivery counts
                pending = await redis.xpending_range(
                    name=channel.name,
                    groupname=channel.group,
                    min="-",
                    max="+",
                    count=100,
                )

                # Move messages exceeding max_retries to DLQ
                for msg_info in pending:
                    msg_id = msg_info["message_id"]
                    times_delivered = msg_info["times_delivered"]

                    if times_delivered >= channel.max_retries:
                        await self._move_to_dlq(channel, msg_id)

                # Success - reset backoff
                self._reset_backoff(f"{channel.id}-dlq")

            except asyncio.CancelledError:
                self.logger.debug(f"DLQ loop cancelled for '{channel.name}'")
                break
            except Exception as e:
                self.logger.error(f"DLQ error for '{channel.name}': {e}")
                # Exponential backoff with jitter
                await self._backoff_sleep(f"{channel.id}-dlq")

    async def _process_message(
        self, channel: Channel, msg_id: bytes, fields: dict[bytes, bytes]
    ) -> None:
        """
        Process a single message from Redis Stream.

        Args:
            channel: Channel configuration
            msg_id: Redis message ID
            fields: Message fields (payload + headers)
        """
        msg_id_str = msg_id.decode()
        start_time = time.time()
        redis = self._require_redis()

        # Track active message
        await self.add_channel_active_messages(channel.id, [msg_id_str])

        try:
            # Deserialize payload
            payload_bytes = fields.get(b"payload")
            if not payload_bytes:
                self.logger.warning(f"Message {msg_id_str} missing payload field")
                await redis.xack(channel.name, channel.group, msg_id)
                return

            payload = self.serializer.deserialize(payload_bytes)

            # Call handler
            await channel.handler(payload, (msg_id, fields))

            # ACK successful processing
            await redis.xack(channel.name, channel.group, msg_id)

            # Delete error metadata on success (if exists)
            error_key = f"chan:{channel.name}:msg:{msg_id_str}"
            await redis.delete(error_key)

            # Metrics: successful processing
            if self.metrics:
                duration_ms = (time.time() - start_time) * 1000
                self.metrics.increment_total(channel.name, channel.group)
                self.metrics.observe_time(channel.name, channel.group, duration_ms)

        except Exception as e:
            self.logger.error(f"Handler error for message {msg_id_str} on '{channel.name}': {e}")

            # Store error metadata in Redis Hash
            error_metadata = self._transform_error_to_metadata(e)
            error_key = f"chan:{channel.name}:msg:{msg_id_str}"

            if error_metadata:
                await self._await_maybe(redis.hset(error_key, mapping=error_metadata))
                # Set TTL to auto-expire stale errors
                await redis.expire(error_key, self._error_info_ttl)

            # Metrics: handler error
            if self.metrics:
                self.metrics.increment_errors(channel.name, channel.group)

            # Don't ACK - message stays pending for retry

        finally:
            # Remove from active tracking
            await self.remove_channel_active_messages(channel.id, [msg_id_str])

            # Metrics: update active gauge
            if self.metrics:
                active_count = await self.get_number_of_channel_active_messages(channel.id)
                self.metrics.set_active(channel.name, channel.group, active_count)

    async def _move_to_dlq(self, channel: Channel, msg_id: bytes) -> None:
        """
        Move failed message to Dead Letter Queue.

        Retrieves error metadata from Redis Hash and includes it in DLQ
        message headers for debugging. Error metadata is deleted after
        being moved to DLQ.

        Args:
            channel: Channel configuration
            msg_id: Message ID to move
        """
        if not channel.dead_lettering:
            return

        msg_id_str = msg_id.decode()
        redis = self._require_redis()

        try:
            # Get original message
            messages = await redis.xrange(channel.name, msg_id, msg_id, count=1)
            if not messages:
                self.logger.warning(f"Message {msg_id_str} not found for DLQ")
                return

            _, original_fields = messages[0]

            # Retrieve error metadata from Redis Hash
            error_key = f"chan:{channel.name}:msg:{msg_id_str}"
            error_metadata = await self._await_maybe(redis.hgetall(error_key))

            # Delete error metadata after retrieval
            await redis.delete(error_key)

            # Add DLQ headers
            dlq_fields = dict(original_fields)
            dlq_fields[HEADER_ORIGINAL_CHANNEL.encode()] = channel.name.encode()
            dlq_fields[HEADER_ORIGINAL_GROUP.encode()] = channel.group.encode()

            # Inject error metadata into DLQ message headers
            if error_metadata:
                for key, value in error_metadata.items():
                    # Keys and values from hgetall are already bytes
                    dlq_fields[key] = value

            # Publish to DLQ
            dlq_name = channel.dead_lettering.queue_name
            await redis.xadd(dlq_name, dlq_fields)

            # ACK original message
            await redis.xack(channel.name, channel.group, msg_id)

            # Metrics: DLQ move
            if self.metrics:
                self.metrics.increment_dlq(channel.name, channel.group)

            self.logger.info(
                f"Moved message {msg_id_str} to DLQ '{dlq_name}' "
                f"(with {len(error_metadata)} error metadata fields)"
            )

        except Exception as e:
            self.logger.error(f"Failed to move message {msg_id_str} to DLQ: {e}")
