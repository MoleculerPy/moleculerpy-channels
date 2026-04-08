"""
Unit tests for ChannelsMiddleware.

Tests:
- Initialization and configuration
- Lifecycle hooks (broker_created, service_created, broker_starting, etc.)
- Channel definition parsing (function vs dict)
- Method/property name conflict detection
- Context propagation in send_to_channel
- Channel registry management
"""

import base64
import json

import pytest

from moleculerpy_channels.adapters.base import BaseAdapter
from moleculerpy_channels.channel import Channel, DeadLetteringOptions
from moleculerpy_channels.errors import ChannelRegistrationError
from moleculerpy_channels.middleware import ChannelsMiddleware

# ── Test Adapter ──────────────────────────────────────────────────────────


class TestAdapter(BaseAdapter):
    """Minimal adapter for testing."""

    __test__ = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def subscribe(self, channel, service):
        if not hasattr(self, "subscriptions"):
            self.subscriptions = []
        self.subscriptions.append({"channel": channel, "service": service})

    async def unsubscribe(self, channel):
        if hasattr(self, "subscriptions"):
            self.subscriptions = [s for s in self.subscriptions if s["channel"].id != channel.id]

    async def publish(self, channel_name, payload, opts):
        if not hasattr(self, "published"):
            self.published = []
        self.published.append({"channel_name": channel_name, "payload": payload, "opts": opts})

    def parse_message_headers(self, raw_message):
        return {}


# ── Mock Objects ──────────────────────────────────────────────────────────


def create_mock_broker():
    """Create mock broker with minimal required interface."""

    class MockSerializer:
        def serialize(self, data):
            return json.dumps(data).encode()

        def deserialize(self, data):
            return json.loads(data.decode())

    class MockLogger:
        def info(self, msg):
            pass

        def debug(self, msg):
            pass

    class MockBroker:
        def __init__(self):
            self.serializer = MockSerializer()
            self.node_id = "test-node-1"

        def get_logger(self, name):
            return MockLogger()

    return MockBroker()


def create_mock_service(full_name: str = "test-service-v1", schema: dict = None):
    """Create mock service with schema."""

    class MockService:
        def __init__(self):
            self.full_name = full_name
            self.schema = schema or {}

    return MockService()


# ── Initialization ────────────────────────────────────────────────────────


def test_middleware_init_default_params():
    """ChannelsMiddleware() with default parameters."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)

    assert middleware.adapter is adapter
    assert middleware.schema_property == "channels"
    assert middleware.send_method_name == "send_to_channel"
    assert middleware.adapter_property_name == "channel_adapter"
    assert middleware.context is False


def test_middleware_init_custom_params():
    """ChannelsMiddleware() with custom parameters."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(
        adapter=adapter,
        schema_property="custom_channels",
        send_method_name="custom_send",
        adapter_property_name="custom_adapter",
        context=True,
    )

    assert middleware.schema_property == "custom_channels"
    assert middleware.send_method_name == "custom_send"
    assert middleware.adapter_property_name == "custom_adapter"
    assert middleware.context is True


def test_middleware_init_requires_base_adapter():
    """ChannelsMiddleware() raises TypeError if adapter is not BaseAdapter."""
    with pytest.raises(TypeError, match="adapter must be BaseAdapter instance"):
        ChannelsMiddleware(adapter="not an adapter")


# ── broker_created() Hook ─────────────────────────────────────────────────


def test_broker_created_stores_broker_and_logger():
    """broker_created() stores broker reference and gets logger."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)
    broker = create_mock_broker()

    middleware.broker_created(broker)

    assert middleware.broker is broker
    assert middleware.logger is not None


def test_broker_created_initializes_adapter():
    """broker_created() calls adapter.init()."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)
    broker = create_mock_broker()

    middleware.broker_created(broker)

    # Verify adapter initialized
    assert adapter.broker is broker
    assert adapter.logger is not None
    assert adapter.serializer is not None


def test_broker_created_registers_send_to_channel_method():
    """broker_created() adds send_to_channel method to broker."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)
    broker = create_mock_broker()

    middleware.broker_created(broker)

    # Verify method registered
    assert hasattr(broker, "send_to_channel")
    assert callable(broker.send_to_channel)


def test_broker_created_raises_on_method_conflict():
    """broker_created() raises if send_to_channel already exists."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)
    broker = create_mock_broker()

    # Pre-existing method
    broker.send_to_channel = lambda: None

    with pytest.raises(ChannelRegistrationError, match="send_to_channel already exists"):
        middleware.broker_created(broker)


def test_broker_created_raises_on_property_conflict():
    """broker_created() raises if channel_adapter property already exists."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)
    broker = create_mock_broker()

    # Pre-existing property
    broker.channel_adapter = "something"

    with pytest.raises(ChannelRegistrationError, match="channel_adapter already exists"):
        middleware.broker_created(broker)


def test_broker_created_adds_adapter_property():
    """broker_created() adds adapter reference to broker."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)
    broker = create_mock_broker()

    middleware.broker_created(broker)

    assert hasattr(broker, "channel_adapter")
    assert broker.channel_adapter is adapter


# ── service_created() Hook ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_service_created_no_schema_property():
    """service_created() returns early if service has no schema."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)
    middleware.broker = create_mock_broker()

    service = create_mock_service(schema=None)

    # Should not raise
    await middleware.service_created(service)

    # No channels registered
    assert len(middleware.channel_registry) == 0


@pytest.mark.asyncio
async def test_service_created_no_channels_definition():
    """service_created() returns early if schema has no 'channels' key."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)
    middleware.broker = create_mock_broker()

    service = create_mock_service(schema={"actions": {}})

    await middleware.service_created(service)

    assert len(middleware.channel_registry) == 0


@pytest.mark.asyncio
async def test_service_created_with_function_handler():
    """service_created() parses simple function handler."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)
    middleware.broker = create_mock_broker()

    async def my_handler(payload, raw):
        pass

    service = create_mock_service(schema={"channels": {"test.channel": my_handler}})

    await middleware.service_created(service)

    # Verify channel registered
    assert len(middleware.channel_registry) == 1
    registered = middleware.channel_registry[0]

    assert registered["name"] == "test.channel"
    assert registered["service"] is service
    assert isinstance(registered["channel"], Channel)


@pytest.mark.asyncio
async def test_service_created_with_dict_definition():
    """service_created() parses dict definition with options."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)
    middleware.broker = create_mock_broker()

    async def my_handler(payload, raw):
        pass

    service = create_mock_service(
        schema={
            "channels": {
                "orders.created": {
                    "handler": my_handler,
                    "group": "order-processors",
                    "max_retries": 5,
                    "max_in_flight": 10,
                }
            }
        }
    )

    await middleware.service_created(service)

    # Verify channel config
    assert len(middleware.channel_registry) == 1
    channel = middleware.channel_registry[0]["channel"]

    assert channel.name == "orders.created"
    assert channel.group == "order-processors"
    assert channel.max_retries == 5
    assert channel.max_in_flight == 10


@pytest.mark.asyncio
async def test_service_created_missing_handler_raises():
    """service_created() raises if dict definition has no handler."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)
    middleware.broker = create_mock_broker()

    service = create_mock_service(
        schema={"channels": {"test.channel": {"max_retries": 3}}}  # No handler!
    )

    with pytest.raises(ChannelRegistrationError, match="Missing or invalid handler"):
        await middleware.service_created(service)


@pytest.mark.asyncio
async def test_service_created_invalid_definition_type_raises():
    """service_created() raises if definition is not function or dict."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)
    middleware.broker = create_mock_broker()

    service = create_mock_service(schema={"channels": {"test.channel": "not a function or dict"}})

    with pytest.raises(ChannelRegistrationError, match="Invalid channel definition"):
        await middleware.service_created(service)


@pytest.mark.asyncio
async def test_service_created_subscribes_if_already_started():
    """service_created() immediately subscribes if middleware already started."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)
    middleware.broker = create_mock_broker()
    middleware.started = True  # Simulate already started

    async def handler(payload, raw):
        pass

    service = create_mock_service(schema={"channels": {"test.channel": handler}})

    await middleware.service_created(service)

    # Verify subscribed immediately
    assert hasattr(adapter, "subscriptions")
    assert len(adapter.subscriptions) == 1


# ── broker_starting() Hook ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_broker_starting_connects_adapter():
    """broker_starting() connects adapter."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)
    broker = create_mock_broker()

    await middleware.broker_starting(broker)

    assert adapter.connected is True
    assert middleware.started is True


@pytest.mark.asyncio
async def test_broker_starting_subscribes_all_channels():
    """broker_starting() subscribes to all registered channels."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)
    broker = create_mock_broker()
    middleware.broker = broker

    # Register 3 channels
    async def handler(p, r):
        pass

    services = [
        create_mock_service(f"service-{i}", {"channels": {f"channel-{i}": handler}})
        for i in range(3)
    ]

    for service in services:
        await middleware.service_created(service)

    assert len(middleware.channel_registry) == 3

    # Start broker
    await middleware.broker_starting(broker)

    # Verify all subscribed
    assert len(adapter.subscriptions) == 3


# ── service_stopping() Hook ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_service_stopping_unsubscribes_service_channels():
    """service_stopping() unsubscribes all channels for service."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)
    broker = create_mock_broker()
    middleware.broker = broker

    # Create 2 services with channels
    async def handler(p, r):
        pass

    service1 = create_mock_service("service-1", {"channels": {"channel-1": handler}})
    service2 = create_mock_service("service-2", {"channels": {"channel-2": handler}})

    await middleware.service_created(service1)
    await middleware.service_created(service2)
    await middleware.broker_starting(broker)

    assert len(adapter.subscriptions) == 2

    # Stop service1
    await middleware.service_stopping(service1)

    # Only service2 channel remains
    assert len(adapter.subscriptions) == 1
    assert adapter.subscriptions[0]["service"] is service2


# ── broker_stopped() Hook ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_broker_stopped_disconnects_adapter():
    """broker_stopped() disconnects adapter."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)
    broker = create_mock_broker()

    # Connect first
    await middleware.broker_starting(broker)
    assert adapter.connected is True

    # Stop
    await middleware.broker_stopped(broker)

    assert adapter.connected is False
    assert middleware.started is False


# ── send_to_channel() Method ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_to_channel_basic():
    """broker.send_to_channel() publishes message via adapter."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)
    broker = create_mock_broker()

    middleware.broker_created(broker)

    # Send message
    await broker.send_to_channel("test.channel", {"msg": "hello"})

    # Verify published
    assert len(adapter.published) == 1
    published = adapter.published[0]

    assert published["channel_name"] == "test.channel"
    assert published["payload"] == {"msg": "hello"}
    assert published["opts"] == {}


@pytest.mark.asyncio
async def test_send_to_channel_with_context():
    """send_to_channel() propagates context fields to headers."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)
    broker = create_mock_broker()

    middleware.broker_created(broker)

    # Mock context
    class MockContext:
        request_id = "req-123"
        id = "ctx-456"
        tracing = True
        level = 2
        meta = {"user_id": "user-1"}
        headers = {"x-custom": "header"}
        service = None  # No service
        channel_name = None  # No parent channel

    ctx = MockContext()

    # Send with context
    await broker.send_to_channel("test.channel", {"msg": "hello"}, opts={"ctx": ctx})

    # Verify headers added
    published = adapter.published[0]
    headers = published["opts"]["headers"]

    assert headers["$requestID"] == "req-123"
    assert headers["$parentID"] == "ctx-456"
    assert headers["$tracing"] == "True"
    assert headers["$level"] == "2"
    assert "$meta" in headers
    assert "$headers" in headers

    # Verify base64 encoding
    meta = json.loads(base64.b64decode(headers["$meta"]))
    assert meta == {"user_id": "user-1"}


@pytest.mark.asyncio
async def test_send_to_channel_with_context_and_service():
    """send_to_channel() includes caller service name."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)
    broker = create_mock_broker()

    middleware.broker_created(broker)

    # Mock context with service
    class MockService:
        full_name = "order-service-v1"

    class MockContext:
        request_id = "req-1"
        id = "ctx-1"
        tracing = False
        level = 1
        meta = {}
        service = MockService()
        channel_name = None

    ctx = MockContext()

    await broker.send_to_channel("test.channel", {}, opts={"ctx": ctx})

    # Verify caller
    headers = adapter.published[0]["opts"]["headers"]
    assert headers["$caller"] == "order-service-v1"


@pytest.mark.asyncio
async def test_send_to_channel_with_parent_channel_name():
    """send_to_channel() includes parent channel name for nested calls."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)
    broker = create_mock_broker()

    middleware.broker_created(broker)

    class MockContext:
        request_id = "req-1"
        id = "ctx-1"
        tracing = False
        level = 2
        meta = {}
        service = None
        channel_name = "parent.channel"

    ctx = MockContext()

    await broker.send_to_channel("child.channel", {}, opts={"ctx": ctx})

    headers = adapter.published[0]["opts"]["headers"]
    assert headers["$parentChannelName"] == "parent.channel"


# ── Channel Definition Parsing ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parse_channel_simple_function():
    """_parse_channel_definition() handles simple function."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)
    broker = create_mock_broker()
    middleware.broker = broker

    async def handler(p, r):
        pass

    service = create_mock_service()
    channel = await middleware._parse_channel_definition("test.chan", handler, service)

    assert isinstance(channel, Channel)
    assert channel.name == "test.chan"
    assert channel.group == "test-service-v1"


@pytest.mark.asyncio
async def test_parse_channel_dict_with_custom_group():
    """_parse_channel_definition() uses custom group from dict."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)
    broker = create_mock_broker()
    middleware.broker = broker

    async def handler(p, r):
        pass

    definition = {
        "handler": handler,
        "group": "custom-group",
    }

    service = create_mock_service()
    channel = await middleware._parse_channel_definition("test.chan", definition, service)

    assert channel.group == "custom-group"


@pytest.mark.asyncio
async def test_parse_channel_dict_with_dead_lettering():
    """_parse_channel_definition() parses dead_lettering options."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)
    broker = create_mock_broker()
    middleware.broker = broker

    async def handler(p, r):
        pass

    definition = {
        "handler": handler,
        "dead_lettering": {
            "enabled": True,
            "queue_name": "FAILED_ORDERS",
            "error_info_ttl": 86400,
        },
    }

    service = create_mock_service()
    channel = await middleware._parse_channel_definition("orders.created", definition, service)

    assert channel.dead_lettering is not None
    assert isinstance(channel.dead_lettering, DeadLetteringOptions)
    assert channel.dead_lettering.enabled is True
    assert channel.dead_lettering.queue_name == "FAILED_ORDERS"


@pytest.mark.asyncio
async def test_parse_channel_accepts_dead_lettering_instance():
    """Regression for KNOWN-ISSUES #16.

    Previously ``_parse_channel_definition`` only accepted a dict for
    ``dead_lettering`` and silently dropped anything else, so callers who
    built a typed ``DeadLetteringOptions`` object up front lost their DLQ
    configuration without any warning. It now accepts both forms.
    """
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)
    broker = create_mock_broker()
    middleware.broker = broker

    async def handler(p, r):
        pass

    dlq = DeadLetteringOptions(enabled=True, queue_name="PRE_BUILT_DLQ", error_info_ttl=3600)
    definition = {"handler": handler, "dead_lettering": dlq}

    service = create_mock_service()
    channel = await middleware._parse_channel_definition("orders.cancelled", definition, service)

    # The exact instance must flow through, not a recreated copy built from
    # its dict representation — equality by identity is the strongest signal
    # the regression is fixed.
    assert channel.dead_lettering is dlq
    assert channel.dead_lettering.queue_name == "PRE_BUILT_DLQ"


# ── Channel Registry ──────────────────────────────────────────────────────


def test_register_channel_adds_to_registry():
    """_register_channel() adds channel to registry."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)

    service = create_mock_service()
    channel = Channel(
        id="test-1",
        name="test.channel",
        group="test-group",
        handler=lambda p, r: None,
    )

    middleware._register_channel(service, channel)

    assert len(middleware.channel_registry) == 1
    registered = middleware.channel_registry[0]

    assert registered["service"] is service
    assert registered["channel"] is channel
    assert registered["name"] == "test.channel"


def test_unregister_channel_removes_from_registry():
    """_unregister_channel() removes channels for service."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)

    service1 = create_mock_service("service-1")
    service2 = create_mock_service("service-2")

    channel1 = Channel(id="1", name="ch1", group="g1", handler=lambda p, r: None)
    channel2 = Channel(id="2", name="ch2", group="g2", handler=lambda p, r: None)

    middleware._register_channel(service1, channel1)
    middleware._register_channel(service2, channel2)

    assert len(middleware.channel_registry) == 2

    # Unregister service1
    middleware._unregister_channel(service1)

    # Only service2 remains
    assert len(middleware.channel_registry) == 1
    assert middleware.channel_registry[0]["service"] is service2


# ── Integration Test (Full Lifecycle) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_full_middleware_lifecycle():
    """Test complete middleware lifecycle: create → start → service → stop."""
    adapter = TestAdapter()
    middleware = ChannelsMiddleware(adapter=adapter)
    broker = create_mock_broker()

    # 1. Create broker
    middleware.broker_created(broker)
    assert hasattr(broker, "send_to_channel")
    assert middleware.broker is broker

    # 2. Create service with channels
    async def handler(payload, raw):
        pass

    service = create_mock_service(
        schema={
            "channels": {
                "orders.created": handler,
                "orders.updated": {"handler": handler, "group": "custom-group"},
            }
        }
    )

    await middleware.service_created(service)
    assert len(middleware.channel_registry) == 2

    # 3. Start broker
    await middleware.broker_starting(broker)
    assert adapter.connected is True
    assert len(adapter.subscriptions) == 2

    # 4. Stop service
    await middleware.service_stopping(service)
    assert len(adapter.subscriptions) == 0

    # 5. Stop broker
    await middleware.broker_stopped(broker)
    assert adapter.connected is False
    assert middleware.started is False
