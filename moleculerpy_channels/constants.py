"""
Constants for MoleculerPy Channels.

Defines header names, error prefixes, and metric names compatible with
Moleculer Channels (Node.js).
"""

# ── Headers ──────────────────────────────────────────────────────────────────

# Number of redelivery attempts
HEADER_REDELIVERED_COUNT = "x-redelivered-count"

# Consumer group name
HEADER_GROUP = "x-group"

# Original channel where error occurred
HEADER_ORIGINAL_CHANNEL = "x-original-channel"

# Original consumer group that failed
HEADER_ORIGINAL_GROUP = "x-original-group"

# ── Error Headers ────────────────────────────────────────────────────────────

# Prefix for all error-related headers
HEADER_ERROR_PREFIX = "x-error-"

# Error message
HEADER_ERROR_MESSAGE = "x-error-message"

# Error code (if applicable)
HEADER_ERROR_CODE = "x-error-code"

# Error stack trace
HEADER_ERROR_STACK = "x-error-stack"

# Error type (class name)
HEADER_ERROR_TYPE = "x-error-type"

# Error data (serialized JSON)
HEADER_ERROR_DATA = "x-error-data"

# Error name
HEADER_ERROR_NAME = "x-error-name"

# Whether error is retryable
HEADER_ERROR_RETRYABLE = "x-error-retryable"

# Timestamp when error occurred (Unix ms)
HEADER_ERROR_TIMESTAMP = "x-error-timestamp"

# ── Metrics ──────────────────────────────────────────────────────────────────

# Number of messages sent to channels
METRIC_CHANNELS_MESSAGES_SENT = "moleculerpy.channels.messages.sent"

# Total messages received
METRIC_CHANNELS_MESSAGES_TOTAL = "moleculerpy.channels.messages.total"

# Active messages being processed
METRIC_CHANNELS_MESSAGES_ACTIVE = "moleculerpy.channels.messages.active"

# Message processing time histogram
METRIC_CHANNELS_MESSAGES_TIME = "moleculerpy.channels.messages.time"

# Total message errors
METRIC_CHANNELS_MESSAGES_ERRORS_TOTAL = "moleculerpy.channels.messages.errors.total"

# Total retry attempts
METRIC_CHANNELS_MESSAGES_RETRIES_TOTAL = "moleculerpy.channels.messages.retries.total"

# Messages sent to dead-letter queue
METRIC_CHANNELS_MESSAGES_DEAD_LETTERING_TOTAL = "moleculerpy.channels.messages.deadLettering.total"

# ── Error Codes ──────────────────────────────────────────────────────────────

# Thrown when incoming messages cannot be deserialized
INVALID_MESSAGE_SERIALIZATION_ERROR_CODE = "INVALID_MESSAGE_SERIALIZATION"
