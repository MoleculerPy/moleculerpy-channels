"""
E2E test fixtures for multi-broker cluster testing.

Based on MoleculerPy reference patterns from /sources/moleculerpy/tests/e2e/.
"""

import asyncio
import sys
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest_asyncio

# Add local moleculerpy to path for E2E tests
MOLECULERPY_ROOT = Path(__file__).parent.parent.parent.parent / "moleculerpy"
if MOLECULERPY_ROOT.exists():
    sys.path.insert(0, str(MOLECULERPY_ROOT))

from moleculerpy import Service, ServiceBroker, Settings  # noqa: E402
from moleculerpy.decorators import action  # noqa: E402

from moleculerpy_channels import ChannelsMiddleware  # noqa: E402
from moleculerpy_channels.adapters import FakeAdapter, RedisAdapter  # noqa: E402


@pytest_asyncio.fixture
async def single_broker() -> AsyncGenerator[ServiceBroker, None]:
    """
    Single broker for simple E2E tests.

    Use when: Testing single-node behavior (no distribution).
    """
    # Use FakeAdapter for E2E tests (no Redis dependency)
    adapter = FakeAdapter()

    settings = Settings(
        transporter="nats://localhost:4222",
        prefer_local=False,
    )

    broker = ServiceBroker(
        id="e2e-node",
        settings=settings,
        middlewares=[ChannelsMiddleware(adapter=adapter)],
    )

    # Register test services
    await broker.register(CounterService("e2e-node"))
    await broker.register(PublisherService("e2e-node"))
    await broker.start()

    await asyncio.sleep(0.3)  # Discovery

    yield broker

    await broker.stop()


@pytest_asyncio.fixture
async def three_broker_cluster() -> AsyncGenerator[list[ServiceBroker], None]:
    """
    3-broker cluster with shared Redis adapter and NATS transporter.

    Architecture:
    ┌─────────┐    ┌─────────┐    ┌─────────┐
    │ node-0  │────│ node-1  │────│ node-2  │
    │         │    │         │    │         │
    └────┬────┘    └────┬────┘    └────┬────┘
         │              │              │
         └──────────────┼──────────────┘
                  NATS Transporter
         ┌──────────────┼──────────────┐
         │              │              │
         └──────────────▼──────────────┘
              Redis Streams (shared)

    Use when: Testing consumer group balancing, load distribution.
    """
    brokers: list[ServiceBroker] = []

    for i in range(3):
        node_id = f"node-{i}"

        # Each broker needs its own RedisAdapter instance
        # Redis Streams handle distribution via consumer groups
        adapter = RedisAdapter(redis_url="redis://localhost:6380")

        settings = Settings(
            transporter="nats://localhost:4222",
            prefer_local=False,
        )

        broker = ServiceBroker(
            id=node_id,
            settings=settings,
            middlewares=[ChannelsMiddleware(adapter=adapter)],
        )

        # Register services with node-specific ID
        await broker.register(CounterService(node_id))
        await broker.register(PublisherService(node_id))
        await broker.register(ConsumerService(node_id))

        brokers.append(broker)

    # Start all
    for broker in brokers:
        await broker.start()

    # Wait for discovery (NATS transporter)
    await asyncio.sleep(0.5)

    yield brokers

    # Cleanup
    for broker in brokers:
        await broker.stop()


# ── Test Services ────────────────────────────────────────────────────────────


class CounterService(Service):
    """
    Service that tracks call count per node.

    Use for: Load balancing verification.
    """

    name = "counter"

    def __init__(self, node_id: str):
        super().__init__()
        self._node_id = node_id
        self._count = 0

    @action()
    async def increment(self, ctx) -> dict:
        """Return node ID and increment count."""
        self._count += 1
        return {"node_id": self._node_id, "count": self._count}


class PublisherService(Service):
    """
    Service that publishes to channels.

    Use for: Message publishing via action call.
    """

    name = "publisher"

    def __init__(self, node_id: str):
        super().__init__()
        self._node_id = node_id

    @action()
    async def send_message(self, ctx) -> dict:
        """Send message to specified channel."""
        channel = ctx.params.get("channel", "test.events")
        payload = ctx.params.get("payload", {})

        await self.broker.send_to_channel(channel, payload, {"ctx": ctx})

        return {"node_id": self._node_id, "sent": True}


class ConsumerService(Service):
    """
    Service that consumes from channels.

    Use for: Tracking which node received messages.
    """

    name = "consumer"

    def __init__(self, node_id: str):
        super().__init__()
        self._node_id = node_id
        self._received = []

    @property
    def schema(self):
        """Return service schema including channels."""
        channels = {
            "test.events": {"group": "event-processors", "context": True, "handler": self._on_event}
        }

        # Only node-0 subscribes to "test.single" (for test_single_consumer_gets_all)
        if self._node_id == "node-0":
            channels["test.single"] = {
                "group": "single-processors",
                "context": True,
                "handler": self._on_event,
            }

        return {"channels": channels}

    async def _on_event(self, ctx, raw):
        """Store received event."""
        self._received.append(
            {
                "node_id": self._node_id,
                "payload": ctx.params,
                "request_id": ctx.request_id,
                "parent_id": ctx.parent_id,
            }
        )

    @action()
    async def get_received(self, ctx) -> list:
        """Return all received messages."""
        return self._received

    @action()
    async def clear_received(self, ctx) -> dict:
        """Clear received messages."""
        count = len(self._received)
        self._received.clear()
        return {"cleared": count}
