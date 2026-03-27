"""
E2E tests for consumer group balancing.

Tests multi-broker scenarios where messages are balanced across
consumer group members running on different nodes.
"""

import asyncio
import pytest

# TODO: Replace with actual imports when integrated with MoleculerPy
# from moleculerpy import ServiceBroker


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_balanced_consumer_groups(three_broker_cluster):
    """
    Messages balanced across 3 consumer group members on different nodes.

    Setup:
    ┌─────────┐  ┌─────────┐  ┌─────────┐
    │ node-0  │  │ node-1  │  │ node-2  │
    │ (cons)  │  │ (cons)  │  │ (cons)  │
    └────┬────┘  └────┬────┘  └────┬────┘
         │            │            │
         └────────┬───┴────────────┘
              Redis Stream
              "test.events"
              group: "event-processors"

    Expected: ~10 messages per node (30 total)
    """
    brokers = three_broker_cluster

    # Publish 30 messages from node-0
    for i in range(30):
        await brokers[0].call("publisher.send_message", {
            "channel": "test.events",
            "payload": {"index": i}
        })

    # Wait for consumption
    await asyncio.sleep(3.0)

    # Query each consumer for received count
    distribution = {}
    total_received = 0

    for i, broker in enumerate(brokers):
        result = await broker.call("consumer.get_received", {})
        node_id = f"node-{i}"
        distribution[node_id] = len(result)
        total_received += len(result)

    # Assertions
    assert total_received == 30, f"Lost messages: {distribution} (total: {total_received}/30)"

    # Each node should get at least 5 messages (loose balancing check)
    for node_id, count in distribution.items():
        assert count >= 5, f"Imbalanced distribution: {distribution}"

    print(f"✅ Distribution: {distribution}")


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_single_consumer_gets_all(three_broker_cluster):
    """
    Only one node has consumer → all messages go to that node.

    Setup:
    ┌─────────┐  ┌─────────┐  ┌─────────┐
    │ node-0  │  │ node-1  │  │ node-2  │
    │ (cons)  │  │ (empty) │  │ (empty) │
    └────┬────┘  └─────────┘  └─────────┘
         │
         └─────────────────────────────────
              Redis Stream "test.single"
              group: "single-processors"

    Expected: node-0 receives all 20 messages
    """
    brokers = three_broker_cluster

    # Only node-0 has consumer (node-1, node-2 don't subscribe to "test.single")

    # Publish 20 messages
    for i in range(20):
        await brokers[0].send_to_channel("test.single", {"index": i}, {})

    await asyncio.sleep(2.0)

    # Check distribution
    node0_received = await brokers[0].call("consumer.get_received", {})

    # All messages should go to node-0
    assert len(node0_received) == 20, f"Expected 20, got {len(node0_received)}"


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_dynamic_consumer_joining(three_broker_cluster):
    """
    New consumer joins mid-stream → starts receiving new messages.

    Flow:
    1. node-0 publishes 10 messages (only node-0 consuming)
    2. node-1 joins consumer group
    3. Publish 20 more messages
    4. Verify: node-0 got ~10 old + ~10 new, node-1 got ~10 new
    """
    # TODO: Implement when MoleculerPy ServiceBroker integration complete
    pytest.skip("Requires MoleculerPy ServiceBroker integration")
