"""Basic integration tests for RedisAdapter."""

import asyncio

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_redis_connect_disconnect(mock_broker):
    """Test Redis connection lifecycle."""
    from moleculerpy_channels.adapters.redis import RedisAdapter

    adapter = RedisAdapter(redis_url="redis://localhost:6380/0")
    adapter.init(mock_broker, mock_broker.get_logger("RedisAdapter"))

    # Connect
    await adapter.connect()
    assert adapter._connected is True
    assert adapter.redis is not None

    # Disconnect
    await adapter.disconnect()
    assert adapter._connected is False


@pytest.mark.asyncio
async def test_redis_publish(redis_adapter, redis_client):
    """Test publishing message to Redis Stream."""
    channel_name = "test.channel"
    payload = {"msg": "hello", "timestamp": 1234567890}

    # Publish
    message_id = await redis_adapter.publish(channel_name, payload, {})

    assert message_id is not None
    assert "-" in message_id  # Redis message ID format: timestamp-sequence

    # Verify message in Redis
    messages = await redis_client.xrange(channel_name.encode(), b"-", b"+")
    assert len(messages) == 1

    msg_id, fields = messages[0]
    assert b"payload" in fields


@pytest.mark.asyncio
async def test_redis_publish_with_headers(redis_adapter, redis_client):
    """Test publishing with custom headers."""
    channel_name = "test.headers"
    payload = {"data": "test"}
    headers = {"userId": "user-123", "traceId": "trace-456"}

    # Publish with headers
    message_id = await redis_adapter.publish(channel_name, payload, {"headers": headers})

    # Verify headers in Redis
    messages = await redis_client.xrange(channel_name.encode(), b"-", b"+")
    _, fields = messages[0]

    assert fields[b"userId"] == b"user-123"
    assert fields[b"traceId"] == b"trace-456"


@pytest.mark.asyncio
async def test_redis_publish_with_maxlen(redis_adapter, redis_client):
    """Test publishing with stream capping (maxlen)."""
    channel_name = "test.capped"

    # Publish 20 messages with maxlen=10
    for i in range(20):
        await redis_adapter.publish(
            channel_name, {"index": i}, {"xaddMaxLen": 10}
        )

    # Verify stream is capped at ~10 messages
    messages = await redis_client.xrange(channel_name, "-", "+")
    assert len(messages) <= 12  # Approximate trimming allows some overhead


@pytest.mark.asyncio
async def test_redis_subscribe_and_consume(redis_adapter, redis_client):
    """Test subscribing to channel and consuming messages."""
    from moleculerpy_channels.channel import Channel

    received = []

    async def handler(payload, raw):
        received.append(payload)

    # Create channel
    channel = Channel(
        name="test.subscribe",
        group="test-group",
        handler=handler,
    )
    channel.id = "consumer-1"

    # Subscribe
    await redis_adapter.subscribe(channel, None)

    # Give subscription time to start
    await asyncio.sleep(0.5)

    # Publish messages
    await redis_adapter.publish("test.subscribe", {"msg": "first"}, {})
    await redis_adapter.publish("test.subscribe", {"msg": "second"}, {})
    await redis_adapter.publish("test.subscribe", {"msg": "third"}, {})

    # Wait for processing
    await asyncio.sleep(2)

    # Verify messages received
    assert len(received) >= 2  # At least 2 messages processed

    # Unsubscribe
    await redis_adapter.unsubscribe(channel)


@pytest.mark.asyncio
async def test_redis_consumer_group_created(redis_adapter, redis_client):
    """Test that consumer group is created automatically."""
    from moleculerpy_channels.channel import Channel

    channel = Channel(
        name="test.group",
        group="my-consumer-group",
        handler=lambda p, r: None,
    )
    channel.id = "consumer-1"

    # Subscribe (should create group)
    await redis_adapter.subscribe(channel, None)

    # Give time for group creation
    await asyncio.sleep(0.2)

    # Verify group exists
    groups = await redis_client.xinfo_groups(channel.name)
    group_names = [g["name"].decode() if isinstance(g["name"], bytes) else g["name"] for g in groups]

    assert "my-consumer-group" in group_names

    await redis_adapter.unsubscribe(channel)


@pytest.mark.asyncio
async def test_redis_parse_message_headers(redis_adapter):
    """Test parsing headers from Redis message."""
    # Simulate Redis message format
    msg_id = b"1234567890-0"
    fields = {
        b"payload": b'{"data":"test"}',
        b"userId": b"user-123",
        b"requestId": b"req-456",
    }

    raw_message = (msg_id, fields)

    # Parse headers
    headers = redis_adapter.parse_message_headers(raw_message)

    assert headers is not None
    assert headers["userId"] == "user-123"
    assert headers["requestId"] == "req-456"
    assert "payload" not in headers  # payload excluded from headers


@pytest.mark.asyncio
async def test_redis_graceful_shutdown(redis_adapter):
    """Test graceful shutdown waits for active messages."""
    from moleculerpy_channels.channel import Channel

    processing_started = asyncio.Event()
    can_finish = asyncio.Event()

    async def slow_handler(payload, raw):
        processing_started.set()
        await can_finish.wait()

    channel = Channel(
        name="test.shutdown",
        group="test-group",
        handler=slow_handler,
    )
    channel.id = "consumer-1"

    await redis_adapter.subscribe(channel, None)
    await asyncio.sleep(0.2)

    # Publish message
    publish_task = asyncio.create_task(
        redis_adapter.publish("test.shutdown", {"msg": "slow"}, {})
    )
    await publish_task

    # Wait for processing to start
    await asyncio.wait_for(processing_started.wait(), timeout=2.0)

    # Start unsubscribe (should wait for handler to finish)
    unsubscribe_task = asyncio.create_task(redis_adapter.unsubscribe(channel))

    # Give unsubscribe time to start waiting
    await asyncio.sleep(0.5)

    # Verify unsubscribe is waiting (not completed yet)
    assert not unsubscribe_task.done()

    # Allow handler to finish
    can_finish.set()

    # Now unsubscribe should complete
    await asyncio.wait_for(unsubscribe_task, timeout=5.0)
