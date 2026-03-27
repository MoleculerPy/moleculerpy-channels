"""
Production example demonstrating MoleculerPy Channels with RedisAdapter.

Features demonstrated:
- Redis Streams for persistent messaging
- Consumer groups for horizontal scaling
- Automatic retry via XAUTOCLAIM (min_idle_time)
- Dead Letter Queue for failed messages (after max_retries)
- Error metadata preservation in DLQ
- Multiple services with different configurations

Prerequisites:
    # Start Redis on port 6380 (to avoid FalkorDB conflict)
    cd tests && docker-compose up -d

    # Verify Redis is running
    docker ps | grep redis

Run example:
    python examples/redis_example.py

Expected output:
    - orders.created events processed successfully
    - orders.payment events: odd IDs succeed, even IDs fail → retry → DLQ
    - notifications.send processed successfully
    - DLQ shows 2 failed payments (order IDs 202, 204)
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from moleculerpy_channels import ChannelsMiddleware, DeadLetteringOptions
from moleculerpy_channels.adapters import RedisAdapter


class MockSerializer:
    """Mock serializer for example."""

    def serialize(self, data):
        import json

        return json.dumps(data).encode()

    def deserialize(self, data):
        import json

        return json.loads(data.decode())


class MockLogger:
    """Mock logger for example."""

    def info(self, msg):
        print(f"\033[94m[INFO]\033[0m {msg}")

    def debug(self, msg):
        print(f"\033[90m[DEBUG]\033[0m {msg}")

    def warning(self, msg):
        print(f"\033[93m[WARN]\033[0m {msg}")

    def error(self, msg):
        print(f"\033[91m[ERROR]\033[0m {msg}")


class MockBroker:
    """Mock broker for example."""

    def __init__(self, middlewares=None):
        self.middlewares = middlewares or []
        self.serializer = MockSerializer()
        self.node_id = "example-node-1"
        self.services = {}

    def get_logger(self, name):
        return MockLogger()

    async def start(self):
        """Start broker and middleware."""
        for mw in self.middlewares:
            if hasattr(mw, "broker_created"):
                mw.broker_created(self)
            if hasattr(mw, "broker_starting"):
                await mw.broker_starting(self)

        print("\n\033[92m✅ Broker started with Redis adapter\033[0m\n")

    async def stop(self):
        """Stop broker and middleware."""
        for mw in self.middlewares:
            if hasattr(mw, "broker_stopped"):
                await mw.broker_stopped(self)

        print("\n\033[92m✅ Broker stopped\033[0m")

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

        print(f"\033[92m✅ Service '{service.name}' created\033[0m")


# ── Services ─────────────────────────────────────────────────────────────────


class OrderService:
    """Example service with channel handlers."""

    name = "orders"

    # Simple handler
    def handle_order_created(self, payload, raw):
        print(f"\n📦 \033[1mOrder Created\033[0m")
        print(f"   Order ID: {payload.get('orderId')}")
        print(f"   Total: ${payload.get('total')}")

    # Handler with error (for DLQ demonstration)
    def handle_order_payment(self, payload, raw):
        order_id = payload.get("orderId")
        print(f"\n💳 \033[1mProcessing Payment\033[0m")
        print(f"   Order ID: {order_id}")

        # Simulate failure for even order IDs
        if order_id % 2 == 0:
            raise ValueError(f"Payment failed for order {order_id}")

        print(f"   \033[92m✓ Payment successful\033[0m")

    channels = {
        # Simple lambda handler
        "orders.created": lambda self, payload, raw: self.handle_order_created(payload, raw),
        # Full definition with DLQ
        "orders.payment": {
            "group": "payment-processors",
            "max_retries": 3,
            "dead_lettering": DeadLetteringOptions(
                enabled=True,
                queue_name="FAILED_PAYMENTS",
                error_info_ttl=86400,  # 24 hours
            ),
            "handler": lambda self, payload, raw: self.handle_order_payment(payload, raw),
        },
    }


class NotificationService:
    """Example service for notifications."""

    name = "notifications"

    def handle_notification(self, payload, raw):
        print(f"\n🔔 \033[1mNotification\033[0m")
        print(f"   Type: {payload.get('type')}")
        print(f"   Message: {payload.get('message')}")

    channels = {
        "notifications.send": {
            "group": "notification-workers",
            "handler": lambda self, payload, raw: self.handle_notification(payload, raw),
        }
    }


# ── Main ─────────────────────────────────────────────────────────────────────


async def main():
    """Run example."""
    print("\n" + "=" * 70)
    print("  🚀 \033[1mMoleculerPy Channels - Redis Example\033[0m")
    print("=" * 70 + "\n")

    # Create broker with Redis adapter
    # Note: Using port 6380 to avoid conflict with FalkorDB (if running)
    redis_adapter = RedisAdapter(redis_url="redis://localhost:6380/0")
    broker = MockBroker(middlewares=[ChannelsMiddleware(adapter=redis_adapter)])

    # Create services
    broker.create_service(OrderService)
    broker.create_service(NotificationService)

    # Start broker (connects adapter, subscribes channels)
    await broker.start()

    # Give middleware time to process service_created
    await asyncio.sleep(0.5)

    print("\n" + "-" * 70)
    print("  📤 \033[1mPublishing Messages\033[0m")
    print("-" * 70)

    # 1. Publish order created events
    print("\n1️⃣  Publishing order.created events...")
    for order_id in [101, 102, 103]:
        await broker.send_to_channel(
            "orders.created", {"orderId": order_id, "total": 99.99 + order_id}
        )

    await asyncio.sleep(1)

    # 2. Publish payment events (some will fail for DLQ demo)
    print("\n2️⃣  Publishing order.payment events (some will fail)...")
    for order_id in [201, 202, 203, 204]:
        await broker.send_to_channel("orders.payment", {"orderId": order_id, "amount": 50.0})

    await asyncio.sleep(2)

    # 3. Publish notifications
    print("\n3️⃣  Publishing notification events...")
    await broker.send_to_channel(
        "notifications.send", {"type": "email", "message": "Order confirmed!"}
    )

    await asyncio.sleep(1)

    print("\n" + "-" * 70)
    print("  ⏸️  \033[1mWaiting for processing...\033[0m")
    print("-" * 70)

    # Wait for processing (including retries)
    await asyncio.sleep(5)

    print("\n" + "-" * 70)
    print("  📊 \033[1mChecking Results\033[0m")
    print("-" * 70)

    # Check DLQ for failed payments
    redis_client = redis_adapter.redis
    dlq_messages = await redis_client.xrange(b"FAILED_PAYMENTS", b"-", b"+")

    print(f"\n📮 Dead Letter Queue: {len(dlq_messages)} failed messages")
    for msg_id, fields in dlq_messages:
        print(f"   - Message {msg_id.decode()}")
        if b"x-original-channel" in fields:
            print(f"     Original: {fields[b'x-original-channel'].decode()}")
        if b"x-error-message" in fields:
            print(f"     Error: {fields[b'x-error-message'].decode()}")

    print("\n" + "-" * 70)
    print("  🛑 \033[1mStopping Broker\033[0m")
    print("-" * 70 + "\n")

    # Stop broker (disconnects adapter)
    await broker.stop()

    print("\n" + "=" * 70)
    print("  ✅ \033[1mExample completed!\033[0m")
    print("=" * 70 + "\n")

    print("\033[90mTip: Check Redis with:\033[0m")
    print("\033[90m  redis-cli XINFO GROUPS orders.created\033[0m")
    print("\033[90m  redis-cli XRANGE FAILED_PAYMENTS - +\033[0m\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n\033[93m⚠️  Interrupted by user\033[0m\n")
    except Exception as e:
        print(f"\n\n\033[91m❌ Error: {e}\033[0m\n")
        import traceback

        traceback.print_exc()
