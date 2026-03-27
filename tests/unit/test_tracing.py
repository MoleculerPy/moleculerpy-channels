"""
Unit tests for TracingMiddleware.

Tests:
- Span creation/finishing/error marking
- Tag extraction (params, meta, custom)
- Span name customization (string, function)
- Safety tags (sensitive data removal)
- Integration with broker.tracer
- Fallback when no tracer available
"""


import pytest

from moleculerpy_channels.channel import Channel
from moleculerpy_channels.tracing import TracingMiddleware

# ── Mock Objects ──────────────────────────────────────────────────────────


def create_mock_broker(with_tracer=True):
    """Create mock broker with optional tracer."""

    class MockSpan:
        def __init__(self, name, **kwargs):
            self.name = name
            self.kwargs = kwargs
            self.sampled = kwargs.get("sampled", True)
            self.finished = False
            self.error = None

        def set_error(self, err):
            self.error = err

    class MockTracer:
        def __init__(self):
            self.spans = []

        def getCurrentTraceID(self):
            return "trace-123"

        def getActiveSpanID(self):
            return "span-456"

        def start_span(self, name, **kwargs):
            span = MockSpan(name, **kwargs)
            self.spans.append(span)
            return span

        def finish_span(self, span):
            span.finished = True

    class MockBroker:
        def __init__(self):
            self.node_id = "node-1"
            self.tracer = MockTracer() if with_tracer else None

        def isTracingEnabled(self):
            return self.tracer is not None

    return MockBroker()


def create_mock_context(broker, **kwargs):
    """Create mock context with tracer methods."""

    class MockContext:
        def __init__(self):
            self.request_id = kwargs.get("request_id")
            self.parent_id = kwargs.get("parent_id")
            self.id = kwargs.get("id", "ctx-1")
            self.level = kwargs.get("level", 1)
            self.tracing = kwargs.get("tracing", True)
            self.params = kwargs.get("params", {})
            self.meta = kwargs.get("meta", {})
            self.service = kwargs.get("service")
            self.node_id = kwargs.get("node_id", broker.node_id)

        def start_span(self, name, **span_kwargs):
            if broker.tracer:
                return broker.tracer.start_span(name, **span_kwargs)
            return None

        def finish_span(self, span):
            if broker.tracer and span:
                broker.tracer.finish_span(span)

    return MockContext()


# ── Initialization ────────────────────────────────────────────────────────


def test_tracing_middleware_init():
    """TracingMiddleware initializes with None broker/tracer."""
    middleware = TracingMiddleware()

    assert middleware.broker is None
    assert middleware.tracer is None


def test_created_hook_stores_broker_and_tracer():
    """created() stores broker and tracer references."""
    middleware = TracingMiddleware()
    broker = create_mock_broker(with_tracer=True)

    middleware.created(broker)

    assert middleware.broker is broker
    assert middleware.tracer is broker.tracer


def test_created_hook_handles_missing_tracer():
    """created() handles broker without tracer gracefully."""
    middleware = TracingMiddleware()
    broker = create_mock_broker(with_tracer=False)

    middleware.created(broker)

    assert middleware.broker is broker
    assert middleware.tracer is None


# ── local_channel Hook ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_local_channel_returns_handler_when_tracing_disabled():
    """local_channel() returns original handler when tracing disabled."""
    middleware = TracingMiddleware()
    broker = create_mock_broker(with_tracer=False)
    middleware.created(broker)

    async def handler(ctx):
        return "result"

    channel = Channel(name="test.channel", handler=handler, group="test-group")

    wrapped = middleware.local_channel(handler, channel)

    # Should return original handler (no wrapping)
    assert wrapped is handler


@pytest.mark.asyncio
async def test_local_channel_returns_handler_when_tracing_opt_false():
    """local_channel() returns original handler when tracing option is False."""
    middleware = TracingMiddleware()
    broker = create_mock_broker(with_tracer=True)
    middleware.created(broker)

    async def handler(ctx):
        return "result"

    channel = Channel(
        name="test.channel",
        handler=handler,
        group="test-group",
        tracing=False,  # Explicitly disabled
    )

    wrapped = middleware.local_channel(handler, channel)

    # Should return original handler
    assert wrapped is handler


@pytest.mark.asyncio
async def test_local_channel_wraps_handler_with_span():
    """local_channel() wraps handler with span creation/finishing."""
    middleware = TracingMiddleware()
    broker = create_mock_broker(with_tracer=True)
    middleware.created(broker)

    handler_called = False

    async def handler(ctx):
        nonlocal handler_called
        handler_called = True
        return "result"

    channel = Channel(name="test.channel", handler=handler, group="test-group")
    ctx = create_mock_context(broker)

    wrapped = middleware.local_channel(handler, channel)

    # Execute wrapped handler
    result = await wrapped(ctx)

    # Verify handler was called
    assert handler_called is True
    assert result == "result"

    # Verify span was created and finished
    assert len(broker.tracer.spans) == 1
    span = broker.tracer.spans[0]

    assert span.name == "channel 'test.channel'"
    assert span.finished is True
    assert span.error is None


@pytest.mark.asyncio
async def test_local_channel_marks_span_error_on_exception():
    """local_channel() marks span with error when handler raises."""
    middleware = TracingMiddleware()
    broker = create_mock_broker(with_tracer=True)
    middleware.created(broker)

    async def failing_handler(ctx):
        raise ValueError("Handler failed")

    channel = Channel(name="test.channel", handler=failing_handler, group="test-group")
    ctx = create_mock_context(broker)

    wrapped = middleware.local_channel(failing_handler, channel)

    # Execute wrapped handler (should raise)
    with pytest.raises(ValueError, match="Handler failed"):
        await wrapped(ctx)

    # Verify span was marked with error
    assert len(broker.tracer.spans) == 1
    span = broker.tracer.spans[0]

    assert span.error is not None
    assert isinstance(span.error, ValueError)
    assert span.finished is True


# ── Span Name Customization ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_custom_span_name_string():
    """local_channel() uses custom span name (string)."""
    middleware = TracingMiddleware()
    broker = create_mock_broker(with_tracer=True)
    middleware.created(broker)

    async def handler(ctx):
        return "result"

    channel = Channel(
        name="orders.process",
        handler=handler,
        group="processors",
        tracing={"spanName": "Custom Span Name"},
    )

    ctx = create_mock_context(broker)
    wrapped = middleware.local_channel(handler, channel)

    await wrapped(ctx)

    # Verify custom span name
    span = broker.tracer.spans[0]
    assert span.name == "Custom Span Name"


@pytest.mark.asyncio
async def test_custom_span_name_function():
    """local_channel() uses custom span name function."""
    middleware = TracingMiddleware()
    broker = create_mock_broker(with_tracer=True)
    middleware.created(broker)

    async def handler(ctx):
        return "result"

    channel = Channel(
        name="orders.process",
        handler=handler,
        group="processors",
        tracing={"spanName": lambda ctx: f"Process Order #{ctx.params.get('orderId', '?')}"},
    )

    ctx = create_mock_context(broker, params={"orderId": 123})
    wrapped = middleware.local_channel(handler, channel)

    await wrapped(ctx)

    # Verify dynamic span name
    span = broker.tracer.spans[0]
    assert span.name == "Process Order #123"


# ── Tag Extraction ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tags_extraction_full_params():
    """local_channel() extracts full params when tags.params=True."""
    middleware = TracingMiddleware()
    broker = create_mock_broker(with_tracer=True)
    middleware.created(broker)

    async def handler(ctx):
        return "result"

    channel = Channel(
        name="test.channel", handler=handler, group="test-group", tracing={"tags": {"params": True}}
    )

    ctx = create_mock_context(broker, params={"orderId": 123, "userId": "user-1"})
    wrapped = middleware.local_channel(handler, channel)

    await wrapped(ctx)

    # Verify params in span tags
    span = broker.tracer.spans[0]
    assert "params" in span.kwargs["tags"]
    assert span.kwargs["tags"]["params"] == {"orderId": 123, "userId": "user-1"}


@pytest.mark.asyncio
async def test_tags_extraction_selective_params():
    """local_channel() extracts selective params when tags.params=[...]."""
    middleware = TracingMiddleware()
    broker = create_mock_broker(with_tracer=True)
    middleware.created(broker)

    async def handler(ctx):
        return "result"

    channel = Channel(
        name="test.channel",
        handler=handler,
        group="test-group",
        tracing={"tags": {"params": ["orderId"]}},  # Only orderId
    )

    ctx = create_mock_context(
        broker, params={"orderId": 123, "userId": "user-1", "secret": "password"}
    )

    wrapped = middleware.local_channel(handler, channel)
    await wrapped(ctx)

    # Verify only orderId in tags
    span = broker.tracer.spans[0]
    assert "params" in span.kwargs["tags"]
    assert span.kwargs["tags"]["params"] == {"orderId": 123}
    assert "userId" not in span.kwargs["tags"]["params"]
    assert "secret" not in span.kwargs["tags"]["params"]


@pytest.mark.asyncio
async def test_tags_extraction_full_meta():
    """local_channel() extracts full meta when tags.meta=True."""
    middleware = TracingMiddleware()
    broker = create_mock_broker(with_tracer=True)
    middleware.created(broker)

    async def handler(ctx):
        return "result"

    channel = Channel(
        name="test.channel", handler=handler, group="test-group", tracing={"tags": {"meta": True}}
    )

    ctx = create_mock_context(broker, meta={"userId": "user-1", "requestId": "req-1"})
    wrapped = middleware.local_channel(handler, channel)

    await wrapped(ctx)

    # Verify meta in span tags
    span = broker.tracer.spans[0]
    assert "meta" in span.kwargs["tags"]
    assert span.kwargs["tags"]["meta"] == {"userId": "user-1", "requestId": "req-1"}


@pytest.mark.asyncio
async def test_tags_extraction_function():
    """local_channel() calls function for custom tag extraction."""
    middleware = TracingMiddleware()
    broker = create_mock_broker(with_tracer=True)
    middleware.created(broker)

    async def handler(ctx):
        return "result"

    def extract_tags(ctx):
        """Custom tag extractor."""
        return {
            "customTag": "value",
            "orderId": ctx.params.get("orderId"),
        }

    channel = Channel(
        name="test.channel", handler=handler, group="test-group", tracing={"tags": extract_tags}
    )

    ctx = create_mock_context(broker, params={"orderId": 456})
    wrapped = middleware.local_channel(handler, channel)

    await wrapped(ctx)

    # Verify custom tags
    span = broker.tracer.spans[0]
    assert "customTag" in span.kwargs["tags"]
    assert span.kwargs["tags"]["customTag"] == "value"
    assert span.kwargs["tags"]["orderId"] == 456


# ── Safety Tags ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_safety_tags_removes_sensitive_data():
    """local_channel() removes sensitive fields when safetyTags=True."""
    middleware = TracingMiddleware()
    broker = create_mock_broker(with_tracer=True)
    middleware.created(broker)

    async def handler(ctx):
        return "result"

    channel = Channel(
        name="auth.login",
        handler=handler,
        group="auth-group",
        tracing={"tags": {"params": True}, "safetyTags": True},
    )

    ctx = create_mock_context(
        broker,
        params={
            "username": "john",
            "password": "secret123",  # Should be redacted
            "apiKey": "key-abc",  # Should be redacted
            "userId": "user-1",  # Should remain
        },
    )

    wrapped = middleware.local_channel(handler, channel)
    await wrapped(ctx)

    # Verify sensitive data removed
    span = broker.tracer.spans[0]
    params = span.kwargs["tags"]["params"]

    assert params["username"] == "john"
    assert params["userId"] == "user-1"
    assert params["password"] == "[REDACTED]"
    assert params["apiKey"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_safety_tags_recursive():
    """_safety_object() recursively sanitizes nested objects."""
    middleware = TracingMiddleware()

    obj = {
        "user": {
            "name": "John",
            "password": "secret",  # Nested sensitive
            "profile": {
                "email": "john@example.com",
                "apiKey": "key-123",  # Deeply nested sensitive
            },
        },
        "token": "bearer-token",  # Top-level sensitive
    }

    sanitized = middleware._safety_object(obj)

    assert sanitized["user"]["name"] == "John"
    assert sanitized["user"]["password"] == "[REDACTED]"
    assert sanitized["user"]["profile"]["email"] == "john@example.com"
    assert sanitized["user"]["profile"]["apiKey"] == "[REDACTED]"
    assert sanitized["token"] == "[REDACTED]"


# ── Tracing Enabled Check ─────────────────────────────────────────────────


def test_is_tracing_enabled_with_tracer():
    """_is_tracing_enabled() returns True when broker has tracer."""
    middleware = TracingMiddleware()
    broker = create_mock_broker(with_tracer=True)
    middleware.created(broker)

    assert middleware._is_tracing_enabled() is True


def test_is_tracing_enabled_without_tracer():
    """_is_tracing_enabled() returns False when no tracer."""
    middleware = TracingMiddleware()
    broker = create_mock_broker(with_tracer=False)
    middleware.created(broker)

    assert middleware._is_tracing_enabled() is False


def test_is_tracing_enabled_no_broker():
    """_is_tracing_enabled() returns False when no broker."""
    middleware = TracingMiddleware()

    assert middleware._is_tracing_enabled() is False


# ── Request/Parent ID Fallback ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_context_gets_trace_id_from_tracer():
    """Wrapped handler sets ctx.request_id from tracer if missing."""
    middleware = TracingMiddleware()
    broker = create_mock_broker(with_tracer=True)
    middleware.created(broker)

    async def handler(ctx):
        return ctx.request_id

    channel = Channel(name="test.channel", handler=handler, group="test-group")

    # Context without request_id
    ctx = create_mock_context(broker, request_id=None)
    wrapped = middleware.local_channel(handler, channel)

    result = await wrapped(ctx)

    # Should get trace ID from tracer
    assert result == "trace-123"


@pytest.mark.asyncio
async def test_context_preserves_existing_request_id():
    """Wrapped handler preserves existing ctx.request_id."""
    middleware = TracingMiddleware()
    broker = create_mock_broker(with_tracer=True)
    middleware.created(broker)

    async def handler(ctx):
        return ctx.request_id

    channel = Channel(name="test.channel", handler=handler, group="test-group")

    # Context WITH request_id
    ctx = create_mock_context(broker, request_id="existing-trace-456")
    wrapped = middleware.local_channel(handler, channel)

    result = await wrapped(ctx)

    # Should preserve existing
    assert result == "existing-trace-456"


# ── Integration Test ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_tracing_flow():
    """Test complete tracing flow: create → execute → finish."""
    middleware = TracingMiddleware()
    broker = create_mock_broker(with_tracer=True)
    middleware.created(broker)

    execution_log = []

    async def handler(ctx):
        execution_log.append("handler_start")
        await import_asyncio_and_sleep(0.01)
        execution_log.append("handler_end")
        return {"status": "ok"}

    channel = Channel(
        name="orders.process",
        handler=handler,
        group="order-processors",
        tracing={
            "spanName": lambda ctx: f"Process Order #{ctx.params.get('orderId')}",
            "tags": {"params": ["orderId"], "meta": True},
            "safetyTags": True,
        },
    )

    ctx = create_mock_context(
        broker,
        request_id="req-789",
        params={"orderId": 999, "password": "secret"},
        meta={"userId": "user-1"},
    )

    wrapped = middleware.local_channel(handler, channel)

    # Execute
    result = await wrapped(ctx)

    # Verify result
    assert result == {"status": "ok"}
    assert execution_log == ["handler_start", "handler_end"]

    # Verify span
    assert len(broker.tracer.spans) == 1
    span = broker.tracer.spans[0]

    assert span.name == "Process Order #999"
    assert span.finished is True
    assert span.error is None

    # Verify tags
    tags = span.kwargs["tags"]
    assert tags["chan"]["name"] == "orders.process"
    assert tags["chan"]["group"] == "order-processors"
    assert tags["params"] == {"orderId": 999}  # password removed by safetyTags
    assert tags["meta"] == {"userId": "user-1"}


async def import_asyncio_and_sleep(duration):
    """Helper to avoid linter warnings."""
    import asyncio

    await asyncio.sleep(duration)


# ── Edge Cases ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tracing_handles_missing_context_methods():
    """Tracing handles context without start_span/finish_span methods."""
    middleware = TracingMiddleware()
    broker = create_mock_broker(with_tracer=True)
    middleware.created(broker)

    async def handler(ctx):
        return "result"

    channel = Channel(name="test.channel", handler=handler, group="test-group")

    # Context without span methods
    class SimpleContext:
        request_id = "req-1"
        parent_id = None
        params = {}
        meta = {}

    ctx = SimpleContext()

    wrapped = middleware.local_channel(handler, channel)

    # Should not crash
    result = await wrapped(ctx)
    assert result == "result"


@pytest.mark.asyncio
async def test_tracing_options_normalization():
    """Tracing options normalize True/False to {enabled: bool}."""
    middleware = TracingMiddleware()
    broker = create_mock_broker(with_tracer=True)
    middleware.created(broker)

    async def handler(ctx):
        pass

    # Test tracing=True
    channel1 = Channel(name="ch1", handler=handler, group="g1", tracing=True)
    ctx = create_mock_context(broker)

    wrapped1 = middleware.local_channel(handler, channel1)
    await wrapped1(ctx)

    # Should create span
    assert len(broker.tracer.spans) == 1

    # Test tracing=False
    broker.tracer.spans = []  # Reset
    channel2 = Channel(name="ch2", handler=handler, group="g2", tracing=False)

    wrapped2 = middleware.local_channel(handler, channel2)
    await wrapped2(ctx)

    # Should NOT create span
    assert len(broker.tracer.spans) == 0
