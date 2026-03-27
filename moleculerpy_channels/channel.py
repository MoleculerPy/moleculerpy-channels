"""
Channel and ChannelOptions dataclasses.

Defines the structure for channel configuration including dead-letter queue options,
max retries, consumer groups, and adapter-specific settings.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

# Type alias for channel handlers
ChannelHandler = Callable[[Any, Any], Awaitable[None]]


@dataclass(frozen=True)
class DeadLetteringOptions:
    """
    Dead-letter queue configuration.

    Attributes:
        enabled: Enable dead-letter queue functionality
        queue_name: Name of the dead-letter queue/channel
        exchange_name: Name of DLQ exchange (AMQP only)
        exchange_options: Options for DLQ exchange (AMQP only)
        queue_options: Options for DLQ queue (AMQP only)
        error_info_ttl: TTL in seconds for error info (Redis only, default 24h)
    """

    enabled: bool = False
    queue_name: str = "DLQ"
    exchange_name: str | None = None
    exchange_options: dict[str, Any] = field(default_factory=dict)
    queue_options: dict[str, Any] = field(default_factory=dict)
    error_info_ttl: int = 86400  # 24 hours


@dataclass(frozen=True)
class RedisOptions:
    """
    Redis adapter-specific options.

    Attributes:
        min_idle_time: Minimum idle time (ms) before claiming pending messages (default 1h)
        claim_interval: Interval (ms) for XAUTOCLAIM loop (default 100ms)
        dlq_check_interval: Interval (seconds) for DLQ detection loop (default 30s)
        start_id: Stream start ID for XREADGROUP (default '>')
        read_timeout_ms: Timeout for XREADGROUP BLOCK (default 5000ms)
    """

    min_idle_time: int = 3600000  # 1 hour
    claim_interval: int = 100  # 100ms
    dlq_check_interval: int = 30  # 30 seconds
    start_id: str = ">"
    read_timeout_ms: int = 5000


@dataclass
class Channel:
    """
    Channel configuration and metadata.

    Attributes:
        id: Unique consumer ID (generated)
        name: Channel/queue/stream name
        group: Consumer group name (default: service fullName)
        context: Create Moleculer Context for handler (default: False)
        unsubscribing: Flag indicating service is stopping
        max_in_flight: Max concurrent messages (None = unlimited)
        max_retries: Max retry attempts before DLQ (default: 3)
        dead_lettering: Dead-letter queue options
        redis: Redis-specific options
        tracing: Distributed tracing options (TracingMiddleware)
        handler: User-defined async handler function
        raw_handler: Original unwrapped handler (for testing)
    """

    name: str
    handler: ChannelHandler
    id: str = ""
    group: str = ""
    context: bool = False
    unsubscribing: bool = False
    max_in_flight: int | None = None
    max_retries: int = 3
    dead_lettering: DeadLetteringOptions | None = None
    redis: RedisOptions | None = None
    tracing: dict[str, Any] | bool | None = None
    raw_handler: ChannelHandler | None = None
