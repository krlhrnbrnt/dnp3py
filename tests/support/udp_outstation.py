"""An `Outstation` served over loopback UDP, for master tests.

The harness drives `OutstationTcpRunner._handle_connection` with an
outstation-side `UdpChannel`, so the outstation's link and transport handling is
the shipped code, not a test copy.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket

from dnp3.datalink.builder import build_unconfirmed_user_data
from dnp3.datalink.frame import DataLinkFrame
from dnp3.datalink.parser import FrameParser
from dnp3.outstation import Outstation, OutstationTcpRunner
from dnp3.transport.segmenter import Segmenter
from dnp3.transport_io.udp import UdpChannel, UdpConfig

LOOPBACK = "127.0.0.1"


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((LOOPBACK, 0))
        return int(sock.getsockname()[1])


class _RecordingChannel:
    """Wraps the outstation's channel to record and drop frames."""

    def __init__(self, channel: UdpChannel) -> None:
        self._channel = channel
        self._parser = FrameParser()
        self.frames_from_master: list[DataLinkFrame] = []
        self.drop_writes = 0

    async def read(self, max_bytes: int) -> bytes:
        data = await self._channel.read(max_bytes)
        self.frames_from_master.extend(self._parser.feed(data))
        return data

    async def write_all(self, data: bytes) -> None:
        if self.drop_writes:
            self.drop_writes -= 1
            return
        await self._channel.write_all(data)

    async def close(self) -> None:
        await self._channel.close()


class UdpOutstationHarness:
    """Serves one `Outstation` to one master over loopback UDP."""

    def __init__(self, outstation: Outstation) -> None:
        self.outstation = outstation
        self._master_port = 0
        self._channel: UdpChannel | None = None
        self._recorder: _RecordingChannel | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._master_port = _free_udp_port()
        self._channel = UdpChannel(UdpConfig(remote_host=LOOPBACK, remote_port=self._master_port, local_host=LOOPBACK))
        await self._channel.open()
        self._recorder = _RecordingChannel(self._channel)
        runner = OutstationTcpRunner(outstation=self.outstation)
        self._task = asyncio.create_task(runner._handle_connection(self._recorder))

    def master_udp_config(self) -> UdpConfig:
        """The config a master needs to reach this outstation."""
        assert self._channel is not None and self._channel.local_address is not None
        return UdpConfig(
            remote_host=LOOPBACK,
            remote_port=self._channel.local_address[1],
            local_host=LOOPBACK,
            local_port=self._master_port,
        )

    @property
    def frames_from_master(self) -> list[DataLinkFrame]:
        assert self._recorder is not None
        return self._recorder.frames_from_master

    def drop_next_response(self) -> None:
        """Swallow the next frame the outstation writes."""
        assert self._recorder is not None
        self._recorder.drop_writes += 1

    async def send_raw(self, app_bytes: bytes) -> None:
        """Frame and send one application fragment, such as an unsolicited response."""
        assert self._channel is not None
        config = self.outstation.config
        for segment in Segmenter().segment(app_bytes):
            frame = build_unconfirmed_user_data(
                destination=config.master_address,
                source=config.address,
                dir_from_master=False,
                user_data=segment.to_bytes(),
            )
            await self._channel.write_all(frame.to_bytes())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._channel is not None:
            await self._channel.close()
