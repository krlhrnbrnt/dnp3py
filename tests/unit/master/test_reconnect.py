"""Unit tests for `MasterRunner.run()`, the reconnecting drive loop.

Each connection attempt takes the next channel from a script: a channel that
refuses to open stands for an unreachable outstation, and a simulator pair for
one that answers. The delay between attempts is read back from the log line
`run()` writes for each one.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

import pytest

from dnp3.core.enums import FunctionCode, LinkFunctionCode
from dnp3.core.flags import IIN
from dnp3.master.config import MasterConfig
from dnp3.master.master import Master
from dnp3.master.polling import IntegrityPollTask
from dnp3.master.runner import LinkResetPolicy, MasterRunner, RequestRejectedError
from dnp3.transport_io.channel import Channel, ChannelConnectionError
from dnp3.transport_io.simulator import SimulatorChannel, create_channel_pair
from tests.unit.master.test_tcp_runner import (
    MASTER_ADDR,
    OUTSTATION_ADDR,
    FakeOutstation,
    RecordingHandler,
    answer_requests,
    null_reply,
)

MIN_DELAY = 0.01
MAX_DELAY = 0.04


class RefusingChannel(SimulatorChannel):
    """A channel whose open() fails the way a refused TCP connect does."""

    async def open(self) -> None:
        raise ChannelConnectionError("Connection refused")


@dataclass
class ScriptedRunner(MasterRunner):
    """A runner that takes each connection's channel from a script."""

    script: list[Channel] = field(default_factory=list)

    def _create_channel(self) -> Channel:
        return self.script.pop(0)


def make_runner(*channels: Channel, link_reset: LinkResetPolicy = LinkResetPolicy.NEVER) -> ScriptedRunner:
    """A runner whose startup is an integrity poll and ENABLE_UNSOLICITED, with no poll due for an hour."""
    master = Master(
        config=MasterConfig(address=MASTER_ADDR, outstation_address=OUTSTATION_ADDR),
        handler=RecordingHandler(),
    )
    master.scheduler.clear()
    task = IntegrityPollTask(interval=3600.0)
    task.mark_executed()
    master.scheduler.add_task(task)
    return ScriptedRunner(
        master=master,
        script=list(channels),
        link_reset=link_reset,
        response_timeout=1.0,
        reconnect_min_delay=MIN_DELAY,
        reconnect_max_delay=MAX_DELAY,
    )


def outstation() -> tuple[SimulatorChannel, FakeOutstation]:
    """The master's end of a fresh connection, and the fake outstation on the other end."""
    master_end, outstation_end = create_channel_pair()
    return master_end, FakeOutstation(outstation_end)


async def open_peer(peer: FakeOutstation) -> None:
    await peer.channel.open()  # type: ignore[attr-defined]


def logged_delays(caplog: pytest.LogCaptureFixture) -> list[float]:
    return [float(m) for m in re.findall(r"reconnecting in ([\d.]+) s", caplog.text)]


async def stop_after_startup(peer: FakeOutstation, stop: asyncio.Event) -> list[bytes]:
    """Answer the startup sequence, then end `run()`."""
    requests = await answer_requests(peer, 2)
    stop.set()
    return requests


@pytest.fixture(autouse=True)
def _log_warnings(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="dnp3.master.runner")


class TestRun:
    async def test_retries_a_refused_connection_with_growing_delay(self, caplog: pytest.LogCaptureFixture) -> None:
        channel, peer = outstation()
        await open_peer(peer)
        runner = make_runner(RefusingChannel(), RefusingChannel(), channel)
        stop = asyncio.Event()

        responder = asyncio.create_task(stop_after_startup(peer, stop))
        await asyncio.wait_for(runner.run(stop=stop), timeout=2.0)
        requests = await responder

        assert logged_delays(caplog) == [MIN_DELAY, 2 * MIN_DELAY]
        assert [FunctionCode(r[1]) for r in requests] == [FunctionCode.READ, FunctionCode.ENABLE_UNSOLICITED]
        assert runner.is_open is False

    async def test_delay_is_capped(self, caplog: pytest.LogCaptureFixture) -> None:
        channel, peer = outstation()
        await open_peer(peer)
        runner = make_runner(*(RefusingChannel() for _ in range(4)), channel)
        stop = asyncio.Event()

        responder = asyncio.create_task(stop_after_startup(peer, stop))
        await asyncio.wait_for(runner.run(stop=stop), timeout=2.0)
        await responder

        assert logged_delays(caplog) == [MIN_DELAY, 2 * MIN_DELAY, MAX_DELAY, MAX_DELAY]

    async def test_reconnects_after_the_peer_closes(self, caplog: pytest.LogCaptureFixture) -> None:
        """A new connection resets the link and runs startup again; the delay starts over."""
        first, first_peer = outstation()
        second, second_peer = outstation()
        await open_peer(first_peer)
        await open_peer(second_peer)
        runner = make_runner(RefusingChannel(), first, second, link_reset=LinkResetPolicy.ON_OPEN)
        stop = asyncio.Event()

        async def first_connection() -> None:
            await answer_requests(first_peer, 2)
            await first_peer.channel.close()  # type: ignore[attr-defined]

        async def second_connection() -> list[bytes]:
            [reset] = await second_peer.read_frames(1)
            assert reset.header.control.function_code == LinkFunctionCode.PRI_RESET_LINK_STATE.value
            return await stop_after_startup(second_peer, stop)

        dropped = asyncio.create_task(first_connection())
        responder = asyncio.create_task(second_connection())
        await asyncio.wait_for(runner.run(stop=stop), timeout=2.0)
        await dropped
        requests = await responder

        assert logged_delays(caplog) == [MIN_DELAY, MIN_DELAY]
        assert [FunctionCode(r[1]) for r in requests] == [FunctionCode.READ, FunctionCode.ENABLE_UNSOLICITED]

    async def test_stop_ends_the_wait_between_attempts(self) -> None:
        runner = make_runner(RefusingChannel())
        runner.reconnect_min_delay = 60.0
        runner.reconnect_max_delay = 60.0
        stop = asyncio.Event()

        running = asyncio.create_task(runner.run(stop=stop))
        await asyncio.sleep(0.05)
        stop.set()

        await asyncio.wait_for(running, timeout=1.0)
        assert runner.channel is None

    async def test_rejected_startup_is_raised(self) -> None:
        """A rejected request is a configuration mismatch that reconnecting would not fix."""
        channel, peer = outstation()
        await open_peer(peer)
        runner = make_runner(channel)

        responder = answer_requests(peer, 2, {FunctionCode.ENABLE_UNSOLICITED: null_reply(IIN.NO_FUNC_CODE_SUPPORT)})
        with pytest.raises(RequestRejectedError):
            await asyncio.wait_for(runner.run(), timeout=2.0)
        await responder

        assert runner.is_open is False

    async def test_cancellation_closes_the_runner(self) -> None:
        channel, peer = outstation()
        await open_peer(peer)
        runner = make_runner(channel)

        running = asyncio.create_task(runner.run())
        await answer_requests(peer, 2)
        running.cancel()

        with pytest.raises(asyncio.CancelledError):
            await running
        assert runner.channel is None
