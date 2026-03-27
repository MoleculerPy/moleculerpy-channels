"""
Adapters for different message brokers.

Provides BaseAdapter abstract class and concrete implementations for
Redis Streams, Kafka, NATS, and in-memory fake adapter.
"""

from .base import BaseAdapter
from .fake import FakeAdapter

# Build __all__ dynamically based on available dependencies
__all__ = ["BaseAdapter", "FakeAdapter"]

# Optional: Redis adapter (requires redis[hiredis] package)
try:
    from .redis import RedisAdapter
    __all__.append("RedisAdapter")
except ImportError:
    pass

# Optional: NATS adapter (requires nats-py package)
try:
    from .nats import NatsAdapter
    __all__.append("NatsAdapter")
except ImportError:
    pass
