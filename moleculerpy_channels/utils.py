"""
Utility functions for error serialization and header manipulation.

Provides default implementations for converting Python exceptions to/from headers
for dead-letter queue processing.
"""

import json
import traceback
from datetime import datetime
from typing import Any

from . import constants as C


def default_transform_error_to_headers(error: Exception) -> dict[str, str]:
    """
    Convert Python exception to error headers.

    Transforms a Python exception into a dictionary of headers suitable for
    storing in dead-letter queue messages.

    Args:
        error: Exception to serialize

    Returns:
        Dictionary of error headers (all values are strings)

    Example:
        >>> try:
        ...     raise ValueError("Invalid input")
        ... except ValueError as e:
        ...     headers = default_transform_error_to_headers(e)
        >>> headers[C.HEADER_ERROR_MESSAGE]
        'Invalid input'
        >>> headers[C.HEADER_ERROR_TYPE]
        'ValueError'
    """
    headers: dict[str, str] = {}

    # Basic error info
    headers[C.HEADER_ERROR_MESSAGE] = str(error)
    headers[C.HEADER_ERROR_TYPE] = type(error).__name__
    headers[C.HEADER_ERROR_NAME] = type(error).__name__

    # Error code (if available)
    if hasattr(error, "code"):
        headers[C.HEADER_ERROR_CODE] = str(error.code)

    # Stack trace
    tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    headers[C.HEADER_ERROR_STACK] = tb

    # Error data (if available)
    if hasattr(error, "data"):
        try:
            headers[C.HEADER_ERROR_DATA] = json.dumps(error.data)
        except (TypeError, ValueError):
            # If data can't be serialized, store repr
            headers[C.HEADER_ERROR_DATA] = repr(error.data)

    # Retryable flag (default: true for most errors)
    retryable = getattr(error, "retryable", True)
    headers[C.HEADER_ERROR_RETRYABLE] = str(retryable).lower()

    # Timestamp (Unix milliseconds)
    timestamp_ms = int(datetime.now().timestamp() * 1000)
    headers[C.HEADER_ERROR_TIMESTAMP] = str(timestamp_ms)

    return headers


def default_transform_headers_to_error_data(headers: dict[str, str]) -> dict[str, Any]:
    """
    Parse error information from headers.

    Converts error headers back into a dictionary containing error details.
    Note: This does NOT reconstruct the original exception object, only the data.

    Args:
        headers: Dictionary of headers from dead-letter queue message

    Returns:
        Dictionary containing parsed error data

    Example:
        >>> headers = {
        ...     "x-error-message": "Connection failed",
        ...     "x-error-type": "ConnectionError",
        ...     "x-error-timestamp": "1706000000000"
        ... }
        >>> data = default_transform_headers_to_error_data(headers)
        >>> data["message"]
        'Connection failed'
        >>> data["type"]
        'ConnectionError'
    """
    error_data: dict[str, Any] = {}

    # Extract all error-related headers
    for key, value in headers.items():
        if not key.startswith(C.HEADER_ERROR_PREFIX):
            continue

        # Remove prefix and convert to snake_case
        field_name = key[len(C.HEADER_ERROR_PREFIX) :].replace("-", "_")

        # Parse specific fields
        if field_name == "retryable":
            error_data[field_name] = value.lower() == "true"
        elif field_name == "timestamp":
            try:
                error_data[field_name] = int(value)
            except ValueError:
                error_data[field_name] = value
        elif field_name == "data":
            try:
                error_data[field_name] = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                error_data[field_name] = value
        else:
            error_data[field_name] = value

    return error_data
