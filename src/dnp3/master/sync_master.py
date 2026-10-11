"""Blocking wrapper around `MasterRunner` for synchronous code.

    runner = MasterTcpRunner(master=Master(handler=handler), host="10.0.0.5")
    with SyncMaster(runner) as master:
        master.integrity_poll()

Each method runs the `MasterRunner` coroutine of the same name to completion,
with the same arguments, result and exceptions. `run()`, `run_polls()` and
`poll()` are not wrapped: they loop until stopped, which a blocking caller
cannot do. Nor is `send()`: its deadline is an event-loop time, which sync code
never sees.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, ParamSpec, Self, TypeVar

from dnp3.application.fragment import RequestFragment
from dnp3.master.commands import DirectOperateTask, SelectTask
from dnp3.master.config import TimeSyncMethod
from dnp3.master.handler import CommandTaskResult, ResponseInfo
from dnp3.master.runner import MasterRunner

__all__ = ["SyncMaster"]

_P = ParamSpec("_P")
_T = TypeVar("_T")


class SyncMaster:
    """Runs a `MasterRunner` with blocking calls on one private event loop.

    Values reach the runner's SOE handler on the calling thread, during the
    call. One thread drives a `SyncMaster`; it is not thread-safe.

    Attributes:
        runner: The runner driven. Use it for `is_open`, `master` and the
            handler. It must not have been used on another event loop.
    """

    def __init__(self, runner: MasterRunner) -> None:
        self.runner = runner
        self._loop = asyncio.Runner()
        self._closed = False

    def _check_usable(self) -> None:
        if self._closed:
            raise RuntimeError("SyncMaster is closed")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        raise RuntimeError("SyncMaster cannot run inside a running event loop; await the MasterRunner instead")

    def _run(self, fn: Callable[_P, Coroutine[Any, Any, _T]], /, *args: _P.args, **kwargs: _P.kwargs) -> _T:
        # Checked before the coroutine exists, so a refused call leaves no
        # never-awaited coroutine behind.
        self._check_usable()
        return self._loop.run(fn(*args, **kwargs))

    def open(self) -> None:
        """Connect, closing the runner first so `open()` also reconnects.

        Most `LinkError`s (a reset connection, a failed write) leave the runner
        open over a dead channel, and only `close()` clears that, which here is
        final. A blocking caller has no request in flight between calls, so
        closing first is always safe, as in `MasterRunner.run()`.
        """
        return self._run(self._reopen)

    async def _reopen(self) -> None:
        await self.runner.close()
        return await self.runner.open()

    def close(self) -> None:
        """Close the runner, its channel and the event loop.

        Final: the runner's asyncio primitives are bound to this loop, so no
        other loop can drive it, and every later call raises `RuntimeError`.
        A second `close()` does nothing.

        Unlike `MasterRunner.close()`, this closes an injected channel too: its
        transport was opened on this private loop, and once the loop is closed
        nothing can close it.
        """
        if self._closed:
            return
        self._check_usable()
        try:
            self._loop.run(self._close_all())
        finally:
            self._closed = True
            self._loop.close()

    async def _close_all(self) -> None:
        try:
            await self.runner.close()
        finally:
            if self.runner.channel is not None:
                await self.runner.channel.close()

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def startup(self) -> None:
        return self._run(self.runner.startup)

    def integrity_poll(self) -> list[ResponseInfo]:
        return self._run(self.runner.integrity_poll)

    def class_poll(self, *, class_1: bool = True, class_2: bool = True, class_3: bool = True) -> list[ResponseInfo]:
        return self._run(self.runner.class_poll, class_1=class_1, class_2=class_2, class_3=class_3)

    def request(self, request: RequestFragment) -> list[ResponseInfo]:
        return self._run(self.runner.request, request)

    def direct_operate(self, task: DirectOperateTask) -> CommandTaskResult:
        return self._run(self.runner.direct_operate, task)

    def select_and_operate(self, task: SelectTask) -> CommandTaskResult:
        return self._run(self.runner.select_and_operate, task)

    def time_sync(self, method: TimeSyncMethod | None = None) -> None:
        return self._run(self.runner.time_sync, method)

    def write_octet_string(self, index: int, value: bytes) -> None:
        return self._run(self.runner.write_octet_string, index, value)

    def clear_restart(self) -> None:
        return self._run(self.runner.clear_restart)

    def enable_unsolicited(
        self, *, class_1: bool = True, class_2: bool = True, class_3: bool = True
    ) -> list[ResponseInfo]:
        return self._run(self.runner.enable_unsolicited, class_1=class_1, class_2=class_2, class_3=class_3)

    def disable_unsolicited(
        self, *, class_1: bool = True, class_2: bool = True, class_3: bool = True
    ) -> list[ResponseInfo]:
        return self._run(self.runner.disable_unsolicited, class_1=class_1, class_2=class_2, class_3=class_3)

    def listen_unsolicited(self, *, timeout: float | None = None) -> ResponseInfo | None:
        return self._run(self.runner.listen_unsolicited, timeout=timeout)
