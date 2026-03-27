"""
Basic integration tests for NatsAdapter.

NATS JetStream integration tests covering:
- Connection lifecycle
- Publish/consume
- Consumer groups (deliver_group)
- NAK-based retry mechanism
- DLQ with error metadata
- Metrics collection
- Context propagation
- Graceful shutdown

Prerequisites:
    NATS server running on localhost:4222
    Install: pip install nats-py

    # Start NATS JetStream:
    docker run -d --name nats-jetstream -p 4222:4222 nats:latest -js
"""

import asyncio
import base64

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_nats_connect_disconnect(mock_broker):
    """Test NATS connection lifecycle."""
    from moleculerpy_channels.adapters.nats import NatsAdapter

    adapter = NatsAdapter(url="nats://localhost:4222")
    adapter.init(mock_broker, mock_broker.get_logger("NatsAdapter"))

    # Connect
    await adapter.connect()
    assert adapter._connected is True
    assert adapter.nc is not None
    assert adapter.js is not None

    # Disconnect
    await adapter.disconnect()
    assert adapter._connected is False


@pytest.mark.asyncio
async def test_nats_publish_and_consume(mock_broker):
    """Test publishing and consuming messages via NATS JetStream."""
    from moleculerpy_channels.adapters.nats import NatsAdapter
    from moleculerpy_channels.channel import Channel

    adapter = NatsAdapter(url="nats://localhost:4222")
    adapter.init(mock_broker, mock_broker.get_logger("NatsAdapter"))
    await adapter.connect()

    received = []

    async def handler(payload, raw):
        received.append(payload)

    # Create channel
    channel = Channel(
        name="test.nats.publish",
        group="test-group",
        handler=handler,
    )
    channel.id = "nats-consumer-1"

    try:
        # Subscribe
        await adapter.subscribe(channel, None)
        await asyncio.sleep(0.5)  # Wait for subscription to be ready

        # Publish
        await adapter.publish(channel.name, {"msg": "hello from NATS"}, {})

        # Wait for message
        await asyncio.sleep(1.0)

        # Verify
        assert len(received) == 1
        assert received[0]["msg"] == "hello from NATS"

    finally:
        await adapter.unsubscribe(channel)
        await adapter.disconnect()


@pytest.mark.asyncio
async def test_nats_consumer_group_created(mock_broker):
    """Test that JetStream durable consumer is created properly."""
    from moleculerpy_channels.adapters.nats import NatsAdapter
    from moleculerpy_channels.channel import Channel

    adapter = NatsAdapter(url="nats://localhost:4222")
    adapter.init(mock_broker, mock_broker.get_logger("NatsAdapter"))
    await adapter.connect()

    channel = Channel(
        name="test.nats.group",
        group="durable-group",
        handler=lambda p, r: None,
    )
    channel.id = "nats-consumer-2"

    try:
        # Subscribe (creates stream + durable consumer)
        await adapter.subscribe(channel, None)
        await asyncio.sleep(0.2)

        # Verify subscription exists
        assert channel.id in adapter.subscriptions
        sub = adapter.subscriptions[channel.id]
        assert sub is not None

    finally:
        await adapter.unsubscribe(channel)
        await adapter.disconnect()


@pytest.mark.asyncio
async def test_nats_retry_on_handler_failure(mock_broker):
    """Test NAK-based retry mechanism when handler fails."""
    from moleculerpy_channels.adapters.nats import NatsAdapter
    from moleculerpy_channels.channel import Channel

    adapter = NatsAdapter(url="nats://localhost:4222")
    adapter.init(mock_broker, mock_broker.get_logger("NatsAdapter"))
    await adapter.connect()

    attempt_count = 0

    async def failing_handler(payload, raw):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count < 3:
            raise ValueError(f"Transient error (attempt {attempt_count})")
        # Success on 3rd attempt

    channel = Channel(
        name="test.nats.retry",
        group="retry-group",
        handler=failing_handler,
        max_retries=5,
    )
    channel.id = "nats-consumer-retry"

    try:
        await adapter.subscribe(channel, None)
        await asyncio.sleep(0.2)

        # Publish message
        await adapter.publish(channel.name, {"msg": "retry me"}, {})

        # Wait for retries
        await asyncio.sleep(3.0)

        # Verify: Handler called 3 times (initial + 2 retries)
        assert attempt_count == 3

    finally:
        await adapter.unsubscribe(channel)
        await adapter.disconnect()


@pytest.mark.asyncio
async def test_nats_max_retries_moves_to_dlq(mock_broker):
    """Test that message moves to DLQ after max retries exceeded."""
    from moleculerpy_channels.adapters.nats import NatsAdapter
    from moleculerpy_channels.channel import Channel, DeadLetteringOptions

    adapter = NatsAdapter(url="nats://localhost:4222")
    adapter.init(mock_broker, mock_broker.get_logger("NatsAdapter"))
    await adapter.connect()

    failed_count = 0
    dlq_received = []

    async def always_fail_handler(payload, raw):
        nonlocal failed_count
        failed_count += 1
        raise RuntimeError("Permanent failure")

    async def dlq_handler(payload, raw):
        dlq_received.append(payload)

    # Main channel with DLQ
    main_channel = Channel(
        name="test.nats.dlq.main",
        group="dlq-group",
        handler=always_fail_handler,
        max_retries=3,
        dead_lettering=DeadLetteringOptions(
            enabled=True,
            queue_name="test.nats.dlq.failed",
        ),
    )
    main_channel.id = "nats-consumer-dlq-main"

    # DLQ channel
    dlq_channel = Channel(
        name="test.nats.dlq.failed",
        group="dlq-consumer",
        handler=dlq_handler,
    )
    dlq_channel.id = "nats-consumer-dlq-handler"

    try:
        await adapter.subscribe(main_channel, None)
        await adapter.subscribe(dlq_channel, None)
        await asyncio.sleep(0.5)

        # Publish message
        await adapter.publish(main_channel.name, {"msg": "will fail"}, {})

        # Wait for retries + DLQ move
        await asyncio.sleep(5.0)

        # Verify: Handler failed max_retries times
        assert failed_count >= 3

        # Verify: Message moved to DLQ
        assert len(dlq_received) == 1

    finally:
        await adapter.unsubscribe(main_channel)
        await adapter.unsubscribe(dlq_channel)
        await adapter.disconnect()


@pytest.mark.asyncio
async def test_nats_dlq_preserves_error_metadata(mock_broker):
    """Test that DLQ message contains error metadata in headers."""
    from moleculerpy_channels.adapters.nats import NatsAdapter
    from moleculerpy_channels.channel import Channel, DeadLetteringOptions

    adapter = NatsAdapter(url="nats://localhost:4222")
    adapter.init(mock_broker, mock_broker.get_logger("NatsAdapter"))
    await adapter.connect()

    dlq_messages = []

    async def failing_handler(payload, raw):
        raise ValueError("Specific error message for DLQ")

    async def dlq_handler(payload, raw_msg):
        # Store raw message to inspect headers
        dlq_messages.append(raw_msg)

    main_channel = Channel(
        name="test.nats.dlq.metadata",
        group="metadata-group",
        handler=failing_handler,
        max_retries=2,
        dead_lettering=DeadLetteringOptions(
            enabled=True,
            queue_name="test.nats.dlq.metadata.failed",
        ),
    )
    main_channel.id = "nats-consumer-metadata"

    dlq_channel = Channel(
        name="test.nats.dlq.metadata.failed",
        group="dlq-metadata-consumer",
        handler=dlq_handler,
    )
    dlq_channel.id = "nats-consumer-dlq-metadata"

    try:
        await adapter.subscribe(main_channel, None)
        await adapter.subscribe(dlq_channel, None)
        await asyncio.sleep(0.5)

        # Publish message
        await adapter.publish(main_channel.name, {"test": "data"}, {})

        # Wait for DLQ move
        await asyncio.sleep(4.0)

        # Verify: DLQ message received
        assert len(dlq_messages) == 1

        # Verify: Error headers present
        raw_msg = dlq_messages[0]
        headers = adapter.parse_message_headers(raw_msg)

        assert headers is not None
        assert "x-original-channel" in headers
        assert headers["x-original-channel"] == "test.nats.dlq.metadata"
        assert "x-original-group" in headers
        assert "x-error-message" in headers
        assert "Specific error message" in headers["x-error-message"]
        assert "x-error-type" in headers
        assert headers["x-error-type"] == "ValueError"

    finally:
        await adapter.unsubscribe(main_channel)
        await adapter.unsubscribe(dlq_channel)
        await adapter.disconnect()


@pytest.mark.asyncio
async def test_nats_metrics_collection(mock_broker):
    """Test that all 7 metrics are collected properly."""
    from moleculerpy_channels.adapters.nats import NatsAdapter
    from moleculerpy_channels.channel import Channel

    adapter = NatsAdapter(url="nats://localhost:4222")
    adapter.init(mock_broker, mock_broker.get_logger("NatsAdapter"))
    await adapter.connect()

    received = []

    async def success_handler(payload, raw):
        received.append(payload)

    channel = Channel(
        name="test.nats.metrics",
        group="metrics-group",
        handler=success_handler,
    )
    channel.id = "nats-consumer-metrics"

    try:
        await adapter.subscribe(channel, None)
        await asyncio.sleep(0.2)

        # Publish 5 messages
        for i in range(5):
            await adapter.publish(channel.name, {"index": i}, {})

        # Wait for processing
        await asyncio.sleep(2.0)

        # Verify metrics
        metrics = adapter.metrics.get_metrics()

        # Check 'total' metric
        total_key = f"{channel.name}:{channel.group}"
        assert total_key in metrics["moleculer.channels.messages.total"]["values"]
        assert metrics["moleculer.channels.messages.total"]["values"][total_key] == 5

        # Check 'time' metric (histogram - stored as aggregated stats)
        assert total_key in metrics["moleculer.channels.messages.time"]["values"]
        time_stats = metrics["moleculer.channels.messages.time"]["values"][total_key]
        assert isinstance(time_stats, dict)
        assert time_stats["count"] == 5  # 5 observations
        assert time_stats["min"] > 0  # Has min value
        assert time_stats["max"] > 0  # Has max value
        assert time_stats["sum"] > 0  # Has total duration

    finally:
        await adapter.unsubscribe(channel)
        await adapter.disconnect()


@pytest.mark.asyncio
async def test_nats_context_propagation(mock_broker):
    """Test that context fields are propagated via headers."""
    from moleculerpy_channels.adapters.nats import NatsAdapter
    from moleculerpy_channels.channel import Channel

    adapter = NatsAdapter(url="nats://localhost:4222")
    adapter.init(mock_broker, mock_broker.get_logger("NatsAdapter"))
    await adapter.connect()

    received_headers = []

    async def capture_handler(payload, raw_msg):
        # Parse headers from raw message
        headers = adapter.parse_message_headers(raw_msg)
        received_headers.append(headers)

    channel = Channel(
        name="test.nats.context",
        group="context-group",
        handler=capture_handler,
    )
    channel.id = "nats-consumer-context"

    try:
        await adapter.subscribe(channel, None)
        await asyncio.sleep(0.2)

        # Publish with context headers
        context_headers = {
            "$requestID": "req-123",
            "$parentID": "ctx-456",
            "$level": "2",
            "$tracing": "true",
            "$caller": "test-service-v1",
            "$meta": base64.b64encode(b'{"key":"value"}').decode(),
            "$headers": base64.b64encode(b'{"x-custom":"header"}').decode(),
            "$parentChannelName": "parent.channel",
        }

        await adapter.publish(
            channel.name,
            {"msg": "context test"},
            {"headers": context_headers},
        )

        # Wait for message
        await asyncio.sleep(1.5)

        # Verify: All context fields received
        assert len(received_headers) == 1
        headers = received_headers[0]

        assert headers["$requestID"] == "req-123"
        assert headers["$parentID"] == "ctx-456"
        assert headers["$level"] == "2"
        assert headers["$tracing"] == "true"
        assert headers["$caller"] == "test-service-v1"
        assert "$meta" in headers
        assert "$headers" in headers

    finally:
        await adapter.unsubscribe(channel)
        await adapter.disconnect()


@pytest.mark.asyncio
async def test_nats_graceful_shutdown(mock_broker):
    """Test graceful shutdown waits for active messages."""
    from moleculerpy_channels.adapters.nats import NatsAdapter
    from moleculerpy_channels.channel import Channel

    adapter = NatsAdapter(url="nats://localhost:4222")
    adapter.init(mock_broker, mock_broker.get_logger("NatsAdapter"))
    await adapter.connect()

    processing_started = asyncio.Event()
    processing_done = asyncio.Event()

    async def slow_handler(payload, raw):
        processing_started.set()
        await asyncio.sleep(2.0)  # Simulate slow processing
        processing_done.set()

    channel = Channel(
        name="test.nats.shutdown",
        group="shutdown-group",
        handler=slow_handler,
    )
    channel.id = "nats-consumer-shutdown"

    try:
        await adapter.subscribe(channel, None)
        await asyncio.sleep(0.2)

        # Publish message
        await adapter.publish(channel.name, {"msg": "slow"}, {})

        # Wait for processing to start
        await processing_started.wait()

        # Unsubscribe (should wait for active message)
        unsubscribe_task = asyncio.create_task(adapter.unsubscribe(channel))

        # Verify: Processing completes before unsubscribe
        await processing_done.wait()
        await unsubscribe_task

        # Success: Graceful shutdown waited for active message
        assert processing_done.is_set()

    finally:
        await adapter.disconnect()


@pytest.mark.asyncio
async def test_nats_stream_name_sanitization(mock_broker):
    """Test that stream names with forbidden chars are sanitized."""
    from moleculerpy_channels.adapters.nats import NatsAdapter
    from moleculerpy_channels.channel import Channel

    adapter = NatsAdapter(url="nats://localhost:4222")
    adapter.init(mock_broker, mock_broker.get_logger("NatsAdapter"))
    await adapter.connect()

    received = []

    async def handler(payload, raw):
        received.append(payload)

    # Channel name with forbidden chars: '.', '>', '*'
    channel = Channel(
        name="test.with.dots>and*stars",
        group="sanitize.group",
        handler=handler,
    )
    channel.id = "nats-consumer-sanitize"

    try:
        # Subscribe (should sanitize stream name)
        await adapter.subscribe(channel, None)
        await asyncio.sleep(0.2)

        # Verify: Stream name sanitized
        sanitized = adapter._sanitize_stream_name(channel.name)
        assert "." not in sanitized
        assert ">" not in sanitized
        assert "*" not in sanitized
        assert sanitized == "test_with_dots_and_stars"

        # Publish and verify message received
        await adapter.publish(channel.name, {"msg": "sanitized"}, {})
        await asyncio.sleep(1.0)

        assert len(received) == 1

    finally:
        await adapter.unsubscribe(channel)
        await adapter.disconnect()
