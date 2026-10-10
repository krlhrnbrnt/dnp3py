"""Tests for the UDP channel, over real loopback sockets."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator

import pytest
from hypothesis import given
from hypothesis import strategies as st

from dnp3.datalink.builder import build_unconfirmed_user_data
from dnp3.transport_io.channel import Channel, ChannelClosedError, ChannelConnectionError, ChannelStatistics
from dnp3.transport_io.udp import UdpChannel, UdpConfig, _UdpProtocol

READ_TIMEOUT = 2.0


def _frame(payload: bytes = b"\xc0\x81\x00\x00") -> bytes:
    return build_unconfirmed_user_data(destination=3, source=1, dir_from_master=False, user_data=payload).to_bytes()


class _Peer(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.received: asyncio.Queue[bytes] = asyncio.Queue()

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.received.put_nowait(data)


class _Link:
    def __init__(self, channel: UdpChannel, transport: asyncio.DatagramTransport, peer: _Peer) -> None:
        self.channel = channel
        self.transport = transport
        self.peer = peer

    def send(self, data: bytes) -> None:
        self.transport.sendto(data, self.channel.local_address)


@pytest.fixture
async def link() -> AsyncIterator[_Link]:
    loop = asyncio.get_running_loop()
    transport, peer = await loop.create_datagram_endpoint(_Peer, local_addr=("127.0.0.1", 0))
    port = transport.get_extra_info("sockname")[1]
    channel = UdpChannel(UdpConfig(remote_host="127.0.0.1", remote_port=port, local_host="127.0.0.1"))
    await channel.open()
    try:
        yield _Link(channel, transport, peer)
    finally:
        await channel.close()
        transport.close()


async def _read(channel: UdpChannel, max_bytes: int = 4096) -> bytes:
    return await asyncio.wait_for(channel.read(max_bytes), READ_TIMEOUT)


async def test_read_returns_frames_from_remote(link: _Link) -> None:
    first, second = _frame(b"\xc0\x81\x00\x00"), _frame(b"\xc1\x81\x00\x00")
    link.send(first + second)

    assert await _read(link.channel) == first + second


async def test_write_reaches_remote(link: _Link) -> None:
    await link.channel.write(_frame())

    assert await asyncio.wait_for(link.peer.received.get(), READ_TIMEOUT) == _frame()
    assert link.channel.statistics.bytes_sent == len(_frame())


async def test_drops_datagram_from_other_source(link: _Link) -> None:
    link.channel._protocol.datagram_received(_frame(), ("127.0.0.2", 9))

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(link.channel.read(4096), 0.1)
    assert link.channel.statistics.errors == 1


async def test_truncated_frame_not_joined_to_next_datagram(link: _Link) -> None:
    whole = _frame()
    link.send(whole[: len(whole) // 2])
    link.send(whole)

    assert await _read(link.channel) == whole
    assert link.channel.statistics.errors == 1


async def test_empty_datagram_is_not_eof(link: _Link) -> None:
    pending = asyncio.ensure_future(_read(link.channel))
    link.send(b"")
    link.send(_frame())

    assert await pending == _frame()


async def test_read_honours_max_bytes(link: _Link) -> None:
    whole = _frame(bytes(200))
    link.send(whole)

    head = await _read(link.channel, 100)
    tail = await _read(link.channel, 4096)

    assert len(head) == 100
    assert head + tail == whole


async def test_cancelled_read_loses_nothing(link: _Link) -> None:
    pending = asyncio.ensure_future(link.channel.read(4096))
    await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    link.send(_frame())

    assert await _read(link.channel) == _frame()


async def test_error_received_is_ignored(link: _Link) -> None:
    link.channel._protocol.error_received(ConnectionResetError())
    link.send(_frame())

    assert await _read(link.channel) == _frame()
    assert link.channel.is_open


async def test_read_after_close_raises(link: _Link) -> None:
    await link.channel.close()

    with pytest.raises(ChannelClosedError):
        await link.channel.read(4096)
    with pytest.raises(ChannelClosedError):
        await link.channel.write(_frame())


async def test_close_wakes_pending_read(link: _Link) -> None:
    pending = asyncio.ensure_future(_read(link.channel))
    await asyncio.sleep(0)

    await link.channel.close()

    with pytest.raises(ChannelClosedError):
        await pending


async def test_close_is_idempotent(link: _Link) -> None:
    await link.channel.close()
    await link.channel.close()

    assert not link.channel.is_open
    assert link.channel.local_address is None
    assert link.channel.statistics.disconnect_count == 1


async def test_open_failure_raises_connection_error() -> None:
    channel = UdpChannel(UdpConfig(remote_host="host.invalid"))

    with pytest.raises(ChannelConnectionError):
        await channel.open()
    assert not channel.is_open


async def test_read_exactly_spans_reads(link: _Link) -> None:
    whole = _frame()
    link.send(whole)

    assert await asyncio.wait_for(link.channel.read_exactly(len(whole)), READ_TIMEOUT) == whole


def _free_port(host: str = "127.0.0.1") -> int:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_DGRAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


async def test_port_unreachable_does_not_deafen_channel() -> None:
    """A write to a closed port must not stop later datagrams from being read.

    Windows reports the ICMP port-unreachable on the socket's next receive.
    """
    port = _free_port()
    channel = UdpChannel(UdpConfig(remote_host="127.0.0.1", remote_port=port, local_host="127.0.0.1"))
    await channel.open()
    loop = asyncio.get_running_loop()
    transport = None
    try:
        await channel.write(_frame())
        await asyncio.sleep(0.2)
        transport, _ = await loop.create_datagram_endpoint(_Peer, local_addr=("127.0.0.1", port))
        transport.sendto(_frame(), channel.local_address)

        assert await _read(channel) == _frame()
    finally:
        await channel.close()
        if transport is not None:
            transport.close()


async def test_hostname_remote_opens() -> None:
    channel = UdpChannel(UdpConfig(remote_host="localhost"))

    await channel.open()
    try:
        assert channel.is_open
    finally:
        await channel.close()


@pytest.mark.skipif(not socket.has_ipv6, reason="no IPv6")
async def test_ipv6_remote_round_trip() -> None:
    loop = asyncio.get_running_loop()
    try:
        transport, peer = await loop.create_datagram_endpoint(_Peer, local_addr=("::1", 0))
    except OSError:
        pytest.skip("no IPv6 loopback")
    port = transport.get_extra_info("sockname")[1]
    channel = UdpChannel(UdpConfig(remote_host="::1", remote_port=port))
    try:
        await channel.open()
        await channel.write(_frame())
        assert await asyncio.wait_for(peer.received.get(), READ_TIMEOUT) == _frame()
    finally:
        await channel.close()
        transport.close()


def test_satisfies_channel_protocol() -> None:
    assert isinstance(UdpChannel(UdpConfig(remote_host="127.0.0.1")), Channel)


def test_exports() -> None:
    from dnp3.transport_io import UdpChannel as Exported
    from dnp3.transport_io import UdpConfig as ExportedConfig

    assert Exported is UdpChannel
    assert ExportedConfig is UdpConfig


REMOTE = ("127.0.0.1", 20000)


@given(st.binary(max_size=600))
def test_arbitrary_datagram_never_raises(data: bytes) -> None:
    protocol = _UdpProtocol(ChannelStatistics(), REMOTE)

    protocol.datagram_received(data, REMOTE)
