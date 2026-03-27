"""Tests for utils module."""

import json

import pytest

from moleculerpy_channels import constants as C
from moleculerpy_channels.utils import (
    default_transform_error_to_headers,
    default_transform_headers_to_error_data,
)


def test_transform_error_to_headers_basic():
    """Test basic error transformation."""
    error = ValueError("Invalid input")
    headers = default_transform_error_to_headers(error)

    assert headers[C.HEADER_ERROR_MESSAGE] == "Invalid input"
    assert headers[C.HEADER_ERROR_TYPE] == "ValueError"
    assert headers[C.HEADER_ERROR_NAME] == "ValueError"
    assert C.HEADER_ERROR_STACK in headers
    assert headers[C.HEADER_ERROR_RETRYABLE] == "true"
    assert C.HEADER_ERROR_TIMESTAMP in headers


def test_transform_error_to_headers_with_code():
    """Test error with custom code attribute."""

    class CustomError(Exception):
        def __init__(self, message: str, code: str):
            super().__init__(message)
            self.code = code

    error = CustomError("Database error", "DB_CONN_FAILED")
    headers = default_transform_error_to_headers(error)

    assert headers[C.HEADER_ERROR_CODE] == "DB_CONN_FAILED"
    assert headers[C.HEADER_ERROR_MESSAGE] == "Database error"


def test_transform_error_to_headers_with_data():
    """Test error with custom data attribute."""

    class DataError(Exception):
        def __init__(self, message: str, data: dict):
            super().__init__(message)
            self.data = data

    error = DataError("Processing failed", {"userId": 123, "step": "validation"})
    headers = default_transform_error_to_headers(error)

    assert C.HEADER_ERROR_DATA in headers
    data = json.loads(headers[C.HEADER_ERROR_DATA])
    assert data["userId"] == 123
    assert data["step"] == "validation"


def test_transform_error_to_headers_non_retryable():
    """Test error with retryable=False."""

    class NonRetryableError(Exception):
        def __init__(self, message: str):
            super().__init__(message)
            self.retryable = False

    error = NonRetryableError("Permanent failure")
    headers = default_transform_error_to_headers(error)

    assert headers[C.HEADER_ERROR_RETRYABLE] == "false"


def test_transform_headers_to_error_data():
    """Test parsing error data from headers."""
    headers = {
        C.HEADER_ERROR_MESSAGE: "Connection failed",
        C.HEADER_ERROR_TYPE: "ConnectionError",
        C.HEADER_ERROR_CODE: "CONN_TIMEOUT",
        C.HEADER_ERROR_RETRYABLE: "true",
        C.HEADER_ERROR_TIMESTAMP: "1706000000000",
        C.HEADER_ERROR_STACK: "Traceback...",
    }

    error_data = default_transform_headers_to_error_data(headers)

    assert error_data["message"] == "Connection failed"
    assert error_data["type"] == "ConnectionError"
    assert error_data["code"] == "CONN_TIMEOUT"
    assert error_data["retryable"] is True
    assert error_data["timestamp"] == 1706000000000
    assert error_data["stack"] == "Traceback..."


def test_transform_headers_to_error_data_with_json_data():
    """Test parsing error data with JSON data field."""
    headers = {
        C.HEADER_ERROR_MESSAGE: "Validation error",
        C.HEADER_ERROR_DATA: '{"field": "email", "rule": "required"}',
    }

    error_data = default_transform_headers_to_error_data(headers)

    assert error_data["message"] == "Validation error"
    assert error_data["data"]["field"] == "email"
    assert error_data["data"]["rule"] == "required"


def test_transform_headers_to_error_data_invalid_timestamp():
    """Test handling invalid timestamp."""
    headers = {
        C.HEADER_ERROR_TIMESTAMP: "not-a-number",
    }

    error_data = default_transform_headers_to_error_data(headers)

    # Should keep as string if parsing fails
    assert error_data["timestamp"] == "not-a-number"


def test_transform_headers_to_error_data_invalid_json():
    """Test handling invalid JSON in data field."""
    headers = {
        C.HEADER_ERROR_DATA: "invalid-json{",
    }

    error_data = default_transform_headers_to_error_data(headers)

    # Should keep as string if JSON parsing fails
    assert error_data["data"] == "invalid-json{"


def test_roundtrip_error_transformation():
    """Test roundtrip: error -> headers -> data."""

    class TestError(Exception):
        def __init__(self, message: str):
            super().__init__(message)
            self.code = "TEST_ERROR"
            self.data = {"key": "value"}
            self.retryable = False

    # Create error
    original_error = TestError("Test message")

    # Transform to headers
    headers = default_transform_error_to_headers(original_error)

    # Transform back to data
    error_data = default_transform_headers_to_error_data(headers)

    # Verify data
    assert error_data["message"] == "Test message"
    assert error_data["type"] == "TestError"
    assert error_data["code"] == "TEST_ERROR"
    assert error_data["retryable"] is False
    assert error_data["data"]["key"] == "value"
