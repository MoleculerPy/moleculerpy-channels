"""
Metrics collection for Channels middleware.

Provides 7 core metrics compatible with moleculer-channels:
- COUNTER: sent, total, errors, retries, deadLettering
- GAUGE: active (in-flight messages)
- HISTOGRAM: time (processing duration)

Example:
    >>> metrics = ChannelMetrics(broker)
    >>> metrics.increment_sent("orders.created", "order-processors")
    >>> metrics.increment_total("orders.created", "order-processors")
    >>> metrics.observe_time("orders.created", "order-processors", 123.45)
"""

from enum import Enum
from typing import Any, Protocol


class MetricType(Enum):
    """Metric types compatible with Moleculer."""

    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"


class BrokerProtocol(Protocol):
    """Protocol for broker with metrics support."""

    def get_logger(self, name: str) -> Any: ...


class ChannelMetrics:
    """
    Metrics collector for Channels middleware.

    Tracks 7 core metrics with labels (channel, group):
    - sent: Messages sent to channel (COUNTER)
    - total: Total messages processed (COUNTER)
    - active: In-flight messages (GAUGE)
    - time: Processing duration in ms (HISTOGRAM)
    - errors: Error count (COUNTER)
    - retries: Retry attempts (COUNTER)
    - deadLettering: DLQ moves (COUNTER)

    Attributes:
        broker: ServiceBroker instance (optional, for Moleculer integration)
        logger: Logger instance
        enabled: Enable/disable metrics collection
        metrics: Internal metrics storage (when broker.metrics not available)
    """

    # Metric names (compatible with moleculer-channels)
    METRIC_MESSAGES_SENT = "moleculer.channels.messages.sent"
    METRIC_MESSAGES_TOTAL = "moleculer.channels.messages.total"
    METRIC_MESSAGES_ACTIVE = "moleculer.channels.messages.active"
    METRIC_MESSAGES_TIME = "moleculer.channels.messages.time"
    METRIC_MESSAGES_ERRORS = "moleculer.channels.messages.errors.total"
    METRIC_MESSAGES_RETRIES = "moleculer.channels.messages.retries.total"
    METRIC_MESSAGES_DLQ = "moleculer.channels.messages.deadLettering.total"

    def __init__(
        self,
        broker: Any | None = None,
        logger: Any | None = None,
        enabled: bool = True,
    ):
        """
        Initialize metrics collector.

        Args:
            broker: ServiceBroker instance (optional)
            logger: Logger instance (optional)
            enabled: Enable metrics collection (default: True)
        """
        self.broker = broker
        self.logger = logger
        self.enabled = enabled

        # Internal metrics storage (fallback when broker.metrics not available)
        self.metrics: dict[str, dict[str, Any]] = {
            self.METRIC_MESSAGES_SENT: {"type": MetricType.COUNTER, "values": {}},
            self.METRIC_MESSAGES_TOTAL: {"type": MetricType.COUNTER, "values": {}},
            self.METRIC_MESSAGES_ACTIVE: {"type": MetricType.GAUGE, "values": {}},
            self.METRIC_MESSAGES_TIME: {"type": MetricType.HISTOGRAM, "values": {}},
            self.METRIC_MESSAGES_ERRORS: {"type": MetricType.COUNTER, "values": {}},
            self.METRIC_MESSAGES_RETRIES: {"type": MetricType.COUNTER, "values": {}},
            self.METRIC_MESSAGES_DLQ: {"type": MetricType.COUNTER, "values": {}},
        }

    def _make_labels(self, channel: str, group: str) -> dict[str, str]:
        """Create labels dict for metrics."""
        return {"channel": channel, "group": group}

    def _make_key(self, channel: str, group: str) -> str:
        """Create unique key for label combination."""
        return f"{channel}:{group}"

    def _increment(self, metric_name: str, channel: str, group: str, delta: int = 1) -> None:
        """
        Increment counter metric.

        Args:
            metric_name: Metric name
            channel: Channel name
            group: Consumer group name
            delta: Increment value (default: 1)
        """
        if not self.enabled:
            return

        # Try broker.metrics first (Moleculer integration)
        if self.broker and hasattr(self.broker, "metrics"):
            try:
                labels = self._make_labels(channel, group)
                self.broker.metrics.increment(metric_name, delta, labels)
                return
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Failed to increment broker metric {metric_name}: {e}")

        # Fallback to internal storage
        key = self._make_key(channel, group)
        if metric_name in self.metrics:
            values = self.metrics[metric_name]["values"]
            values[key] = values.get(key, 0) + delta

    def _set_gauge(self, metric_name: str, channel: str, group: str, value: float) -> None:
        """
        Set gauge metric value.

        Args:
            metric_name: Metric name
            channel: Channel name
            group: Consumer group name
            value: Gauge value
        """
        if not self.enabled:
            return

        # Try broker.metrics first
        if self.broker and hasattr(self.broker, "metrics"):
            try:
                labels = self._make_labels(channel, group)
                self.broker.metrics.set(metric_name, value, labels)
                return
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Failed to set broker metric {metric_name}: {e}")

        # Fallback to internal storage
        key = self._make_key(channel, group)
        if metric_name in self.metrics:
            self.metrics[metric_name]["values"][key] = value

    def _observe(self, metric_name: str, channel: str, group: str, value: float) -> None:
        """
        Observe histogram metric value.

        Args:
            metric_name: Metric name
            channel: Channel name
            group: Consumer group name
            value: Observed value (e.g., duration in ms)
        """
        if not self.enabled:
            return

        # Try broker.metrics first
        if self.broker and hasattr(self.broker, "metrics"):
            try:
                labels = self._make_labels(channel, group)
                self.broker.metrics.observe(metric_name, value, labels)
                return
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Failed to observe broker metric {metric_name}: {e}")

        # Fallback to internal storage (track min/max/count for histograms)
        key = self._make_key(channel, group)
        if metric_name in self.metrics:
            values = self.metrics[metric_name]["values"]
            if key not in values:
                values[key] = {"min": value, "max": value, "count": 1, "sum": value}
            else:
                stats = values[key]
                stats["min"] = min(stats["min"], value)
                stats["max"] = max(stats["max"], value)
                stats["count"] += 1
                stats["sum"] += value

    # Public API methods

    def increment_sent(self, channel: str, group: str) -> None:
        """Increment 'sent' counter (message published)."""
        self._increment(self.METRIC_MESSAGES_SENT, channel, group)

    def increment_total(self, channel: str, group: str) -> None:
        """Increment 'total' counter (message processed)."""
        self._increment(self.METRIC_MESSAGES_TOTAL, channel, group)

    def increment_errors(self, channel: str, group: str) -> None:
        """Increment 'errors' counter (handler failure)."""
        self._increment(self.METRIC_MESSAGES_ERRORS, channel, group)

    def increment_retries(self, channel: str, group: str) -> None:
        """Increment 'retries' counter (XAUTOCLAIM retry)."""
        self._increment(self.METRIC_MESSAGES_RETRIES, channel, group)

    def increment_dlq(self, channel: str, group: str) -> None:
        """Increment 'deadLettering' counter (DLQ move)."""
        self._increment(self.METRIC_MESSAGES_DLQ, channel, group)

    def set_active(self, channel: str, group: str, count: int) -> None:
        """Set 'active' gauge (in-flight messages)."""
        self._set_gauge(self.METRIC_MESSAGES_ACTIVE, channel, group, count)

    def observe_time(self, channel: str, group: str, duration_ms: float) -> None:
        """Observe 'time' histogram (processing duration in ms)."""
        self._observe(self.METRIC_MESSAGES_TIME, channel, group, duration_ms)

    def get_metrics(self) -> dict[str, Any]:
        """
        Get current metrics snapshot (internal storage only).

        Returns:
            Dict of metrics with values per label combination

        Note:
            When broker.metrics is available, this returns internal storage only.
            Use broker.metrics.list() to get Moleculer metrics.
        """
        return {
            name: {"type": info["type"].value, "values": dict(info["values"])}
            for name, info in self.metrics.items()
        }
