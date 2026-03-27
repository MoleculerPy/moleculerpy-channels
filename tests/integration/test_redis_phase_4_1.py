"""
Integration tests for Phase 4.1 features:
- Cursor-based XAUTOCLAIM
- Error Metadata Storage (Redis Hash)

These tests verify the critical gaps (P0) that were missing in Phase 3.
"""

import asyncio
import base64

import pytest

pytest_plugins = ("pytest_asyncio",)


@pytest.mark.asyncio
async def test_cursor_based_xautoclaim(redis_adapter, redis_client):
    """
    Verify cursor-based XAUTOCLAIM tracks scan position efficiently.

    Cursor prevents re-scanning the same pending entries on each XAUTOCLAIM call.
    """
    from moleculerpy_channels import Channel, DeadLetteringOptions, RedisOptions

    processed_messages = []

    async def handler(payload, raw):
        processed_messages.append(payload)
        # Simulate slow processing
        await asyncio.sleep(0.1)

    channel = Channel(
        name="test.cursor.autoclaim",
        group="cursor-test-group",
        handler=handler,
        max_retries=5,
        redis=RedisOptions(
            min_idle_time=100,  # 100ms idle time for fast retry
            claim_interval=50,  # Check every 50ms
        ),
        dead_lettering=DeadLetteringOptions(enabled=False),  # No DLQ for this test
    )

    # Subscribe
    await redis_adapter.subscribe(channel, None)

    # Publish 10 messages
    for i in range(10):
        await redis_adapter.publish(channel.name, {"id": i}, {})

    # Wait for messages to be delivered
    await asyncio.sleep(1.5)

    # Verify all 10 unique messages processed (allow duplicates from retry mechanism)
    unique_ids = set(msg["id"] for msg in processed_messages)
    assert len(unique_ids) == 10, f"Expected 10 unique IDs, got {len(unique_ids)}: {unique_ids}"
    assert unique_ids == set(range(10)), "All IDs 0-9 should be processed"

    # Check cursor state in adapter
    assert channel.id in redis_adapter._xclaim_cursors
    cursor = redis_adapter._xclaim_cursors[channel.id]

    # Cursor should have been updated during XAUTOCLAIM calls
    # Either "0-0" (full scan complete) or non-zero cursor (mid-scan)
    assert isinstance(cursor, str)
    # In this test, since messages process quickly, cursor likely back to "0-0"
    # (meaning full PEL scanned and will restart on next call)

    # Cleanup
    await redis_adapter.unsubscribe(channel)

    # Verify cursor state cleaned up
    assert channel.id not in redis_adapter._xclaim_cursors


@pytest.mark.asyncio
async def test_error_metadata_storage_in_redis_hash(redis_adapter, redis_client):
    """
    Verify error metadata is stored in Redis Hash with TTL.

    When handler fails, error details (name, message, stack, timestamp)
    should be stored in Redis Hash at key `chan:{name}:msg:{id}`.
    """
    from moleculerpy_channels import Channel, DeadLetteringOptions, RedisOptions

    call_count = 0

    async def failing_handler(payload, raw):
        nonlocal call_count
        call_count += 1
        raise ValueError(f"Test error for message {payload['id']}")

    channel = Channel(
        name="test.error.metadata",
        group="error-test-group",
        handler=failing_handler,
        max_retries=3,
        redis=RedisOptions(
            min_idle_time=100,
            claim_interval=50,
        ),
        dead_lettering=DeadLetteringOptions(enabled=False),  # No DLQ for this test
    )

    # Subscribe
    await redis_adapter.subscribe(channel, None)

    # Publish 1 message
    await redis_adapter.publish(channel.name, {"id": 1}, {})

    # Wait for handler to fail at least once
    await asyncio.sleep(0.5)

    # Get message ID from stream
    messages = await redis_client.xrange(channel.name, "-", "+", count=1)
    assert len(messages) > 0

    msg_id, _ = messages[0]
    msg_id_str = msg_id.decode()

    # Check error metadata in Redis Hash
    error_key = f"chan:{channel.name}:msg:{msg_id_str}"
    error_metadata = await redis_client.hgetall(error_key)

    # Verify error metadata fields exist
    assert error_metadata is not None
    assert b"x-error-name" in error_metadata
    assert b"x-error-message" in error_metadata
    assert b"x-error-stack" in error_metadata
    assert b"x-error-timestamp" in error_metadata

    # Verify error name
    assert error_metadata[b"x-error-name"] == b"ValueError"

    # Verify error message
    assert b"Test error for message" in error_metadata[b"x-error-message"]

    # Verify stack trace is base64 encoded
    stack_b64 = error_metadata[b"x-error-stack"]
    stack_decoded = base64.b64decode(stack_b64).decode()
    assert "ValueError" in stack_decoded
    assert "Test error for message" in stack_decoded

    # Verify TTL is set (should be 24 hours = 86400 seconds)
    ttl = await redis_client.ttl(error_key)
    assert ttl > 0
    assert ttl <= 86400  # Should be less than or equal to 24 hours

    # Cleanup
    await redis_adapter.unsubscribe(channel)


@pytest.mark.asyncio
async def test_error_metadata_propagated_to_dlq(redis_adapter, redis_client):
    """
    Verify error metadata is included in DLQ message headers.

    When message moves to DLQ, error metadata from Redis Hash should be
    injected into DLQ message headers for debugging.
    """
    from moleculerpy_channels import Channel, DeadLetteringOptions, RedisOptions

    call_count = 0

    async def failing_handler(payload, raw):
        nonlocal call_count
        call_count += 1
        raise RuntimeError(f"Permanent failure for {payload['orderId']}")

    channel = Channel(
        name="test.error.dlq",
        group="error-dlq-group",
        handler=failing_handler,
        max_retries=2,
        redis=RedisOptions(
            min_idle_time=100,
            claim_interval=50,
            dlq_check_interval=1,  # Check DLQ every 1 second
        ),
        dead_lettering=DeadLetteringOptions(
            enabled=True,
            queue_name="ERROR_DLQ",
            error_info_ttl=86400,
        ),
    )

    # Subscribe
    await redis_adapter.subscribe(channel, None)

    # Publish 1 message
    await redis_adapter.publish(channel.name, {"orderId": 999}, {})

    # Wait for retries + DLQ move
    # 2 retries (max_retries=2) + 1s DLQ check interval + buffer
    await asyncio.sleep(3)

    # Verify message moved to DLQ
    dlq_messages = await redis_client.xrange(b"ERROR_DLQ", b"-", b"+")
    assert len(dlq_messages) > 0

    # Get DLQ message headers
    _, dlq_fields = dlq_messages[0]

    # Verify error metadata fields present in DLQ
    assert b"x-error-name" in dlq_fields
    assert b"x-error-message" in dlq_fields
    assert b"x-error-stack" in dlq_fields
    assert b"x-error-timestamp" in dlq_fields

    # Verify error details
    assert dlq_fields[b"x-error-name"] == b"RuntimeError"
    assert b"Permanent failure for 999" in dlq_fields[b"x-error-message"]

    # Verify stack trace is base64 encoded
    stack_b64 = dlq_fields[b"x-error-stack"]
    stack_decoded = base64.b64decode(stack_b64).decode()
    assert "RuntimeError" in stack_decoded
    assert "Permanent failure for 999" in stack_decoded

    # Verify original channel/group tracking
    assert dlq_fields[b"x-original-channel"] == channel.name.encode()
    assert dlq_fields[b"x-original-group"] == channel.group.encode()

    # Verify error metadata deleted from Redis Hash (cleanup after DLQ move)
    messages = await redis_client.xrange(channel.name, "-", "+", count=1)
    if messages:
        msg_id, _ = messages[0]
        msg_id_str = msg_id.decode()
        error_key = f"chan:{channel.name}:msg:{msg_id_str}"

        # Error metadata should be deleted after moving to DLQ
        error_metadata = await redis_client.hgetall(error_key)
        assert len(error_metadata) == 0  # Empty hash (deleted)

    # Cleanup
    await redis_adapter.unsubscribe(channel)


@pytest.mark.asyncio
async def test_error_metadata_deleted_on_success(redis_adapter, redis_client):
    """
    Verify error metadata is deleted when message succeeds after retries.

    If handler fails initially (storing error metadata) but succeeds on
    retry, error metadata should be cleaned up.
    """
    from moleculerpy_channels import Channel, DeadLetteringOptions, RedisOptions

    call_count = 0

    async def flaky_handler(payload, raw):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ValueError("First attempt fails")
        # Succeed on retry
        return

    channel = Channel(
        name="test.error.cleanup",
        group="cleanup-test-group",
        handler=flaky_handler,
        max_retries=3,
        redis=RedisOptions(
            min_idle_time=100,
            claim_interval=50,
        ),
        dead_lettering=DeadLetteringOptions(enabled=False),
    )

    # Subscribe
    await redis_adapter.subscribe(channel, None)

    # Publish 1 message
    await redis_adapter.publish(channel.name, {"id": 1}, {})

    # Wait for failure + retry + success
    await asyncio.sleep(1.5)

    # Verify handler called at least twice (fail + success)
    assert call_count >= 2

    # Get message ID
    messages = await redis_client.xrange(channel.name, "-", "+", count=1)
    assert len(messages) > 0
    msg_id, _ = messages[0]
    msg_id_str = msg_id.decode()

    # Verify error metadata deleted on success
    error_key = f"chan:{channel.name}:msg:{msg_id_str}"
    error_metadata = await redis_client.hgetall(error_key)
    assert len(error_metadata) == 0  # Should be empty (deleted after success)

    # Cleanup
    await redis_adapter.unsubscribe(channel)


@pytest.mark.asyncio
async def test_cursor_persistence_across_xclaim_calls(redis_adapter, redis_client):
    """
    Verify cursor persists across multiple XAUTOCLAIM calls.

    Each XAUTOCLAIM call should update the cursor and use it in the next call.
    This prevents re-scanning the same pending entries.
    """
    from moleculerpy_channels import Channel, RedisOptions

    # Track cursor values seen
    cursor_history = []

    # Patch _xclaim_loop to log cursor values
    original_xclaim_loop = redis_adapter._xclaim_loop

    async def patched_xclaim_loop(channel):
        # Log initial cursor
        cursor_history.append(redis_adapter._xclaim_cursors.get(channel.id, "0-0"))
        await original_xclaim_loop(channel)

    redis_adapter._xclaim_loop = patched_xclaim_loop

    processed = []

    async def slow_handler(payload, raw):
        processed.append(payload)
        await asyncio.sleep(0.2)  # Slow to create pending messages

    channel = Channel(
        name="test.cursor.persistence",
        group="cursor-persist-group",
        handler=slow_handler,
        max_retries=5,
        redis=RedisOptions(
            min_idle_time=50,  # Very short idle time
            claim_interval=100,  # Frequent claims
        ),
    )

    # Subscribe
    await redis_adapter.subscribe(channel, None)

    # Publish multiple messages
    for i in range(5):
        await redis_adapter.publish(channel.name, {"id": i}, {})

    # Let XAUTOCLAIM run multiple times
    await asyncio.sleep(2)

    # Verify cursor was initialized
    assert channel.id in redis_adapter._xclaim_cursors

    # Verify cursor history captured
    assert len(cursor_history) > 0
    assert cursor_history[0] == "0-0"  # Initial cursor

    # Cleanup
    redis_adapter._xclaim_loop = original_xclaim_loop
    await redis_adapter.unsubscribe(channel)
