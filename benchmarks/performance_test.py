"""
Performance benchmarks for MoleculerPy Channels Redis Adapter.

Measures:
- Throughput (messages/sec)
- Latency (p50, p95, p99)
- Memory usage
- Concurrent consumers

Run: python benchmarks/performance_test.py
"""

import asyncio
import statistics
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import redis.asyncio as aioredis

from moleculerpy_channels.adapters.redis import RedisAdapter
from moleculerpy_channels.channel import Channel


class MockBroker:
    """Mock MoleculerPy broker for testing."""

    def __init__(self):
        self.serializer = MockSerializer()
        self.node_id = "benchmark-node"

    def get_logger(self, name: str):
        return MockLogger()


class MockSerializer:
    """Mock serializer."""

    def serialize(self, data: Any) -> bytes:
        import json
        return json.dumps(data).encode()

    def deserialize(self, data: bytes) -> Any:
        import json
        return json.loads(data.decode())


class MockLogger:
    """Mock logger (silent)."""

    def info(self, msg: str):
        pass

    def debug(self, msg: str):
        pass

    def warning(self, msg: str):
        pass

    def error(self, msg: str):
        pass


# ============================================================================
# Benchmark: Throughput - Publish
# ============================================================================


async def benchmark_publish_throughput(
    adapter: RedisAdapter, num_messages: int = 10000
) -> dict:
    """
    Measure publish throughput (messages/second).

    Args:
        adapter: RedisAdapter instance
        num_messages: Number of messages to publish

    Returns:
        dict with throughput metrics
    """
    channel_name = "bench.publish"

    start_time = time.perf_counter()

    for i in range(num_messages):
        await adapter.publish(channel_name, {"index": i}, {})

    elapsed = time.perf_counter() - start_time
    throughput = num_messages / elapsed

    return {
        "name": "Publish Throughput",
        "messages": num_messages,
        "elapsed_sec": round(elapsed, 2),
        "throughput_msg_per_sec": round(throughput, 2),
    }


# ============================================================================
# Benchmark: Throughput - Consume
# ============================================================================


async def benchmark_consume_throughput(
    adapter: RedisAdapter, num_messages: int = 10000
) -> dict:
    """
    Measure consume throughput (messages/second).

    Args:
        adapter: RedisAdapter instance
        num_messages: Number of messages to consume

    Returns:
        dict with throughput metrics
    """
    channel_name = "bench.consume"
    received_count = 0
    received_event = asyncio.Event()
    first_received_time = None
    last_received_time = None

    async def handler(payload: Any, raw: Any):
        nonlocal received_count, first_received_time, last_received_time
        if first_received_time is None:
            first_received_time = time.perf_counter()
        received_count += 1
        last_received_time = time.perf_counter()
        if received_count >= num_messages:
            received_event.set()

    # Subscribe
    channel = Channel(
        name=channel_name,
        group="bench-group",
        handler=handler,
    )
    channel.id = "bench-consumer"

    await adapter.subscribe(channel, None)
    await asyncio.sleep(0.5)  # Let consumer start

    # Publish messages
    publish_start = time.perf_counter()
    for i in range(num_messages):
        await adapter.publish(channel_name, {"index": i}, {})

    # Wait for all messages consumed
    await asyncio.wait_for(received_event.wait(), timeout=30.0)

    # Measure total time (publish + consume)
    total_elapsed = time.perf_counter() - publish_start
    consume_elapsed = last_received_time - first_received_time if (last_received_time and first_received_time) else total_elapsed

    throughput = num_messages / consume_elapsed

    # Cleanup
    await adapter.unsubscribe(channel)

    return {
        "name": "Consume Throughput",
        "messages": num_messages,
        "total_elapsed_sec": round(total_elapsed, 2),
        "consume_elapsed_sec": round(consume_elapsed, 2),
        "throughput_msg_per_sec": round(throughput, 2),
    }


# ============================================================================
# Benchmark: Latency - Publish
# ============================================================================


async def benchmark_publish_latency(
    adapter: RedisAdapter, num_samples: int = 1000
) -> dict:
    """
    Measure publish latency distribution (p50, p95, p99).

    Args:
        adapter: RedisAdapter instance
        num_samples: Number of samples

    Returns:
        dict with latency percentiles
    """
    channel_name = "bench.latency.publish"
    latencies: list[float] = []

    for i in range(num_samples):
        start = time.perf_counter()
        await adapter.publish(channel_name, {"index": i}, {})
        latency_ms = (time.perf_counter() - start) * 1000
        latencies.append(latency_ms)

    latencies.sort()

    return {
        "name": "Publish Latency",
        "samples": num_samples,
        "p50_ms": round(statistics.median(latencies), 3),
        "p95_ms": round(latencies[int(num_samples * 0.95)], 3),
        "p99_ms": round(latencies[int(num_samples * 0.99)], 3),
        "min_ms": round(min(latencies), 3),
        "max_ms": round(max(latencies), 3),
        "avg_ms": round(statistics.mean(latencies), 3),
    }


# ============================================================================
# Benchmark: Latency - End-to-End
# ============================================================================


async def benchmark_e2e_latency(
    adapter: RedisAdapter, num_samples: int = 1000
) -> dict:
    """
    Measure end-to-end latency (publish → consume).

    Args:
        adapter: RedisAdapter instance
        num_samples: Number of samples

    Returns:
        dict with latency percentiles
    """
    channel_name = "bench.latency.e2e"
    latencies: list[float] = []
    pending_messages: dict[int, float] = {}  # msg_id -> start_time
    received_count = 0
    received_event = asyncio.Event()

    async def handler(payload: Any, raw: Any):
        nonlocal received_count
        msg_index = payload.get("index")
        if msg_index is not None and msg_index in pending_messages:
            start_time = pending_messages.pop(msg_index)
            latency_ms = (time.perf_counter() - start_time) * 1000
            latencies.append(latency_ms)

        received_count += 1
        if received_count >= num_samples:
            received_event.set()

    # Subscribe
    channel = Channel(
        name=channel_name,
        group="bench-e2e-group",
        handler=handler,
    )
    channel.id = "bench-e2e-consumer"

    await adapter.subscribe(channel, None)
    await asyncio.sleep(0.5)  # Let consumer start

    # Publish with timestamps
    for i in range(num_samples):
        pending_messages[i] = time.perf_counter()
        await adapter.publish(channel_name, {"index": i}, {})

    # Wait for all messages
    await asyncio.wait_for(received_event.wait(), timeout=30.0)

    # Cleanup
    await adapter.unsubscribe(channel)

    latencies.sort()

    return {
        "name": "End-to-End Latency",
        "samples": num_samples,
        "p50_ms": round(statistics.median(latencies), 3),
        "p95_ms": round(latencies[int(num_samples * 0.95)], 3),
        "p99_ms": round(latencies[int(num_samples * 0.99)], 3),
        "min_ms": round(min(latencies), 3),
        "max_ms": round(max(latencies), 3),
        "avg_ms": round(statistics.mean(latencies), 3),
    }


# ============================================================================
# Benchmark: Concurrent Consumers
# ============================================================================


async def benchmark_concurrent_consumers(
    adapter: RedisAdapter, num_consumers: int = 5, num_messages: int = 5000
) -> dict:
    """
    Measure throughput with multiple concurrent consumers.

    Args:
        adapter: RedisAdapter instance
        num_consumers: Number of concurrent consumers
        num_messages: Total messages to process

    Returns:
        dict with throughput metrics
    """
    channel_name = "bench.concurrent"
    received_counts: list[int] = [0] * num_consumers
    total_received = 0
    received_event = asyncio.Event()

    def make_handler(consumer_id: int):
        async def handler(payload: Any, raw: Any):
            nonlocal total_received
            received_counts[consumer_id] += 1
            total_received += 1
            if total_received >= num_messages:
                received_event.set()

        return handler

    # Subscribe multiple consumers
    channels = []
    for i in range(num_consumers):
        channel = Channel(
            name=channel_name,
            group="bench-concurrent-group",
            handler=make_handler(i),
        )
        channel.id = f"bench-consumer-{i}"
        channels.append(channel)

        await adapter.subscribe(channel, None)

    await asyncio.sleep(0.5)  # Let consumers start

    # Publish messages
    publish_start = time.perf_counter()
    for i in range(num_messages):
        await adapter.publish(channel_name, {"index": i}, {})

    # Measure consume time
    consume_start = time.perf_counter()
    await asyncio.wait_for(received_event.wait(), timeout=30.0)
    elapsed = time.perf_counter() - consume_start

    throughput = num_messages / elapsed

    # Cleanup
    for channel in channels:
        await adapter.unsubscribe(channel)

    return {
        "name": "Concurrent Consumers",
        "consumers": num_consumers,
        "messages": num_messages,
        "elapsed_sec": round(elapsed, 2),
        "throughput_msg_per_sec": round(throughput, 2),
        "distribution": received_counts,
    }


# ============================================================================
# Benchmark: Memory Usage
# ============================================================================


async def benchmark_memory_usage(
    adapter: RedisAdapter, num_channels: int = 10, num_active_messages: int = 100
) -> dict:
    """
    Measure memory usage with multiple channels and active messages.

    Args:
        adapter: RedisAdapter instance
        num_channels: Number of channels to create
        num_active_messages: Messages in-flight per channel

    Returns:
        dict with memory metrics
    """
    # Start memory tracking
    tracemalloc.start()
    snapshot_before = tracemalloc.take_snapshot()

    channels = []
    processing_events: list[asyncio.Event] = []

    # Create channels with slow handlers
    for i in range(num_channels):
        can_finish = asyncio.Event()
        processing_events.append(can_finish)

        async def make_handler(event: asyncio.Event):
            async def handler(payload: Any, raw: Any):
                await event.wait()  # Block until released

            return handler

        handler_fn = await make_handler(can_finish)

        channel = Channel(
            name=f"bench.memory.{i}",
            group=f"bench-memory-group-{i}",
            handler=handler_fn,
        )
        channel.id = f"bench-memory-consumer-{i}"
        channels.append(channel)

        await adapter.subscribe(channel, None)

    await asyncio.sleep(0.5)

    # Publish messages (will be in-flight)
    for i, channel in enumerate(channels):
        for j in range(num_active_messages):
            await adapter.publish(channel.name, {"channel": i, "index": j}, {})
        # Small pause between channels to avoid connection flood
        await asyncio.sleep(0.1)

    await asyncio.sleep(1.0)  # Let messages accumulate

    # Take memory snapshot
    snapshot_after = tracemalloc.take_snapshot()
    top_stats = snapshot_after.compare_to(snapshot_before, "lineno")

    total_memory_kb = sum(stat.size for stat in top_stats) / 1024

    # Release handlers and cleanup
    for event in processing_events:
        event.set()

    await asyncio.sleep(1.0)  # Let messages process

    for channel in channels:
        await adapter.unsubscribe(channel)

    tracemalloc.stop()

    return {
        "name": "Memory Usage",
        "channels": num_channels,
        "active_messages_per_channel": num_active_messages,
        "total_active_messages": num_channels * num_active_messages,
        "memory_usage_kb": round(total_memory_kb, 2),
        "memory_per_channel_kb": round(total_memory_kb / num_channels, 2),
        "memory_per_message_kb": round(
            total_memory_kb / (num_channels * num_active_messages), 3
        ),
    }


# ============================================================================
# Main Benchmark Runner
# ============================================================================


async def run_benchmarks():
    """Run all benchmarks and print results."""
    print("=" * 80)
    print("MoleculerPy Channels - Performance Benchmarks")
    print("=" * 80)
    print()

    # Connect to Valkey (Redis-compatible on port 6381)
    redis_url = "redis://localhost:6381/0"
    redis_client = await aioredis.from_url(redis_url)
    await redis_client.flushdb()
    await redis_client.aclose()

    # Create adapter
    mock_broker = MockBroker()
    adapter = RedisAdapter(redis_url=redis_url)
    adapter.init(mock_broker, mock_broker.get_logger("RedisAdapter"))
    await adapter.connect()

    results = []

    # 1. Publish Throughput
    print("⏱️  Running: Publish Throughput (10,000 messages)...")
    result = await benchmark_publish_throughput(adapter, num_messages=10000)
    results.append(result)
    print(f"   ✅ {result['throughput_msg_per_sec']} msg/sec")
    print()

    # 2. Consume Throughput
    print("⏱️  Running: Consume Throughput (10,000 messages)...")
    result = await benchmark_consume_throughput(adapter, num_messages=10000)
    results.append(result)
    print(f"   ✅ {result['throughput_msg_per_sec']} msg/sec")
    print()

    # 3. Publish Latency
    print("⏱️  Running: Publish Latency (1,000 samples)...")
    result = await benchmark_publish_latency(adapter, num_samples=1000)
    results.append(result)
    print(
        f"   ✅ p50: {result['p50_ms']}ms | p95: {result['p95_ms']}ms | p99: {result['p99_ms']}ms"
    )
    print()

    # 4. End-to-End Latency
    print("⏱️  Running: End-to-End Latency (1,000 samples)...")
    result = await benchmark_e2e_latency(adapter, num_samples=1000)
    results.append(result)
    print(
        f"   ✅ p50: {result['p50_ms']}ms | p95: {result['p95_ms']}ms | p99: {result['p99_ms']}ms"
    )
    print()

    # 5. Concurrent Consumers
    print("⏱️  Running: Concurrent Consumers (5 consumers, 5,000 messages)...")
    result = await benchmark_concurrent_consumers(
        adapter, num_consumers=5, num_messages=5000
    )
    results.append(result)
    print(f"   ✅ {result['throughput_msg_per_sec']} msg/sec")
    print(f"   Distribution: {result['distribution']}")
    print()

    # 6. Memory Usage
    print("⏱️  Running: Memory Usage (3 channels, 100 messages each)...")
    result = await benchmark_memory_usage(
        adapter, num_channels=3, num_active_messages=100
    )
    results.append(result)
    print(f"   ✅ Total: {result['memory_usage_kb']} KB")
    print(f"   Per channel: {result['memory_per_channel_kb']} KB")
    print(f"   Per message: {result['memory_per_message_kb']} KB")
    print()

    # Disconnect
    await adapter.disconnect()

    # Print Summary
    print("=" * 80)
    print("Summary")
    print("=" * 80)
    print()

    for result in results:
        print(f"📊 {result['name']}:")
        for key, value in result.items():
            if key != "name":
                print(f"   {key}: {value}")
        print()

    # Comparison Table
    print("=" * 80)
    print("Comparison: MoleculerPy vs moleculer-channels (estimated)")
    print("=" * 80)
    print()
    print("| Metric                    | MoleculerPy (Python) | moleculer-channels (Node.js) |")
    print("|---------------------------|--------------------|------------------------------|")

    publish_throughput = next(
        r for r in results if r["name"] == "Publish Throughput"
    )["throughput_msg_per_sec"]
    consume_throughput = next(
        r for r in results if r["name"] == "Consume Throughput"
    )["throughput_msg_per_sec"]
    publish_latency = next(r for r in results if r["name"] == "Publish Latency")[
        "p50_ms"
    ]
    e2e_latency = next(r for r in results if r["name"] == "End-to-End Latency")[
        "p50_ms"
    ]

    print(f"| Publish Throughput        | {publish_throughput:>15,.0f} | ~12,000 msg/sec              |")
    print(f"| Consume Throughput        | {consume_throughput:>15,.0f} | ~8,000 msg/sec               |")
    print(f"| Publish Latency (p50)     | {publish_latency:>15.3f}ms | ~0.5ms                       |")
    print(f"| End-to-End Latency (p50)  | {e2e_latency:>15.3f}ms | ~5ms                         |")
    print()
    print("Note: moleculer-channels numbers are estimates based on Node.js async performance.")
    print()

    print("=" * 80)
    print("✅ Benchmarks Complete!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_benchmarks())
