"""
Unit tests for custom exception types.

Tests all 9 exception classes defined in moleculerpy_channels.errors:
- Inheritance hierarchy
- Exception messages
- Custom attributes (original_error)
- Exception catching behavior
"""

import pytest
from moleculerpy_channels.errors import (
    AdapterError,
    ChannelRegistrationError,
    ChannelsError,
    MessagePublishError,
    MessageSerializationError,
    NatsConnectionError,
    NatsConsumerError,
    NatsStreamError,
    SubscriptionError,
)


# ── Base Exception ────────────────────────────────────────────────────────


def test_channels_error_is_base_exception():
    """ChannelsError is the base for all channel exceptions."""
    assert issubclass(ChannelsError, Exception)


def test_channels_error_message():
    """ChannelsError accepts and stores message."""
    error = ChannelsError("Test error message")
    assert str(error) == "Test error message"


def test_channels_error_can_be_raised():
    """ChannelsError can be raised and caught."""
    with pytest.raises(ChannelsError) as exc_info:
        raise ChannelsError("Something went wrong")

    assert "Something went wrong" in str(exc_info.value)


# ── Adapter Errors ────────────────────────────────────────────────────────


def test_adapter_error_inherits_from_channels_error():
    """AdapterError inherits from ChannelsError."""
    assert issubclass(AdapterError, ChannelsError)


def test_adapter_error_can_catch_as_channels_error():
    """AdapterError can be caught as ChannelsError."""
    with pytest.raises(ChannelsError):
        raise AdapterError("Adapter failed")


def test_adapter_error_message():
    """AdapterError preserves message."""
    error = AdapterError("Connection timeout")
    assert str(error) == "Connection timeout"


# ── Channel Registration ──────────────────────────────────────────────────


def test_channel_registration_error_inheritance():
    """ChannelRegistrationError inherits from ChannelsError."""
    assert issubclass(ChannelRegistrationError, ChannelsError)


def test_channel_registration_error_message():
    """ChannelRegistrationError with descriptive message."""
    error = ChannelRegistrationError("Duplicate channel name 'orders.created'")
    assert "Duplicate channel name" in str(error)


# ── Message Serialization ─────────────────────────────────────────────────


def test_message_serialization_error_inheritance():
    """MessageSerializationError inherits from ChannelsError."""
    assert issubclass(MessageSerializationError, ChannelsError)


def test_message_serialization_error_stores_original_error():
    """MessageSerializationError stores original exception."""
    original = ValueError("Invalid JSON")
    error = MessageSerializationError("Failed to serialize", original_error=original)

    assert error.original_error is original
    assert isinstance(error.original_error, ValueError)


def test_message_serialization_error_without_original():
    """MessageSerializationError works without original_error."""
    error = MessageSerializationError("Serialization failed")

    assert error.original_error is None
    assert str(error) == "Serialization failed"


def test_message_serialization_error_chaining():
    """MessageSerializationError can be chained with 'from' syntax."""
    try:
        try:
            raise ValueError("Invalid format")
        except ValueError as e:
            raise MessageSerializationError("Failed to parse", original_error=e)
    except MessageSerializationError as mse:
        assert mse.original_error is not None
        assert isinstance(mse.original_error, ValueError)
        assert "Invalid format" in str(mse.original_error)


# ── Message Publishing ────────────────────────────────────────────────────


def test_message_publish_error_inheritance():
    """MessagePublishError inherits from AdapterError."""
    assert issubclass(MessagePublishError, AdapterError)
    assert issubclass(MessagePublishError, ChannelsError)


def test_message_publish_error_can_catch_as_adapter_error():
    """MessagePublishError can be caught as AdapterError."""
    with pytest.raises(AdapterError):
        raise MessagePublishError("Redis XADD failed")


# ── Subscription ──────────────────────────────────────────────────────────


def test_subscription_error_inheritance():
    """SubscriptionError inherits from AdapterError."""
    assert issubclass(SubscriptionError, AdapterError)


def test_subscription_error_message():
    """SubscriptionError with context."""
    error = SubscriptionError("Consumer group 'my-group' already exists")
    assert "Consumer group" in str(error)


# ── NATS-Specific Errors ──────────────────────────────────────────────────


def test_nats_connection_error_inheritance():
    """NatsConnectionError inherits from AdapterError."""
    assert issubclass(NatsConnectionError, AdapterError)


def test_nats_stream_error_inheritance():
    """NatsStreamError inherits from AdapterError."""
    assert issubclass(NatsStreamError, AdapterError)


def test_nats_consumer_error_inheritance():
    """NatsConsumerError inherits from AdapterError."""
    assert issubclass(NatsConsumerError, AdapterError)


def test_nats_errors_can_catch_as_adapter_error():
    """All NATS errors can be caught as AdapterError."""
    with pytest.raises(AdapterError):
        raise NatsConnectionError("NATS server unreachable")

    with pytest.raises(AdapterError):
        raise NatsStreamError("Stream 'orders' not found")

    with pytest.raises(AdapterError):
        raise NatsConsumerError("Consumer 'processor-1' creation failed")


# ── Inheritance Chain ─────────────────────────────────────────────────────


@pytest.mark.parametrize("error_class,expected_bases", [
    (ChannelsError, [Exception]),
    (AdapterError, [ChannelsError, Exception]),
    (ChannelRegistrationError, [ChannelsError, Exception]),
    (MessageSerializationError, [ChannelsError, Exception]),
    (MessagePublishError, [AdapterError, ChannelsError, Exception]),
    (SubscriptionError, [AdapterError, ChannelsError, Exception]),
    (NatsConnectionError, [AdapterError, ChannelsError, Exception]),
    (NatsStreamError, [AdapterError, ChannelsError, Exception]),
    (NatsConsumerError, [AdapterError, ChannelsError, Exception]),
])
def test_exception_inheritance_chain(error_class, expected_bases):
    """Test complete inheritance chain for each exception."""
    for base in expected_bases:
        assert issubclass(error_class, base), (
            f"{error_class.__name__} should inherit from {base.__name__}"
        )


# ── Error Catching Scenarios ──────────────────────────────────────────────


def test_catch_all_channels_errors():
    """Verify all custom exceptions can be caught as ChannelsError."""
    exceptions = [
        AdapterError("test"),
        ChannelRegistrationError("test"),
        MessageSerializationError("test"),
        MessagePublishError("test"),
        SubscriptionError("test"),
        NatsConnectionError("test"),
        NatsStreamError("test"),
        NatsConsumerError("test"),
    ]

    for exc in exceptions:
        with pytest.raises(ChannelsError):
            raise exc


def test_exception_messages_preserved():
    """Verify exception messages are preserved through inheritance."""
    test_message = "Critical failure in production"

    exceptions = [
        ChannelsError(test_message),
        AdapterError(test_message),
        MessagePublishError(test_message),
        NatsConnectionError(test_message),
    ]

    for exc in exceptions:
        assert str(exc) == test_message
