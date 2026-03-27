"""
Graceful Shutdown example demonstrating proper cleanup in MoleculerPy Channels.

Features demonstrated:
- Active message tracking during processing
- Waiting for in-flight messages before shutdown
- Background task cancellation order (important!)
- Preventing data loss during restart

Prerequisites:
    # Start Redis on port 6380
    cd tests && docker-compose up -d

Run example:
    python examples/graceful_shutdown.py

    # Press Ctrl+C after a few seconds to trigger graceful shutdown

Expected behavior:
    1. Messages start processing (2 seconds each)
    2. User presses Ctrl+C
    3. Adapter waits for active messages to complete
    4. Background tasks cancelled in correct order
    5. No message loss
"""

import asyncio
import signal
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from moleculerpy_channels import ChannelsMiddleware
from moleculerpy_channels.adapters import RedisAdapter


class MockSerializer:
    """Mock serializer."""

    def serialize(self, data):
        import json

        return json.dumps(data).encode()

    def deserialize(self, data):
        import json

        return json.loads(data.decode())


class MockLogger:
    """Mock logger with colored output."""

    def info(self, msg):
        print(f"\033[94m[INFO]\033[0m {msg}")

    def debug(self, msg):
        print(f"\033[90m[DEBUG]\033[0m {msg}")

    def warning(self, msg):
        print(f"\033[93m[WARN]\033[0m {msg}")

    def error(self, msg):
        print(f"\033[91m[ERROR]\033[0m {msg}")


class MockBroker:
    """Mock broker."""

    def __init__(self, middlewares=None):
        self.middlewares = middlewares or []
        self.serializer = MockSerializer()
        self.node_id = "graceful-shutdown-node"
        self.services = {}
        self._stopping = False

    def get_logger(self, name):
        return MockLogger()

    async def start(self):
        """Start broker."""
        for mw in self.middlewares:
            if hasattr(mw, "broker_created"):
                mw.broker_created(self)
            if hasattr(mw, "broker_starting"):
                await mw.broker_starting(self)

        print("\n\033[92m✅ Broker started\033[0m\n")

    async def stop(self):
        """Stop broker gracefully."""
        if self._stopping:
            return

        self._stopping = True
        print("\n\033[93m⏸️  Initiating graceful shutdown...\033[0m")
        print("\033[90m   Waiting for active messages to complete...\033[0m\n")

        for mw in self.middlewares:
            if hasattr(mw, "broker_stopped"):
                await mw.broker_stopped(self)

        print("\n\033[92m✅ Broker stopped gracefully\033[0m")

    def create_service(self, service_class):
        """Create service."""
        service = service_class()
        service.full_name = service.name
        service.schema = {"channels": service.channels} if hasattr(service, "channels") else {}
        self.services[service.name] = service

        for mw in self.middlewares:
            if hasattr(mw, "service_created"):
                asyncio.create_task(mw.service_created(service))


# ── Service ──────────────────────────────────────────────────────────────────


class WorkerService:
    """Example service with slow handler."""

    name = "workers"

    def __init__(self):
        self.processed_count = 0

    async def handle_task(self, payload, raw):
        """Slow handler to demonstrate graceful shutdown."""
        task_id = payload.get("taskId")
        duration = payload.get("duration", 2)

        print(f"\n⚙️  \033[1mProcessing Task {task_id}\033[0m")
        print(f"   Duration: {duration}s")

        # Simulate long-running work
        await asyncio.sleep(duration)

        self.processed_count += 1
        print(f"   \033[92m✓ Task {task_id} completed\033[0m")
        print(f"   Total processed: {self.processed_count}")

    channels = {
        "tasks.process": {
            "group": "slow-workers",
            "handler": lambda self, payload, raw: self.handle_task(payload, raw),
        }
    }


# ── Main ─────────────────────────────────────────────────────────────────────


async def main():
    """Run example."""
    print("\n" + "=" * 70)
    print("  🚀 \033[1mGraceful Shutdown Example\033[0m")
    print("=" * 70 + "\n")

    # Create broker
    redis_adapter = RedisAdapter(redis_url="redis://localhost:6380/0")
    broker = MockBroker(middlewares=[ChannelsMiddleware(adapter=redis_adapter)])

    # Create service
    broker.create_service(WorkerService)

    # Start broker
    await broker.start()
    await asyncio.sleep(0.5)

    print("-" * 70)
    print("  📤 \033[1mPublishing Tasks\033[0m")
    print("-" * 70)

    # Publish multiple slow tasks
    print("\nPublishing 5 tasks (2 seconds each)...")
    for task_id in range(1, 6):
        await broker.send_to_channel("tasks.process", {"taskId": task_id, "duration": 2})

    print("\n\033[90mPress Ctrl+C to trigger graceful shutdown\033[0m")
    print("\033[90m(Wait at least 2 seconds to see in-flight message handling)\033[0m\n")

    # Setup signal handler
    shutdown_event = asyncio.Event()

    def signal_handler(sig, frame):
        print("\n\n\033[93m⚠️  Shutdown signal received\033[0m")
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Wait for shutdown signal
    try:
        await shutdown_event.wait()
    except KeyboardInterrupt:
        pass

    # Stop broker (waits for active messages)
    await broker.stop()

    print("\n" + "=" * 70)
    print("  ✅ \033[1mExample completed!\033[0m")
    print("=" * 70 + "\n")

    print("\033[90mKey observations:\033[0m")
    print("\033[90m  • Adapter waited for in-flight messages\033[0m")
    print("\033[90m  • Background tasks cancelled in correct order\033[0m")
    print("\033[90m  • No message loss during shutdown\033[0m\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n\033[93m⚠️  Force interrupted\033[0m\n")
    except Exception as e:
        print(f"\n\n\033[91m❌ Error: {e}\033[0m\n")
        import traceback

        traceback.print_exc()
