"""End-to-end: `SyncMaster` against a live `Outstation` over TCP.

The outstation serves on its own event loop in a background thread, so the
test thread is free of a running loop, as a synchronous caller would be.
Sockets bind to `127.0.0.1` port 0; the master is told the port the OS chose.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Iterator

import pytest

from dnp3.core.enums import CommandStatus, ControlCode
from dnp3.core.flags import AnalogQuality, BinaryQuality
from dnp3.database import AnalogInputConfig, BinaryInputConfig, BinaryOutputConfig, Database, EventClass
from dnp3.master import (
    CommandBuilder,
    CommandPointState,
    DefaultSOEHandler,
    Master,
    MasterConfig,
    MasterTcpRunner,
    SyncMaster,
)
from dnp3.outstation import Outstation, OutstationConfig, OutstationTcpRunner
from dnp3.outstation.handler import CommandResult, DefaultCommandHandler

MASTER_ADDR = 3
OUTSTATION_ADDR = 1
TIMEOUT = 5.0


class _AcceptAll(DefaultCommandHandler):
    def direct_operate_binary_output(
        self, index: int, code: ControlCode, count: int, on_time: int, off_time: int
    ) -> CommandResult:
        return CommandResult.success()


@contextlib.contextmanager
def _outstation_thread(database: Database) -> Iterator[int]:
    """Serve `database` over TCP from a background thread, yielding the port."""
    outstation = Outstation(
        config=OutstationConfig(address=OUTSTATION_ADDR, master_address=MASTER_ADDR),
        database=database,
        handler=_AcceptAll(),
    )
    os_runner = OutstationTcpRunner(outstation=outstation, host="127.0.0.1", port=0)
    ready = threading.Event()
    done = threading.Event()

    async def serve() -> None:
        task = asyncio.create_task(os_runner.run())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + TIMEOUT
        while not (os_runner.is_running and os_runner.local_address is not None) and loop.time() < deadline:
            await asyncio.sleep(0.02)
        ready.set()
        await asyncio.to_thread(done.wait)
        await os_runner.stop()
        # Cancel before awaiting: the accept loop parks in `accept()`.
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.wait_for(task, timeout=TIMEOUT)

    thread = threading.Thread(target=asyncio.run, args=(serve(),), daemon=True)
    thread.start()
    try:
        if not ready.wait(TIMEOUT) or os_runner.local_address is None:
            pytest.fail("outstation runner did not bind in time")
        yield os_runner.local_address[1]
    finally:
        done.set()
        thread.join(TIMEOUT)


def _sync_master(port: int, handler: DefaultSOEHandler) -> SyncMaster:
    master = Master(config=MasterConfig(address=MASTER_ADDR, outstation_address=OUTSTATION_ADDR), handler=handler)
    return SyncMaster(MasterTcpRunner(master=master, host="127.0.0.1", port=port, response_timeout=TIMEOUT))


def test_integrity_poll_delivers_values() -> None:
    database = Database()
    database.add_binary_input(0, BinaryInputConfig(event_class=EventClass.NONE))
    database.add_analog_input(0, AnalogInputConfig(event_class=EventClass.NONE))
    database.update_binary_input(0, value=True, quality=BinaryQuality.ONLINE)
    database.update_analog_input(0, value=42, quality=AnalogQuality.ONLINE)
    handler = DefaultSOEHandler()

    with _outstation_thread(database) as port, _sync_master(port, handler) as master:
        infos = master.integrity_poll()

    assert infos[-1].fin is True
    assert handler.binary_inputs[0].value is True
    assert handler.analog_inputs[0].value == 42.0


def test_direct_operate_succeeds() -> None:
    database = Database()
    database.add_binary_output(0, BinaryOutputConfig())

    with _outstation_thread(database) as port, _sync_master(port, DefaultSOEHandler()) as master:
        result = master.direct_operate(CommandBuilder().latch_on(0).build_direct_operate())

    assert [(p.index, p.state, p.status) for p in result.points] == [
        (0, CommandPointState.SUCCESS, CommandStatus.SUCCESS),
    ]
