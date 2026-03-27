"""Tests for FakeAdapter."""

import asyncio
import json

import pytest

from moleculerpy_channels.adapters.fake import FakeAdapter, FakeMessage
from moleculerpy_channels.channel import Channel
from moleculerpy_channels.errors import MessagePublishError


@pytest.fixture
def mock_broker():
    """Create mock broker."""

    class MockSerializer:
        def serialize(self, data):
            return json.dumps(data).encode()

        def deserialize(self, data):
            return json.loads(data.decode())

    class MockBroker:
        def __init__(self):
            self.serializer = MockSerializer()

    return MockBroker()


@pytest.fixture
def mock_logger():
    """Create mock logger."""

    class MockLogger:
        def info(self, msg):
            pass

        def debug(self, msg):
            pass

        def warning(self, msg):
            pass

        def error(self, msg):
            pass

    return MockLogger()


@pytest.fixture
async def adapter(mock_broker, mock_logger):
    """Create and initialize FakeAdapter."""
    adapter = FakeAdapter()
    adapter.init(mock_broker, mock_logger)
    await adapter.connect()
    yield adapter
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_connect_disconnect(mock_broker, mock_logger):
    """Test adapter connection lifecycle."""
    adapter = FakeAdapter()
    adapter.init(mock_broker, mock_logger)

    assert not adapter._connected

    await adapter.connect()
    assert adapter._connected

    await adapter.disconnect()
    assert not adapter._connected


@pytest.mark.asyncio
async def test_publish_without_connect(mock_broker, mock_logger):
    """Test publishing without connection fails."""
    adapter = FakeAdapter()
    adapter.init(mock_broker, mock_logger)

    with pytest.raises(MessagePublishError, match="not connected"):
        await adapter.publish("test.channel", {"msg": "hello"}, {})


@pytest.mark.asyncio
async def test_publish_and_subscribe(adapter):
    """Test basic publish and subscribe."""
    received_messages = []

    async def handler(payload, raw):
        received_messages.append(payload)

    channel = Channel(name="test.channel", handler=handler, group="test-group")
    channel.id = "consumer-1"

    await adapter.subscribe(channel, None)

    # Publish message
    await adapter.publish("test.channel", {"msg": "hello"}, {})

    # Wait for delivery
    await asyncio.sleep(0.1)

    assert len(received_messages) == 1
    assert received_messages[0] == {"msg": "hello"}


@pytest.mark.asyncio
async def test_publish_with_headers(adapter):
    """Test publishing with headers."""
    received_headers = []

    async def handler(payload, raw):
        headers = adapter.parse_message_headers(raw)
        received_headers.append(headers)

    channel = Channel(name="test.channel", handler=handler, group="test-group")
    channel.id = "consumer-1"

    await adapter.subscribe(channel, None)

    # Publish with headers
    await adapter.publish(
        "test.channel", {"msg": "hello"}, {"headers": {"userId": "123", "tracing": "true"}}
    )

    await asyncio.sleep(0.1)

    assert len(received_headers) == 1
    assert received_headers[0]["userId"] == "123"
    assert received_headers[0]["tracing"] == "true"


@pytest.mark.asyncio
async def test_multiple_subscribers(adapter):
    """Test multiple subscribers receive messages."""
    received_1 = []
    received_2 = []

    async def handler_1(payload, raw):
        received_1.append(payload)

    async def handler_2(payload, raw):
        received_2.append(payload)

    channel_1 = Channel(name="test.channel", handler=handler_1, group="group-1")
    channel_1.id = "consumer-1"

    channel_2 = Channel(name="test.channel", handler=handler_2, group="group-2")
    channel_2.id = "consumer-2"

    await adapter.subscribe(channel_1, None)
    await adapter.subscribe(channel_2, None)

    await adapter.publish("test.channel", {"msg": "hello"}, {})

    await asyncio.sleep(0.1)

    assert len(received_1) == 1
    assert len(received_2) == 1


@pytest.mark.asyncio
async def test_unsubscribe(adapter):
    """Test unsubscribing stops message delivery."""
    received = []

    async def handler(payload, raw):
        received.append(payload)

    channel = Channel(name="test.channel", handler=handler, group="test-group")
    channel.id = "consumer-1"

    await adapter.subscribe(channel, None)
    await adapter.publish("test.channel", {"msg": "1"}, {})
    await asyncio.sleep(0.1)

    await adapter.unsubscribe(channel)

    await adapter.publish("test.channel", {"msg": "2"}, {})
    await asyncio.sleep(0.1)

    assert len(received) == 1
    assert received[0]["msg"] == "1"


@pytest.mark.asyncio
async def test_handler_error_doesnt_crash(adapter):
    """Test that handler errors are caught and don't crash adapter."""

    async def failing_handler(payload, raw):
        raise ValueError("Handler error")

    channel = Channel(name="test.channel", handler=failing_handler, group="test-group")
    channel.id = "consumer-1"

    await adapter.subscribe(channel, None)

    # Should not raise
    await adapter.publish("test.channel", {"msg": "hello"}, {})
    await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_message_history(adapter):
    """Test message history tracking."""
    channel = Channel(name="test.channel", handler=lambda p, r: None, group="test-group")
    channel.id = "consumer-1"

    await adapter.subscribe(channel, None)

    await adapter.publish("test.channel", {"msg": "1"}, {})
    await adapter.publish("test.channel", {"msg": "2"}, {})
    await adapter.publish("test.channel", {"msg": "3"}, {})

    history = adapter.get_message_history("test.channel")
    assert len(history) == 3

    # Clear history
    adapter.clear_message_history("test.channel")
    history = adapter.get_message_history("test.channel")
    assert len(history) == 0


@pytest.mark.asyncio
async def test_active_message_tracking(adapter):
    """Test active message tracking."""
    processing_started = asyncio.Event()
    continue_processing = asyncio.Event()

    async def slow_handler(payload, raw):
        processing_started.set()
        await continue_processing.wait()

    channel = Channel(name="test.channel", handler=slow_handler, group="test-group")
    channel.id = "consumer-1"

    await adapter.subscribe(channel, None)

    # Publish message (handler will block)
    task = asyncio.create_task(adapter.publish("test.channel", {"msg": "hello"}, {}))

    # Wait for handler to start
    await processing_started.wait()

    # Check active messages
    assert await adapter.get_number_of_channel_active_messages(channel.id) > 0

    # Release handler
    continue_processing.set()
    await task

    # Wait for cleanup
    await asyncio.sleep(0.1)

    # Check active messages cleared
    assert await adapter.get_number_of_channel_active_messages(channel.id) == 0


def test_parse_message_headers():
    """Test parsing headers from FakeMessage."""
    adapter = FakeAdapter()

    message = FakeMessage(payload=b"test", headers={"key": "value"})
    headers = adapter.parse_message_headers(message)

    assert headers == {"key": "value"}


def test_parse_message_headers_none():
    """Test parsing headers from non-FakeMessage."""
    adapter = FakeAdapter()

    headers = adapter.parse_message_headers("not a FakeMessage")
    assert headers is None
