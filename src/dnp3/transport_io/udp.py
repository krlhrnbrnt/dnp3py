"""UDP channel for DNP3 over IP.

`UdpChannel` presents a UDP socket that talks to one remote endpoint as a
`Channel` byte stream. Each datagram must hold whole link frames: it is parsed on its own, and only the
bytes of complete, CRC-valid frames reach `read()`, so a truncated frame cannot
join the next datagram in the reader's stream.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass
from typing import Any

from dnp3.datalink.parser import FrameParser
from dnp3.transport_io.channel import (
    ChannelClosedError,
    ChannelConnectionError,
    ChannelState,
    ChannelStatistics,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UdpConfig:
    """Configuration for a UDP channel.

    Attributes:
        remote_host: Host datagrams are sent to and accepted from.
        remote_port: Remote UDP port.
        local_host: Local address to bind. None binds every address of the
            remote's family.
        local_port: Local port to bind; 0 picks an ephemeral port. Fix it when
            the peer sends unsolicited responses to a configured port.
    """

    remote_host: str
    remote_port: int = 20000
    local_host: str | None = None
    local_port: int = 0


class _UdpProtocol(asyncio.DatagramProtocol):
    """Buffers the frame bytes of datagrams from one peer."""

    def __init__(self, statistics: ChannelStatistics, peer: tuple[str, int] | None) -> None:
        self._statistics = statistics
        self._peer = peer
        self.buffer = bytearray()
        self.ready = asyncio.Event()
        self.lost = False

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        # The socket is unconnected, so this check is the only filter. DNP3 is
        # unauthenticated: a datagram from anywhere else could inject values.
        if self._peer is None or (addr[0], addr[1]) != self._peer:
            self._statistics.errors += 1
            logger.debug("Dropping datagram from %s, expected %s", addr, self._peer)
            return

        frames = b"".join(frame.to_bytes() for frame in FrameParser().feed(data))
        if len(frames) != len(data):
            self._statistics.errors += 1
        self._statistics.bytes_received += len(data)
        self._statistics.messages_received += 1
        if frames:
            self.buffer.extend(frames)
            self.ready.set()

    def error_received(self, exc: Exception) -> None:
        # Windows reports an ICMP port-unreachable as ConnectionResetError on a
        # UDP socket. The outstation may only be restarting, so the channel
        # stays open and the runner's response timeout handles the loss.
        logger.debug("UDP error ignored: %s", exc)

    def connection_lost(self, exc: Exception | None) -> None:
        self.lost = True
        self.ready.set()


class UdpChannel:
    """A `Channel` over a UDP socket connected to one remote endpoint.

    `read()` waits for frame bytes and never returns b"" while open: an empty
    datagram is not end of stream.
    """

    def __init__(self, config: UdpConfig) -> None:
        self.config = config
        self._statistics = ChannelStatistics()
        self._transport: asyncio.DatagramTransport | None = None
        # Full sockaddr: Windows rejects a (host, port) destination on an
        # IPv6 socket.
        self._remote: tuple[Any, ...] | None = None
        self._protocol = _UdpProtocol(self._statistics, None)

    @property
    def state(self) -> ChannelState:
        """Get current channel state."""
        return ChannelState.OPEN if self.is_open else ChannelState.CLOSED

    @property
    def is_open(self) -> bool:
        """Check if channel is open and ready for I/O."""
        return self._transport is not None and not self._protocol.lost

    @property
    def statistics(self) -> ChannelStatistics:
        """Get channel statistics."""
        return self._statistics

    @property
    def local_address(self) -> tuple[str, int] | None:
        """Get the bound local address (host, port) if open."""
        if not self.is_open or self._transport is None:
            return None
        name = self._transport.get_extra_info("sockname")
        return (name[0], name[1])

    async def open(self) -> None:
        """Resolve the remote endpoint and bind the local one.

        The socket is left unconnected. On Windows a connected UDP socket stops
        receiving for good after one ICMP port-unreachable, which an outstation
        that is still starting up produces.

        Raises:
            ChannelConnectionError: The address cannot be resolved or bound.
        """
        if self.is_open:
            return
        loop = asyncio.get_running_loop()
        config = self.config
        try:
            infos = await loop.getaddrinfo(
                config.remote_host, config.remote_port, type=socket.SOCK_DGRAM, proto=socket.IPPROTO_UDP
            )
            family, _, _, _, sockaddr = infos[0]
            local_host = config.local_host
            if local_host is None:
                local_host = "::" if family == socket.AF_INET6 else "0.0.0.0"  # nosec B104 - any-address default
            protocol = _UdpProtocol(self._statistics, (str(sockaddr[0]), int(sockaddr[1])))
            transport, _ = await loop.create_datagram_endpoint(
                lambda: protocol,
                local_addr=(local_host, config.local_port),
                family=family,
                proto=socket.IPPROTO_UDP,
            )
        except (OSError, ValueError) as exc:
            self._statistics.errors += 1
            msg = f"Cannot open UDP channel to {self.config.remote_host}:{self.config.remote_port}: {exc}"
            raise ChannelConnectionError(msg) from exc
        self._transport = transport
        self._remote = sockaddr
        self._protocol = protocol
        self._statistics.connect_count += 1

    async def close(self) -> None:
        """Close the socket and wake a pending read."""
        if self._transport is None:
            return
        transport, self._transport = self._transport, None
        transport.close()
        self._protocol.ready.set()
        self._statistics.disconnect_count += 1

    async def read(self, max_bytes: int) -> bytes:
        """Read up to max_bytes of buffered frame bytes, waiting for some.

        Cancelling a pending read loses no bytes: nothing leaves the buffer
        until the wait is over.

        Raises:
            ChannelClosedError: The channel is closed, or closes while waiting.
        """
        protocol = self._protocol
        while True:
            if not self.is_open:
                msg = "Channel is closed"
                raise ChannelClosedError(msg)
            if protocol.buffer:
                break
            protocol.ready.clear()
            await protocol.ready.wait()
        data = bytes(protocol.buffer[:max_bytes])
        del protocol.buffer[:max_bytes]
        return data

    async def write(self, data: bytes) -> int:
        """Send data as one datagram.

        Raises:
            ChannelClosedError: The channel is closed.
        """
        if not self.is_open or self._transport is None:
            msg = "Channel is closed"
            raise ChannelClosedError(msg)
        self._transport.sendto(data, self._remote)
        self._statistics.bytes_sent += len(data)
        self._statistics.messages_sent += 1
        return len(data)

    async def read_exactly(self, num_bytes: int) -> bytes:
        """Read exactly num_bytes, across as many reads as it takes."""
        result = bytearray()
        while len(result) < num_bytes:
            result.extend(await self.read(num_bytes - len(result)))
        return bytes(result)

    async def write_all(self, data: bytes) -> None:
        """Send data as one datagram."""
        await self.write(data)
