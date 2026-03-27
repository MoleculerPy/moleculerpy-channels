"""
Custom exceptions for MoleculerPy Channels.

Provides specialized exception types for channel operations, adapter errors,
and message processing failures.
"""


class ChannelsError(Exception):
    """Base exception for all Channels errors."""

    pass


class AdapterError(ChannelsError):
    """Raised when adapter operations fail."""

    pass


class ChannelRegistrationError(ChannelsError):
    """Raised when channel registration fails."""

    pass


class MessageSerializationError(ChannelsError):
    """Raised when message serialization/deserialization fails."""

    def __init__(self, message: str, original_error: Exception | None = None):
        super().__init__(message)
        self.original_error = original_error


class MessagePublishError(AdapterError):
    """Raised when message publishing fails."""

    pass


class SubscriptionError(AdapterError):
    """Raised when subscription operations fail."""

    pass


# NATS-specific exceptions (added in Phase 4.3)
class NatsConnectionError(AdapterError):
    """Raised when NATS connection fails or is lost."""

    pass


class NatsStreamError(AdapterError):
    """Raised when NATS JetStream stream operations fail."""

    pass


class NatsConsumerError(AdapterError):
    """Raised when NATS JetStream consumer operations fail."""

    pass
