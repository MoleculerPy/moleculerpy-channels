"""
E2E tests for Dead Letter Queue (DLQ) across multiple brokers.

Tests scenarios where messages fail processing and move to DLQ.
"""

import asyncio
import pytest


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_dlq_cross_broker(three_broker_cluster):
    """
    Message fails on node-0 → moves to DLQ → consumed by node-1.

    Setup:
    ┌─────────────────────────────────────────────┐
    │ node-0: Failing consumer (test.failing)    │
    │   ↓ (after 3 retries)                       │
    │ DEAD_LETTER queue                           │
    │   ↓                                         │
    │ node-1: DLQ consumer (DEAD_LETTER)          │
    └─────────────────────────────────────────────┘

    Expected:
    - Failing handler called 3 times (max_retries=3)
    - DLQ handler called 1 time with error metadata
    """
    # TODO: Implement when MoleculerPy integration complete
    # brokers = three_broker_cluster

    # # Track calls
    # fail_count = 0
    # dlq_received = []

    # async def failing_handler(payload, raw):
    #     nonlocal fail_count
    #     fail_count += 1
    #     raise ValueError(f"Simulated failure #{fail_count}")

    # async def dlq_handler(ctx, raw):
    #     dlq_received.append({
    #         "payload": ctx.params,
    #         "error_message": ctx.headers.get("x-error-message"),
    #         "error_type": ctx.headers.get("x-error-type"),
    #         "original_channel": ctx.headers.get("x-original-channel"),
    #     })

    # # Register failing service on node-0
    # class FailingService(Service):
    #     name = "failing"
    #     channels = {
    #         "test.failing": {
    #             "group": "dlq-test-group",
    #             "max_retries": 3,
    #             "handler": failing_handler,
    #             "dead_lettering": {
    #                 "enabled": True,
    #                 "queue_name": "DEAD_LETTER"
    #             }
    #         }
    #     }

    # # Register DLQ service on node-1
    # class DLQService(Service):
    #     name = "dlq_processor"
    #     channels = {
    #         "DEAD_LETTER": {
    #             "context": True,
    #             "handler": dlq_handler
    #         }
    #     }

    # await brokers[0].register(FailingService())
    # await brokers[1].register(DLQService())

    # # Publish message
    # await brokers[0].send_to_channel("test.failing", {"data": "will fail"}, {})

    # # Wait for retries + DLQ
    # await asyncio.sleep(5.0)

    # # Assertions
    # assert fail_count == 3, f"Expected 3 failures, got {fail_count}"
    # assert len(dlq_received) == 1, f"DLQ not received: {dlq_received}"

    # dlq_msg = dlq_received[0]
    # assert dlq_msg["payload"] == {"data": "will fail"}
    # assert "Simulated failure" in dlq_msg["error_message"]
    # assert dlq_msg["error_type"] == "ValueError"
    # assert dlq_msg["original_channel"] == "test.failing"

    pytest.skip("Requires MoleculerPy ServiceBroker integration")


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_dlq_preserves_context(three_broker_cluster):
    """
    DLQ messages preserve full distributed tracing context.

    Verifies:
    - requestID preserved
    - parentID preserved
    - meta preserved
    - caller preserved
    """
    # TODO: Implement context preservation verification
    pytest.skip("Requires MoleculerPy ServiceBroker integration")
