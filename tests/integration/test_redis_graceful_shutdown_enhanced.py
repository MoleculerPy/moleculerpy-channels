"""
Enhanced graceful shutdown tests for RedisAdapter.

Tests verify the critical fixes applied in Phase 2:
1. Correct order: wait for active messages BEFORE cancelling background tasks
2. Timeout handling for hanging handlers
3. Backoff state cleanup
4. Multiple concurrent handlers
"""

import asyncio

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
async def redis_adapter_with_tracking(redis_client, mock_broker):
    """Create RedisAdapter with task tracking for verification."""
    from moleculerpy_channels.adapters.redis import RedisAdapter

    adapter = RedisAdapter(redis_url="redis://localhost:6380/0")
    adapter.init(mock_broker, mock_broker.get_logger("RedisAdapter"))

    await adapter.connect()

    # Track task states for assertions
    adapter._test_tracking = {
        "tasks_cancelled_before_wait": False,
        "wait_completed": False,
    }

    yield adapter

    await adapter.disconnect()


@pytest.mark.asyncio
async def test_unsubscribe_waits_for_active_messages_first(redis_adapter):
    """
    Verify that unsubscribe waits for active message handlers to complete
    BEFORE cancelling background tasks (critical race condition fix).
    """
    from moleculerpy_channels.channel import Channel

    handler_started = asyncio.Event()
    handler_can_finish = asyncio.Event()
    handler_completed = asyncio.Event()

    async def slow_handler(payload, raw):
        """Handler that takes 2 seconds to process."""
        handler_started.set()
        await handler_can_finish.wait()
        # Simulate work
        await asyncio.sleep(0.1)
        handler_completed.set()

    channel = Channel(
        name="test.wait.first",
        group="wait-group",
        handler=slow_handler,
    )
    channel.id = "consumer-wait"

    await redis_adapter.subscribe(channel, None)
    await asyncio.sleep(0.5)  # Let subscription settle

    # Publish message
    await redis_adapter.publish("test.wait.first", {"test": "data"}, {})

    # Wait for handler to start
    await asyncio.wait_for(handler_started.wait(), timeout=2.0)

    # Verify message is being processed
    active_count = await redis_adapter.get_number_of_channel_active_messages(channel.id)
    assert active_count == 1, "Handler should be actively processing"

    # Start unsubscribe (should wait for handler)
    unsubscribe_task = asyncio.create_task(redis_adapter.unsubscribe(channel))

    # Give unsubscribe time to check active messages
    await asyncio.sleep(0.3)

    # Verify unsubscribe is WAITING (not completed)
    assert not unsubscribe_task.done(), "Unsubscribe should wait for handler"

    # Verify handler is STILL running
    assert not handler_completed.is_set(), "Handler should still be processing"

    # Allow handler to complete
    handler_can_finish.set()

    # Wait for handler to finish
    await asyncio.wait_for(handler_completed.wait(), timeout=2.0)

    # Now unsubscribe should complete
    await asyncio.wait_for(unsubscribe_task, timeout=3.0)

    # Verify no active messages
    active_count_after = await redis_adapter.get_number_of_channel_active_messages(channel.id)
    assert active_count_after == 0, "No active messages after unsubscribe"


@pytest.mark.asyncio
async def test_unsubscribe_timeout_on_hanging_handler(redis_adapter):
    """
    Verify that unsubscribe respects timeout if handler hangs indefinitely.
    Should log warning but not crash.
    """
    from moleculerpy_channels.channel import Channel

    handler_started = asyncio.Event()

    async def hanging_handler(payload, raw):
        """Handler that never completes (simulates deadlock)."""
        handler_started.set()
        # Hang forever (no await can_finish)
        await asyncio.Event().wait()

    channel = Channel(
        name="test.timeout",
        group="timeout-group",
        handler=hanging_handler,
    )
    channel.id = "consumer-timeout"

    await redis_adapter.subscribe(channel, None)
    await asyncio.sleep(0.5)

    # Publish message
    await redis_adapter.publish("test.timeout", {"test": "hang"}, {})

    # Wait for handler to start hanging
    await asyncio.wait_for(handler_started.wait(), timeout=2.0)

    # Verify message is stuck in processing
    active_count = await redis_adapter.get_number_of_channel_active_messages(channel.id)
    assert active_count == 1

    # Unsubscribe should timeout after 30s (but we'll verify it starts waiting)
    # We don't wait full 30s in test, just verify behavior
    unsubscribe_task = asyncio.create_task(redis_adapter.unsubscribe(channel))

    # Give it 2 seconds to check (not full 30s timeout)
    await asyncio.sleep(2.0)

    # Verify unsubscribe is still waiting (hasn't completed)
    assert not unsubscribe_task.done(), "Unsubscribe should be waiting for timeout"

    # Cancel the unsubscribe task (cleanup for test)
    unsubscribe_task.cancel()
    try:
        await unsubscribe_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_multiple_active_handlers_all_complete(redis_adapter, redis_client):
    """
    Verify unsubscribe waits for ALL active handlers to complete,
    not just the first one.
    """
    from moleculerpy_channels.channel import Channel

    handlers_started = []
    handlers_can_finish = []
    handlers_completed = []

    # Create 3 independent handlers
    for i in range(3):
        handlers_started.append(asyncio.Event())
        handlers_can_finish.append(asyncio.Event())
        handlers_completed.append(asyncio.Event())

    call_count = 0

    async def multi_handler(payload, raw):
        """Handler tracks which invocation it is."""
        nonlocal call_count
        idx = call_count
        call_count += 1

        if idx < 3:
            handlers_started[idx].set()
            await handlers_can_finish[idx].wait()
            handlers_completed[idx].set()

    channel = Channel(
        name="test.multi",
        group="multi-group",
        handler=multi_handler,
    )
    channel.id = "consumer-multi"

    await redis_adapter.subscribe(channel, None)
    # Give XREADGROUP loop time to start and enter blocking read
    await asyncio.sleep(1.5)

    # Publish 3 messages
    for i in range(3):
        await redis_adapter.publish("test.multi", {"index": i}, {})

    # Wait for all 3 handlers to start
    await asyncio.wait_for(asyncio.gather(*[e.wait() for e in handlers_started]), timeout=3.0)

    # Verify 3 active messages
    active_count = await redis_adapter.get_number_of_channel_active_messages(channel.id)
    assert active_count == 3, "All 3 handlers should be active"

    # Start unsubscribe
    unsubscribe_task = asyncio.create_task(redis_adapter.unsubscribe(channel))

    await asyncio.sleep(0.3)

    # Should be waiting for ALL handlers
    assert not unsubscribe_task.done()

    # Complete handlers one by one
    for i in range(3):
        handlers_can_finish[i].set()
        await asyncio.wait_for(handlers_completed[i].wait(), timeout=2.0)
        await asyncio.sleep(0.1)  # Let cleanup happen

    # Now unsubscribe should complete
    await asyncio.wait_for(unsubscribe_task, timeout=3.0)

    # Verify no active messages
    final_count = await redis_adapter.get_number_of_channel_active_messages(channel.id)
    assert final_count == 0


@pytest.mark.asyncio
async def test_backoff_state_cleaned_on_unsubscribe(redis_adapter):
    """
    Verify that backoff state is properly cleaned up on unsubscribe.
    """
    from moleculerpy_channels.channel import Channel

    async def simple_handler(payload, raw):
        pass

    channel = Channel(
        name="test.backoff.cleanup",
        group="backoff-group",
        handler=simple_handler,
    )
    channel.id = "consumer-backoff"

    await redis_adapter.subscribe(channel, None)
    await asyncio.sleep(0.5)

    # Simulate backoff state (would normally happen on errors)
    redis_adapter._backoff_state[channel.id] = 5.0
    redis_adapter._backoff_state[f"{channel.id}-xclaim"] = 10.0
    redis_adapter._backoff_state[f"{channel.id}-dlq"] = 15.0

    # Verify backoff state exists
    assert channel.id in redis_adapter._backoff_state
    assert f"{channel.id}-xclaim" in redis_adapter._backoff_state
    assert f"{channel.id}-dlq" in redis_adapter._backoff_state

    # Unsubscribe
    await redis_adapter.unsubscribe(channel)

    # Verify backoff state is cleaned up
    assert channel.id not in redis_adapter._backoff_state, "Main backoff should be cleaned"
    assert f"{channel.id}-xclaim" not in redis_adapter._backoff_state, (
        "xclaim backoff should be cleaned"
    )
    assert f"{channel.id}-dlq" not in redis_adapter._backoff_state, "dlq backoff should be cleaned"


@pytest.mark.asyncio
async def test_background_tasks_stop_gracefully(redis_adapter):
    """
    Verify that background tasks (xreadgroup, xclaim, dlq) stop gracefully
    when channel.unsubscribing is set, without being forcefully cancelled
    while processing messages.
    """
    from moleculerpy_channels.channel import Channel, DeadLetteringOptions, RedisOptions

    message_received = asyncio.Event()

    async def handler(payload, raw):
        message_received.set()

    channel = Channel(
        name="test.tasks.graceful",
        group="tasks-group",
        handler=handler,
        redis=RedisOptions(
            claim_interval=100,  # Fast claim interval
            min_idle_time=1000,
        ),
        dead_lettering=DeadLetteringOptions(
            enabled=True,
            queue_name="DLQ_TASKS",
        ),
    )
    channel.id = "consumer-tasks"

    await redis_adapter.subscribe(channel, None)
    await asyncio.sleep(0.5)

    # Verify 3 background tasks started
    assert channel.id in redis_adapter._background_tasks
    tasks = redis_adapter._background_tasks[channel.id]
    assert len(tasks) == 3, "Should have 3 background tasks (xreadgroup, xclaim, dlq)"

    # Publish a message to trigger xreadgroup
    await redis_adapter.publish("test.tasks.graceful", {"test": "stop"}, {})

    # Wait for message processing
    await asyncio.wait_for(message_received.wait(), timeout=2.0)

    # Unsubscribe (background tasks should stop gracefully)
    await redis_adapter.unsubscribe(channel)

    # Verify tasks are cleaned up
    assert channel.id not in redis_adapter._background_tasks


@pytest.mark.asyncio
async def test_unsubscribe_with_no_active_messages(redis_adapter):
    """
    Verify unsubscribe completes quickly when there are no active messages
    (regression test - should not hang waiting).
    """
    from moleculerpy_channels.channel import Channel

    async def handler(payload, raw):
        pass

    channel = Channel(
        name="test.no.active",
        group="no-active-group",
        handler=handler,
    )
    channel.id = "consumer-no-active"

    await redis_adapter.subscribe(channel, None)
    await asyncio.sleep(0.5)

    # No messages published - no active messages

    # Unsubscribe should complete immediately (within 1s)
    await asyncio.wait_for(redis_adapter.unsubscribe(channel), timeout=1.0)

    # Success if no timeout
    assert True
