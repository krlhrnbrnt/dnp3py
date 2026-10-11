"""Secure Authentication in `MasterRunner`, against a scripted SAv5 outstation.

The outstation half is written from the spec with `hmac` and `aes_key_unwrap`,
not with `dnp3.master.secauth`, so these tests do not just check the module
against itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import struct
from typing import Any

import pytest
from cryptography.hazmat.primitives.keywrap import InvalidUnwrap, aes_key_unwrap

from dnp3.application.builder import build_null_response, build_response
from dnp3.application.fragment import ObjectBlock
from dnp3.application.qualifiers import ObjectHeader
from dnp3.core.enums import CommandStatus, FunctionCode
from dnp3.core.flags import IIN
from dnp3.master.commands import OperateTask, SelectTask
from dnp3.master.config import SecureAuthConfig, TimeSyncMethod
from dnp3.master.master import IINAction
from dnp3.master.polling import IntegrityPollTask
from dnp3.master.runner import AuthenticationError, SessionKeyError
from dnp3.master.tcp_runner import MasterTcpRunner
from dnp3.transport_io.simulator import SimulatorChannel, create_channel_pair
from tests.unit.master.test_file_runner import status_reply
from tests.unit.master.test_reconnect import RedialingChannel
from tests.unit.master.test_tcp_runner import (
    FakeOutstation,
    RecordingHandler,
    answer_requests,
    crobs,
    echo_reply,
    make_runner,
    null_reply,
    unsolicited_response,
)

UPDATE_KEY = bytes(range(16))
CHALLENGE_DATA = bytes.fromhex("a5a5a5a5")
USER = 1
SHA256_16 = 4
AES_128, AES_256 = 1, 2
OK, NOT_INIT, AUTH_FAIL = 1, 2, 4
AUTHENTICATION_FAILED, MAX_SESSION_KEY_STATUS_REQUESTS_EXCEEDED = 1, 12  # g120v7 error codes


def g120(variation: int, obj: bytes) -> ObjectBlock:
    """One free-format (qualifier 0x5B) group 120 object."""
    header = ObjectHeader(group=120, variation=variation, qualifier=0x5B)
    return ObjectBlock(header=header, data=b"\x01" + struct.pack("<H", len(obj)) + obj)


def auth_response(seq: int, block: ObjectBlock) -> bytes:
    """An AUTHENTICATE_RESPONSE fragment carrying one object."""
    data = bytearray(build_response(objects=(block,), seq=seq).to_bytes())
    data[1] = FunctionCode.AUTHENTICATE_RESPONSE
    return bytes(data)


def hmac_sha256_16(key: bytes, *parts: bytes) -> bytes:
    return hmac.new(key, b"".join(parts), hashlib.sha256).digest()[:16]


def g120_object(request: bytes) -> tuple[int, bytes]:
    """Variation and object octets of a request's single g120 object.

    Read from the raw bytes: `parse_request` cannot frame g120v4, which has no width in the object table.
    """
    group, variation, qualifier = request[2:5]
    assert group == 120
    return variation, request[6:] if qualifier == 0x07 else request[8:]


class SaPeer:
    """The outstation half of SAv5: answers key changes and challenges requests."""

    def __init__(self, peer: FakeOutstation, update_key: bytes = UPDATE_KEY, *, key_wrap: int = AES_128) -> None:
        self.peer = peer
        self.update_key = update_key
        self.key_wrap = key_wrap
        self.control = b""
        self.monitor = b""

    def key_status(self, status: int, mac_value: bytes = b"") -> bytes:
        head = struct.pack("<IHBBBH", 7, USER, self.key_wrap, status, SHA256_16, len(CHALLENGE_DATA))
        return head + CHALLENGE_DATA + mac_value

    async def serve_key_change(self) -> int:
        """Answer a key status request, then a key change, taking the keys the master sends.

        Returns the key change's sequence.
        """
        [request] = await self.peer.read_fragments(1)
        assert request[1] == FunctionCode.AUTHENTICATE_REQUEST
        assert g120_object(request) == (4, struct.pack("<H", USER))
        not_init = self.key_status(NOT_INIT)
        await self.peer.send_fragment(auth_response(request[0] & 0x0F, g120(5, not_init)))

        [change] = await self.peer.read_fragments(1)
        variation, obj = g120_object(change)
        assert variation == 6
        assert struct.unpack_from("<IH", obj) == (7, USER)
        try:
            plain = aes_key_unwrap(self.update_key, obj[6:])
        except InvalidUnwrap:
            await self.peer.send_fragment(auth_response(change[0] & 0x0F, g120(5, self.key_status(AUTH_FAIL))))
            return change[0] & 0x0F
        (length,) = struct.unpack_from("<H", plain)
        self.control, self.monitor = plain[2 : 2 + length], plain[2 + length : 2 + 2 * length]
        assert plain[2 + 2 * length :].startswith(not_init)
        ok = self.key_status(OK, hmac_sha256_16(self.monitor, change))
        await self.peer.send_fragment(auth_response(change[0] & 0x0F, g120(5, ok)))
        return change[0] & 0x0F

    def challenge(self, seq: int, mac_algorithm: int = SHA256_16) -> bytes:
        obj = struct.pack("<IHBB", 1, USER, mac_algorithm, 1) + CHALLENGE_DATA
        return auth_response(seq, g120(1, obj))

    async def serve_challenged(self) -> bytes:
        """Challenge the next request, check the master's reply, then answer the request. Returns the request."""
        [request] = await self.peer.read_fragments(1)
        seq = request[0] & 0x0F
        challenge = self.challenge(seq)
        await self.peer.send_fragment(challenge)
        [reply] = await self.peer.read_fragments(1)
        assert reply[0] & 0x0F == seq
        assert reply[1] == FunctionCode.AUTHENTICATE_REQUEST
        variation, obj = g120_object(reply)
        assert variation == 2
        assert struct.unpack_from("<IH", obj) == (1, USER)
        assert obj[6:] == hmac_sha256_16(self.control, challenge, request)
        await self.peer.send_fragment(build_null_response(seq=seq).to_bytes())
        return request

    async def serve_error(self, code: int) -> None:
        """Answer the next request with a g120v7 error."""
        [request] = await self.peer.read_fragments(1)
        error = struct.pack("<IHHB", 1, USER, 0, code) + bytes(6)
        await self.peer.send_fragment(auth_response(request[0] & 0x0F, g120(7, error)))


async def sa_pair(**config: Any) -> tuple[MasterTcpRunner, RecordingHandler, FakeOutstation]:
    """A runner configured for SA, not yet opened, and the fake outstation facing it."""
    channel_a, channel_b = create_channel_pair()
    await channel_a.open()
    await channel_b.open()
    secure_auth = SecureAuthConfig(update_key=UPDATE_KEY, **config.pop("secure_auth_options", {}))
    runner, handler = make_runner(channel_a, secure_auth=secure_auth, **config)
    return runner, handler, FakeOutstation(channel_b)


async def open_sa_runner(**config: Any) -> tuple[MasterTcpRunner, RecordingHandler, SaPeer]:
    """An opened SA runner whose session keys the outstation holds."""
    runner, handler, peer = await sa_pair(**config)
    sa = SaPeer(peer)
    serve = asyncio.create_task(sa.serve_key_change())
    await runner.open()
    await serve
    return runner, handler, sa


async def cancelled(task: asyncio.Task[Any]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def assert_nothing_sent(peer: FakeOutstation) -> None:
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(peer.read_fragments(1), timeout=0.2)


class TestSessionKeys:
    async def test_open_sets_up_session_keys(self) -> None:
        runner, _, sa = await open_sa_runner()

        assert runner.is_open
        assert len(sa.control) == 16 and len(sa.monitor) == 16
        assert sa.control != sa.monitor

    async def test_request_during_open_waits_for_key_setup(self) -> None:
        """A command issued while `open()` is mid key setup must not start a second key setup."""
        runner, _, peer = await sa_pair()
        sa = SaPeer(peer)
        opening = asyncio.create_task(runner.open())
        while runner._reassembler is None:
            await asyncio.sleep(0)
        other = asyncio.create_task(runner.clear_restart())
        await asyncio.sleep(0.05)

        await sa.serve_key_change()
        await opening
        request = await sa.serve_challenged()
        await other

        assert request[1] == FunctionCode.WRITE

    async def test_wrong_update_key(self) -> None:
        runner, _, peer = await sa_pair()
        serve = asyncio.create_task(SaPeer(peer, update_key=bytes(16)).serve_key_change())

        with pytest.raises(SessionKeyError, match="AUTH_FAIL"):
            await runner.open()
        await serve

        assert not runner.is_open

    async def test_outstation_without_secure_auth(self) -> None:
        runner, _, peer = await sa_pair()
        responder = answer_requests(peer, 1, {FunctionCode.AUTHENTICATE_REQUEST: null_reply(IIN.NO_FUNC_CODE_SUPPORT)})

        with pytest.raises(SessionKeyError, match="without a key status"):
            await runner.open()
        await responder

    async def test_key_wrap_mismatch_on_open(self) -> None:
        runner, _, peer = await sa_pair()
        serve = asyncio.create_task(SaPeer(peer, key_wrap=AES_256).serve_key_change())

        with pytest.raises(SessionKeyError, match="key wrap algorithm 2"):
            await runner.open()
        await cancelled(serve)

    async def test_error_during_key_change(self) -> None:
        """A g120v7 answer to the key status request fails setup, with the outstation's code."""
        runner, _, peer = await sa_pair()
        error = struct.pack("<IHHB", 0, USER, 0, 11) + bytes(6)
        responder = answer_requests(
            peer, 1, {FunctionCode.AUTHENTICATE_REQUEST: lambda r: auth_response(r[0] & 0x0F, g120(7, error))}
        )

        with pytest.raises(SessionKeyError, match="UNKNOWN_USER") as excinfo:
            await runner.open()
        await responder

        assert excinfo.value.code == 11

    async def test_run_stops_on_session_key_error(self) -> None:
        """Reconnecting cannot fix a wrong update key, so `run()` raises instead of looping."""
        runner, _, peer = await sa_pair()
        serve = asyncio.create_task(SaPeer(peer, update_key=bytes(16)).serve_key_change())

        with pytest.raises(SessionKeyError):
            await asyncio.wait_for(runner.run(), timeout=5.0)
        await serve

    async def test_run_reconnects_when_renewal_fails(self) -> None:
        """Keys the outstation once took can fail to renew, after its restart or a transient fault; that is retried."""
        peers = [SimulatorChannel(), SimulatorChannel()]
        for peer in peers:
            await peer.open()
        runner, _ = make_runner(
            RedialingChannel(peers=list(peers)),
            secure_auth=SecureAuthConfig(update_key=UPDATE_KEY),
            startup_integrity_poll=False,
            enable_unsolicited_on_startup=False,
            poll_retry_delay=0.01,
        )
        runner.reconnect_min_delay = 0.01
        runner.master.scheduler.clear()
        runner.master.scheduler.add_task(IntegrityPollTask(interval=3600.0))
        first, second = (SaPeer(FakeOutstation(peer)) for peer in peers)
        stop = asyncio.Event()

        async def serve() -> None:
            await first.serve_key_change()
            await first.serve_error(AUTHENTICATION_FAILED)
            await first.serve_error(MAX_SESSION_KEY_STATUS_REQUESTS_EXCEEDED)
            await second.serve_key_change()
            stop.set()

        task = asyncio.create_task(serve())
        await asyncio.wait_for(runner.run(stop=stop), timeout=5.0)
        await task

    async def test_close_drops_session_keys(self) -> None:
        runner, _, _ = await open_sa_runner()

        await runner.close()

        assert runner._session_keys is None


class TestKeyStatusMisplaced:
    async def test_key_status_answering_another_request(self) -> None:
        """Only key setup's requests can be answered with a key status."""
        runner, _, sa = await open_sa_runner()
        responder = answer_requests(
            sa.peer,
            1,
            {FunctionCode.WRITE: lambda r: auth_response(r[0] & 0x0F, g120(5, sa.key_status(OK)))},
        )

        with pytest.raises(AuthenticationError, match="WRITE with a key status"):
            await runner.clear_restart()
        await responder


class TestChallenges:
    async def test_challenged_request_completes(self) -> None:
        runner, _, sa = await open_sa_runner()
        serve = asyncio.create_task(sa.serve_challenged())

        await runner.clear_restart()
        request = await serve

        assert request[1] == FunctionCode.WRITE

    async def test_challenge_reply_restarts_the_response_timeout(self) -> None:
        """The response may come later than one `response_timeout` after the request, but not after the reply."""
        runner, _, sa = await open_sa_runner(response_timeout=0.4)

        async def answer_slowly() -> None:
            [request] = await sa.peer.read_fragments(1)
            seq = request[0] & 0x0F
            await asyncio.sleep(0.25)
            await sa.peer.send_fragment(sa.challenge(seq))
            await sa.peer.read_fragments(1)
            await asyncio.sleep(0.25)
            await sa.peer.send_fragment(build_null_response(seq=seq).to_bytes())

        serve = asyncio.create_task(answer_slowly())
        await runner.clear_restart()
        await serve

    async def test_unchallenged_request_unchanged(self) -> None:
        runner, _, sa = await open_sa_runner()
        responder = answer_requests(sa.peer, 1)

        await runner.clear_restart()
        await responder

    async def test_second_challenge_raises(self) -> None:
        """A challenge to the reply means the outstation rejected it; the request is not resent."""
        runner, _, sa = await open_sa_runner()

        async def challenge_twice() -> None:
            [request] = await sa.peer.read_fragments(1)
            seq = request[0] & 0x0F
            await sa.peer.send_fragment(sa.challenge(seq))
            await sa.peer.read_fragments(1)
            await sa.peer.send_fragment(sa.challenge(seq))

        serve = asyncio.create_task(challenge_twice())
        with pytest.raises(AuthenticationError, match="rejected the challenge reply"):
            await runner.clear_restart()
        await serve
        await assert_nothing_sent(sa.peer)

    async def test_unsolicited_challenge_ignored(self) -> None:
        """A challenge with UNS set answers no request, so it gets no reply."""
        runner, _, sa = await open_sa_runner()

        async def serve() -> None:
            [request] = await sa.peer.read_fragments(1)
            seq = request[0] & 0x0F
            challenge = bytearray(sa.challenge(seq))
            challenge[0] |= 0x10  # UNS
            await sa.peer.send_fragment(bytes(challenge))
            await sa.peer.send_fragment(build_null_response(seq=seq).to_bytes())

        task = asyncio.create_task(serve())
        await runner.clear_restart()
        await task
        await assert_nothing_sent(sa.peer)

    async def test_challenge_with_other_sequence_dropped(self) -> None:
        """A challenge carrying another request's sequence is dropped like any late response."""
        runner, _, sa = await open_sa_runner()

        async def serve() -> None:
            [request] = await sa.peer.read_fragments(1)
            seq = request[0] & 0x0F
            await sa.peer.send_fragment(sa.challenge((seq + 1) % 16))
            await sa.peer.send_fragment(build_null_response(seq=seq).to_bytes())

        task = asyncio.create_task(serve())
        await runner.clear_restart()
        await task
        await assert_nothing_sent(sa.peer)

    async def test_challenge_inside_a_burst(self) -> None:
        """A challenge after the first response fragment is not a challenge to the request."""
        runner, _, sa = await open_sa_runner()

        async def serve() -> None:
            [request] = await sa.peer.read_fragments(1)
            seq = request[0] & 0x0F
            first = bytearray(build_null_response(seq=seq).to_bytes())
            first[0] &= ~0x40  # FIN clear: more fragments follow
            await sa.peer.send_fragment(bytes(first))
            await sa.peer.send_fragment(sa.challenge((seq + 1) % 16))

        task = asyncio.create_task(serve())
        with pytest.raises(AuthenticationError, match="middle of the response"):
            await runner.clear_restart()
        await task
        await assert_nothing_sent(sa.peer)

    async def test_challenge_after_sequence_wrap(self) -> None:
        """Sixteen fragments walk the sequence back to the request's, so position must spot a mid-burst challenge."""
        runner, _, sa = await open_sa_runner()

        async def serve() -> None:
            [request] = await sa.peer.read_fragments(1)
            seq = request[0] & 0x0F
            for i in range(16):
                fragment = bytearray(build_null_response(seq=(seq + i) % 16).to_bytes())
                fragment[0] &= ~0x40  # FIN clear
                if i:
                    fragment[0] &= ~0x80  # FIR only on the first
                await sa.peer.send_fragment(bytes(fragment))
            await sa.peer.send_fragment(sa.challenge(seq))

        task = asyncio.create_task(serve())
        with pytest.raises(AuthenticationError, match="middle of the response"):
            await runner.clear_restart()
        await task
        await assert_nothing_sent(sa.peer)

    async def test_challenge_with_trailing_bytes(self) -> None:
        """The reply's MAC would cover bytes the master did not parse, so it is not sent."""
        runner, _, sa = await open_sa_runner()

        async def challenge() -> None:
            [request] = await sa.peer.read_fragments(1)
            await sa.peer.send_fragment(sa.challenge(request[0] & 0x0F) + b"\x00")

        serve = asyncio.create_task(challenge())
        with pytest.raises(AuthenticationError, match="bytes after its object"):
            await runner.clear_restart()
        await serve
        await assert_nothing_sent(sa.peer)

    async def test_challenge_without_secure_auth(self) -> None:
        channel_a, channel_b = create_channel_pair()
        await channel_a.open()
        await channel_b.open()
        runner, _ = make_runner(channel_a)
        await runner.open()
        sa = SaPeer(FakeOutstation(channel_b))

        async def challenge() -> None:
            [request] = await sa.peer.read_fragments(1)
            await sa.peer.send_fragment(sa.challenge(request[0] & 0x0F))

        serve = asyncio.create_task(challenge())
        with pytest.raises(AuthenticationError, match=r"MasterConfig\.secure_auth"):
            await runner.clear_restart()
        await serve

    async def test_malformed_challenge(self) -> None:
        runner, _, sa = await open_sa_runner()

        async def challenge() -> None:
            [request] = await sa.peer.read_fragments(1)
            await sa.peer.send_fragment(auth_response(request[0] & 0x0F, g120(1, b"\x01\x00")))

        serve = asyncio.create_task(challenge())
        with pytest.raises(AuthenticationError, match="Malformed authentication response"):
            await runner.clear_restart()
        await serve

        assert runner._session_keys is not None

    async def test_unsupported_mac_algorithm(self) -> None:
        runner, _, sa = await open_sa_runner()

        async def challenge() -> None:
            [request] = await sa.peer.read_fragments(1)
            await sa.peer.send_fragment(sa.challenge(request[0] & 0x0F, mac_algorithm=1))

        serve = asyncio.create_task(challenge())
        with pytest.raises(AuthenticationError, match="Unsupported MAC algorithm 1"):
            await runner.clear_restart()
        await serve

        # The fault is the master's own finding, not a sign the outstation lost the keys: no key change follows.
        serve_next = asyncio.create_task(sa.serve_challenged())
        await runner.clear_restart()
        assert (await serve_next)[1] == FunctionCode.WRITE

    async def test_unsolicited_during_challenge(self) -> None:
        runner, handler, sa = await open_sa_runner()

        async def serve() -> None:
            [request] = await sa.peer.read_fragments(1)
            seq = request[0] & 0x0F
            await sa.peer.send_fragment(sa.challenge(seq))
            await sa.peer.read_fragments(1)
            await sa.peer.send_fragment(unsolicited_response(seq=0, index=4, value=1.5))
            [confirm] = await sa.peer.read_fragments(1)
            assert confirm[1] == FunctionCode.CONFIRM
            await sa.peer.send_fragment(build_null_response(seq=seq).to_bytes())

        task = asyncio.create_task(serve())
        await runner.clear_restart()
        await task

        assert handler.analog_inputs == {4: 1.5}


class TestKeyRenewal:
    async def test_error_drops_keys_and_next_request_rekeys(self) -> None:
        runner, _, sa = await open_sa_runner()
        old_control = sa.control
        serve = asyncio.create_task(sa.serve_error(1))

        with pytest.raises(AuthenticationError, match="AUTHENTICATION_FAILED") as excinfo:
            await runner.clear_restart()
        await serve
        assert excinfo.value.code == 1

        async def rekey_then_answer() -> bytes:
            await sa.serve_key_change()
            return await sa.serve_challenged()

        serve_next = asyncio.create_task(rekey_then_answer())
        await runner.clear_restart()
        request = await serve_next

        assert request[1] == FunctionCode.WRITE
        assert sa.control != old_control

    async def test_second_challenge_drops_keys(self) -> None:
        """A rejected challenge reply renews the keys, like a g120v7 report."""
        runner, _, sa = await open_sa_runner()

        async def challenge_twice() -> None:
            [request] = await sa.peer.read_fragments(1)
            seq = request[0] & 0x0F
            await sa.peer.send_fragment(sa.challenge(seq))
            await sa.peer.read_fragments(1)
            await sa.peer.send_fragment(sa.challenge(seq))

        serve = asyncio.create_task(challenge_twice())
        with pytest.raises(AuthenticationError, match="rejected the challenge reply"):
            await runner.clear_restart()
        await serve

        async def rekey_then_answer() -> bytes:
            await sa.serve_key_change()
            return await sa.serve_challenged()

        serve_next = asyncio.create_task(rekey_then_answer())
        await runner.clear_restart()

        assert (await serve_next)[1] == FunctionCode.WRITE

    async def test_keys_renewed_after_interval(self) -> None:
        runner, _, sa = await open_sa_runner(secure_auth_options={"session_key_change_interval": 0.05})
        old_control = sa.control
        await asyncio.sleep(0.1)

        async def rekey_then_answer() -> tuple[int, bytes]:
            change_seq = await sa.serve_key_change()
            return change_seq, await sa.serve_challenged()

        serve = asyncio.create_task(rekey_then_answer())
        await runner.clear_restart()
        change_seq, request = await serve

        assert sa.control != old_control
        assert request[0] & 0x0F == (change_seq + 1) % 16  # numbered after the key change

    async def test_select_and_operate_renews_keys_before_select(self) -> None:
        """Keys due at a select-before-operate change before the SELECT; the OPERATE still follows it directly."""
        runner, _, sa = await open_sa_runner(secure_auth_options={"session_key_change_interval": 0.5})
        await asyncio.sleep(0.6)
        echo_ok = echo_reply(CommandStatus.SUCCESS)

        async def rekey_then_answer() -> tuple[int, list[bytes]]:
            change_seq = await sa.serve_key_change()
            replies = {FunctionCode.SELECT: echo_ok, FunctionCode.OPERATE: echo_ok}
            return change_seq, await answer_requests(sa.peer, 2, replies)

        serve = asyncio.create_task(rekey_then_answer())
        result = await runner.select_and_operate(SelectTask(operations=crobs(0)))
        change_seq, (select, operate) = await serve

        assert [select[1], operate[1]] == [FunctionCode.SELECT, FunctionCode.OPERATE]
        assert select[0] & 0x0F == (change_seq + 1) % 16
        assert operate[0] & 0x0F == ((select[0] & 0x0F) + 1) % 16
        assert result.is_success

    async def test_select_not_renumbered_when_keys_fall_due_again(self) -> None:
        """A key change slower than the interval must not rekey the SELECT away from its OPERATE."""
        runner, _, sa = await open_sa_runner(secure_auth_options={"session_key_change_interval": 0.02})
        await asyncio.sleep(0.05)
        send = sa.peer.send_fragment

        async def slow_send(data: bytes) -> None:
            await asyncio.sleep(0.03)
            await send(data)

        sa.peer.send_fragment = slow_send  # type: ignore[method-assign]
        echo_ok = echo_reply(CommandStatus.SUCCESS)

        async def serve() -> list[bytes]:
            await sa.serve_key_change()
            return await answer_requests(sa.peer, 2, {FunctionCode.SELECT: echo_ok, FunctionCode.OPERATE: echo_ok})

        task = asyncio.create_task(serve())
        result = await runner.select_and_operate(SelectTask(operations=crobs(0)))
        select, operate = await task

        assert operate[0] & 0x0F == ((select[0] & 0x0F) + 1) % 16
        assert result.is_success
        await assert_nothing_sent(sa.peer)

    @pytest.mark.parametrize("method", [TimeSyncMethod.LAN, TimeSyncMethod.NON_LAN])
    async def test_time_sync_reads_clock_after_key_renewal(self, method: TimeSyncMethod) -> None:
        """The clock is read once the keys are renewed, not before the key change's round trips."""
        runner, _, sa = await open_sa_runner(secure_auth_options={"session_key_change_interval": 0.05})
        old_keys = runner._session_keys
        await asyncio.sleep(0.1)
        keys_at_clock_read: list[object] = []

        def clock() -> int:
            keys_at_clock_read.append(runner._session_keys)
            return 1_000

        runner.wall_clock_ms = clock
        sync = asyncio.create_task(runner.time_sync(method))
        await sa.serve_key_change()
        await asyncio.sleep(0.02)
        await cancelled(sync)

        assert keys_at_clock_read
        assert keys_at_clock_read[0] is not None and keys_at_clock_read[0] is not old_keys

    async def test_keys_not_renewed_before_interval(self) -> None:
        runner, _, sa = await open_sa_runner()
        serve = asyncio.create_task(sa.serve_challenged())

        await runner.clear_restart()

        assert (await serve)[1] == FunctionCode.WRITE

    async def test_run_polls_stops_on_session_key_error(self) -> None:
        """A rekey the outstation refuses ends `run_polls()` rather than retrying every poll."""
        runner, _, sa = await open_sa_runner(secure_auth_options={"session_key_change_interval": 0.05})
        runner.master.scheduler.clear()
        runner.master.scheduler.add_task(IntegrityPollTask(interval=3600.0))
        sa.update_key = bytes(16)
        await asyncio.sleep(0.1)
        serve = asyncio.create_task(sa.serve_key_change())

        with pytest.raises(SessionKeyError):
            await asyncio.wait_for(runner.run_polls(), timeout=5.0)
        await serve

    async def test_challenge_before_session_keys(self) -> None:
        """An OPERATE never waits for key setup, so a challenge may find the runner without keys."""
        runner, _, sa = await open_sa_runner()
        runner._session_keys = None
        operate = runner.master.build_operate(OperateTask(operations=crobs(0)))

        async def challenge() -> None:
            [request] = await sa.peer.read_fragments(1)
            await sa.peer.send_fragment(sa.challenge(request[0] & 0x0F))

        serve = asyncio.create_task(challenge())
        with pytest.raises(AuthenticationError, match="before session keys"):
            await runner.request(operate)
        await serve

    async def test_react_stops_on_session_key_error(self) -> None:
        runner, _, sa = await open_sa_runner(secure_auth_options={"session_key_change_interval": 0.05})
        sa.update_key = bytes(16)
        await asyncio.sleep(0.1)
        serve = asyncio.create_task(sa.serve_key_change())

        with pytest.raises(SessionKeyError):
            await runner._react(IINAction.CLEAR_RESTART)
        await serve


class TestFileTransfer:
    async def test_challenged_open_completes(self) -> None:
        """An outstation may treat OPEN_FILE as critical; the upload continues once the master answers."""
        runner, _, sa = await open_sa_runner()
        upload = asyncio.create_task(runner.upload_file("cfg.txt", b""))

        [request] = await sa.peer.read_fragments(1)
        assert request[1] == FunctionCode.OPEN_FILE
        await sa.peer.send_fragment(sa.challenge(request[0] & 0x0F))
        [reply] = await sa.peer.read_fragments(1)
        assert reply[1] == FunctionCode.AUTHENTICATE_REQUEST
        await sa.peer.send_fragment(status_reply()(request))
        [close] = await sa.peer.read_fragments(1)
        assert close[1] == FunctionCode.CLOSE_FILE
        await sa.peer.send_fragment(status_reply(max_block=0)(close))

        await upload
