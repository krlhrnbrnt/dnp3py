"""TCP client channel for DNP3 communication.

Provides an async TCP client implementation for connecting to
DNP3 outstations or other TCP-based DNP3 endpoints.
"""

import asyncio
import socket
from dataclasses import dataclass, field, replace

from dnp3.transport_io.channel import (
    ChannelClosedError,
    ChannelConnectionError,
    ChannelError,
    ChannelState,
    ChannelStatistics,
    ChannelTimeoutError,
    TcpConfig,
)


def configure_socket(sock: socket.socket, config: TcpConfig) -> None:
    """Apply the TCP_NODELAY and keepalive options from config to sock."""
    # TCP_NODELAY - disable Nagle's algorithm
    if config.nodelay:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    # SO_KEEPALIVE - enable keepalive
    if config.keepalive:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

        # Platform-specific keepalive options
        if hasattr(socket, "TCP_KEEPIDLE"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, int(config.keepalive_idle))
        if hasattr(socket, "TCP_KEEPINTVL"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, int(config.keepalive_interval))
        if hasattr(socket, "TCP_KEEPCNT"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, config.keepalive_count)


class StreamIO:
    """Read, write and close over an asyncio stream pair.

    Shared by the client and server channels, which differ only in where the streams come from.
    """

    config: TcpConfig
    reader: asyncio.StreamReader | None
    writer: asyncio.StreamWriter | None
    _state: ChannelState
    _statistics: ChannelStatistics

    def _address(self, key: str) -> tuple[str, int] | None:
        if self.is_open and self.writer is not None:
            try:
                name = self.writer.get_extra_info(key)
                if name:
                    return (name[0], name[1])
            except (AttributeError, IndexError):
                pass
        return None

    @property
    def state(self) -> ChannelState:
        """Get current channel state."""
        return self._state

    @property
    def is_open(self) -> bool:
        """Check if channel is open and ready for I/O."""
        return self._state == ChannelState.OPEN

    @property
    def statistics(self) -> ChannelStatistics:
        """Get channel statistics."""
        return self._statistics

    @property
    def local_address(self) -> tuple[str, int] | None:
        """Get local address (host, port) if connected."""
        return self._address("sockname")

    @property
    def remote_address(self) -> tuple[str, int] | None:
        """Get remote address (host, port) if connected."""
        return self._address("peername")

    async def close(self) -> None:
        """Close the channel, gracefully if the peer lets it.

        Waits at most `config.close_timeout` for unsent bytes to drain, then
        aborts the transport.
        """
        if self._state == ChannelState.CLOSED:
            return

        self._state = ChannelState.CLOSING

        try:
            if self.writer is not None:
                try:
                    self.writer.close()
                    await asyncio.wait_for(self.writer.wait_closed(), timeout=self.config.close_timeout)
                except TimeoutError:
                    # A peer that stopped reading never drains the send buffer, so a
                    # graceful close would wait forever.
                    self.writer.transport.abort()
                except asyncio.CancelledError:
                    # Cancelled while waiting for the drain: abort so the transport
                    # is not left half-closed, then let the cancellation propagate.
                    self.writer.transport.abort()
                    raise
                except (OSError, ConnectionError):
                    pass  # The peer may already be gone; the channel is closing regardless.
        finally:
            # Runs on every path, including a re-raised cancellation, so the
            # channel always ends CLOSED with the disconnect counted once.
            self._state = ChannelState.CLOSED
            self._statistics.disconnect_count += 1

    async def read(self, max_bytes: int) -> bytes:
        """Read up to max_bytes from the channel.

        Args:
            max_bytes: Maximum number of bytes to read.

        Returns:
            Bytes read. Empty bytes indicates EOF.

        Raises:
            ChannelClosedError: If channel is closed.
            ChannelTimeoutError: If read times out.
            ChannelError: If read fails.
        """
        if self._state != ChannelState.OPEN or self.reader is None:
            raise ChannelClosedError("Channel is not open")

        try:
            timeout = self.config.read_timeout if self.config.read_timeout > 0 else None
            data = await asyncio.wait_for(
                self.reader.read(max_bytes),
                timeout=timeout,
            )
            if data:
                self._statistics.bytes_received += len(data)
                self._statistics.messages_received += 1
            return data
        except TimeoutError as e:
            raise ChannelTimeoutError("Read timed out") from e
        except (OSError, ConnectionError) as e:
            self._statistics.errors += 1
            raise ChannelError(f"Read failed: {e}") from e

    async def write(self, data: bytes) -> int:
        """Write data to the channel.

        Args:
            data: Bytes to write.

        Returns:
            Number of bytes written.

        Raises:
            ChannelClosedError: If channel is closed.
            ChannelTimeoutError: If write times out.
            ChannelError: If write fails.
        """
        if self._state != ChannelState.OPEN or self.writer is None:
            raise ChannelClosedError("Channel is not open")

        try:
            self.writer.write(data)
            timeout = self.config.write_timeout if self.config.write_timeout > 0 else None
            await asyncio.wait_for(
                self.writer.drain(),
                timeout=timeout,
            )
            self._statistics.bytes_sent += len(data)
            self._statistics.messages_sent += 1
            return len(data)
        except TimeoutError as e:
            raise ChannelTimeoutError("Write timed out") from e
        except (OSError, ConnectionError) as e:
            self._statistics.errors += 1
            raise ChannelError(f"Write failed: {e}") from e

    async def read_exactly(self, num_bytes: int) -> bytes:
        """Read exactly num_bytes from the channel.

        Args:
            num_bytes: Exact number of bytes to read.

        Returns:
            Exactly num_bytes of data.

        Raises:
            ChannelClosedError: If channel is closed or EOF.
            ChannelTimeoutError: If read times out.
            ChannelError: If read fails.
        """
        if self._state != ChannelState.OPEN or self.reader is None:
            raise ChannelClosedError("Channel is not open")

        try:
            timeout = self.config.read_timeout if self.config.read_timeout > 0 else None
            data = await asyncio.wait_for(
                self.reader.readexactly(num_bytes),
                timeout=timeout,
            )
            self._statistics.bytes_received += len(data)
            self._statistics.messages_received += 1
            return data
        except asyncio.IncompleteReadError as e:
            raise ChannelClosedError(f"EOF before reading {num_bytes} bytes (got {len(e.partial)})") from e
        except TimeoutError as e:
            raise ChannelTimeoutError("Read timed out") from e
        except (OSError, ConnectionError) as e:
            self._statistics.errors += 1
            raise ChannelError(f"Read failed: {e}") from e

    async def write_all(self, data: bytes) -> None:
        """Write all data to the channel.

        Args:
            data: Bytes to write.

        Raises:
            ChannelClosedError: If channel is closed.
            ChannelTimeoutError: If write times out.
            ChannelError: If write fails.
        """
        written = await self.write(data)
        if written != len(data):
            raise ChannelError(f"Only wrote {written} of {len(data)} bytes")


@dataclass
class TcpClientChannel(StreamIO):
    """TCP client channel for DNP3 communication.

    Connects to a remote TCP server and provides async read/write operations.

    Attributes:
        config: TCP configuration.
    """

    config: TcpConfig = field(default_factory=TcpConfig)

    _state: ChannelState = field(default=ChannelState.CLOSED, init=False)
    _statistics: ChannelStatistics = field(default_factory=ChannelStatistics, init=False)
    reader: asyncio.StreamReader | None = field(default=None, init=False)
    writer: asyncio.StreamWriter | None = field(default=None, init=False)

    async def open(self) -> None:
        """Open the channel by connecting to the remote server.

        Raises:
            ChannelConnectionError: If connection fails.
            ChannelTimeoutError: If connection times out.
        """
        if self._state == ChannelState.OPEN:
            return

        self._state = ChannelState.OPENING

        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(
                    host=self.config.host,
                    port=self.config.port,
                ),
                timeout=self.config.connect_timeout,
            )

            # Configure socket options
            assert self.writer is not None  # Just assigned above
            sock = self.writer.get_extra_info("socket")
            if sock is not None:
                configure_socket(sock, self.config)

            self._state = ChannelState.OPEN
            self._statistics.connect_count += 1

        except TimeoutError as e:
            self._state = ChannelState.CLOSED
            raise ChannelTimeoutError(f"Connection to {self.config.host}:{self.config.port} timed out") from e
        except OSError as e:
            self._state = ChannelState.CLOSED
            raise ChannelConnectionError(f"Failed to connect to {self.config.host}:{self.config.port}: {e}") from e

    async def close(self) -> None:
        """Close the channel as `StreamIO.close` does, then drop the streams."""
        try:
            await super().close()
        finally:
            self.reader = None
            self.writer = None

    async def __aenter__(self) -> "TcpClientChannel":
        """Async context manager entry."""
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Async context manager exit."""
        await self.close()


async def connect(
    host: str,
    port: int,
    config: TcpConfig | None = None,
) -> TcpClientChannel:
    """Connect to a TCP server and return an open channel.

    Args:
        host: Host to connect to.
        port: Port to connect to.
        config: Optional TCP configuration.

    Returns:
        Open TcpClientChannel.

    Raises:
        ChannelConnectionError: If connection fails.
        ChannelTimeoutError: If connection times out.
    """
    config = TcpConfig(host=host, port=port) if config is None else replace(config, host=host, port=port)

    channel = TcpClientChannel(config=config)
    await channel.open()
    return channel
