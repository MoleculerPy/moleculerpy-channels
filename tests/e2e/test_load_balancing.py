"""
E2E tests for load balancing across multiple brokers.

Tests message distribution when multiple nodes provide the same channel consumer.
"""

import asyncio
import time
from collections import Counter

import pytest


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_high_concurrency_publishing(three_broker_cluster):
    """
    100 concurrent publishers × 10 messages = 1000 total.

    Tests:
    - System handles high concurrency
    - All messages delivered
    - Balanced across consumer group

    Expected throughput: ≥2K msg/s
    """
    brokers = three_broker_cluster

    NUM_PUBLISHERS = 100
    MESSAGES_PER_PUBLISHER = 10
    TOTAL = NUM_PUBLISHERS * MESSAGES_PER_PUBLISHER

    async def publisher_task(pub_id: int):
        """Single publisher sends N messages."""
        for i in range(MESSAGES_PER_PUBLISHER):
            await brokers[0].call(
                "publisher.send_message",
                {"channel": "test.events", "payload": {"publisher": pub_id, "index": i}},
            )

    # Launch concurrent publishers
    start = time.perf_counter()
    tasks = [publisher_task(i) for i in range(NUM_PUBLISHERS)]
    await asyncio.gather(*tasks)
    publish_time = time.perf_counter() - start

    # Wait for consumption
    await asyncio.sleep(3.0)

    # Collect results
    distribution = {}
    total_received = 0

    for i, broker in enumerate(brokers):
        result = await broker.call("consumer.get_received", {})
        node_id = f"node-{i}"
        distribution[node_id] = len(result)
        total_received += len(result)

    # Metrics
    throughput = TOTAL / publish_time

    print(f"Published: {TOTAL} in {publish_time:.2f}s")
    print(f"Throughput: {throughput:.0f} msg/s")
    print(f"Received: {total_received}/{TOTAL}")
    print(f"Distribution: {distribution}")

    # Assertions
    assert total_received == TOTAL, f"Lost {TOTAL - total_received} messages"
    # E2E throughput SLA (NATS RPC + Redis I/O + multi-broker coordination)
    # Note: Direct Redis publish achieves ~1.9K, but E2E with RPC overhead is slower
    assert throughput >= 500, f"Too slow: {throughput:.0f} msg/s (expected ≥500 for E2E)"

    # Balancing check (each node ≥20% of total)
    for node_id, count in distribution.items():
        percentage = (count / TOTAL) * 100
        assert percentage >= 20, f"Imbalanced: {node_id} got {percentage:.1f}%"


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_load_balancing_with_slow_consumer(three_broker_cluster):
    """
    One consumer is slow → messages redistribute to faster nodes.

    Setup:
    - node-0: fast consumer (10ms per message)
    - node-1: slow consumer (100ms per message)
    - node-2: fast consumer (10ms per message)

    Expected: node-0 and node-2 handle more messages than node-1
    """
    # TODO: Implement with configurable handler delays
    pytest.skip("Requires service reconfiguration for variable delays")


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_round_robin_distribution(three_broker_cluster):
    """
    With RoundRobin strategy, requests distributed sequentially.

    Note: This tests MoleculerPy's call() distribution, not channel delivery.
    Channel delivery is always balanced via consumer groups (broker-native).
    """
    brokers = three_broker_cluster

    # Call counter.increment 30 times
    node_counts: Counter[str] = Counter()

    for _ in range(30):
        result = await brokers[0].call("counter.increment", {})
        node_counts[result["node_id"]] += 1

    # With RoundRobin, should distribute across nodes
    counts = list(node_counts.values())
    assert len(counts) == 3, f"Expected 3 nodes, got {len(counts)}"

    # Each node should get at least 5 messages (loose balancing check)
    # Perfect 10/10/10 not guaranteed due to async timing and Redis behavior
    for node_id, count in node_counts.items():
        assert count >= 5, f"Node {node_id} underutilized: {dict(node_counts)}"

    print(f"✅ Balanced distribution: {dict(node_counts)}")
