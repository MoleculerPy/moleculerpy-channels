"""
Type stubs for NatsAdapter.

Provides type information for IDE autocomplete and mypy type checking
without requiring nats-py dependency at type-check time.
"""

from typing import Any

from ..channel import Channel
from ..metrics import ChannelMetrics
from .base import BaseAdapter
from .protocols import JetStreamMessage

class NatsAdapter(BaseAdapter):
    """NATS JetStream adapter type stub."""

    url: str
    stream_config: dict[str, Any]
    consumer_config: dict[str, Any]
    max_reconnect_attempts: int
    reconnect_time_wait: int
    nc: Any | None
    js: Any | None
    subscriptions: dict[str, Any]
    metrics: ChannelMetrics | None

    def __init__(
        self,
        url: str = ...,
        stream_config: dict[str, Any] | None = ...,
        consumer_config: dict[str, Any] | None = ...,
        max_reconnect_attempts: int = ...,
        reconnect_time_wait: int = ...,
    ) -> None: ...
    def init(self, broker: Any, logger: Any) -> None: ...
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def subscribe(self, channel: Channel, service: Any) -> None: ...
    async def unsubscribe(self, channel: Channel) -> None: ...
    async def publish(
        self, channel_name: str, payload: Any, opts: dict[str, Any]
    ) -> str | None: ...
    def parse_message_headers(self, raw_message: JetStreamMessage) -> dict[str, str] | None: ...
