"""
Unit tests for BaseAdapter abstract class.

Tests:
- Abstract method enforcement
- Active message tracking (init/add/remove/stop)
- Graceful shutdown helpers (wait_for_channel_active_messages)
- Error transformation (error ↔ headers)
- Thread-safety (_lock usage)
"""

import asyncio

import pytest

from moleculerpy_channels.adapters.base import BaseAdapter
from moleculerpy_channels.errors import AdapterError

# ── Test Adapter Implementation ───────────────────────────────────────────


class TestAdapter(BaseAdapter):
    """Concrete test adapter for testing base functionality."""

    __test__ = False

    async def connect(self):
        self._connected = True

    async def disconnect(self):
        self._connected = False

    async def subscribe(self, channel, service):
        pass

    async def unsubscribe(self, channel):
        pass

    async def publish(self, channel_name, payload, opts):
        pass

    def parse_message_headers(self, raw_message):
        return {}


# ── Abstract Method Enforcement ───────────────────────────────────────────


def test_base_adapter_cannot_be_instantiated():
    """BaseAdapter is abstract and cannot be instantiated directly."""
    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        BaseAdapter()


def test_base_adapter_requires_all_abstract_methods():
    """Subclass must implement all 5 abstract methods."""

    # Missing connect()
    class IncompleteAdapter1(BaseAdapter):
        async def disconnect(self):
            pass

        async def subscribe(self, channel, service):
            pass

        async def unsubscribe(self, channel):
            pass

        async def publish(self, channel_name, payload, opts):
            pass

        def parse_message_headers(self, raw_message):
            pass

    with pytest.raises(TypeError):
        IncompleteAdapter1()

    # Missing parse_message_headers()
    class IncompleteAdapter2(BaseAdapter):
        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def subscribe(self, channel, service):
            pass

        async def unsubscribe(self, channel):
            pass

        async def publish(self, channel_name, payload, opts):
            pass

    with pytest.raises(TypeError):
        IncompleteAdapter2()


def test_concrete_adapter_can_be_instantiated():
    """Concrete adapter with all methods can be instantiated."""
    adapter = TestAdapter()
    assert adapter is not None
    assert isinstance(adapter, BaseAdapter)


# ── Initialization ────────────────────────────────────────────────────────


def test_init_sets_default_state():
    """__init__() sets broker, logger, serializer to None."""
    adapter = TestAdapter()

    assert adapter.broker is None
    assert adapter.logger is None
    assert adapter.serializer is None
    assert adapter._active_messages == {}


def test_init_method_stores_broker_and_logger():
    """init() stores broker and logger references."""
    adapter = TestAdapter()

    class MockBroker:
        serializer = {"type": "json"}
        node_id = "test-node"

    class MockLogger:
        def info(self, msg):
            pass

    broker = MockBroker()
    logger = MockLogger()

    adapter.init(broker, logger)

    assert adapter.broker is broker
    assert adapter.logger is logger
    assert adapter.serializer == {"type": "json"}


# ── Active Message Tracking ───────────────────────────────────────────────


def test_init_channel_active_messages():
    """init_channel_active_messages() creates empty set."""
    adapter = TestAdapter()

    adapter.init_channel_active_messages("channel-1")

    assert "channel-1" in adapter._active_messages
    assert adapter._active_messages["channel-1"] == set()


def test_init_channel_active_messages_duplicate_throws():
    """init_channel_active_messages() throws on duplicate by default."""
    adapter = TestAdapter()

    adapter.init_channel_active_messages("channel-1")

    with pytest.raises(AdapterError, match="Already tracking"):
        adapter.init_channel_active_messages("channel-1")


def test_init_channel_active_messages_duplicate_no_throw():
    """init_channel_active_messages() with to_throw=False doesn't raise."""
    adapter = TestAdapter()

    adapter.init_channel_active_messages("channel-1")
    adapter.init_channel_active_messages("channel-1", to_throw=False)  # Should not raise

    # Still only one set
    assert len(adapter._active_messages) == 1


@pytest.mark.asyncio
async def test_add_channel_active_messages():
    """add_channel_active_messages() adds message IDs to set."""
    adapter = TestAdapter()
    adapter.init_channel_active_messages("channel-1")

    await adapter.add_channel_active_messages("channel-1", ["msg-1", "msg-2"])

    assert adapter._active_messages["channel-1"] == {"msg-1", "msg-2"}


@pytest.mark.asyncio
async def test_add_channel_active_messages_multiple_calls():
    """add_channel_active_messages() accumulates message IDs."""
    adapter = TestAdapter()
    adapter.init_channel_active_messages("channel-1")

    await adapter.add_channel_active_messages("channel-1", ["msg-1"])
    await adapter.add_channel_active_messages("channel-1", ["msg-2", "msg-3"])

    assert adapter._active_messages["channel-1"] == {"msg-1", "msg-2", "msg-3"}


@pytest.mark.asyncio
async def test_remove_channel_active_messages():
    """remove_channel_active_messages() removes message IDs."""
    adapter = TestAdapter()
    adapter.init_channel_active_messages("channel-1")

    await adapter.add_channel_active_messages("channel-1", ["msg-1", "msg-2", "msg-3"])
    await adapter.remove_channel_active_messages("channel-1", ["msg-1", "msg-3"])

    assert adapter._active_messages["channel-1"] == {"msg-2"}


@pytest.mark.asyncio
async def test_remove_channel_active_messages_cleans_up_empty_set():
    """remove_channel_active_messages() deletes key when set becomes empty."""
    adapter = TestAdapter()
    adapter.init_channel_active_messages("channel-1")

    await adapter.add_channel_active_messages("channel-1", ["msg-1"])
    await adapter.remove_channel_active_messages("channel-1", ["msg-1"])

    # Key should be deleted
    assert "channel-1" not in adapter._active_messages


@pytest.mark.asyncio
async def test_get_number_of_channel_active_messages():
    """get_number_of_channel_active_messages() returns count with lock."""
    adapter = TestAdapter()
    adapter.init_channel_active_messages("channel-1")

    count = await adapter.get_number_of_channel_active_messages("channel-1")
    assert count == 0

    await adapter.add_channel_active_messages("channel-1", ["msg-1", "msg-2"])

    count = await adapter.get_number_of_channel_active_messages("channel-1")
    assert count == 2


@pytest.mark.asyncio
async def test_get_number_of_channel_active_messages_non_existent_channel():
    """get_number_of_channel_active_messages() returns 0 for missing channel."""
    adapter = TestAdapter()

    count = await adapter.get_number_of_channel_active_messages("non-existent")
    assert count == 0


def test_stop_channel_active_messages():
    """stop_channel_active_messages() removes channel tracking."""
    adapter = TestAdapter()
    adapter.init_channel_active_messages("channel-1")

    adapter.stop_channel_active_messages("channel-1")

    assert "channel-1" not in adapter._active_messages


def test_stop_channel_active_messages_with_active_raises():
    """stop_channel_active_messages() raises if messages still active."""
    adapter = TestAdapter()
    adapter.init_channel_active_messages("channel-1")
    adapter._active_messages["channel-1"] = {"msg-1", "msg-2"}

    with pytest.raises(AdapterError, match="2 messages still active"):
        adapter.stop_channel_active_messages("channel-1")


def test_stop_channel_active_messages_already_stopped():
    """stop_channel_active_messages() safe to call on non-tracked channel."""
    adapter = TestAdapter()

    # Should not raise
    adapter.stop_channel_active_messages("never-initialized")


# ── Graceful Shutdown Helpers ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wait_for_channel_active_messages_returns_when_empty():
    """wait_for_channel_active_messages() returns immediately if no active."""
    adapter = TestAdapter()
    adapter.init_channel_active_messages("channel-1")

    # Should return quickly
    await adapter.wait_for_channel_active_messages("channel-1", timeout=1.0)


@pytest.mark.asyncio
async def test_wait_for_channel_active_messages_waits_for_completion():
    """wait_for_channel_active_messages() waits until messages complete."""
    adapter = TestAdapter()
    adapter.init_channel_active_messages("channel-1")

    # Add active message
    await adapter.add_channel_active_messages("channel-1", ["msg-1"])

    # Simulate message completion in background
    async def complete_message():
        await asyncio.sleep(0.5)
        await adapter.remove_channel_active_messages("channel-1", ["msg-1"])

    task = asyncio.create_task(complete_message())

    # Wait should block until message completes
    start = asyncio.get_event_loop().time()
    await adapter.wait_for_channel_active_messages("channel-1", timeout=2.0)
    elapsed = asyncio.get_event_loop().time() - start

    assert elapsed >= 0.4  # Waited for message
    await task


@pytest.mark.asyncio
async def test_wait_for_channel_active_messages_timeout():
    """wait_for_channel_active_messages() raises on timeout."""
    adapter = TestAdapter()
    adapter.init_channel_active_messages("channel-1")

    # Add message that never completes
    await adapter.add_channel_active_messages("channel-1", ["msg-1"])

    with pytest.raises(AdapterError, match="Timeout waiting for 1 active messages"):
        await adapter.wait_for_channel_active_messages("channel-1", timeout=0.5)


# ── Error Transformation ──────────────────────────────────────────────────


def test_transform_error_to_headers_default_implementation():
    """transform_error_to_headers() uses default utility function."""
    adapter = TestAdapter()

    error = ValueError("Test error")
    headers = adapter.transform_error_to_headers(error)

    assert "x-error-message" in headers
    assert headers["x-error-message"] == "Test error"
    assert "x-error-type" in headers
    assert headers["x-error-type"] == "ValueError"


def test_transform_headers_to_error_data_default_implementation():
    """transform_headers_to_error_data() uses default utility function."""
    adapter = TestAdapter()

    headers = {
        "x-error-message": "Connection failed",
        "x-error-type": "ConnectionError",
        "x-error-timestamp": "1706000000000",
    }

    error_data = adapter.transform_headers_to_error_data(headers)

    assert error_data["message"] == "Connection failed"
    assert error_data["type"] == "ConnectionError"
    assert error_data["timestamp"] == 1706000000000


# ── Prefix Topic ──────────────────────────────────────────────────────────


def test_add_prefix_topic_default_no_prefix():
    """add_prefix_topic() returns topic unchanged by default."""
    adapter = TestAdapter()

    assert adapter.add_prefix_topic("orders.created") == "orders.created"
    assert adapter.add_prefix_topic("test.topic") == "test.topic"


def test_add_prefix_topic_can_be_overridden():
    """add_prefix_topic() can be overridden by subclasses."""

    class PrefixedAdapter(TestAdapter):
        def add_prefix_topic(self, topic):
            return f"myapp.{topic}"

    adapter = PrefixedAdapter()

    assert adapter.add_prefix_topic("orders") == "myapp.orders"


# ── Thread Safety ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_active_messages_tracking_is_thread_safe():
    """Active message operations use lock for thread safety."""
    adapter = TestAdapter()
    adapter.init_channel_active_messages("channel-1")

    # Concurrent adds
    async def add_messages(msg_ids):
        await adapter.add_channel_active_messages("channel-1", msg_ids)

    tasks = [add_messages([f"msg-{i}-{j}" for j in range(10)]) for i in range(5)]

    await asyncio.gather(*tasks)

    # Verify all 50 messages tracked
    count = await adapter.get_number_of_channel_active_messages("channel-1")
    assert count == 50

    # Concurrent removes
    async def remove_messages(msg_ids):
        await adapter.remove_channel_active_messages("channel-1", msg_ids)

    remove_tasks = [remove_messages([f"msg-{i}-{j}" for j in range(10)]) for i in range(5)]

    await asyncio.gather(*remove_tasks)

    # Verify all removed
    count = await adapter.get_number_of_channel_active_messages("channel-1")
    assert count == 0
