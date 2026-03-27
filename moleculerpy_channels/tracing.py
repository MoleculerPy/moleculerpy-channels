"""
Tracing middleware for Channels.

Automatically wraps channel handlers with distributed tracing spans,
providing automatic timing, error tracking, and tag extraction.

Based on moleculer-channels/src/tracing.js (122 lines).

Example:
    >>> from moleculerpy import ServiceBroker
    >>> from moleculerpy_channels import ChannelsMiddleware, TracingMiddleware
    >>> from moleculerpy_channels.adapters import RedisAdapter
    >>>
    >>> broker = ServiceBroker(middlewares=[
    ...     ChannelsMiddleware(adapter=RedisAdapter()),
    ...     TracingMiddleware()  # Add after ChannelsMiddleware
    ... ])
    >>>
    >>> # Service with tracing
    >>> class OrderService(Service):
    ...     channels = {
    ...         "orders.process": {
    ...             "context": True,
    ...             "handler": async (ctx) => await process_order(ctx.params),
    ...             "tracing": {
    ...                 "spanName": lambda ctx: f"Process Order #{ctx.params['orderId']}",
    ...                 "tags": {
    ...                     "params": ["orderId", "userId"],  # Selective
    ...                     "meta": True  # Full meta
    ...                 },
    ...                 "safetyTags": True  # Remove sensitive data
    ...             }
    ...         }
    ...     }
"""

from collections.abc import Awaitable, Callable
from typing import Any

from .channel import Channel


class TracingMiddleware:
    """
    Middleware for automatic distributed tracing in channel handlers.

    Wraps channel handlers with span creation/finishing, timing measurement,
    error attribution, and tag extraction.

    Compatible with Moleculer tracer interface (startSpan, finishSpan, setError).
    If broker has no tracer, middleware is no-op (returns handler unchanged).
    """

    def __init__(self) -> None:
        """Initialize tracing middleware."""
        self.broker: Any = None
        self.tracer: Any = None

    def created(self, broker: Any) -> None:
        """
        Hook called when broker is created.

        Stores broker and tracer references.

        Args:
            broker: ServiceBroker instance
        """
        self.broker = broker
        self.tracer = getattr(broker, "tracer", None)

    def local_channel(
        self,
        next: Callable[..., Awaitable[Any]],
        channel: Channel,
    ) -> Callable[..., Awaitable[Any]]:
        """
        Wrap channel handler with tracing logic.

        Creates span on handler entry, finishes on exit, tracks errors.

        Args:
            next: Original handler function
            channel: Channel configuration

        Returns:
            Wrapped handler with tracing
        """
        # Get tracing options from channel
        opts = getattr(channel, "tracing", None)

        # Normalize options
        if opts is True or opts is False:
            opts = {"enabled": bool(opts)}
        elif opts is None:
            opts = {}
        elif not isinstance(opts, dict):
            opts = {}

        # Default: enabled
        opts = {"enabled": True, **opts}

        # If tracing disabled OR no tracer available, return handler unchanged
        if not opts.get("enabled"):
            return next

        if not self._is_tracing_enabled():
            return next

        # Wrap handler with span creation
        async def tracing_wrapper(ctx: Any, *args: Any, **kwargs: Any) -> Any:
            """Wrapped handler with automatic span creation."""
            # Ensure context has tracing fields
            if not hasattr(ctx, "request_id") or ctx.request_id is None:
                ctx.request_id = self._get_current_trace_id()

            if not hasattr(ctx, "parent_id") or not ctx.parent_id:
                ctx.parent_id = self._get_active_span_id()

            # Build span tags
            tags = self._build_span_tags(ctx, channel, opts)

            # Build span name
            span_name = self._build_span_name(ctx, channel, opts)

            # Create span
            span = self._start_span(ctx, span_name, tags)

            try:
                # Execute handler
                result = await next(ctx, *args, **kwargs)

                # Finish span on success
                self._finish_span(ctx, span)

                return result

            except Exception as err:
                # Mark span with error
                self._set_span_error(span, err)

                # Finish span
                self._finish_span(ctx, span)

                # Re-raise
                raise

        return tracing_wrapper

    def _is_tracing_enabled(self) -> bool:
        """Check if broker has tracing enabled."""
        if not self.broker:
            return False

        # Check if broker has isTracingEnabled method
        if hasattr(self.broker, "isTracingEnabled") and callable(self.broker.isTracingEnabled):
            result = self.broker.isTracingEnabled()
            return bool(result)

        # Fallback: check if tracer exists
        return self.tracer is not None

    def _get_current_trace_id(self) -> str | None:
        """Get current trace ID from tracer."""
        if self.tracer and hasattr(self.tracer, "getCurrentTraceID"):
            trace_id = self.tracer.getCurrentTraceID()
            return trace_id if isinstance(trace_id, str) else None
        return None

    def _get_active_span_id(self) -> str | None:
        """Get active span ID from tracer."""
        if self.tracer and hasattr(self.tracer, "getActiveSpanID"):
            span_id = self.tracer.getActiveSpanID()
            return span_id if isinstance(span_id, str) else None
        return None

    def _build_span_name(self, ctx: Any, channel: Channel, opts: dict[str, Any]) -> str:
        """
        Build span name from options.

        Args:
            ctx: Context instance
            channel: Channel configuration
            opts: Tracing options

        Returns:
            Span name (default: "channel 'channel.name'")
        """
        # Default name
        span_name = f"channel '{channel.name}'"

        # Custom span name
        if "spanName" in opts:
            span_name_opt = opts["spanName"]

            # String: use as-is
            if isinstance(span_name_opt, str):
                span_name = span_name_opt

            # Function: call with context
            elif callable(span_name_opt):
                try:
                    result = span_name_opt(ctx)
                    if result:
                        span_name = str(result)
                except Exception:
                    pass  # Fallback to default

        return span_name

    def _build_span_tags(
        self, ctx: Any, channel: Channel, opts: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Build span tags from context and channel.

        Args:
            ctx: Context instance
            channel: Channel configuration
            opts: Tracing options with 'tags' config

        Returns:
            Dictionary of span tags
        """
        # Base tags (always included)
        # MoleculerPy uses 'nodeID' (Moleculer.js compatible attribute name)
        broker_node_id = getattr(self.broker, 'nodeID', getattr(self.broker, 'node_id', None)) if self.broker else None

        tags: dict[str, Any] = {
            "callingLevel": getattr(ctx, "level", 0),
            "chan": {
                "name": channel.name,
                "group": channel.group,
            },
            "nodeID": broker_node_id,
            "requestID": getattr(ctx, "request_id", None),
        }

        # Optional: remoteCall flag
        if hasattr(ctx, "node_id"):
            tags["remoteCall"] = ctx.node_id != broker_node_id
            tags["callerNodeID"] = ctx.node_id

        # Extract action tags from options
        action_tags_opt = opts.get("tags")

        # Default: include params
        if action_tags_opt is None:
            action_tags_opt = {"params": True}

        # Function: call and merge result
        if callable(action_tags_opt):
            try:
                result = action_tags_opt(ctx)
                if result and isinstance(result, dict):
                    tags.update(result)
            except Exception:
                pass  # Ignore errors in tag extraction

        # Dict: extract params/meta selectively
        elif isinstance(action_tags_opt, dict):
            # Extract params
            if action_tags_opt.get("params") is True:
                # Full params
                if hasattr(ctx, "params") and ctx.params:
                    tags["params"] = dict(ctx.params) if isinstance(ctx.params, dict) else ctx.params
            elif isinstance(action_tags_opt.get("params"), list):
                # Selective params (pick specific keys)
                if hasattr(ctx, "params") and isinstance(ctx.params, dict):
                    tags["params"] = {
                        k: ctx.params[k]
                        for k in action_tags_opt["params"]
                        if k in ctx.params
                    }

            # Extract meta
            if action_tags_opt.get("meta") is True:
                # Full meta
                if hasattr(ctx, "meta") and ctx.meta:
                    tags["meta"] = dict(ctx.meta) if isinstance(ctx.meta, dict) else ctx.meta
            elif isinstance(action_tags_opt.get("meta"), list):
                # Selective meta (pick specific keys)
                if hasattr(ctx, "meta") and isinstance(ctx.meta, dict):
                    tags["meta"] = {
                        k: ctx.meta[k]
                        for k in action_tags_opt["meta"]
                        if k in ctx.meta
                    }

        # Apply safety tags (remove sensitive data)
        if opts.get("safetyTags"):
            tags = self._safety_object(tags)

        return tags

    def _safety_object(self, obj: Any) -> Any:
        """
        Remove sensitive data from object.

        Removes fields with common sensitive names:
        - password, pass, pw
        - token, secret, key, apiKey
        - credit, card, cvv

        Args:
            obj: Object to sanitize

        Returns:
            Sanitized copy of object
        """
        if not isinstance(obj, dict):
            return obj

        sensitive_keys = {
            "password", "pass", "pw", "pwd",
            "token", "secret", "key", "apikey", "api_key",
            "credit", "card", "cvv", "ssn",
            "authorization", "auth"
        }

        sanitized: dict[str, Any] = {}
        for key, value in obj.items():
            key_lower = key.lower()

            # Skip sensitive fields
            if any(sens in key_lower for sens in sensitive_keys):
                sanitized[key] = "[REDACTED]"
            # Recursively sanitize nested dicts
            elif isinstance(value, dict):
                sanitized[key] = self._safety_object(value)
            # Recursively sanitize lists
            elif isinstance(value, list):
                sanitized[key] = [self._safety_object(item) for item in value]
            else:
                sanitized[key] = value

        return sanitized

    def _start_span(self, ctx: Any, span_name: str, tags: dict[str, Any]) -> Any:
        """
        Create and start a new span.

        Args:
            ctx: Context instance
            span_name: Name of the span
            tags: Span tags

        Returns:
            Span object (or None if no tracer)
        """
        if not ctx or not hasattr(ctx, "start_span"):
            return None

        try:
            span = ctx.start_span(
                span_name,
                id=getattr(ctx, "id", None),
                type="channel",
                trace_id=getattr(ctx, "request_id", None),
                parent_id=getattr(ctx, "parent_id", None),
                service=getattr(ctx, "service", None),
                sampled=getattr(ctx, "tracing", False),
                tags=tags,
            )

            # Update ctx.tracing with span's sampled state
            if span and hasattr(span, "sampled"):
                ctx.tracing = span.sampled

            return span

        except Exception:
            # If span creation fails, return None (tracing disabled)
            return None

    def _finish_span(self, ctx: Any, span: Any) -> None:
        """
        Finish a span.

        Args:
            ctx: Context instance
            span: Span to finish
        """
        if not span or not ctx or not hasattr(ctx, "finish_span"):
            return

        try:
            ctx.finish_span(span)
        except Exception:
            pass  # Ignore errors in span finishing

    def _set_span_error(self, span: Any, error: Exception) -> None:
        """
        Mark span with error.

        Args:
            span: Span object
            error: Exception that occurred
        """
        if not span or not hasattr(span, "set_error"):
            return

        try:
            span.set_error(error)
        except Exception:
            pass  # Ignore errors in error marking
