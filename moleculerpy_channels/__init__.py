"""
MoleculerPy Channels — Python port of Moleculer Channels middleware.

Provides reliable pub/sub messaging with persistence, guaranteed delivery,
dead-letter queues, and consumer groups for MoleculerPy microservices.
"""

from importlib.metadata import PackageNotFoundError, version

from .channel import Channel, DeadLetteringOptions, RedisOptions
from .middleware import ChannelsMiddleware
from .tracing import TracingMiddleware

try:
    __version__ = version("moleculerpy-channels")
except PackageNotFoundError:
    __version__ = "0.14.2"

__all__ = [
    "ChannelsMiddleware",
    "TracingMiddleware",
    "Channel",
    "DeadLetteringOptions",
    "RedisOptions",
]
