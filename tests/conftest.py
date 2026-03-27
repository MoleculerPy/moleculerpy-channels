"""Shared pytest fixtures for all tests."""

import asyncio
import os

import pytest
import redis.asyncio as aioredis

NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
NATS_CONNECT_TIMEOUT = 0.5


async def _cleanup_nats_streams() -> None:
    """Best-effort cleanup of JetStream state with fail-fast connection settings."""
    from nats.aio.client import Client as NATS

    nc = NATS()
    try:
        await nc.connect(
            NATS_URL,
            connect_timeout=NATS_CONNECT_TIMEOUT,
            max_reconnect_attempts=0,
            allow_reconnect=False,
        )
        js = nc.jetstream()

        streams = await js.streams_info()
        for stream in streams:
            try:
                await js.delete_stream(stream.config.name)
            except Exception:
                pass
    except Exception:
        # NATS is optional for many test paths; missing infrastructure should not hang tests.
        return
    finally:
        if nc.is_connected:
            await nc.close()


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def redis_url():
    """Get Redis URL from environment."""
    return os.getenv("REDIS_URL", "redis://localhost:6380/0")


@pytest.fixture
async def redis_client(redis_url):
    """Create Redis client for testing."""
    client = await aioredis.from_url(redis_url, decode_responses=False)

    # Clear database before test
    await client.flushdb()

    yield client

    # Cleanup after test
    await client.flushdb()
    await client.aclose()


@pytest.fixture
def mock_broker():
    """Create mock broker for testing."""

    class MockSerializer:
        def serialize(self, data):
            import json
            return json.dumps(data).encode()

        def deserialize(self, data):
            import json
            return json.loads(data.decode())

    class MockLogger:
        def info(self, msg):
            print(f"[INFO] {msg}")

        def debug(self, msg):
            print(f"[DEBUG] {msg}")

        def warning(self, msg):
            print(f"[WARN] {msg}")

        def error(self, msg):
            print(f"[ERROR] {msg}")

    class MockBroker:
        def __init__(self):
            self.serializer = MockSerializer()
            self.node_id = "test-node-1"

        def get_logger(self, name):
            return MockLogger()

    return MockBroker()


@pytest.fixture
async def redis_adapter(redis_client, mock_broker):
    """Create RedisAdapter with test Redis."""
    from moleculerpy_channels.adapters.redis import RedisAdapter

    # Clear all streams before test
    await redis_client.flushdb()

    adapter = RedisAdapter(redis_url="redis://localhost:6380/0")
    adapter.init(mock_broker, mock_broker.get_logger("RedisAdapter"))

    await adapter.connect()

    yield adapter

    await adapter.disconnect()

    # Clear all streams after test
    await redis_client.flushdb()


@pytest.fixture
async def nats_adapter(mock_broker):
    """Create NatsAdapter with test NATS (includes cleanup)."""
    from moleculerpy_channels.adapters.nats import NatsAdapter

    # Cleanup NATS streams BEFORE test
    await _cleanup_nats_streams()

    # Create adapter
    adapter = NatsAdapter(url=NATS_URL)
    adapter.init(mock_broker, mock_broker.get_logger("NatsAdapter"))

    await adapter.connect()

    yield adapter

    await adapter.disconnect()

    # Cleanup NATS streams AFTER test
    await _cleanup_nats_streams()


@pytest.fixture(autouse=True, scope="function")
async def cleanup_nats_streams(request: pytest.FixtureRequest):
    """Auto-cleanup NATS streams only for tests that actually need NATS."""
    if "integration" not in request.keywords and "e2e" not in request.keywords:
        yield
        return

    await _cleanup_nats_streams()
    yield
    await _cleanup_nats_streams()
