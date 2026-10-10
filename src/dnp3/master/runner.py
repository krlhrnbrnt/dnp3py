"""Transport-independent DNP3 master runner.

`Master` builds requests and parses responses but owns no I/O; a `Channel`
moves bytes but knows no DNP3. `MasterRunner` is the layer between them: data
link framing, transport segmentation and reassembly, link reset, the
multi-fragment application CONFIRM handshake, scheduled polls, IIN reactions and
controls. `MasterTcpRunner` and `MasterUdpRunner` add only the channel they
build when none is supplied.

`PollScheduler` models when a poll is due and is transport-independent.
`poll(task)` runs one scheduled task. `run_polls()` drives the master's scheduler
with it and listens for unsolicited responses between polls; a caller that needs
different scheduling writes its own loop around `poll(task)` instead. `run()`
wraps `open()`, `startup()` and `run_polls()` and reconnects when the link fails.

One exchange uses the channel at a time. `request()` and `send()` wait for an
exchange in progress, but pre-empt an idle `listen_unsolicited()`, so a command
issued while `run_polls()` waits between polls goes out at once.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Self

from dnp3.application.fragment import ObjectBlock, RequestFragment
from dnp3.application.header import MAX_APP_SEQUENCE
from dnp3.application.parser import ParseError, parse_response, parse_response_header
from dnp3.core.enums import LinkFunctionCode
from dnp3.core.flags import IIN
from dnp3.core.timestamp import DNP3Timestamp
from dnp3.datalink.builder import build_ack, build_link_status, build_reset_link_state, build_unconfirmed_user_data
from dnp3.datalink.frame import DataLinkFrame
from dnp3.datalink.parser import FrameParser
from dnp3.master.command_status import command_point_results
from dnp3.master.commands import DirectOperateTask, OperateTask, SelectTask
from dnp3.master.config import TimeSyncMethod
from dnp3.master.handler import CommandPointState, CommandTaskResult, ResponseInfo
from dnp3.master.master import IINAction, Master, propagation_delay_ms
from dnp3.master.polling import PollTask, PollType
from dnp3.transport.reassembler import Reassembler, ReassemblyError
from dnp3.transport.segment import TransportSegment
from dnp3.transport.segmenter import Segmenter
from dnp3.transport_io.channel import Channel, ChannelError

logger = logging.getLogger(__name__)

READ_CHUNK_SIZE = 4096
"""Bytes requested per channel read. Frames are reassembled across reads."""

MAX_BURST_FRAGMENTS = 512
"""Fragments accepted in one response burst before the exchange is abandoned.

The mod-16 sequence walk wraps, so sequence continuity alone cannot bound a
burst: a peer that keeps incrementing forever stays "in sequence" forever. A
real burst is bounded by the outstation's own database size; this cap is set far
above any legitimate response so it only fires on a peer that will not stop.
"""

CONFIRM_WRITE_BUDGET = 0.5
"""Seconds a CONFIRM for already-delivered values may take, however little of
the exchange deadline is left.

Without a floor a CONFIRM owed as the deadline passes is abandoned before it is
written, and the outstation resends values the handler already has.
"""

_ACKED_FUNCTION_CODES = frozenset(
    {
        LinkFunctionCode.PRI_RESET_LINK_STATE,
        LinkFunctionCode.PRI_TEST_LINK_STATE,
        LinkFunctionCode.PRI_CONFIRMED_USER_DATA,
    }
)
"""Primary link function codes a secondary station answers with ACK (IEEE 1815-2012 9.2.4)."""

_USER_DATA_FUNCTION_CODES = frozenset(
    {
        LinkFunctionCode.PRI_UNCONFIRMED_USER_DATA,
        LinkFunctionCode.PRI_CONFIRMED_USER_DATA,
    }
)
"""Link function codes that carry a transport segment.

An outstation sends responses as primary frames reusing these codes
(`build_primary_frame` sets PRM=1), so a master matches the same values a
master sends.
"""


class LinkResetPolicy(Enum):
    """When to send RESET_LINK_STATE.

    Attributes:
        ON_OPEN: Send a link reset when the channel opens. The default, and what
            IEEE 1815-2012 expects of a master establishing a new association.
        NEVER: Skip the reset. For peers that treat an unsolicited reset as an
            error, or when the link is known to be already reset.
    """

    ON_OPEN = auto()
    NEVER = auto()


_REJECTED = IIN.NO_FUNC_CODE_SUPPORT | IIN.OBJECT_UNKNOWN | IIN.PARAMETER_ERROR
"""IIN2 bits an outstation sets when it did not carry out a request."""


def _wall_clock_ms() -> int:
    return DNP3Timestamp.now().milliseconds


class MasterRunnerError(Exception):
    """Raised when the runner cannot complete an exchange."""


class ResponseTimeoutError(MasterRunnerError):
    """Raised when no response fragment arrives before the deadline."""


class LinkError(MasterRunnerError):
    """Raised when the link fails or delivers unusable bytes.

    Wraps the channel and transport layers' own exceptions (`ChannelError`,
    `ReassemblyError`) so a caller can catch everything this runner raises
    under `MasterRunnerError` without importing from those packages.
    """


class TimeSyncError(MasterRunnerError):
    """Raised when the outstation does not accept a time synchronization."""


class RequestRejectedError(MasterRunnerError):
    """Raised when the outstation answers that it did not carry out a request.

    Attributes:
        iin: Internal indications of the response's final fragment.
    """

    def __init__(self, message: str, iin: IIN) -> None:
        super().__init__(message)
        self.iin = iin


class _InterruptedError(Exception):
    """Raised inside the runner when a wait gives way to another exchange or a stop."""


@dataclass
class _Burst:
    """One solicited response burst, accumulated across fragments.

    Attributes:
        expected_seq: Sequence the next fragment must carry. Seeded with the
            request's own sequence, so the *first* fragment is correlated to the
            request rather than accepted at whatever sequence arrives.
        fragments: Info for each fragment, in arrival order.
        objects: Object blocks of the latest fragment.
    """

    expected_seq: int
    fragments: list[ResponseInfo] = field(default_factory=list)
    objects: Sequence[ObjectBlock] = ()


@dataclass
class MasterRunner:
    """Runs a DNP3 master over a `Channel`, handling the full protocol stack.

    Attributes:
        master: The application-layer master. Supplies request building,
            response parsing, and the SOE handler values are reported to.
        channel: Channel to use. When None, `open()` builds one with
            `_create_channel()`, which a transport subclass provides.
        response_timeout: Seconds to wait for a response. None uses the
            master's `MasterConfig.response_timeout`.
        link_reset: Whether to reset the data link on open.
        poll_retry_delay: Seconds `run_polls()` waits before retrying a
            scheduled poll or IIN reaction that timed out or broke its
            sequence walk.
        reconnect_min_delay: Seconds `run()` waits before its first
            reconnect attempt. Each failed attempt doubles the wait.
        reconnect_max_delay: Longest wait between `run()`'s reconnect
            attempts.
        wall_clock_ms: Master clock, in milliseconds since the Unix epoch, that
            `time_sync()` sets the outstation to. Defaults to the system clock.
    """

    master: Master
    channel: Channel | None = None
    response_timeout: float | None = None
    link_reset: LinkResetPolicy = LinkResetPolicy.ON_OPEN
    poll_retry_delay: float = 5.0
    reconnect_min_delay: float = 1.0
    reconnect_max_delay: float = 60.0
    wall_clock_ms: Callable[[], int] = _wall_clock_ms

    _parser: FrameParser = field(default_factory=FrameParser, init=False, repr=False)
    _segmenter: Segmenter = field(default_factory=Segmenter, init=False, repr=False)
    _reassembler: Reassembler | None = field(default=None, init=False, repr=False)
    _owns_channel: bool = field(default=False, init=False, repr=False)
    _pending: deque[DataLinkFrame] = field(default_factory=deque, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    # Set while a request or send is queued for the lock; an idle listen gives
    # way to it rather than holding the channel until its own deadline.
    _contended: asyncio.Event = field(default_factory=asyncio.Event, init=False, repr=False)
    _contenders: int = field(default=0, init=False, repr=False)
    # IIN reactions the outstation refused; `run_polls()` skips them until `open()`.
    _disabled_actions: IINAction = field(default=IINAction(0), init=False, repr=False)

    @property
    def is_open(self) -> bool:
        """Whether the runner is open and its channel still is.

        False once the peer has closed the connection, even when an injected
        channel still reports itself open.
        """
        return self._reassembler is not None and self.channel is not None and self.channel.is_open

    @property
    def local_address(self) -> tuple[str, int] | None:
        """Local address of the channel, if it exposes one."""
        return getattr(self.channel, "local_address", None)

    # -- lifecycle ------------------------------------------------------------

    async def open(self) -> None:
        """Open the channel and, by policy, reset the data link.

        On any failure the runner is closed again, together with a channel it
        created, so a later `open()` starts clean.

        Raises:
            MasterRunnerError: The runner is already open. Re-opening would
                replace the reassembler and re-send RESET_LINK_STATE underneath
                any in-flight request. Also raised when no channel was supplied
                and the runner cannot build one.
            LinkError: The link reset could not be written.
        """
        # `_reassembler`, not `is_open`: an injected channel is often already
        # open before the runner touches it, and opening the runner over it is
        # the normal test and embedding pattern. Only `open()` sets a
        # reassembler, so it is what distinguishes "runner opened" from
        # "channel happens to be open".
        if self._reassembler is not None:
            msg = "Runner is already open; close() before opening again"
            raise MasterRunnerError(msg)

        self._disabled_actions = IINAction(0)
        if self.channel is None:
            self.channel = self._create_channel()
            self._owns_channel = True

        try:
            if not self.channel.is_open:
                await self.channel.open()

            # Bound reassembly by the master's own fragment cap so a peer that
            # never sets FIN cannot exhaust memory.
            self._reassembler = Reassembler(max_fragment_size=self.master.config.max_fragment_size)
            # A previous connection may have left a partial frame mid-parse and
            # frames unconsumed; neither belongs in this connection's stream.
            self._parser.reset()
            self._pending.clear()
            logger.info("Master channel open")

            if self.link_reset is LinkResetPolicy.ON_OPEN:
                await self._send_link_reset()
        except BaseException:
            await self.close()
            raise

    def _create_channel(self) -> Channel:
        """Build the channel `open()` uses when none was supplied."""
        msg = "no channel supplied"
        raise MasterRunnerError(msg)

    async def close(self) -> None:
        """Close the channel if this runner opened it, and clear protocol state.

        An injected channel is left open for its owner to close, but the
        runner's own state is dropped either way: a reused runner must not
        reassemble the next connection's bytes onto the last one's remnants.
        """
        try:
            if self.channel is not None and self._owns_channel:
                await self.channel.close()
        finally:
            if self._owns_channel:
                self.channel = None
                self._owns_channel = False
            self._reassembler = None
            self._parser.reset()
            self._pending.clear()

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    # -- requests -------------------------------------------------------------

    async def startup(self) -> None:
        """Run the startup sequence the `MasterConfig` flags describe.

        In this order, each step only if its flag is set:

        1. `disable_unsolicited_on_startup`: DISABLE_UNSOLICITED for classes 1-3,
           so no event report arrives before the integrity poll has read
           current values.
        2. `time_sync_on_startup`: `time_sync()` with the configured method.
        3. `startup_integrity_poll`: `integrity_poll()`.
        4. `enable_unsolicited_on_startup`: ENABLE_UNSOLICITED for classes 1-3.

        A failure raises and skips the remaining steps, which leaves unsolicited
        reporting disabled if step 1 ran. An outstation without unsolicited
        reporting rejects steps 1 and 4, so clear both flags for one. Calling
        `startup()` again repeats the whole sequence. The steps taken are
        dropped from `Master.pending_actions`, so `run_polls()` does not repeat
        them.

        Raises:
            RequestRejectedError: The outstation rejected DISABLE_UNSOLICITED or
                ENABLE_UNSOLICITED.
            TimeSyncError: The outstation rejected the time synchronization.
            ResponseTimeoutError: No response arrived before the deadline.
            LinkError: The link failed or delivered unusable bytes.
            MasterRunnerError: The runner is not open, or a response broke the
                burst's sequence walk.
        """
        config = self.master.config
        done = IINAction(0)
        if config.disable_unsolicited_on_startup:
            await self._request_accepted(self.master.build_disable_unsolicited(), "DISABLE_UNSOLICITED")
        if config.time_sync_on_startup:
            await self.time_sync()
            done |= IINAction.TIME_SYNC
        if config.startup_integrity_poll:
            await self.integrity_poll()
            done |= IINAction.INTEGRITY_POLL
        if config.enable_unsolicited_on_startup:
            await self._request_accepted(self.master.build_enable_unsolicited(), "ENABLE_UNSOLICITED")
            done |= IINAction.ENABLE_UNSOLICITED
        # A restarted outstation reports IIN1.7 throughout, demanding the steps
        # just taken. Only the clear is left for `run_polls()`.
        self.master.complete_action(done)

    async def integrity_poll(self) -> list[ResponseInfo]:
        """READ Class 0/1/2/3 and report every value to the SOE handler.

        A completed poll restarts the interval of the scheduled integrity polls;
        a failed one leaves them unchanged.

        Returns:
            Info for each fragment of the response burst, in arrival order.
        """
        infos = await self.request(self.master.build_integrity_poll())
        self.master.scheduler.mark_executed(PollType.INTEGRITY)
        return infos

    async def class_poll(
        self,
        *,
        class_1: bool = True,
        class_2: bool = True,
        class_3: bool = True,
    ) -> list[ResponseInfo]:
        """READ the named event classes.

        Class 0 (static data) is not a class poll upstream; use
        `integrity_poll()` for a full snapshot.

        Args:
            class_1: Include Class 1 events.
            class_2: Include Class 2 events.
            class_3: Include Class 3 events.

        Returns:
            Info for each fragment of the response burst.
        """
        request = self.master.build_class_poll(
            class_1=class_1,
            class_2=class_2,
            class_3=class_3,
        )
        return await self.request(request)

    async def request(self, request: RequestFragment) -> list[ResponseInfo]:
        """Send one application request and consume its whole response burst.

        A response may span several fragments. The outstation sets CON on every
        non-final fragment and waits for an application CONFIRM before sending
        the next, so this must answer or the exchange stalls until the
        outstation's confirm timer expires.

        Args:
            request: Application request to send.

        Returns:
            Info for each fragment of the burst, in arrival order.

        Raises:
            ResponseTimeoutError: No fragment arrived before the deadline, or
                the burst as a whole outran `response_timeout`.
            LinkError: The link failed, a write did not complete before the
                deadline, or the link delivered unusable bytes.
            MasterRunnerError: The channel is not open, a fragment broke the
                burst's sequence walk, or the burst exceeded
                `MAX_BURST_FRAGMENTS`.
        """
        self._require_open()
        async with self._claim():
            return await self._exchange(request)

    async def _exchange(self, request: RequestFragment) -> list[ResponseInfo]:
        """Send a request and consume its response burst; the caller holds the channel."""
        return (await self._exchange_burst(request)).fragments

    async def _exchange_burst(self, request: RequestFragment) -> _Burst:
        """`_exchange`, returning the whole burst."""
        # One deadline for the whole exchange, writes included, not one per
        # fragment: a per fragment deadline lets a peer that answers slowly but
        # steadily hold the request open indefinitely.
        deadline = self._deadline(None)
        await self._send(request, deadline)

        burst = _Burst(expected_seq=request.header.control.seq)
        while True:
            info = await self._next_solicited(burst, deadline)
            burst.fragments.append(info)

            if info.con:
                await self._send_confirm(info.sequence, uns=False, deadline=deadline)
            if info.fin:
                return burst

            if len(burst.fragments) >= MAX_BURST_FRAGMENTS:
                msg = (
                    f"Response burst exceeded {MAX_BURST_FRAGMENTS} fragments "
                    "without setting FIN; abandoning the exchange"
                )
                raise MasterRunnerError(msg)

    async def _send_confirm(self, sequence: int, *, uns: bool, deadline: float) -> None:
        """Confirm a fragment whose values were already delivered.

        Args:
            sequence: Sequence of the fragment being confirmed.
            uns: Whether the fragment was unsolicited.
            deadline: Exchange deadline; the write gets at least
                `CONFIRM_WRITE_BUDGET` beyond now even when it has passed.

        Raises:
            LinkError: The write failed or did not complete in its budget.
        """
        budget_end = asyncio.get_running_loop().time() + CONFIRM_WRITE_BUDGET
        await self._send(self.master.build_confirm(sequence, uns=uns), max(deadline, budget_end))

    async def send(self, request: RequestFragment, *, deadline: float | None = None) -> None:
        """Segment an application fragment and frame each segment onto the link.

        Args:
            request: Application request to transmit.
            deadline: Event-loop time every write must finish by. None allows
                `response_timeout` from now.

        Raises:
            LinkError: A write failed or did not complete before the deadline.
            MasterRunnerError: The channel is not open.
        """
        self._require_open()
        async with self._claim():
            await self._send(request, self._deadline(None) if deadline is None else deadline)

    async def _send(self, request: RequestFragment, deadline: float) -> None:
        """Transmit a fragment; the caller holds the channel."""
        for segment in self._segmenter.segment(request.to_bytes()):
            await self._write_frame(
                build_unconfirmed_user_data(
                    destination=self.master.config.outstation_address,
                    source=self.master.config.address,
                    dir_from_master=True,
                    user_data=segment.to_bytes(),
                ),
                deadline,
            )

    # -- commands -------------------------------------------------------------

    async def direct_operate(self, task: DirectOperateTask) -> CommandTaskResult:
        """Send a DIRECT_OPERATE and report each point's echoed status.

        Point failures are returned, not raised. A command is never retried:
        after a timeout it is unknown whether the outstation carried it out.

        Raises:
            ValueError: The task has no operations.
            ResponseTimeoutError: No response arrived before the deadline.
            LinkError: The link failed or delivered unusable bytes.
            MasterRunnerError: The runner is not open, or the response spanned
                more than one fragment.
        """
        _require_operations(task)
        self._require_open()
        async with self._claim():
            return await self._command(self.master.build_direct_operate(task))

    async def select_and_operate(self, task: SelectTask) -> CommandTaskResult:
        """SELECT the task's points, then OPERATE them if every point was selected.

        The OPERATE carries the SELECT's sequence + 1 and the same objects, as
        select-before-operate requires (IEEE 1815-2012 4.4.4.3). No other
        request goes out between the two; a CONFIRM for an unsolicited
        response may. Point failures are returned, not raised: when a point is
        not selected, no OPERATE is sent and the SELECT's result is returned.
        After a timeout on OPERATE it is unknown whether it took effect.

        Raises:
            ValueError: The task has no operations.
            ResponseTimeoutError: No response arrived before the deadline.
            LinkError: The link failed or delivered unusable bytes.
            MasterRunnerError: The runner is not open, or a response spanned
                more than one fragment.
        """
        _require_operations(task)
        self._require_open()
        async with self._claim():
            # Both built before the SELECT is sent: a request built meanwhile,
            # such as a poll waiting for the channel, would take n+1.
            select = self.master.build_select(task)
            operate = self.master.build_operate(OperateTask(operations=list(task.operations)))
            result = await self._command(select)
            if not all(p.state is CommandPointState.SELECT_SUCCESS for p in result.points):
                return result
            operated = await self._command(operate)
            # A point the OPERATE echo didn't match stays SELECT_SUCCESS, as in
            # opendnp3: selected, but not known to have operated.
            points = tuple(
                selected if op.state is CommandPointState.INIT else op
                for selected, op in zip(result.points, operated.points, strict=True)
            )
            return CommandTaskResult(points, operated.iin)

    async def _command(self, request: RequestFragment) -> CommandTaskResult:
        """Exchange a control request and match its echo; the caller holds the channel."""
        burst = await self._exchange_burst(request)
        if len(burst.fragments) != 1:
            msg = f"Control response spanned {len(burst.fragments)} fragments; expected one"
            raise MasterRunnerError(msg)
        return CommandTaskResult(command_point_results(request, burst.objects), burst.fragments[0].iin)

    # -- time synchronization -------------------------------------------------

    async def time_sync(self, method: TimeSyncMethod | None = None) -> None:
        """Set the outstation's clock to `wall_clock_ms`.

        Runs one of the time synchronization procedures of IEEE 1815-2012:

        - `TimeSyncMethod.NON_LAN`: DELAY_MEASURE, whose round trip less the
          outstation's reported turnaround gives twice the one-way link delay,
          then a WRITE of g50v1 carrying the master's time plus that delay.
        - `TimeSyncMethod.LAN`: RECORD_CURRENT_TIME, on whose arrival the
          outstation notes its own time, then a WRITE of g50v3 carrying the
          master's time when it was sent. The outstation adds the time elapsed
          since.

        The two requests are separate exchanges, so another task's request or
        a poll from `run_polls()` can run between them. Neither procedure
        depends on that gap, and each clock reading is taken once the channel
        is held, so waiting for another exchange does not skew the result.

        Args:
            method: Procedure to run. None runs `MasterConfig.time_sync_method`.

        Raises:
            TimeSyncError: The outstation rejected a request, or its
                DELAY_MEASURE response carried no time delay.
            ResponseTimeoutError: No response arrived before the deadline.
            LinkError: The link failed or delivered unusable bytes.
            MasterRunnerError: The runner is not open, or a response broke the
                burst's sequence walk.
        """
        self._require_open()
        if (method or self.master.config.time_sync_method) is TimeSyncMethod.LAN:
            await self._time_sync_lan()
        else:
            await self._time_sync_non_lan()

    async def _time_sync_non_lan(self) -> None:
        async with self._claim():
            sent_ms = self.wall_clock_ms()
            responses = await self._exchange(self.master.build_delay_measure())
            received_ms = self.wall_clock_ms()
        _raise_if_rejected("DELAY_MEASURE", responses)
        turnaround_ms = responses[-1].time_delay_ms
        if turnaround_ms is None:
            msg = "DELAY_MEASURE response carried no time delay object (g52)"
            raise TimeSyncError(msg)

        delay_ms = propagation_delay_ms(sent_ms=sent_ms, received_ms=received_ms, outstation_delay_ms=turnaround_ms)
        async with self._claim():
            # Read the clock again rather than reuse `received_ms`: the write
            # must carry the time it leaves the master.
            write = self.master.build_write_time(DNP3Timestamp(self.wall_clock_ms() + delay_ms))
            responses = await self._exchange(write)
        _raise_if_rejected("WRITE of g50v1", responses)

    async def _time_sync_lan(self) -> None:
        async with self._claim():
            sent_ms = self.wall_clock_ms()
            responses = await self._exchange(self.master.build_record_current_time())
        if responses[-1].iin & IIN.NO_FUNC_CODE_SUPPORT:
            msg = (
                "Outstation does not support RECORD_CURRENT_TIME; set MasterConfig.time_sync_method "
                f"to {TimeSyncMethod.NON_LAN} or pass it to time_sync()"
            )
            raise TimeSyncError(msg)
        _raise_if_rejected("RECORD_CURRENT_TIME", responses)

        write = self.master.build_write_time(DNP3Timestamp(sent_ms), recorded=True)
        _raise_if_rejected("WRITE of g50v3", await self.request(write))

    # -- writes ---------------------------------------------------------------

    async def write_octet_string(self, index: int, value: bytes) -> None:
        """Write one g110 octet string to the outstation.

        A WRITE response carries no value, so reading the string back to confirm
        what the outstation stored is the caller's job.

        Raises:
            ValueError: `index` is outside 0..65535, or `value` is empty or
                longer than 255 octets. Nothing is sent.
            RequestRejectedError: The outstation did not carry out the write.
            ResponseTimeoutError: No response arrived before the deadline.
            LinkError: The link failed or delivered unusable bytes.
            MasterRunnerError: The runner is not open, or a response broke the
                burst's sequence walk.
        """
        request = self.master.build_write_octet_string(index, value)
        await self._request_accepted(request, f"WRITE of g110v{len(value)} index {index}")

    async def clear_restart(self) -> None:
        """Clear the outstation's DEVICE_RESTART indication (IIN1.7).

        An outstation sets IIN1.7 when it restarts and keeps it set until a
        master clears it, so a master that clears it learns of the next restart.

        Raises:
            RequestRejectedError: The outstation rejected the WRITE, or its
                answer still carries DEVICE_RESTART.
            ResponseTimeoutError: No response arrived before the deadline.
            LinkError: The link failed or delivered unusable bytes.
            MasterRunnerError: The runner is not open, or a response broke the
                burst's sequence walk.
        """
        await self._request_accepted(
            self.master.build_clear_restart(), "WRITE of g80v1 clearing IIN1.7", must_clear=IIN.DEVICE_RESTART
        )

    async def _request_accepted(
        self, request: RequestFragment, what: str, *, must_clear: IIN | None = None
    ) -> list[ResponseInfo]:
        """Run `request()`, raising `RequestRejectedError` if the outstation did not carry it out.

        The outstation did not carry it out if the final fragment carries a
        `_REJECTED` bit or any `must_clear` bit.
        """
        responses = await self.request(request)
        iin = responses[-1].iin
        rejected = iin & (_REJECTED | must_clear if must_clear else _REJECTED)
        if rejected:
            msg = f"Outstation rejected {what}: {rejected.name}"
            raise RequestRejectedError(msg, iin=iin)
        return responses

    # -- unsolicited ----------------------------------------------------------

    async def enable_unsolicited(
        self,
        *,
        class_1: bool = True,
        class_2: bool = True,
        class_3: bool = True,
    ) -> list[ResponseInfo]:
        """Ask the outstation to report the named classes unsolicited.

        Args:
            class_1: Enable Class 1 reporting.
            class_2: Enable Class 2 reporting.
            class_3: Enable Class 3 reporting.

        Returns:
            Info for each fragment of the response burst.
        """
        request = self.master.build_enable_unsolicited(
            class_1=class_1,
            class_2=class_2,
            class_3=class_3,
        )
        return await self.request(request)

    async def disable_unsolicited(
        self,
        *,
        class_1: bool = True,
        class_2: bool = True,
        class_3: bool = True,
    ) -> list[ResponseInfo]:
        """Ask the outstation to stop reporting the named classes unsolicited.

        Args:
            class_1: Disable Class 1 reporting.
            class_2: Disable Class 2 reporting.
            class_3: Disable Class 3 reporting.

        Returns:
            Info for each fragment of the response burst.
        """
        request = self.master.build_disable_unsolicited(
            class_1=class_1,
            class_2=class_2,
            class_3=class_3,
        )
        return await self.request(request)

    async def listen_unsolicited(self, *, timeout: float | None = None) -> ResponseInfo | None:
        """Wait for one unsolicited response, reporting its values and confirming.

        Values reach the SOE handler as a side effect of parsing, the same as for
        a poll. Use this when the master is otherwise idle; unsolicited responses
        that arrive mid-request are handled inline by `request()`. A solicited
        fragment arriving meanwhile is dropped unparsed and logged. A request
        from another task ends the wait early.

        `None` means "nothing arrived in time" and nothing more. A link that
        has failed raises `LinkError` rather than returning `None`, so a caller
        looping on this method can tell a quiet outstation from a dead one
        instead of spinning forever on a socket that will never speak again.

        Args:
            timeout: Seconds to wait. None waits `response_timeout`.

        Returns:
            Info for the unsolicited response, or None if none arrived in time
            or a request pre-empted the wait.

        Raises:
            LinkError: The link failed or delivered unusable bytes.
            MasterRunnerError: The channel is not open.
        """
        self._require_open()
        return await self._listen(self._deadline(timeout))

    async def _listen(self, deadline: float, stop: asyncio.Event | None = None) -> ResponseInfo | None:
        """Wait for one unsolicited response, giving way to a queued request.

        Args:
            deadline: Event-loop time after which to give up.
            stop: Event that also ends the wait when set.

        Returns:
            Info for the unsolicited response, or None if the wait ended first.

        Raises:
            LinkError: The link failed or delivered unusable bytes.
            MasterRunnerError: The channel is not open.
        """
        interrupts = (self._contended,) if stop is None else (self._contended, stop)
        # The lock itself, not `_claim()`: an idle listen must not pre-empt itself.
        async with self._lock:
            while True:
                try:
                    info = await self._receive_fragment(deadline, interrupts=interrupts)
                except ResponseTimeoutError as exc:
                    logger.debug("No unsolicited response: %s", exc)
                    return None
                except _InterruptedError:
                    return None
                if info is None or not info.is_unsolicited:
                    continue
                return info

    # -- scheduling -----------------------------------------------------------

    async def poll(self, task: PollTask) -> list[ResponseInfo]:
        """Run one scheduled poll task and mark it executed.

        The request is built by the task itself, using a sequence drawn from the
        master so a scheduled poll is numbered from the same counter as a direct
        one. `Master` allocates request sequences internally for its own
        builders; `next_request_sequence()` exposes that counter so a transport
        can number a task-built request without reaching into master state.

        Args:
            task: Task from the master's scheduler.

        Returns:
            Info for each fragment of the response burst.
        """
        request = task.build_request(seq=self.master.next_request_sequence())
        responses = await self.request(request)
        self.master.mark_poll_executed(task)
        return responses

    async def run_polls(self, *, stop: asyncio.Event | None = None) -> None:
        """Drive the master's `PollScheduler` until stopped.

        Composes scheduling with transport rather than owning either: the
        intervals come from `PollingConfig`, the due-time arithmetic from
        `PollScheduler`, and only the sending happens here.

        Between polls the loop listens for unsolicited responses, so they are
        confirmed before the outstation's confirm timer expires. A poll that
        times out or breaks its sequence walk is logged and retried after
        `poll_retry_delay`; a failed link ends the loop.

        The loop also takes the actions response IIN bits demand
        (`Master.pending_actions`): before each scheduled poll, and while idle
        as soon as an unsolicited response demands one. It takes them one at a
        time in this order, as opendnp3 does:

        1. `clear_restart()`, after IIN1.7 DEVICE_RESTART.
        2. `integrity_poll()`, after DEVICE_RESTART, or after IIN2.3
           EVENT_BUFFER_OVERFLOW since events were lost.
        3. `time_sync()`, after IIN1.4 NEED_TIME.
        4. ENABLE_UNSOLICITED for classes 1-3, after DEVICE_RESTART.

        `MasterConfig` says which bits demand which actions. An action that
        fails like a poll is retried like one. One the outstation rejects is
        logged and not taken again until the runner is reopened. Demands the
        reactions' own responses raise are dropped, so an outstation that
        never clears a bit gets its reactions once per scheduled poll, not
        back to back.

        Explicit calls such as `request()` or `integrity_poll()` record the
        demands but take no action; only this loop does.

        Args:
            stop: Event that ends the loop when set. Without one the loop runs
                until cancelled, or until the scheduler has no task left.

        Raises:
            LinkError: The link failed or delivered unusable bytes.
            MasterRunnerError: The channel is not open.
        """
        self._require_open()
        stop = stop if stop is not None else asyncio.Event()

        while not stop.is_set():
            action = next(iter(self._actionable()), None)
            if action is not None:
                await self._react(action, stop)
                continue

            task = self.master.scheduler.get_next_task()
            if task is None:
                wait = self.master.scheduler.get_time_until_next()
                if wait is None:
                    return
                await self._idle(max(wait, 0.0), stop, wake_on_demand=True)
                continue

            try:
                await self.poll(task)
            except LinkError:
                raise
            except MasterRunnerError as exc:
                logger.warning(
                    "%s failed, retrying in %.1f s: %s",
                    type(task).__name__,
                    self.poll_retry_delay,
                    exc,
                )
                await self._idle(self.poll_retry_delay, stop)

    async def run(self, *, stop: asyncio.Event | None = None) -> None:
        """Open, run `startup()` and `run_polls()`, reconnecting whenever the link fails.

        A connection that cannot be opened, a link that fails and a startup step
        that times out all close the runner and try again after a wait. The wait
        starts at `reconnect_min_delay`, doubles after each failed attempt up to
        `reconnect_max_delay`, and starts over once a connection completes
        startup. These are opendnp3's channel retry defaults.

        Every connection resets the link (per `link_reset`) and runs the full
        startup sequence again: the outstation may have restarted while the link
        was down, losing its unsolicited reporting state.

        `run()` opens and closes the runner itself, closing it on every exit,
        including cancellation. Calls such as `direct_operate()` made while the
        link is down raise `MasterRunnerError` rather than wait.

        Args:
            stop: Event that ends the loop when set, including during the wait
                between attempts. Without one the loop runs until cancelled, or
                until the scheduler has no task left.

        Raises:
            RequestRejectedError: The outstation rejected a startup request.
                Reconnecting would not change its configuration.
            TimeSyncError: The outstation rejected the startup time sync.
        """
        stop = stop if stop is not None else asyncio.Event()
        delay = self.reconnect_min_delay
        try:
            while not stop.is_set():
                try:
                    await self.open()
                    await self.startup()
                    delay = self.reconnect_min_delay
                    await self.run_polls(stop=stop)
                    return
                except (RequestRejectedError, TimeSyncError):
                    raise
                except (MasterRunnerError, ChannelError) as exc:
                    delay = min(delay, self.reconnect_max_delay)
                    logger.warning("Outstation link down, reconnecting in %g s: %s", delay, exc)
                    await self.close()
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(stop.wait(), delay)
                    delay *= 2
        finally:
            await self.close()

    async def _react(self, action: IINAction, stop: asyncio.Event) -> None:
        """Take one IIN reaction for `run_polls()`."""
        demanded = self.master.pending_actions
        try:
            match action:
                case IINAction.CLEAR_RESTART:
                    await self.clear_restart()
                case IINAction.INTEGRITY_POLL:
                    await self.integrity_poll()
                case IINAction.TIME_SYNC:
                    await self.time_sync()
                case _:
                    await self._request_accepted(self.master.build_enable_unsolicited(), "ENABLE_UNSOLICITED")
        except LinkError:
            raise
        except (RequestRejectedError, TimeSyncError) as exc:
            logger.warning("%s refused, not repeated until the runner is reopened: %s", action.name, exc)
            self._disabled_actions |= action
        except MasterRunnerError as exc:
            logger.warning("%s failed, retrying in %.1f s: %s", action.name, self.poll_retry_delay, exc)
            await self._idle(self.poll_retry_delay, stop)
            return
        # Demands the action's own responses raised are dropped with it: an
        # integrity poll answered with EVENT_BUFFER_OVERFLOW still set does not
        # demand another, and after a refused clear, the follow-ups' answers
        # carrying IIN1.7 do not demand each other. The next poll's answer
        # demands them again.
        self.master.complete_action(action | (self.master.pending_actions & ~demanded))

    def _actionable(self) -> IINAction:
        """Pending IIN reactions `run_polls()` has not given up on."""
        return self.master.pending_actions & ~self._disabled_actions

    async def _idle(self, timeout: float, stop: asyncio.Event, *, wake_on_demand: bool = False) -> None:
        """Handle unsolicited responses until `timeout` elapses or `stop` is set.

        Args:
            timeout: Seconds to idle.
            stop: Event that ends the wait early.
            wake_on_demand: Also end the wait once an unsolicited response has
                demanded an IIN reaction. Not for an action's own retry delay,
                which would end at once.

        Raises:
            LinkError: The link failed or delivered unusable bytes.
            MasterRunnerError: The channel is not open.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not stop.is_set() and loop.time() < deadline:
            if wake_on_demand and self._actionable():
                return
            await self._listen(deadline, stop)

    # -- protocol stack -------------------------------------------------------

    async def _next_solicited(self, burst: _Burst, deadline: float) -> ResponseInfo:
        """Read until the next fragment of a solicited burst arrives.

        Unsolicited responses interleaved with a request are confirmed and
        skipped: the outstation may report an event at any time, and discarding
        one would lose data the SOE handler has already been given.

        Args:
            burst: Burst being accumulated, for sequence continuity.
            deadline: Event-loop time the whole exchange must finish by.

        Returns:
            Info for the next solicited fragment.

        Raises:
            ResponseTimeoutError: No fragment arrived before the deadline.
            LinkError: The link failed or delivered unusable bytes.
            MasterRunnerError: A fragment broke the burst's sequence walk.
        """
        while True:
            info = await self._receive_fragment(deadline, burst=burst)
            if info is None or info.is_unsolicited:
                continue
            return info

    def _screen_solicited(self, burst: _Burst, data: bytes) -> bool:
        """Correlate raw fragment bytes to the burst before they are dispatched.

        Reads only the application header, which is two bytes and cheap, so an
        uncorrelated fragment is rejected without its objects ever being parsed
        or handed to the SOE handler.

        Unsolicited fragments pass through untouched: they are not part of any
        burst and are confirmed and reported wherever they arrive.

        Args:
            burst: Burst being accumulated.
            data: Reassembled application fragment.

        Returns:
            True if the fragment should be parsed, False if it belongs to an
            earlier request and should be dropped.

        Raises:
            MasterRunnerError: The fragment broke the burst's sequence walk.
        """
        try:
            header, _ = parse_response_header(data)
        except (ParseError, ValueError, IndexError):
            # Not a parseable response header; `_receive_fragment` logs and
            # discards it when the full parse fails.
            return True
        if header.control.uns:
            return True
        return self._check_sequence(burst, header.control.seq)

    def _screen_unsolicited(self, data: bytes) -> bool:
        """Reject a solicited fragment while listening, before its values are parsed.

        With no request outstanding a solicited fragment answers nothing; parsing
        it would hand its values to the SOE handler as though they were current.

        Args:
            data: Reassembled application fragment.

        Returns:
            True if the fragment should be parsed, False if it was dropped.
        """
        try:
            header, _ = parse_response_header(data)
        except (ParseError, ValueError, IndexError):
            # Not a parseable response header; `_receive_fragment` logs and
            # discards it when the full parse fails.
            return True
        if header.control.uns:
            return True
        logger.warning(
            "Dropping solicited response fragment with sequence %d while listening for unsolicited responses",
            header.control.seq,
        )
        return False

    def _check_sequence(self, burst: _Burst, sequence: int) -> bool:
        """Correlate a fragment to the request and its place in the burst.

        IEEE 1815-2012 clause 4.2.2.4.5: the first fragment carries the
        request's sequence and each subsequent fragment increments by one,
        modulo 16. Because the burst is seeded with the request's own sequence,
        the same comparison does both jobs: it correlates fragment one to the
        request that is outstanding, and walks the rest.

        A first fragment that does not match is dropped rather than raised on.
        It is almost always the late answer to a request that already timed
        out, and failing the current request for it would leave the current
        answer queued to fail the next request in turn, indefinitely.

        Tracked per burst rather than in `SequenceState`, whose
        `last_request_seq` is a request-sequence allocator with a different
        lifetime; this walk lives and dies with one response.

        Args:
            burst: Burst being accumulated.
            sequence: Application sequence the fragment carried.

        Returns:
            True if the fragment belongs to the burst, False if it is a first
            fragment for some other request and was dropped.

        Raises:
            MasterRunnerError: A later fragment broke the burst's sequence walk.
        """
        if sequence != burst.expected_seq:
            if not burst.fragments:
                logger.warning(
                    "Dropping response fragment with sequence %d, expected %d for the outstanding request",
                    sequence,
                    burst.expected_seq,
                )
                return False
            position = len(burst.fragments) + 1
            msg = (
                f"Response fragment {position} carried sequence {sequence}, "
                f"expected {burst.expected_seq}: broke the burst's sequence walk"
            )
            raise MasterRunnerError(msg)
        burst.expected_seq = (sequence + 1) % (MAX_APP_SEQUENCE + 1)
        return True

    async def _receive_fragment(
        self,
        deadline: float,
        *,
        burst: _Burst | None = None,
        interrupts: tuple[asyncio.Event, ...] = (),
    ) -> ResponseInfo | None:
        """Read until one application fragment is parsed, or the deadline passes.

        When accumulating a solicited burst, the fragment's sequence is checked
        *before* `Master.process_fragment` sees it. That ordering is the whole
        point: `process_fragment` dispatches parsed values to the SOE handler
        ahead of its own sequence validation, so a fragment
        rejected afterwards has already delivered its values. Raising later
        would tell the caller something was wrong but leave stale analog values
        sitting in the handler as current.

        Args:
            deadline: Event-loop time after which to give up.
            burst: Solicited burst being accumulated, if any. Unsolicited
                listening passes None, and then only unsolicited fragments are
                parsed.
            interrupts: Events that end the wait early when set.

        Returns:
            Info for the fragment, or None if it did not parse as a response
            or belonged to an earlier request.

        Raises:
            ResponseTimeoutError: The deadline passed.
            _InterruptedError: An interrupt was set before a fragment completed.
            LinkError: The link failed, the peer closed the connection, or it
                delivered unusable bytes.
            MasterRunnerError: A fragment broke the burst's sequence walk.
        """
        data = await self._read_fragment_bytes(deadline, interrupts)
        if burst is None:
            if not self._screen_unsolicited(data):
                return None
        elif not self._screen_solicited(burst, data):
            return None
        try:
            response = parse_response(data)
        except ParseError as exc:
            logger.warning("Discarding %d bytes that did not parse as a response: %s", len(data), exc)
            return None
        info = self.master.process_fragment(response)
        if burst is not None and not info.is_unsolicited:
            burst.objects = response.objects

        # An unsolicited response asking for CON must be confirmed whether or
        # not the master is mid-request; the outstation retries until it is.
        if info.is_unsolicited and info.con:
            logger.debug("Confirming unsolicited response seq=%d", info.sequence)
            await self._send_confirm(info.sequence, uns=True, deadline=deadline)
            self.master.on_confirm_sent()

        return info

    async def _read_fragment_bytes(self, deadline: float, interrupts: tuple[asyncio.Event, ...] = ()) -> bytes:
        """Read link frames until one application fragment is reassembled.

        Frames addressed elsewhere and link-management frames carrying no user
        data are skipped.

        Args:
            deadline: Event-loop time after which to give up.
            interrupts: Events that end the wait early when set.

        Returns:
            The reassembled application fragment.

        Raises:
            ResponseTimeoutError: The deadline passed.
            _InterruptedError: An interrupt was set before a fragment completed.
            LinkError: The link failed, the peer closed the connection, or a
                transport segment did not fit the stream being reassembled.
        """
        channel, reassembler = self._require_open()
        loop = asyncio.get_running_loop()
        # Checked before queued frames are consumed, so a spent deadline takes
        # nothing: the frames wait for the next call rather than delivering
        # values whose CONFIRM would then have no time left.
        if deadline <= loop.time():
            msg = "Timed out waiting for a response fragment"
            raise ResponseTimeoutError(msg)

        while True:
            # Frames already parsed but not yet consumed come first: one read can
            # yield several, and the fragment that completes here may be followed
            # by frames belonging to the next one.
            while self._pending:
                fragment = await self._consume_frame(self._pending.popleft(), reassembler)
                if fragment is not None:
                    return fragment

            if any(event.is_set() for event in interrupts):
                raise _InterruptedError
            remaining = deadline - loop.time()
            if remaining <= 0:
                msg = "Timed out waiting for a response fragment"
                raise ResponseTimeoutError(msg)

            try:
                data = await _read_chunk(channel, remaining, interrupts)
            except TimeoutError as exc:
                msg = "Timed out reading from the outstation"
                raise ResponseTimeoutError(msg) from exc
            except ChannelError as exc:
                # Catches the whole family, not just ChannelClosedError:
                # TcpClientChannel.read raises the bare parent on OSError, so an
                # ECONNRESET (the most common way a real link dies) arrives
                # as ChannelError itself.
                msg = f"Link failed while awaiting a response: {exc}"
                raise LinkError(msg) from exc

            if not data:
                # EOF: nothing more will ever arrive. Closing the runner makes
                # is_open report it and stops every later call early.
                await self.close()
                msg = "Peer closed the connection"
                raise LinkError(msg)

            # Drain the parser in full before handling any frame. `feed()`
            # materializes every complete frame from the chunk, so consuming
            # them inside the iteration and returning early would discard the
            # rest. A kernel that coalesces an ACK with a response, or two
            # back-to-back segments, into one read makes that routine.
            self._pending.extend(self._parser.feed(data))

    async def _consume_frame(self, frame: DataLinkFrame, reassembler: Reassembler) -> bytes | None:
        """Filter one link frame, answer it if it is a link request, and offer its segment to the reassembler.

        Args:
            frame: Frame to consider.
            reassembler: Reassembler accumulating the current fragment.

        Returns:
            The reassembled application fragment, or None if this frame was
            skipped or the fragment is still incomplete.

        Raises:
            LinkError: The segment did not fit the stream being reassembled,
                or a link reply could not be written.
        """
        config = self.master.config
        # Both addresses, not just the destination. A frame merely addressed to
        # this master may still come from another outstation on the same link,
        # and would otherwise satisfy an outstanding request with foreign values.
        if frame.header.destination != config.address:
            return None
        if frame.header.source != config.outstation_address:
            logger.warning(
                "Ignoring frame addressed to this master from source %d; expected %d",
                frame.header.source,
                config.outstation_address,
            )
            return None
        # DIR set means a master sent the frame, whatever its source address says.
        if frame.header.control.dir_from_master:
            return None
        # Secondary frames (ACK, NACK, LINK_STATUS) answer the master's own link
        # requests and carry nothing to reassemble. Checked before the function
        # code because the codes collide numerically across the PRM bit:
        # SEC_ACK and PRI_RESET_LINK_STATE are both 0, and
        # SEC_NACK and PRI_RESET_USER_PROCESS are both 1.
        if not frame.header.control.prm:
            return None
        await self._answer_link_request(frame)
        if frame.header.control.function_code not in _USER_DATA_FUNCTION_CODES or not frame.user_data:
            return None

        try:
            result = reassembler.add(TransportSegment.from_bytes(frame.user_data))
        except ReassemblyError as exc:
            # Fail closed, as the outstation sibling does: drop the partial
            # stream so the next fragment starts clean rather than being
            # assembled onto a desynchronized prefix.
            reassembler.reset()
            msg = f"Transport reassembly failed: {exc}"
            raise LinkError(msg) from exc
        return None if result is None else result.data

    async def _answer_link_request(self, frame: DataLinkFrame) -> None:
        """Send the secondary reply a primary frame calls for.

        IEEE 1815-2012 9.2.4: REQUEST_LINK_STATUS gets LINK_STATUS, and
        RESET_LINK_STATES, TEST_LINK_STATES and CONFIRMED_USER_DATA get ACK.
        An outstation that goes unanswered retransmits confirmed user data,
        and may drop the connection after unanswered link-status keep-alives.

        Args:
            frame: Primary frame from the outstation.

        Raises:
            LinkError: The reply could not be written.
        """
        code = frame.header.control.function_code
        destination = self.master.config.outstation_address
        source = self.master.config.address
        if code == LinkFunctionCode.PRI_REQUEST_LINK_STATUS:
            reply = build_link_status(destination, source, dir_from_master=True)
        elif code in _ACKED_FUNCTION_CODES:
            reply = build_ack(destination, source, dir_from_master=True)
        else:
            return
        await self._write_frame(reply, self._deadline(None))

    async def _send_link_reset(self) -> None:
        """Send RESET_LINK_STATE.

        The ACK is consumed opportunistically by the next read: a reset is
        advisory for a master, and some outstations answer only once the first
        request arrives.
        """
        await self._write_frame(
            build_reset_link_state(
                destination=self.master.config.outstation_address,
                source=self.master.config.address,
                dir_from_master=True,
            ),
            self._deadline(None),
        )
        logger.debug("Sent RESET_LINK_STATE to outstation %d", self.master.config.outstation_address)

    async def _write_frame(self, frame: DataLinkFrame, deadline: float) -> None:
        """Write one link frame to the channel.

        Bounded because a peer that stops reading blocks the write forever
        once the socket buffer fills, and TCP channels default to no write
        timeout of their own.

        Args:
            frame: Frame to transmit.
            deadline: Event-loop time the write must finish by.

        Raises:
            LinkError: The write failed or did not complete before the deadline.
        """
        channel, _ = self._require_open()
        remaining = max(deadline - asyncio.get_running_loop().time(), 0.0)
        try:
            await asyncio.wait_for(channel.write_all(frame.to_bytes()), timeout=remaining)
        except TimeoutError as exc:
            msg = "Write to the outstation did not complete before the deadline"
            raise LinkError(msg) from exc
        except ChannelError as exc:
            msg = f"Link failed while writing: {exc}"
            raise LinkError(msg) from exc

    def _deadline(self, timeout: float | None) -> float:
        """Absolute event-loop time a wait should end at.

        Args:
            timeout: Seconds to wait, or None for `response_timeout`.

        Returns:
            Event-loop time of the deadline.
        """
        if timeout is None:
            timeout = self.master.config.response_timeout if self.response_timeout is None else self.response_timeout
        return asyncio.get_running_loop().time() + timeout

    @contextlib.asynccontextmanager
    async def _claim(self) -> AsyncIterator[None]:
        """Hold the channel for one exchange, pre-empting an idle listen."""
        self._contenders += 1
        self._contended.set()
        try:
            await self._lock.acquire()
        finally:
            self._contenders -= 1
            if not self._contenders:
                self._contended.clear()
        try:
            yield
        finally:
            self._lock.release()

    def _require_open(self) -> tuple[Channel, Reassembler]:
        """Return the channel and reassembler, or fail if `open()` has not run.

        Returning the narrowed pair rather than asserting keeps the invariant
        enforced under `python -O`, where `assert` is stripped.

        Returns:
            The open channel and its reassembler.

        Raises:
            MasterRunnerError: `open()` has not been awaited, or the channel has
                since closed.
        """
        if self.channel is None or self._reassembler is None:
            msg = "open() must be awaited before using the runner"
            raise MasterRunnerError(msg)
        # `is_open` rather than `is not None`: an injected channel closed by its
        # owner, or a peer that dropped the link, would otherwise surface as a
        # bare ChannelClosedError from the first write and a ResponseTimeoutError
        # from the first read: two wrong types for one condition.
        if not self.channel.is_open:
            msg = "Channel is closed; open() must be awaited before using the runner"
            raise MasterRunnerError(msg)
        return self.channel, self._reassembler


def _require_operations(task: DirectOperateTask | SelectTask) -> None:
    if not task.operations:
        msg = "Command task has no operations"
        raise ValueError(msg)


def _raise_if_rejected(request: str, responses: list[ResponseInfo]) -> None:
    """Raise `TimeSyncError` if the outstation reports it did not carry out `request`."""
    rejected = responses[-1].iin & _REJECTED
    if rejected:
        msg = f"Outstation rejected {request}: {rejected.name}"
        raise TimeSyncError(msg)


async def _read_chunk(channel: Channel, timeout: float, interrupts: tuple[asyncio.Event, ...]) -> bytes:
    """Read from the channel until data arrives, the timeout passes, or an interrupt is set.

    Only the channel read is ever cancelled, never the processing of what it
    returned, and it is awaited to completion before returning so the next
    reader cannot overlap it.

    Returns:
        Bytes read, empty at end of stream.

    Raises:
        TimeoutError: The timeout passed.
        _InterruptedError: An interrupt was set first.
        ChannelError: The read failed.
    """
    reader = asyncio.ensure_future(channel.read(READ_CHUNK_SIZE))
    watchers = [asyncio.ensure_future(event.wait()) for event in interrupts]
    try:
        await asyncio.wait([reader, *watchers], timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (reader, *watchers):
            task.cancel()
        await asyncio.wait([reader, *watchers])

    if not reader.cancelled():
        return reader.result()
    if any(event.is_set() for event in interrupts):
        raise _InterruptedError
    raise TimeoutError
