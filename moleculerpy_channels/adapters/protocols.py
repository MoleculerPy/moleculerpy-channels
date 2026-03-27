"""
Protocol definitions for adapter types.

Provides structural typing (Protocols) for adapter-specific objects,
enabling type-safe interactions without tight coupling to implementation details.
"""

from typing import Any, NewType, Protocol, runtime_checkable

# Branded types for domain safety
SanitizedStreamName = NewType("SanitizedStreamName", str)
"""
Branded type for NATS stream names that have been sanitized.

Stream names in NATS cannot contain: '.', '>', '*'
This type guarantees that the name has passed through _sanitize_stream_name().

Example:
    >>> raw_name = "orders.created"
    >>> sanitized = _sanitize_stream_name(raw_name)  # Returns SanitizedStreamName
    >>> # Type checker prevents using raw_name where SanitizedStreamName expected
"""


@runtime_checkable
class JetStreamMessage(Protocol):
    """
    Protocol for NATS JetStream message objects.

    Defines the interface for JetStream messages used in NatsAdapter.
    This is a structural type (duck typing with type checking) that matches
    the nats.aio.msg.Msg class without requiring a direct import.

    Attributes:
        metadata: Message metadata (sequence, timestamps, delivery count)
        data: Raw message payload (bytes)
        headers: Optional message headers (dict-like)

    Methods:
        ack(): Acknowledge successful processing
        nak(): Negative acknowledge (trigger retry)
        in_progress(): Mark message as being processed (prevents redelivery)

    Example:
        >>> async def handler(msg: JetStreamMessage) -> None:
        ...     print(f"Sequence: {msg.metadata.sequence.stream}")
        ...     payload = msg.data
        ...     await msg.ack()
    """

    # Attributes
    metadata: Any  # nats.js.api.ConsumerInfo (avoid hard dependency)
    data: bytes
    headers: Any | None  # nats.aio.msg.Headers (optional)

    # Methods
    async def ack(self) -> None:
        """Acknowledge message (mark as successfully processed)."""
        ...

    async def nak(self, delay: float | None = None) -> None:
        """
        Negative acknowledge (request redelivery).

        Args:
            delay: Optional delay before redelivery (seconds)
        """
        ...

    async def in_progress(self) -> None:
        """
        Mark message as in-progress.

        Extends the ack deadline to prevent redelivery while processing.
        Must be called before processing to avoid duplicate deliveries.
        """
        ...


@runtime_checkable
class MessageHeaders(Protocol):
    """
    Protocol for message header objects.

    Defines the interface for accessing message headers in a dict-like manner.
    Matches nats.aio.msg.Headers without requiring the import.

    Example:
        >>> headers: MessageHeaders = msg.headers
        >>> request_id = headers.get("$requestID")
    """

    def get(self, key: str, default: Any = None) -> Any:
        """Get header value by key."""
        ...

    def __getitem__(self, key: str) -> Any:
        """Get header value by key (raises if missing)."""
        ...

    def __contains__(self, key: str) -> bool:
        """Check if header key exists."""
        ...

    def keys(self) -> Any:
        """Return header keys."""
        ...
