"""Unit tests for `SyncMaster`, against an autospecced `MasterRunner`.

`SyncMaster` only runs runner coroutines to completion, so a mock runner whose
coroutines record their arguments is enough; real I/O is covered end to end in
`tests/integration/test_sync_master_e2e.py`.
"""

from __future__ import annotations

import asyncio
import signal
import warnings
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, call, create_autospec

import pytest

from dnp3.master import (
    DefaultSOEHandler,
    LinkError,
    Master,
    MasterConfig,
    MasterRunner,
    ResponseTimeoutError,
    SyncMaster,
    TimeSyncMethod,
)
from dnp3.master.runner import LinkResetPolicy
from dnp3.transport_io.channel import Channel, ChannelError
from dnp3.transport_io.simulator import SimulatorChannel

# Each wrapped method, with arguments it must hand the runner unchanged.
CALLS: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = [
    ("open", (), {}),
    ("startup", (), {}),
    ("integrity_poll", (), {}),
    ("class_poll", (), {"class_1": False, "class_2": True, "class_3": False}),
    ("request", (object(),), {}),
    ("direct_operate", (object(),), {}),
    ("select_and_operate", (object(),), {}),
    ("time_sync", (TimeSyncMethod.NON_LAN,), {}),
    ("write_octet_string", (3, b"ab"), {}),
    ("clear_restart", (), {}),
    ("enable_unsolicited", (), {"class_1": True, "class_2": False, "class_3": True}),
    ("disable_unsolicited", (), {"class_1": False, "class_2": False, "class_3": True}),
    ("listen_unsolicited", (), {"timeout": 0.5}),
]


@pytest.fixture
def runner() -> Any:
    return create_autospec(MasterRunner, instance=True)


@pytest.fixture
def master(runner: Any) -> Any:
    sync = SyncMaster(runner)
    yield sync
    sync.close()


def test_exported() -> None:
    import dnp3.master

    assert "SyncMaster" in dnp3.master.__all__


@pytest.mark.parametrize(("name", "args", "kwargs"), CALLS, ids=[c[0] for c in CALLS])
def test_passes_arguments_and_returns_result(
    runner: Any, master: SyncMaster, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> None:
    result = object()
    coroutine: AsyncMock = getattr(runner, name)
    coroutine.return_value = result

    assert getattr(master, name)(*args, **kwargs) is result
    coroutine.assert_awaited_once_with(*args, **kwargs)


def test_runner_exception_propagates_unchanged(runner: Any, master: SyncMaster) -> None:
    error = ResponseTimeoutError("no response")
    runner.integrity_poll.side_effect = error

    with pytest.raises(ResponseTimeoutError) as raised:
        master.integrity_poll()
    assert raised.value is error


def test_close_closes_runner(runner: Any) -> None:
    SyncMaster(runner).close()

    runner.close.assert_awaited_once_with()


def test_background_loop_api_not_wrapped() -> None:
    for name in ("run", "run_polls", "poll", "send"):
        assert not hasattr(SyncMaster, name), name


def test_with_opens_and_closes(runner: Any) -> None:
    with SyncMaster(runner) as sync:
        assert runner.mock_calls == [call.close(), call.open()]
        assert sync.runner is runner

    assert runner.mock_calls == [call.close(), call.open(), call.close()]


def test_with_closes_when_body_raises(runner: Any) -> None:
    with pytest.raises(ValueError), SyncMaster(runner):
        raise ValueError("body failed")

    assert runner.mock_calls[-1] == call.close()


def test_calls_after_close_raise(runner: Any) -> None:
    sync = SyncMaster(runner)
    sync.close()

    with pytest.raises(RuntimeError, match="closed"):
        sync.integrity_poll()
    with pytest.raises(RuntimeError, match="closed"):
        sync.open()
    runner.integrity_poll.assert_not_called()


def test_second_close_is_a_no_op(runner: Any) -> None:
    sync = SyncMaster(runner)
    sync.close()
    sync.close()

    runner.close.assert_awaited_once_with()


def test_close_without_open(runner: Any) -> None:
    SyncMaster(runner).close()

    runner.open.assert_not_called()
    runner.close.assert_awaited_once_with()


def test_close_is_final_even_when_runner_close_raises(runner: Any) -> None:
    runner.close.side_effect = LinkError("close failed")
    sync = SyncMaster(runner)

    with pytest.raises(LinkError):
        sync.close()
    with pytest.raises(RuntimeError, match="closed"):
        sync.integrity_poll()


class _ResetChannel(SimulatorChannel):
    """A channel whose reads fail the way an ECONNRESET does."""

    async def read(self, max_bytes: int) -> bytes:
        raise ChannelError("Connection reset by peer")


@dataclass
class _ResetRunner(MasterRunner):
    def _create_channel(self) -> Channel:
        return _ResetChannel()


def test_reopen_after_connection_reset() -> None:
    master = Master(config=MasterConfig(address=3, outstation_address=1), handler=DefaultSOEHandler())
    runner = _ResetRunner(master=master, link_reset=LinkResetPolicy.NEVER, response_timeout=1.0)

    with SyncMaster(runner) as sync:
        with pytest.raises(LinkError):
            sync.integrity_poll()

        sync.open()

        assert runner.is_open


async def test_inside_running_loop_raises_without_creating_coroutine(runner: Any) -> None:
    sync = SyncMaster(runner)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(RuntimeError, match="await"):
            sync.integrity_poll()
        with pytest.raises(RuntimeError, match="await"):
            sync.close()
    runner.integrity_poll.assert_not_called()
    runner.close.assert_not_called()


def test_ctrl_c_interrupts_call_and_close_still_works(runner: Any) -> None:
    async def interrupted() -> None:
        signal.raise_signal(signal.SIGINT)
        await asyncio.sleep(10)

    runner.integrity_poll.side_effect = interrupted
    sync = SyncMaster(runner)

    with pytest.raises(KeyboardInterrupt):
        sync.integrity_poll()
    sync.close()

    runner.close.assert_awaited_once_with()
