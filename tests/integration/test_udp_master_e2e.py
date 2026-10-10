"""End-to-end tests: `MasterUdpRunner` against a live `Outstation` over loopback UDP.

The outstation side is `UdpOutstationHarness`, which runs the shipped
outstation connection handler over a `UdpChannel`. Both sockets bind to
`127.0.0.1`, the master on a probed free port and the outstation on an
ephemeral one.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import pytest

from dnp3.core.enums import LinkFunctionCode
from dnp3.core.flags import AnalogQuality, BinaryQuality
from dnp3.database import AnalogInputConfig, BinaryInputConfig, Database, EventClass
from dnp3.datalink.frame import DataLinkFrame
from dnp3.master import (
    CommandBuilder,
    CommandPointState,
    Master,
    MasterConfig,
    MasterUdpRunner,
    PollingConfig,
    ResponseTimeoutError,
    TimeSyncMethod,
)
from dnp3.outstation import Outstation, OutstationConfig
from dnp3.outstation.handler import CommandHandler, DefaultCommandHandler
from tests.integration.test_tcp_master_runner_e2e import (
    ANALOG_COUNT,
    MIN_FRAGMENT_SIZE,
    RecordingHandler,
    _AcceptPointZero,
    _binary_outputs,
)
from tests.support.udp_outstation import UdpOutstationHarness

MASTER_ADDR = 3
OUTSTATION_ADDR = 1
POLL_TIMEOUT = 5.0

_UNS = 0x10
_CON = 0x20
_FIR_FIN = 0xC0
_FC_CONFIRM = 0x00
_FC_READ = 0x01
_FC_UNSOLICITED_RESPONSE = 0x82


@contextlib.asynccontextmanager
async def _runner_over_udp(
    database: Database,
    *,
    max_fragment_size: int | None = None,
    command_handler: CommandHandler | None = None,
    response_timeout: float = POLL_TIMEOUT,
    **master_options: Any,
) -> AsyncIterator[tuple[MasterUdpRunner, RecordingHandler, UdpOutstationHarness]]:
    extra = {} if max_fragment_size is None else {"max_fragment_size": max_fragment_size}
    outstation = Outstation(
        config=OutstationConfig(address=OUTSTATION_ADDR, master_address=MASTER_ADDR, **extra),
        database=database,
        handler=command_handler or DefaultCommandHandler(),
    )
    harness = UdpOutstationHarness(outstation)
    await harness.start()
    handler = RecordingHandler()
    master = Master(
        config=MasterConfig(address=MASTER_ADDR, outstation_address=OUTSTATION_ADDR, **master_options),
        handler=handler,
    )
    try:
        runner = MasterUdpRunner(master=master, udp=harness.master_udp_config(), response_timeout=response_timeout)
        async with runner:
            yield runner, handler, harness
    finally:
        await harness.stop()


def _app(frame: DataLinkFrame) -> bytes:
    """Application bytes of a single-segment frame, after the transport header."""
    return bytes(frame.user_data[1:])


def _confirms(frames: list[DataLinkFrame]) -> list[bytes]:
    return [_app(f) for f in frames if len(_app(f)) >= 2 and _app(f)[1] == _FC_CONFIRM]


async def _confirms_seen(harness: UdpOutstationHarness) -> list[bytes]:
    """The master's CONFIRMs, once the outstation side has read at least one."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + POLL_TIMEOUT
    while not (confirms := _confirms(harness.frames_from_master)) and loop.time() < deadline:
        await asyncio.sleep(0.01)
    return confirms


def _analog_database() -> Database:
    database = Database()
    database.add_binary_input(0, BinaryInputConfig(event_class=EventClass.NONE))
    database.add_analog_input(0, AnalogInputConfig(event_class=EventClass.NONE))
    database.update_binary_input(0, value=True, quality=BinaryQuality.ONLINE)
    database.update_analog_input(0, value=2401, quality=AnalogQuality.ONLINE)
    return database


@pytest.mark.parametrize("event_class", [EventClass.CLASS_1, EventClass.NONE])
async def test_integrity_poll_values_over_udp(event_class: EventClass) -> None:
    database = Database()
    for index in (0, 1):
        database.add_binary_input(index, BinaryInputConfig(event_class=event_class))
    database.add_analog_input(0, AnalogInputConfig(event_class=event_class))
    database.update_binary_input(0, value=True, quality=BinaryQuality.ONLINE)
    database.update_binary_input(1, value=False, quality=BinaryQuality.ONLINE)
    database.update_analog_input(0, value=-1500, quality=AnalogQuality.ONLINE)

    async with _runner_over_udp(database) as (runner, handler, _):
        infos = await asyncio.wait_for(runner.integrity_poll(), POLL_TIMEOUT)

    assert handler.binary_inputs == {0: True, 1: False}
    assert handler.analog_inputs == {0: -1500.0}
    assert infos[-1].fin is True


async def test_multi_fragment_burst_over_udp() -> None:
    database = Database()
    expected = {}
    for index in range(ANALOG_COUNT):
        database.add_analog_input(index, AnalogInputConfig(event_class=EventClass.NONE))
        database.update_analog_input(index, value=index * 10, quality=AnalogQuality.ONLINE)
        expected[index] = float(index * 10)

    async with _runner_over_udp(database, max_fragment_size=MIN_FRAGMENT_SIZE) as (runner, handler, harness):
        infos = await asyncio.wait_for(runner.integrity_poll(), POLL_TIMEOUT)
        confirmed = [app[0] & 0x0F for app in _confirms(harness.frames_from_master)]

    assert len(infos) > 1
    assert handler.analog_inputs == expected
    assert confirmed == [info.sequence for info in infos[:-1]]


async def test_no_link_reset_by_default() -> None:
    async with _runner_over_udp(_analog_database()) as (runner, _, harness):
        await asyncio.wait_for(runner.integrity_poll(), POLL_TIMEOUT)
        first = harness.frames_from_master[0]

    assert first.header.control.function_code == LinkFunctionCode.PRI_UNCONFIRMED_USER_DATA


async def test_lost_response_times_out_then_next_poll_succeeds() -> None:
    async with _runner_over_udp(_analog_database(), response_timeout=0.3) as (runner, handler, harness):
        harness.drop_next_response()
        with pytest.raises(ResponseTimeoutError):
            await runner.integrity_poll()
        await asyncio.wait_for(runner.integrity_poll(), POLL_TIMEOUT)

    assert handler.analog_inputs == {0: 2401.0}


async def test_unsolicited_over_udp() -> None:
    seq = 5
    # g2v1 binary input event, 1-byte count and index: index 0, ONLINE | STATE.
    fragment = bytes([_FIR_FIN | _CON | _UNS | seq, _FC_UNSOLICITED_RESPONSE, 0x00, 0x00])
    fragment += bytes([0x02, 0x01, 0x17, 0x01, 0x00, 0x81])

    async with _runner_over_udp(Database()) as (runner, handler, harness):
        await harness.send_raw(fragment)
        info = await runner.listen_unsolicited(timeout=POLL_TIMEOUT)
        confirms = await _confirms_seen(harness)

    assert info is not None
    assert handler.binary_inputs == {0: True}
    assert [(app[0] & _UNS, app[0] & 0x0F) for app in confirms] == [(_UNS, seq)]


async def test_run_polls_over_udp() -> None:
    stop = asyncio.Event()
    async with _runner_over_udp(_analog_database(), polling=PollingConfig(class_1_poll_interval=0.1)) as (
        runner,
        _,
        harness,
    ):
        polls = asyncio.create_task(runner.run_polls(stop=stop))
        await asyncio.sleep(0.5)
        stop.set()
        await asyncio.wait_for(polls, POLL_TIMEOUT)
        reads = [_app(f) for f in harness.frames_from_master if _app(f)[1:2] == bytes([_FC_READ])]

    class_1_reads = [app for app in reads if bytes([0x3C, 0x02]) in app]
    assert len(class_1_reads) >= 2


async def test_time_sync_non_lan_over_udp() -> None:
    async with _runner_over_udp(Database()) as (runner, _, _):
        await asyncio.wait_for(runner.time_sync(TimeSyncMethod.NON_LAN), POLL_TIMEOUT)


async def test_direct_operate_over_udp() -> None:
    async with _runner_over_udp(_binary_outputs(), command_handler=_AcceptPointZero()) as (runner, _, _):
        result = await runner.direct_operate(CommandBuilder().latch_on(0).build_direct_operate())

    assert [p.state for p in result.points] == [CommandPointState.SUCCESS]


async def test_select_and_operate_over_udp() -> None:
    command_handler = _AcceptPointZero()
    async with _runner_over_udp(_binary_outputs(), command_handler=command_handler) as (runner, _, _):
        result = await runner.select_and_operate(CommandBuilder().latch_on(0).build_select())

    assert result.is_success
    assert command_handler.operated == [0]


async def test_context_manager_closes_socket() -> None:
    async with _runner_over_udp(Database()) as (runner, _, _):
        assert runner.local_address is not None
        channel = runner.channel
        assert channel is not None

    assert not channel.is_open
    assert not runner.is_open
