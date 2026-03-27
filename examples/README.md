# MoleculerPy Channels — Examples

Production-ready examples demonstrating MoleculerPy Channels features.

---

## Quick Start

```bash
# 1. Start Redis (port 6380 to avoid FalkorDB conflict)
cd tests
docker-compose up -d

# 2. Verify Redis is running
docker ps | grep redis

# 3. Run example
python examples/redis_example.py
```

---

## Examples Overview

| Example | Features | Dependencies | Duration |
|---------|----------|--------------|----------|
| **simple.py** | Basic pub/sub, FakeAdapter | None | ~1 second |
| **redis_example.py** | Redis, DLQ, Retry, Multiple services | Redis | ~10 seconds |
| **graceful_shutdown.py** | Active message tracking, Cleanup order | Redis | User-controlled |

---

## 1. simple.py

**Purpose**: Minimal example with in-memory adapter (no external dependencies).

**Features**:
- FakeAdapter for testing
- Two handler patterns: lambda and dict config
- In-memory message delivery

**Run**:
```bash
python examples/simple.py
```

**Output**:
```
✅ Broker started
✅ Service 'orders' created

📤 Publishing messages...

📦 Order created: {'orderId': 123, 'total': 99.99}
❌ Order cancelled: {'orderId': 456, 'reason': 'customer request'}

🛑 Stopping broker...

✅ Broker stopped
✅ Example completed!
```

**When to use**:
- Quick prototyping
- Unit testing
- Development without Redis

---

## 2. redis_example.py

**Purpose**: Production example with Redis Streams, DLQ, and retry.

**Features**:
- Redis Streams for persistent messaging
- Consumer groups (horizontal scaling)
- Automatic retry via XAUTOCLAIM
- Dead Letter Queue for failed messages
- Error metadata preservation
- Multiple services with different configurations

**Run**:
```bash
# Start Redis first
cd tests && docker-compose up -d

# Run example
python examples/redis_example.py
```

**Output**:
```
🚀 MoleculerPy Channels - Redis Example
══════════════════════════════════════════════════════════════════════

✅ Broker started with Redis adapter
✅ Service 'orders' created
✅ Service 'notifications' created

📤 Publishing Messages
──────────────────────────────────────────────────────────────────────

1️⃣  Publishing order.created events...

📦 Order Created
   Order ID: 101
   Total: $200.99

📦 Order Created
   Order ID: 102
   Total: $201.99

...

2️⃣  Publishing order.payment events (some will fail)...

💳 Processing Payment
   Order ID: 201
   ✓ Payment successful

💳 Processing Payment
   Order ID: 202
   [ERROR] Payment failed for order 202
   (Will retry 3 times, then move to DLQ)

...

📊 Checking Results
──────────────────────────────────────────────────────────────────────

📮 Dead Letter Queue: 2 failed messages
   - Message 1706000001-0
     Original: orders.payment
     Error: Payment failed for order 202
   - Message 1706000002-0
     Original: orders.payment
     Error: Payment failed for order 204

✅ Example completed!
```

**Expected DLQ behavior**:
- Even order IDs (202, 204) fail payment processing
- Each message retried 3 times (via XAUTOCLAIM)
- After max_retries, moved to `FAILED_PAYMENTS` stream
- Error metadata preserved in DLQ message headers

**Verify with Redis CLI**:
```bash
# Check consumer groups
redis-cli -p 6380 XINFO GROUPS orders.created

# Check DLQ messages
redis-cli -p 6380 XRANGE FAILED_PAYMENTS - +

# Check pending messages
redis-cli -p 6380 XPENDING orders.payment payment-processors
```

---

## 3. graceful_shutdown.py

**Purpose**: Demonstrate proper cleanup and prevention of data loss during restart.

**Features**:
- Active message tracking during processing
- Waiting for in-flight messages before shutdown
- Background task cancellation order (critical!)
- Signal handling (SIGINT, SIGTERM)

**Run**:
```bash
# Start Redis first
cd tests && docker-compose up -d

# Run example (press Ctrl+C after 2+ seconds)
python examples/graceful_shutdown.py
```

**Output**:
```
🚀 Graceful Shutdown Example
══════════════════════════════════════════════════════════════════════

✅ Broker started

📤 Publishing Tasks
──────────────────────────────────────────────────────────────────────

Publishing 5 tasks (2 seconds each)...

Press Ctrl+C to trigger graceful shutdown
(Wait at least 2 seconds to see in-flight message handling)

⚙️  Processing Task 1
   Duration: 2s

⚙️  Processing Task 2
   Duration: 2s

^C

⚠️  Shutdown signal received

⏸️  Initiating graceful shutdown...
   Waiting for active messages to complete...

   ✓ Task 1 completed
   Total processed: 1

   ✓ Task 2 completed
   Total processed: 2

✅ Broker stopped gracefully

✅ Example completed!

Key observations:
  • Adapter waited for in-flight messages
  • Background tasks cancelled in correct order
  • No message loss during shutdown
```

**Critical cleanup order** (from `redis.py:unsubscribe`):
1. Wait for active messages (`while active_count > 0`)
2. Cancel background tasks (`_xreadgroup_loop`, `_xclaim_loop`, `_dlq_loop`)
3. Close Redis connection

**Why order matters**:
- If tasks cancelled first → active messages lost ❌
- If wait first → graceful completion ✅

---

## Common Issues

### Issue: `ConnectionError: Too many connections`

**Cause**: Publishing too many messages without pauses

**Solution**: Add small delay between publishes:
```python
for i in range(1000):
    await broker.send_to_channel("channel", {"id": i})
    if i % 100 == 0:
        await asyncio.sleep(0.1)  # Prevent connection flood
```

### Issue: `ResponseError: WRONGTYPE` (FalkorDB)

**Cause**: Connecting to FalkorDB (port 6379) instead of Redis

**Solution**: Use port 6380:
```python
RedisAdapter(redis_url="redis://localhost:6380/0")
```

### Issue: DLQ messages not appearing

**Cause**: `dlq_check_interval` too long (default 30s)

**Solution**: Lower interval for testing:
```python
Channel(
    redis=RedisOptions(dlq_check_interval=1)  # 1 second
)
```

---

## Next Steps

- **Production deployment**: See [README.md](../README.md) for Redis configuration
- **Performance tuning**: See [benchmarks/README.md](../benchmarks/README.md) for optimization tips
- **Feature gaps**: See [GAP-ANALYSIS.md](../GAP-ANALYSIS.md) for missing features
- **Implementation details**: See [IMPLEMENTATION-PLAN.md](../IMPLEMENTATION-PLAN.md) for architecture

---

## Contributing

Found a bug or have an improvement? See [CONTRIBUTING.md](../CONTRIBUTING.md) for guidelines.

**Priority areas**:
- Consumer Groups (P0) — Horizontal scaling
- Cursor-based XAUTOCLAIM (P0) — Efficient retry
- Error Metadata Storage (P0) — Full error context

---

**MoleculerPy Channels** — Production-ready pub/sub for Python microservices 🚀
