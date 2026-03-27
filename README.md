# MoleculerPy Channels

[![CI](https://github.com/MoleculerPy/moleculerpy-channels/workflows/CI/badge.svg)](https://github.com/MoleculerPy/moleculerpy-channels/actions)
[![PyPI version](https://img.shields.io/pypi/v/moleculerpy-channels.svg)](https://pypi.org/project/moleculerpy-channels/)
[![Python versions](https://img.shields.io/pypi/pyversions/moleculerpy-channels.svg)](https://pypi.org/project/moleculerpy-channels/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Python port of [Moleculer Channels](https://github.com/moleculerjs/moleculer-channels) middleware for reliable pub/sub messaging in microservices.

---

## Features

- ✅ **Persistent Messaging** — Messages stored in Redis Streams/NATS JetStream
- ✅ **Guaranteed Delivery** — ACK/NACK support with automatic retry
- ✅ **Dead Letter Queue** — Failed messages routed to DLQ after max retries
- ✅ **Cursor-based Retry** — Efficient XAUTOCLAIM with cursor tracking (5-10x faster)
- ✅ **Error Metadata** — Full error context (stack, timestamp) in DLQ for debugging
- ✅ **Metrics System** — 7 Prometheus-compatible metrics (sent, total, active, time, errors, retries, dlq)
- ✅ **Full Context Propagation** — Complete distributed tracing (requestID, parentID, level, meta, headers, caller)
- ✅ **Graceful Shutdown** — Waits for active messages before disconnect
- ✅ **Type-Safe** — Full type hints with mypy strict mode
- ✅ **Production-Ready** — 155 tests passing, comprehensive benchmarks

---

## Performance

Based on local Redis (localhost:6380), Python 3.12:

| Metric | Performance |
|--------|-------------|
| **Publish Throughput** | 1,948 msg/sec |
| **Consume Throughput** | 1,532 msg/sec |
| **Publish Latency (p50)** | 0.35ms |
| **End-to-End Latency (p50)** | **0.77ms** |
| **Memory per Message** | 2.3 KB |

See [benchmarks/](./benchmarks) for details.

**Key Insight**: MoleculerPy has **85% better latency** than moleculer-channels (0.77ms vs ~5ms)!

---

## Quick Start

### Installation

```bash
# Basic (includes FakeAdapter for testing)
pip install moleculerpy-channels

# With Redis support
pip install moleculerpy-channels[redis]

# With all adapters
pip install moleculerpy-channels[all]
```

### Simple Example

```python
from moleculerpy import ServiceBroker, Service
from moleculerpy_channels import ChannelsMiddleware
from moleculerpy_channels.adapters import RedisAdapter

# Create broker with Channels middleware
broker = ServiceBroker(
    middlewares=[
        ChannelsMiddleware(
            adapter=RedisAdapter(redis_url="redis://localhost:6380/0")
        )
    ]
)

# Define service with channel handlers
class OrderService(Service):
    name = "orders"

    channels = {
        "orders.created": {
            "group": "order-processors",
            "max_retries": 3,
            "dead_lettering": {
                "enabled": True,
                "queue_name": "FAILED_ORDERS"
            },
            "handler": lambda self, payload, raw: self.process_order(payload)
        }
    }

    def process_order(self, order):
        print(f"Processing order: {order}")

# Register service
broker.create_service(OrderService)

# Start broker
await broker.start()

# Send message to channel
await broker.send_to_channel("orders.created", {"orderId": 123, "total": 99.99})
```

---

## 🔌 Adapters

| Adapter | Description | Status |
|---------|-------------|--------|
| `FakeAdapter` | In-memory (testing) | Complete |
| `RedisAdapter` | Redis Streams | **Production-Ready** |
| `NatsAdapter` | NATS JetStream | **Complete** (Phase 4.3) |
| `KafkaAdapter` | Apache Kafka | 📋 Planned (Future) |

### RedisAdapter Configuration

```python
from moleculerpy_channels.adapters import RedisAdapter
from moleculerpy_channels.channel import RedisOptions, DeadLetteringOptions

adapter = RedisAdapter(
    redis_url="redis://localhost:6379/0"
)

# Advanced channel configuration
channels = {
    "orders.created": {
        "group": "order-processors",
        "max_retries": 3,
        "max_in_flight": 10,  # Backpressure control
        "redis": RedisOptions(
            min_idle_time=3600000,      # 1 hour before retry
            claim_interval=100,         # Check every 100ms
            dlq_check_interval=30       # DLQ check every 30s
        ),
        "dead_lettering": DeadLetteringOptions(
            enabled=True,
            queue_name="FAILED_ORDERS",
            error_info_ttl=86400        # 24 hours
        ),
        "handler": process_order
    }
}
```

---

## 📚 Documentation

| Document | Description |
|----------|-------------|
| [IMPLEMENTATION-PLAN.md](./docs/IMPLEMENTATION-PLAN.md) | Complete implementation report with architecture details |
| [GAP-ANALYSIS.md](./docs/GAP-ANALYSIS.md) | Feature comparison vs moleculer-channels |
| [benchmarks/README.md](./benchmarks/README.md) | Performance benchmarks and optimization tips |
| [examples/README.md](./examples/README.md) | Working code examples (simple, Redis, graceful shutdown) |
| [CHANGELOG.md](./CHANGELOG.md) | Version history and changes |

---

## Testing

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run all tests
pytest

# Run integration tests only
pytest tests/integration -v

# Run with coverage
pytest --cov=moleculerpy_channels --cov-report=html

# Type check
mypy --strict moleculerpy_channels
```

**Test Results**: 19/19 integration tests passing

---

## 📦 Project Status

| Phase | Status | Description |
|-------|--------|-------------|
| **Phase 1** | Complete | Foundation + FakeAdapter |
| **Phase 2** | Complete | Redis Adapter implementation |
| **Phase 3.1** | Complete | DLQ & Retry tests (19/19 passing) |
| **Phase 3.2** | Complete | Performance benchmarks |
| **Phase 3.3** | Complete | Documentation polish |
| **Phase 4.1** | Complete | Critical Gaps (P0) — Cursor + Error Metadata |
| **Phase 4.2** | Complete | High Priority (P1) — Metrics System, Full Context Propagation |
| **Phase 4.3** | 📋 Optional | Kafka/NATS adapters |

**Current Version**: 1.2.0 (Phase 4.2 — Production-Ready with Metrics & Complete Context Propagation)

**Compatibility**: ~95% feature parity with moleculer-channels (+10% from Phase 4.2)
**Remaining Gaps**: See [GAP-ANALYSIS.md](./docs/GAP-ANALYSIS.md) for P2-P3 features (Kafka/NATS adapters)

---

## Development

```bash
# Clone repository
git clone https://github.com/MoleculerPy/moleculerpy-channels.git
cd moleculerpy-channels

# Create virtual environment
python3.12 -m venv .venv
source .venv/bin/activate  # or `.venv\Scripts\activate` on Windows

# Install in development mode
pip install -e ".[dev,redis]"

# Run tests
pytest

# Format code
black moleculerpy_channels tests
ruff check --fix moleculerpy_channels tests

# Type check
mypy --strict moleculerpy_channels

# Run benchmarks
python benchmarks/performance_test.py
```

---

## Contributing

Contributions welcome! Please see [CONTRIBUTING.md](./CONTRIBUTING.md) for guidelines.

**Priority areas**:
- Consumer Groups (P0) — Horizontal scaling
- Cursor-based XAUTOCLAIM (P0) — Efficient retry
- Error Metadata Storage (P0) — Full error context

See [GAP-ANALYSIS.md](./docs/GAP-ANALYSIS.md) for complete feature roadmap.

---

## 📄 License

MIT License - see [LICENSE](./LICENSE) file for details.

---

## 🙏 Acknowledgments

- **Moleculer Channels** — Original Node.js implementation
- **MoleculerPy** — Python Moleculer framework
- **Redis** — Battle-tested message broker

---

## Support

- **Issues**: [GitHub Issues](https://github.com/MoleculerPy/moleculerpy-channels/issues)
- **Discussions**: [GitHub Discussions](https://github.com/MoleculerPy/moleculerpy-channels/discussions)
- **Documentation**: [Full Docs](./docs/IMPLEMENTATION-PLAN.md)

---

**Made with ❤️ for the MoleculerPy community**
