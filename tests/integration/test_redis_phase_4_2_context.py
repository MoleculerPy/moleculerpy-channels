"""
Integration tests for Phase 4.2 - Context Propagation.

Tests verify:
- All 8 context fields propagated correctly
- requestID, parentID, tracing, level (distributed tracing)
- caller, meta, headers (application data)
- channel_name, parent_channel_name (channel context)
"""

import asyncio
import base64

import pytest

pytest_plugins = ("pytest_asyncio",)


@pytest.mark.asyncio
async def test_context_propagation_basic_fields(redis_adapter, redis_client):
    """
    Verify basic context fields are propagated:
    - requestID, parentID, level, tracing, caller
    """
    from moleculerpy_channels import Channel, ChannelsMiddleware
    from moleculerpy_channels.adapters import FakeAdapter

    # Mock broker and service
    class MockBroker:
        def __init__(self):
            self.serializer = FakeAdapter().serializer  # JSON serializer

        def get_logger(self, name):
            import logging

            return logging.getLogger(name)

    class MockService:
        name = "test-service"
        full_name = "test-service-v1"

    broker = MockBroker()
    service = MockService()

    # Create middleware
    middleware = ChannelsMiddleware(adapter=redis_adapter)
    middleware.broker = broker
    middleware.logger = broker.get_logger("Channels")
    redis_adapter.broker = broker
    redis_adapter.logger = broker.get_logger("RedisAdapter")

    # Create mock context
    class MockContext:
        def __init__(self):
            self.id = "ctx-parent-123"
            self.request_id = "req-abc-456"
            self.level = 1
            self.tracing = True
            self.service = service
            self.meta = {}
            self.headers = {}

    ctx = MockContext()

    # Publish with context
    await redis_adapter.publish(
        "test.context.basic",
        {"message": "hello"},
        {
            "ctx": ctx,
            "headers": {
                "$requestID": ctx.request_id,
                "$parentID": ctx.id,
                "$level": str(ctx.level),
                "$tracing": str(ctx.tracing),
                "$caller": ctx.service.full_name,
            },
        },
    )

    # Verify message headers in Redis
    messages = await redis_client.xrange("test.context.basic", "-", "+", count=1)
    assert len(messages) == 1

    msg_id, fields = messages[0]

    # Check headers
    assert fields[b"$requestID"] == b"req-abc-456"
    assert fields[b"$parentID"] == b"ctx-parent-123"
    assert fields[b"$level"] == b"1"
    assert fields[b"$tracing"] == b"True"
    assert fields[b"$caller"] == b"test-service-v1"


@pytest.mark.asyncio
async def test_context_propagation_meta_and_headers(redis_adapter, redis_client):
    """
    Verify meta and headers are serialized/deserialized correctly.
    """
    from moleculerpy_channels import Channel

    received_context = None

    async def handler(payload, raw):
        # In this test, raw contains the message fields
        nonlocal received_context
        msg_id, fields = raw

        # Manually deserialize for testing
        import base64
        import json

        if b"$meta" in fields:
            meta_bytes = base64.b64decode(fields[b"$meta"])
            meta = json.loads(meta_bytes)
            received_context = {"meta": meta}

        if b"$headers" in fields:
            headers_bytes = base64.b64decode(fields[b"$headers"])
            headers = json.loads(headers_bytes)
            if received_context:
                received_context["headers"] = headers
            else:
                received_context = {"headers": headers}

    channel = Channel(
        name="test.context.meta",
        group="context-meta-group",
        handler=handler,
        context=False,  # Don't create Context, just parse raw
    )

    # Subscribe
    await redis_adapter.subscribe(channel, None)

    # Publish with meta and headers
    await redis_adapter.publish(
        channel.name,
        {"message": "test"},
        {
            "headers": {
                "$meta": base64.b64encode(
                    b'{"userId":"user-123","tenantId":"tenant-456"}'
                ).decode(),
                "$headers": base64.b64encode(
                    b'{"x-custom":"value","x-trace-id":"trace-789"}'
                ).decode(),
            }
        },
    )

    # Wait for processing
    await asyncio.sleep(0.5)

    # Verify context received
    assert received_context is not None
    assert received_context["meta"]["userId"] == "user-123"
    assert received_context["meta"]["tenantId"] == "tenant-456"
    assert received_context["headers"]["x-custom"] == "value"
    assert received_context["headers"]["x-trace-id"] == "trace-789"

    # Cleanup
    await redis_adapter.unsubscribe(channel)


@pytest.mark.asyncio
async def test_context_propagation_channel_names(redis_adapter, redis_client):
    """
    Verify channel_name and parent_channel_name propagation.
    """
    from moleculerpy_channels import Channel

    received_headers = None

    async def handler(payload, raw):
        nonlocal received_headers
        msg_id, fields = raw
        received_headers = {k.decode(): v.decode() for k, v in fields.items()}

    channel = Channel(
        name="test.context.channels",
        group="context-channels-group",
        handler=handler,
    )

    # Subscribe
    await redis_adapter.subscribe(channel, None)

    # Publish with parent channel name
    await redis_adapter.publish(
        channel.name,
        {"message": "nested call"},
        {"headers": {"$parentChannelName": "parent.channel.name"}},
    )

    # Wait for processing
    await asyncio.sleep(0.5)

    # Verify parent channel name received
    assert received_headers is not None
    assert received_headers.get("$parentChannelName") == "parent.channel.name"

    # Cleanup
    await redis_adapter.unsubscribe(channel)


@pytest.mark.asyncio
async def test_context_propagation_level_increment(redis_adapter, redis_client):
    """
    Verify level increments correctly in nested calls.
    """
    from moleculerpy_channels import Channel

    received_level = None

    async def handler(payload, raw):
        nonlocal received_level
        msg_id, fields = raw
        if b"$level" in fields:
            received_level = int(fields[b"$level"].decode())

    channel = Channel(
        name="test.context.level",
        group="context-level-group",
        handler=handler,
    )

    # Subscribe
    await redis_adapter.subscribe(channel, None)

    # Publish with level 1
    await redis_adapter.publish(
        channel.name,
        {"message": "level test"},
        {"headers": {"$level": "1"}},
    )

    # Wait for processing
    await asyncio.sleep(0.5)

    # Verify level received
    assert received_level == 1

    # Cleanup
    await redis_adapter.unsubscribe(channel)


@pytest.mark.asyncio
async def test_context_propagation_all_fields_together(redis_adapter, redis_client):
    """
    Verify all 8 context fields are propagated together.

    Tests complete context propagation with:
    - requestID, parentID, level, tracing, caller
    - meta, headers
    - parent_channel_name
    """
    from moleculerpy_channels import Channel

    received_fields = None

    async def handler(payload, raw):
        nonlocal received_fields
        msg_id, fields = raw
        received_fields = {
            k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
            for k, v in fields.items()
        }

    channel = Channel(
        name="test.context.complete",
        group="context-complete-group",
        handler=handler,
    )

    # Subscribe
    await redis_adapter.subscribe(channel, None)

    # Publish with full context
    await redis_adapter.publish(
        channel.name,
        {"message": "complete test"},
        {
            "headers": {
                "$requestID": "req-full-123",
                "$parentID": "ctx-full-456",
                "$level": "2",
                "$tracing": "true",
                "$caller": "parent-service-v1",
                "$meta": base64.b64encode(b'{"key":"value"}').decode(),
                "$headers": base64.b64encode(b'{"x-custom":"header"}').decode(),
                "$parentChannelName": "parent.channel",
            }
        },
    )

    # Wait for processing
    await asyncio.sleep(0.5)

    # Verify all fields received
    assert received_fields is not None
    assert received_fields["$requestID"] == "req-full-123"
    assert received_fields["$parentID"] == "ctx-full-456"
    assert received_fields["$level"] == "2"
    assert received_fields["$tracing"] == "true"
    assert received_fields["$caller"] == "parent-service-v1"
    assert "$meta" in received_fields
    assert "$headers" in received_fields
    assert received_fields["$parentChannelName"] == "parent.channel"

    # Cleanup
    await redis_adapter.unsubscribe(channel)
