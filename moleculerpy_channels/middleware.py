"""
Channels middleware for MoleculerPy.

Integrates channel adapters into the MoleculerPy broker lifecycle, handles
service schema parsing, context propagation, and message routing.
"""

import importlib
import base64
from collections.abc import Awaitable, Callable
from typing import Any

from .adapters.base import BaseAdapter
from .channel import Channel, DeadLetteringOptions, RedisOptions
from .errors import ChannelRegistrationError


class ChannelsMiddleware:
    """
    Middleware that adds persistent pub/sub messaging to MoleculerPy.

    Integrates with broker lifecycle hooks to:
    1. Register `broker.send_to_channel()` method
    2. Parse service schema `channels` property
    3. Subscribe to channels via adapter
    4. Handle context propagation for distributed tracing

    Example:
        >>> from moleculerpy import ServiceBroker
        >>> from moleculerpy_channels import ChannelsMiddleware
        >>> from moleculerpy_channels.adapters import FakeAdapter
        >>>
        >>> broker = ServiceBroker(
        ...     middlewares=[ChannelsMiddleware(adapter=FakeAdapter())]
        ... )
    """

    def __init__(
        self,
        adapter: BaseAdapter,
        schema_property: str = "channels",
        send_method_name: str = "send_to_channel",
        adapter_property_name: str = "channel_adapter",
        context: bool = False,
    ) -> None:
        """
        Initialize Channels middleware.

        Args:
            adapter: Adapter instance (Redis, Kafka, NATS, or Fake)
            schema_property: Property name in service schema (default: "channels")
            send_method_name: Method name on broker (default: "send_to_channel")
            adapter_property_name: Property name for adapter reference (default: "channel_adapter")
            context: Create Moleculer Context by default (default: False)

        Raises:
            TypeError: If adapter is not a BaseAdapter instance
        """
        if not isinstance(adapter, BaseAdapter):
            raise TypeError(f"adapter must be BaseAdapter instance, got {type(adapter)}")

        self.adapter = adapter
        self.schema_property = schema_property
        self.send_method_name = send_method_name
        self.adapter_property_name = adapter_property_name
        self.context = context

        self.broker: Any = None
        self.logger: Any = None
        self.started = False
        self.channel_registry: list[dict[str, Any]] = []

    def broker_created(self, broker: Any) -> None:
        """
        Hook called when broker is created.

        Registers `broker.send_to_channel()` method and initializes adapter.

        Args:
            broker: ServiceBroker instance

        Raises:
            ChannelRegistrationError: If method/property name conflicts exist
        """
        self.broker = broker

        # Get logger from broker
        # Priority 1: broker.get_logger() (if exists)
        # Priority 2: broker._create_logger() (if exists)
        # Priority 3: broker.logger (fallback)
        # Priority 4: None
        if hasattr(broker, "get_logger"):
            self.logger = broker.get_logger("Channels")
        elif hasattr(broker, "_create_logger"):
            self.logger = broker._create_logger("Channels")
        elif hasattr(broker, "logger"):
            self.logger = broker.logger
        else:
            self.logger = None

        # Initialize adapter
        self.adapter.init(broker, self.logger)

        # Register send_to_channel method
        if hasattr(broker, self.send_method_name):
            raise ChannelRegistrationError(
                f"broker.{self.send_method_name} already exists, use different send_method_name"
            )

        setattr(broker, self.send_method_name, self._create_send_to_channel_method())

        # Add adapter reference
        if hasattr(broker, self.adapter_property_name):
            raise ChannelRegistrationError(
                f"broker.{self.adapter_property_name} already exists, use different adapter_property_name"
            )

        setattr(broker, self.adapter_property_name, self.adapter)

        if self.logger:
            self.logger.info("ChannelsMiddleware initialized")

    def _create_send_to_channel_method(
        self,
    ) -> Callable[[str, Any, dict[str, Any] | None], Awaitable[None]]:
        """
        Create the send_to_channel method that will be attached to broker.

        Returns:
            Async function that publishes messages to channels
        """

        async def send_to_channel(
            channel_name: str, payload: Any, opts: dict[str, Any] | None = None
        ) -> None:
            """
            Send message to a channel.

            Args:
                channel_name: Name of the channel
                payload: Message payload (will be serialized)
                opts: Publishing options (ctx, headers, etc.)

            Example:
                >>> await broker.send_to_channel("orders.created", {"orderId": 123})
                >>> await broker.send_to_channel("logs", {"msg": "Hello"}, opts={"ctx": ctx})
            """
            if opts is None:
                opts = {}

            # Context propagation
            if "ctx" in opts:
                ctx = opts["ctx"]
                if "headers" not in opts:
                    opts["headers"] = {}

                # Transfer Context properties to headers
                opts["headers"]["$requestID"] = ctx.request_id
                opts["headers"]["$parentID"] = ctx.id
                opts["headers"]["$tracing"] = (
                    str(ctx.tracing) if ctx.tracing is not None else "false"
                )
                opts["headers"]["$level"] = str(ctx.level)

                # Caller service name
                if hasattr(ctx, "service") and ctx.service:
                    opts["headers"]["$caller"] = getattr(
                        ctx.service, "full_name", getattr(ctx.service, "name", "unknown")
                    )

                # Parent channel name (for nested channel calls)
                if hasattr(ctx, "channel_name") and ctx.channel_name:
                    opts["headers"]["$parentChannelName"] = ctx.channel_name

                # Serialize meta
                if ctx.meta:
                    serialized_meta = self.adapter.serializer.serialize(ctx.meta)
                    opts["headers"]["$meta"] = base64.b64encode(serialized_meta).decode("ascii")

                # Serialize headers (if ctx has headers)
                if hasattr(ctx, "headers") and ctx.headers:
                    serialized_headers = self.adapter.serializer.serialize(ctx.headers)
                    opts["headers"]["$headers"] = base64.b64encode(serialized_headers).decode(
                        "ascii"
                    )

                # Remove ctx from opts (don't send to adapter)
                del opts["ctx"]

            # Add prefix to channel name
            prefixed_name = self.adapter.add_prefix_topic(channel_name)

            # Publish via adapter
            await self.adapter.publish(prefixed_name, payload, opts)

        return send_to_channel

    async def service_created(self, service: Any) -> None:
        """
        Hook called after service is created.

        Parses service schema for `channels` definition and registers handlers.

        Args:
            service: Service instance
        """
        if not hasattr(service, "schema") or not isinstance(service.schema, dict):
            return

        channels_def = service.schema.get(self.schema_property)
        if not channels_def or not isinstance(channels_def, dict):
            return

        # Process each channel definition
        for name, definition in channels_def.items():
            channel = await self._parse_channel_definition(name, definition, service)

            # Register channel
            self._register_channel(service, channel)

            if self.logger:
                self.logger.debug(
                    f"Registered '{channel.name}' channel in '{getattr(service, 'full_name', getattr(service, 'name', 'unknown'))}' service "
                    f"with group '{channel.group}'"
                )

            # If middleware already started, subscribe immediately
            if self.started:
                await self.adapter.subscribe(channel, service)

    async def _parse_channel_definition(self, name: str, definition: Any, service: Any) -> Channel:
        """
        Parse channel definition from service schema.

        Args:
            name: Channel name
            definition: Channel definition (function or dict)
            service: Service instance

        Returns:
            Channel configuration object

        Raises:
            ChannelRegistrationError: If definition is invalid
        """
        # Simple function handler
        if callable(definition):
            handler = definition
            channel_def: dict[str, Any] = {}
        # Dictionary definition
        elif isinstance(definition, dict):
            if "handler" not in definition or not callable(definition["handler"]):
                raise ChannelRegistrationError(
                    f"Missing or invalid handler in '{name}' channel for '{getattr(service, 'full_name', getattr(service, 'name', 'unknown'))}'"
                )
            handler = definition["handler"]
            channel_def = definition.copy()
            del channel_def["handler"]
        else:
            raise ChannelRegistrationError(
                f"Invalid channel definition for '{name}' in '{getattr(service, 'full_name', getattr(service, 'name', 'unknown'))}': "
                "must be function or dict with 'handler'"
            )

        # Build Channel object
        channel_name = channel_def.get("name", self.adapter.add_prefix_topic(name))
        group = channel_def.get(
            "group", getattr(service, "full_name", getattr(service, "name", "unknown"))
        )
        context = channel_def.get("context", self.context)

        # Consumer ID: <nodeID>.<serviceName>.<channelName>
        # MoleculerPy uses 'nodeID' (Moleculer.js compatible attribute name)
        node_id = getattr(self.broker, "nodeID", getattr(self.broker, "node_id", "node-1"))
        service_name = getattr(service, "full_name", getattr(service, "name", "unknown"))
        consumer_id = self.adapter.add_prefix_topic(f"{node_id}.{service_name}.{channel_name}")

        # Dead lettering options
        dlq_opts = None
        if "dead_lettering" in channel_def:
            dlq_config = channel_def["dead_lettering"]
            if isinstance(dlq_config, dict):
                dlq_opts = DeadLetteringOptions(**dlq_config)

        # Redis options
        redis_opts = None
        if "redis" in channel_def:
            redis_config = channel_def["redis"]
            if isinstance(redis_config, dict):
                redis_opts = RedisOptions(**redis_config)

        # Tracing options
        tracing_opts = channel_def.get("tracing")
        # Can be: True/False, dict with {enabled, spanName, tags, safetyTags}, or None

        # Wrap handler (pass channel_name for context.channel_name)
        wrapped_handler = await self._wrap_handler(handler, service, context, channel_name)

        return Channel(
            id=consumer_id,
            name=channel_name,
            group=group,
            context=context,
            max_in_flight=channel_def.get("max_in_flight"),
            max_retries=channel_def.get("max_retries", 3),
            dead_lettering=dlq_opts,
            redis=redis_opts,
            tracing=tracing_opts,
            handler=wrapped_handler,
            raw_handler=handler,  # Keep original for testing
        )

    async def _wrap_handler(
        self,
        handler: Callable[..., Awaitable[None]],
        service: Any,
        create_context: bool,
        channel_name: str,
    ) -> Callable[[Any, Any], Awaitable[None]]:
        """
        Wrap channel handler with context creation logic.

        Args:
            handler: Original handler function
            service: Service instance
            create_context: Whether to create Moleculer Context
            channel_name: Name of the channel (for ctx.channel_name)

        Returns:
            Wrapped async handler
        """
        # Bind handler to service
        if hasattr(handler, "__self__"):
            # Already bound
            bound_handler: Callable[[Any, Any], Awaitable[None]] = handler
        else:
            # Bind to service instance
            async def bound_handler(payload: Any, raw: Any) -> None:
                await handler(service, payload, raw)

        # If context creation is disabled, return bound handler
        if not create_context:

            async def simple_handler(payload: Any, raw: Any) -> None:
                await bound_handler(payload, raw)

            return simple_handler

        # Context-creating wrapper
        async def context_handler(payload: Any, raw: Any) -> None:
            """Handler that creates Moleculer Context from message headers."""
            # Parse headers
            headers = self.adapter.parse_message_headers(raw)

            parent_ctx: dict[str, Any] | None = None
            caller = None
            meta = None
            ctx_headers = None
            parent_channel_name = None

            if headers:
                # Parent context info
                if "$requestID" in headers:
                    parent_ctx = {
                        "id": headers.get("$parentID"),
                        "request_id": headers["$requestID"],
                        "tracing": headers.get("$tracing", "false") == "true",
                        "level": int(headers.get("$level", "0")),
                    }
                    caller = headers.get("$caller")
                    parent_channel_name = headers.get("$parentChannelName")

                # Deserialize meta
                if "$meta" in headers:
                    try:
                        meta_bytes = base64.b64decode(headers["$meta"])
                        meta = self.adapter.serializer.deserialize(meta_bytes)
                    except Exception as e:
                        if self.logger:
                            self.logger.warning(f"Failed to deserialize $meta: {e}")

                # Deserialize headers
                if "$headers" in headers:
                    try:
                        headers_bytes = base64.b64decode(headers["$headers"])
                        ctx_headers = self.adapter.serializer.deserialize(headers_bytes)
                    except Exception as e:
                        if self.logger:
                            self.logger.warning(f"Failed to deserialize $headers: {e}")

                # Error headers from DLQ
                if any(k.startswith("x-error-") for k in headers.keys()):
                    if ctx_headers is None:
                        ctx_headers = {}
                    error_data = self.adapter.transform_headers_to_error_data(headers)
                    ctx_headers.update(error_data)

            # Create Context
            # Note: Using MoleculerPy Context (assumed to be compatible)
            context_module = importlib.import_module("moleculerpy")
            context_cls = getattr(context_module, "Context")

            # Merge headers into meta (MoleculerPy Context doesn't have separate headers param)
            merged_meta = {**(meta or {})}
            if ctx_headers:
                merged_meta["$headers"] = ctx_headers

            parent_level = int(parent_ctx["level"]) + 1 if parent_ctx else 1

            ctx = context_cls(
                id=None,  # Will be generated
                params=payload,
                meta=merged_meta,
                broker=self.broker,
                parent_id=parent_ctx["id"] if parent_ctx else None,
                request_id=parent_ctx["request_id"] if parent_ctx else None,
                level=parent_level,
                caller=caller,
                tracing=parent_ctx["tracing"] if parent_ctx else False,
                service=service,
            )

            # Set channel name (from closure parameter)
            ctx.channel_name = channel_name
            ctx.parent_channel_name = parent_channel_name

            # Call handler with context
            await bound_handler(ctx, raw)

        return context_handler

    def _register_channel(self, service: Any, channel: Channel) -> None:
        """
        Register channel in internal registry.

        Args:
            service: Service instance
            channel: Channel configuration
        """
        # Remove existing registration (if any)
        self._unregister_channel(service, channel)

        self.channel_registry.append({"service": service, "name": channel.name, "channel": channel})

    def _unregister_channel(self, service: Any, channel: Channel | None = None) -> None:
        """
        Remove channel from registry.

        Args:
            service: Service instance
            channel: Specific channel to remove (None = remove all for service)
        """
        self.channel_registry = [
            item
            for item in self.channel_registry
            if not (
                getattr(item["service"], "full_name", getattr(item["service"], "name", "unknown"))
                == getattr(service, "full_name", getattr(service, "name", "unknown"))
                and (channel is None or channel.name == item["name"])
            )
        ]

    async def broker_starting(self, broker: Any) -> None:
        """
        Hook called before broker starts.

        Connects adapter and subscribes to all registered channels.

        Args:
            broker: ServiceBroker instance
        """
        if self.logger:
            self.logger.info("Channel adapter is connecting...")

        await self.adapter.connect()

        if self.logger:
            self.logger.debug("Channel adapter connected")
            self.logger.info(f"Subscribing to {len(self.channel_registry)} channels...")

        for item in self.channel_registry:
            await self.adapter.subscribe(item["channel"], item["service"])

        self.started = True

    async def service_stopping(self, service: Any) -> None:
        """
        Hook called when service is stopping.

        Unsubscribes all channels for the service.

        Args:
            service: Service instance
        """
        service_channels = [
            item
            for item in self.channel_registry
            if getattr(item["service"], "full_name", getattr(item["service"], "name", "unknown"))
            == getattr(service, "full_name", getattr(service, "name", "unknown"))
        ]

        for item in service_channels:
            await self.adapter.unsubscribe(item["channel"])

        self._unregister_channel(service)

    async def broker_stopped(self, broker: Any) -> None:
        """
        Hook called after broker stops.

        Disconnects adapter.

        Args:
            broker: ServiceBroker instance
        """
        if self.logger:
            self.logger.info("Channel adapter is disconnecting...")

        await self.adapter.disconnect()

        if self.logger:
            self.logger.debug("Channel adapter disconnected")

        self.started = False
