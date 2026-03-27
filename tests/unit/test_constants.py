"""Tests for constants module."""

import pytest

from moleculerpy_channels import constants as C


def test_header_constants_defined():
    """Test that all header constants are defined."""
    assert C.HEADER_REDELIVERED_COUNT == "x-redelivered-count"
    assert C.HEADER_GROUP == "x-group"
    assert C.HEADER_ORIGINAL_CHANNEL == "x-original-channel"
    assert C.HEADER_ORIGINAL_GROUP == "x-original-group"


def test_error_header_constants_defined():
    """Test that all error header constants are defined."""
    assert C.HEADER_ERROR_PREFIX == "x-error-"
    assert C.HEADER_ERROR_MESSAGE == "x-error-message"
    assert C.HEADER_ERROR_CODE == "x-error-code"
    assert C.HEADER_ERROR_STACK == "x-error-stack"
    assert C.HEADER_ERROR_TYPE == "x-error-type"
    assert C.HEADER_ERROR_DATA == "x-error-data"
    assert C.HEADER_ERROR_NAME == "x-error-name"
    assert C.HEADER_ERROR_RETRYABLE == "x-error-retryable"
    assert C.HEADER_ERROR_TIMESTAMP == "x-error-timestamp"


def test_metric_constants_defined():
    """Test that all metric constants are defined."""
    assert C.METRIC_CHANNELS_MESSAGES_SENT == "moleculerpy.channels.messages.sent"
    assert C.METRIC_CHANNELS_MESSAGES_TOTAL == "moleculerpy.channels.messages.total"
    assert C.METRIC_CHANNELS_MESSAGES_ACTIVE == "moleculerpy.channels.messages.active"
    assert C.METRIC_CHANNELS_MESSAGES_TIME == "moleculerpy.channels.messages.time"
    assert C.METRIC_CHANNELS_MESSAGES_ERRORS_TOTAL == "moleculerpy.channels.messages.errors.total"
    assert C.METRIC_CHANNELS_MESSAGES_RETRIES_TOTAL == "moleculerpy.channels.messages.retries.total"
    assert (
        C.METRIC_CHANNELS_MESSAGES_DEAD_LETTERING_TOTAL
        == "moleculerpy.channels.messages.deadLettering.total"
    )


def test_error_code_constants_defined():
    """Test that error code constants are defined."""
    assert C.INVALID_MESSAGE_SERIALIZATION_ERROR_CODE == "INVALID_MESSAGE_SERIALIZATION"
