"""
Integration tests for Dead Letter Queue and Retry mechanisms.

Tests verify:
1. Retry on handler failures (no ACK → XAUTOCLAIM → retry)
2. DLQ after max_retries exceeded
3. Original metadata preservation in DLQ
4. Successful retry after transient errors
5. Independent DLQ handling for multiple messages
"""

import asyncio

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_retry_on_handler_failure(redis_adapter, redis_client):
    """
    Verify that failed messages are retried via XAUTOCLAIM.

    Flow:
    1. Handler throws exception → no ACK
    2. Message stays pending
    3. XAUTOCLAIM claims message after min_idle_time
    4. Handler called again
    """
    from moleculerpy_channels.channel import Channel, RedisOptions

    call_count = 0
    first_attempt = asyncio.Event()
    second_attempt = asyncio.Event()

    async def flaky_handler(payload, raw):
        """Handler that fails first time, succeeds second."""
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            first_attempt.set()
            raise ValueError("Simulated transient error")
        else:
            second_attempt.set()

    channel = Channel(
        name="test.retry",
        group="retry-group",
        handler=flaky_handler,
        max_retries=3,
        redis=RedisOptions(
            min_idle_time=100,  # 100ms (fast retry for testing)
            claim_interval=50,   # Check every 50ms
        ),
    )
    channel.id = "consumer-retry"

    await redis_adapter.subscribe(channel, None)
    await asyncio.sleep(0.5)  # Let loops start

    # Publish message
    await redis_adapter.publish("test.retry", {"test": "retry"}, {})

    # Wait for first attempt
    await asyncio.wait_for(first_attempt.wait(), timeout=2.0)

    # Verify message NOT acked (still pending)
    pending = await redis_client.xpending(
        name="test.retry",
        groupname="retry-group",
    )
    assert pending["pending"] == 1, "Message should be pending after failure"

    # Wait for retry (XAUTOCLAIM will claim after 100ms + processing time)
    await asyncio.wait_for(second_attempt.wait(), timeout=3.0)

    # Verify message now acked (not pending anymore)
    await asyncio.sleep(0.3)  # Let ACK propagate
    pending_after = await redis_client.xpending(
        name="test.retry",
        groupname="retry-group",
    )
    assert pending_after["pending"] == 0, "Message should be acked after success"

    await redis_adapter.unsubscribe(channel)


@pytest.mark.asyncio
async def test_max_retries_exceeded_moves_to_dlq(redis_adapter, redis_client):
    """
    Verify that messages exceeding max_retries are moved to DLQ.

    Flow:
    1. Handler always throws → no ACK
    2. XAUTOCLAIM retries message multiple times
    3. After max_retries (3) → DLQ loop detects via times_delivered
    4. Message moved to DLQ stream
    """
    from moleculerpy_channels.channel import Channel, DeadLetteringOptions, RedisOptions

    call_count = 0
    attempts = []

    async def always_failing_handler(payload, raw):
        """Handler that always fails."""
        nonlocal call_count
        call_count += 1
        attempts.append(call_count)
        raise RuntimeError(f"Permanent failure (attempt {call_count})")

    channel = Channel(
        name="test.dlq",
        group="dlq-group",
        handler=always_failing_handler,
        max_retries=3,
        redis=RedisOptions(
            min_idle_time=50,         # Fast retry (50ms)
            claim_interval=30,        # Check every 30ms
            dlq_check_interval=1,     # Check DLQ every 1 second (like moleculer-channels)
        ),
        dead_lettering=DeadLetteringOptions(
            enabled=True,
            queue_name="DLQ_TEST",
        ),
    )
    channel.id = "consumer-dlq"

    await redis_adapter.subscribe(channel, None)
    await asyncio.sleep(0.5)  # Let loops start

    # Publish message
    msg_id = await redis_adapter.publish("test.dlq", {"test": "dlq"}, {})

    # Wait for retries + DLQ to happen
    # With 50ms min_idle, 30ms claim_interval, and 1s DLQ check,
    # should take ~2-3 seconds total
    await asyncio.sleep(3.0)

    # Verify handler was called multiple times (retries happened)
    assert call_count >= 3, f"Handler should be called at least 3 times, got {call_count}"

    # Verify message moved to DLQ
    dlq_messages = await redis_client.xrange("DLQ_TEST", "-", "+", count=10)
    assert len(dlq_messages) > 0, "Message should be in DLQ"

    # Verify original message acked (not pending anymore)
    pending_after = await redis_client.xpending(
        name="test.dlq",
        groupname="dlq-group",
    )
    assert pending_after["pending"] == 0, "Original message should be acked after DLQ move"

    await redis_adapter.unsubscribe(channel)


@pytest.mark.asyncio
async def test_successful_retry_after_transient_error(redis_adapter, redis_client):
    """
    Verify successful processing after transient error recovery.

    Simulates network flake or temporary resource unavailability.
    """
    from moleculerpy_channels.channel import Channel, RedisOptions

    attempts = []
    success_event = asyncio.Event()

    async def recovering_handler(payload, raw):
        """Handler that recovers after 2 failures."""
        attempts.append(True)

        if len(attempts) < 3:
            raise ConnectionError(f"Transient error (attempt {len(attempts)})")

        # Success on 3rd attempt
        success_event.set()

    channel = Channel(
        name="test.recovery",
        group="recovery-group",
        handler=recovering_handler,
        max_retries=5,
        redis=RedisOptions(
            min_idle_time=100,
            claim_interval=50,
        ),
    )
    channel.id = "consumer-recovery"

    await redis_adapter.subscribe(channel, None)
    await asyncio.sleep(0.5)

    # Publish message
    await redis_adapter.publish("test.recovery", {"data": "test"}, {})

    # Wait for successful processing
    await asyncio.wait_for(success_event.wait(), timeout=3.0)

    # Verify exactly 3 attempts
    assert len(attempts) == 3, f"Expected 3 attempts, got {len(attempts)}"

    # Verify message acked (not pending)
    await asyncio.sleep(0.3)
    pending = await redis_client.xpending("test.recovery", "recovery-group")
    assert pending["pending"] == 0, "Message should be acked after success"

    await redis_adapter.unsubscribe(channel)


@pytest.mark.asyncio
async def test_dlq_preserves_original_metadata(redis_adapter, redis_client):
    """
    Verify that DLQ messages include original channel/group headers.
    """
    from moleculerpy_channels.channel import Channel, DeadLetteringOptions, RedisOptions
    from moleculerpy_channels.constants import HEADER_ORIGINAL_CHANNEL, HEADER_ORIGINAL_GROUP

    async def failing_handler(payload, raw):
        """Handler that always fails."""
        raise ValueError("Metadata test failure")

    channel = Channel(
        name="test.metadata",
        group="metadata-group",
        handler=failing_handler,
        max_retries=3,
        redis=RedisOptions(
            min_idle_time=50,
            claim_interval=30,
            dlq_check_interval=1,  # Fast DLQ check
        ),
        dead_lettering=DeadLetteringOptions(
            enabled=True,
            queue_name="DLQ_METADATA",
        ),
    )
    channel.id = "consumer-metadata"

    await redis_adapter.subscribe(channel, None)
    await asyncio.sleep(0.5)

    # Publish message with headers
    await redis_adapter.publish(
        "test.metadata",
        {"test": "metadata"},
        {"headers": {"custom-header": "custom-value"}},
    )

    # Wait for retries + DLQ
    await asyncio.sleep(3.0)

    # Verify DLQ message has metadata
    dlq_messages = await redis_client.xrange("DLQ_METADATA", "-", "+", count=10)
    assert len(dlq_messages) > 0, "Should have message in DLQ"

    # Parse DLQ message headers
    msg_id, fields = dlq_messages[0]
    headers_bytes = fields.get(b"headers")

    if headers_bytes:
        import json

        headers = json.loads(headers_bytes.decode())

        # Verify original metadata preserved
        assert (
            HEADER_ORIGINAL_CHANNEL in headers
        ), f"DLQ should have {HEADER_ORIGINAL_CHANNEL} header"
        assert headers[HEADER_ORIGINAL_CHANNEL] == "test.metadata"

        assert HEADER_ORIGINAL_GROUP in headers, f"DLQ should have {HEADER_ORIGINAL_GROUP} header"
        assert headers[HEADER_ORIGINAL_GROUP] == "metadata-group"

        # Verify custom headers preserved
        assert "custom-header" in headers, "Custom headers should be preserved"
        assert headers["custom-header"] == "custom-value"

    await redis_adapter.unsubscribe(channel)


@pytest.mark.asyncio
async def test_multiple_messages_independent_retry(redis_adapter, redis_client):
    """
    Verify that multiple messages are retried independently.

    One message succeeds, another keeps failing.
    """
    from moleculerpy_channels.channel import Channel, RedisOptions

    message_attempts = {}  # msg_id -> attempt_count
    success_events = {}    # msg_id -> Event

    async def selective_handler(payload, raw):
        """Handler that succeeds for msg1, fails for msg2."""
        msg_id = payload.get("id")

        if msg_id not in message_attempts:
            message_attempts[msg_id] = 0
            success_events[msg_id] = asyncio.Event()

        message_attempts[msg_id] += 1

        if msg_id == "msg1":
            # Success immediately
            success_events[msg_id].set()
        elif msg_id == "msg2":
            # Always fail
            raise ValueError(f"msg2 permanent failure (attempt {message_attempts[msg_id]})")

    channel = Channel(
        name="test.multi",
        group="multi-group",
        handler=selective_handler,
        max_retries=3,
        redis=RedisOptions(
            min_idle_time=100,
            claim_interval=50,
        ),
    )
    channel.id = "consumer-multi"

    await redis_adapter.subscribe(channel, None)
    await asyncio.sleep(0.5)

    # Publish 2 messages
    await redis_adapter.publish("test.multi", {"id": "msg1"}, {})
    await redis_adapter.publish("test.multi", {"id": "msg2"}, {})

    # Wait for msg1 success
    await asyncio.wait_for(success_events["msg1"].wait(), timeout=2.0)

    # Give time for msg2 retries
    await asyncio.sleep(1.5)

    # Verify msg1 processed once, msg2 retried multiple times
    assert message_attempts["msg1"] == 1, "msg1 should succeed on first attempt"
    assert message_attempts["msg2"] >= 2, f"msg2 should be retried, got {message_attempts['msg2']} attempts"

    # Verify msg1 acked, msg2 still pending
    await asyncio.sleep(0.3)
    pending = await redis_client.xpending_range(
        name="test.multi",
        groupname="multi-group",
        min="-",
        max="+",
        count=10,
    )

    # Should have 1 pending message (msg2)
    assert len(pending) == 1, f"Expected 1 pending message, got {len(pending)}"

    await redis_adapter.unsubscribe(channel)
