"""TCP server channel for DNP3 communication.

Provides an async TCP server implementation for hosting DNP3 outstations
and accepting connections from DNP3 masters.
"""

import asyncio
import logging
from dataclasses import dataclass, field, replace

from dnp3.transport_io.channel import (
    ChannelClosedError,
    ChannelError,
    ChannelState,
    ChannelStatistics,
    TcpConfig,
    TcpServerConfig,
)
from dnp3.transport_io.tcp_client import StreamIO, configure_socket

logger = logging.getLogger(__name__)


@dataclass
class TcpServerChannel(StreamIO):
    """Server-side TCP channel for an accepted connection.

    Created by TcpServer when a client connects.

    Attributes:
        reader: Asyncio stream reader.
        writer: Asyncio stream writer.
        config: TCP configuration.
    """

    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    config: TcpConfig = field(default_factory=TcpConfig)

    _state: ChannelState = field(default=ChannelState.OPEN, init=False)
    _statistics: ChannelStatistics = field(default_factory=ChannelStatistics, init=False)

    def __post_init__(self) -> None:
        """Initialize channel state."""
        self._statistics.connect_count = 1

    async def open(self) -> None:
        """Open is a no-op for server channels (already open)."""
        pass


@dataclass
class TcpServer:
    """TCP server for DNP3 communication.

    Listens for incoming TCP connections and creates TcpServerChannel
    instances for each accepted connection.

    Attributes:
        config: Server configuration.
    """

    config: TcpServerConfig = field(default_factory=TcpServerConfig)

    _state: ChannelState = field(default=ChannelState.CLOSED, init=False)
    _server: asyncio.Server | None = field(default=None, init=False)
    _accept_queue: asyncio.Queue[TcpServerChannel] = field(default=None, init=False)  # type: ignore[arg-type]
    _connections: list[TcpServerChannel] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        """Initialize internal state."""
        self._accept_queue = asyncio.Queue()

    @property
    def state(self) -> ChannelState:
        """Get current server state."""
        return self._state

    @property
    def is_listening(self) -> bool:
        """Check if server is listening for connections."""
        return self._state == ChannelState.OPEN

    @property
    def local_address(self) -> tuple[str, int] | None:
        """Get local address (host, port) if listening."""
        if self._server is not None and self._server.sockets:
            try:
                sockname = self._server.sockets[0].getsockname()
                return (sockname[0], sockname[1])
            except (AttributeError, IndexError):
                pass
        return None

    @property
    def connection_count(self) -> int:
        """Number of active connections."""
        return len(self._connections)

    async def start(self) -> None:
        """Start listening for connections.

        Raises:
            ChannelError: If server cannot start.
        """
        if self._state == ChannelState.OPEN:
            return

        self._state = ChannelState.OPENING

        try:
            self._server = await asyncio.start_server(
                self._handle_connection,
                host=self.config.host,
                port=self.config.port,
                backlog=self.config.backlog,
                reuse_address=self.config.reuse_address,
            )
            self._state = ChannelState.OPEN
        except OSError as e:
            self._state = ChannelState.CLOSED
            raise ChannelError(f"Failed to start server on {self.config.host}:{self.config.port}: {e}") from e

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle an incoming connection.

        Args:
            reader: Stream reader for the connection.
            writer: Stream writer for the connection.
        """
        # Refuse a connection that reaches here while not OPEN: stop() sets
        # CLOSING before it closes tracked connections, and this can still
        # run after that point for a connection already in flight.
        if self._state != ChannelState.OPEN:
            writer.close()
            await writer.wait_closed()
            return

        # Check max connections
        if self.config.max_connections > 0 and len(self._connections) >= self.config.max_connections:
            writer.close()
            await writer.wait_closed()
            return

        # Configure socket options
        sock = writer.get_extra_info("socket")
        if sock is not None:
            configure_socket(sock, self.config)

        # Create server channel
        channel = TcpServerChannel(
            reader=reader,
            writer=writer,
            config=TcpConfig(
                host=self.config.host,
                port=self.config.port,
                read_timeout=self.config.read_timeout,
                write_timeout=self.config.write_timeout,
                nodelay=self.config.nodelay,
                keepalive=self.config.keepalive,
                keepalive_idle=self.config.keepalive_idle,
                keepalive_interval=self.config.keepalive_interval,
                keepalive_count=self.config.keepalive_count,
                close_timeout=self.config.close_timeout,
            ),
        )

        self._connections.append(channel)
        await self._accept_queue.put(channel)

    async def stop(self) -> None:
        """Stop listening and close all connections."""
        if self._state == ChannelState.CLOSED:
            return

        self._state = ChannelState.CLOSING

        # Stop accepting first: with the listener closed and
        # _handle_connection() refusing while state is not OPEN, a
        # connection arriving during the bounded close below is refused.
        if self._server is not None:
            self._server.close()

        # Close concurrently so one stalled peer cannot multiply the bound
        # across every connection. _handle_connection() refuses anything
        # else that arrives, so this loop runs once in practice.
        while self._connections:
            pending = list(self._connections)
            results = await asyncio.gather(*(conn.close() for conn in pending), return_exceptions=True)
            for conn, result in zip(pending, results, strict=True):
                if isinstance(result, Exception):
                    logger.error("Unexpected error closing a connection during stop()", exc_info=result)
                self.remove_connection(conn)

        # wait_closed() waits for every connection this server ever accepted
        # to close, so it must run last.
        if self._server is not None:
            await self._server.wait_closed()
            self._server = None

        # Clear accept queue
        while not self._accept_queue.empty():
            try:
                self._accept_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        self._state = ChannelState.CLOSED

    async def accept(self) -> TcpServerChannel:
        """Accept an incoming connection.

        Returns:
            Channel for the accepted connection.

        Raises:
            ChannelClosedError: If server is not listening.
        """
        if self._state != ChannelState.OPEN:
            raise ChannelClosedError("Server is not listening")

        return await self._accept_queue.get()

    def remove_connection(self, channel: TcpServerChannel) -> None:
        """Remove a connection from the tracked list.

        Args:
            channel: Channel to remove.
        """
        if channel in self._connections:
            self._connections.remove(channel)

    async def __aenter__(self) -> "TcpServer":
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Async context manager exit."""
        await self.stop()


async def serve(
    host: str = "127.0.0.1",
    port: int = 20000,
    config: TcpServerConfig | None = None,
) -> TcpServer:
    """Create and start a TCP server.

    Args:
        host: Host address to bind to.
        port: Port to listen on.
        config: Optional server configuration.

    Returns:
        Started TcpServer.

    Raises:
        ChannelError: If server cannot start.
    """
    config = TcpServerConfig(host=host, port=port) if config is None else replace(config, host=host, port=port)

    server = TcpServer(config=config)
    await server.start()
    return server
