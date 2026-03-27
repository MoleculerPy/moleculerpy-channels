"""
Simple example demonstrating MoleculerPy Channels with FakeAdapter.

Features demonstrated:
- Basic channel subscription with handler
- Two handler patterns: lambda and dict config
- In-memory message delivery (no external dependencies)
- FakeAdapter for testing without Redis

FakeAdapter characteristics:
- Messages stored in memory (lost on restart)
- No persistence, no retry, no DLQ
- Ideal for unit testing and development
- Production use requires RedisAdapter (see redis_example.py)

Run with:
    python examples/simple.py

Note:
    This example uses MockBroker instead of actual MoleculerPy broker
    to avoid dependency on the full MoleculerPy framework. For production,
    use actual ServiceBroker from moleculerpy package.
"""

import asyncio

# Note: Import from actual package after install
# from moleculerpy import ServiceBroker, Service
# from moleculerpy_channels import ChannelsMiddleware
# from moleculerpy_channels.adapters import FakeAdapter


# For now, using mock classes for demonstration
class MockSerializer:
    """Mock serializer."""

    def serialize(self, data):
        import json

        return json.dumps(data).encode()

    def deserialize(self, data):
        import json

        return json.loads(data.decode())


class MockBroker:
    """Mock broker for example."""

    def __init__(self, middlewares=None):
        self.middlewares = middlewares or []
        self.serializer = MockSerializer()
        self.node_id = "node-1"
        self.services = {}

    def get_logger(self, name):
        class Logger:
            def info(self, msg):
                print(f"[INFO] {msg}")

            def debug(self, msg):
                print(f"[DEBUG] {msg}")

            def warning(self, msg):
                print(f"[WARN] {msg}")

            def error(self, msg):
                print(f"[ERROR] {msg}")

        return Logger()

    async def start(self):
        """Start broker and middleware."""
        for mw in self.middlewares:
            if hasattr(mw, "broker_created"):
                mw.broker_created(self)
            if hasattr(mw, "broker_starting"):
                await mw.broker_starting(self)

        print("✅ Broker started")

    async def stop(self):
        """Stop broker and middleware."""
        for mw in self.middlewares:
            if hasattr(mw, "broker_stopped"):
                await mw.broker_stopped(self)

        print("✅ Broker stopped")

    def create_service(self, service_class):
        """Create service instance."""
        service = service_class()
        service.full_name = service.name
        service.schema = {"channels": service.channels} if hasattr(service, "channels") else {}
        self.services[service.name] = service

        # Trigger middleware hook
        for mw in self.middlewares:
            if hasattr(mw, "service_created"):
                asyncio.create_task(mw.service_created(service))

        print(f"✅ Service '{service.name}' created")


# Import actual Channels middleware
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from moleculerpy_channels import ChannelsMiddleware
from moleculerpy_channels.adapters import FakeAdapter


class OrderService:
    """Example service with channel handlers."""

    name = "orders"

    channels = {
        "orders.created": lambda self, payload, raw: self.handle_order_created(payload, raw),
        "orders.cancelled": {
            "group": "order-processors",
            "max_retries": 3,
            "handler": lambda self, payload, raw: self.handle_order_cancelled(payload, raw),
        },
    }

    async def handle_order_created(self, payload, raw):
        """Handle order created event."""
        print(f"📦 Order created: {payload}")

    async def handle_order_cancelled(self, payload, raw):
        """Handle order cancelled event."""
        print(f"❌ Order cancelled: {payload}")


async def main():
    """Run example."""
    print("🚀 Starting MoleculerPy Channels example...\n")

    # Create broker with Channels middleware
    broker = MockBroker(middlewares=[ChannelsMiddleware(adapter=FakeAdapter())])

    # Create service
    broker.create_service(OrderService)

    # Start broker (connects adapter, subscribes channels)
    await broker.start()

    # Give middleware time to process service_created
    await asyncio.sleep(0.2)

    print("\n📤 Publishing messages...\n")

    # Send messages
    await broker.send_to_channel("orders.created", {"orderId": 123, "total": 99.99})

    await broker.send_to_channel("orders.cancelled", {"orderId": 456, "reason": "customer request"})

    # Wait for delivery
    await asyncio.sleep(0.5)

    print("\n🛑 Stopping broker...\n")

    # Stop broker (disconnects adapter)
    await broker.stop()

    print("\n✅ Example completed!")


if __name__ == "__main__":
    asyncio.run(main())
