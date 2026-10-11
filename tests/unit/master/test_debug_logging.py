"""The runner's debug trace and failure messages, read by people diagnosing a failed test."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import pytest

from dnp3.datalink.builder import build_unconfirmed_user_data
from dnp3.master.runner import LinkResetPolicy
from dnp3.master.tcp_runner import MasterTcpRunner, ResponseTimeoutError
from dnp3.transport.segment import TransportSegment
from dnp3.transport_io.simulator import create_channel_pair
from tests.unit.master.test_tcp_runner import (
    MASTER_ADDR,
    OUTSTATION_ADDR,
    FakeOutstation,
    RecordingHandler,
    analog_response,
    make_runner,
)

TRACE = "dnp3.master.runner"
LINK = "dnp3.master.runner.link"
TRANSPORT = "dnp3.master.runner.transport"
APP = "dnp3.master.runner.app"


async def open_pair(**options: Any) -> tuple[MasterTcpRunner, RecordingHandler, FakeOutstation]:
    """An open runner and the fake outstation on the other end of its channel."""
    channel_a, channel_b = create_channel_pair()
    await channel_a.open()
    await channel_b.open()
    runner, handler = make_runner(channel_a, **options)
    await runner.open()
    return runner, handler, FakeOutstation(channel_b)


def frame_segment(
    segment: TransportSegment,
    *,
    destination: int = MASTER_ADDR,
    source: int = OUTSTATION_ADDR,
    dir_from_master: bool = False,
) -> bytes:
    return build_unconfirmed_user_data(
        destination=destination,
        source=source,
        dir_from_master=dir_from_master,
        user_data=segment.to_bytes(),
    ).to_bytes()


def frame_app(app: bytes, **addressing: Any) -> bytes:
    """One application fragment as a single segment in one frame."""
    return frame_segment(TransportSegment.build(fir=True, fin=True, seq=0, payload=app), **addressing)


def messages(caplog: pytest.LogCaptureFixture, logger: str) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == logger]


async def answered_poll(runner: MasterTcpRunner, peer: FakeOutstation, *, con: bool = False) -> None:
    """One integrity poll answered by a single-fragment response."""

    async def respond() -> None:
        seq = await peer.read_request_seq()
        await peer.send_fragment(analog_response(seq=seq, fir=True, fin=True, con=con, index=4, value=1.0))
        if con:
            await peer.read_fragments(1)

    responder = asyncio.create_task(respond())
    await runner.integrity_poll()
    await responder


class TestLinkTrace:
    async def test_frames_in_both_directions(self, caplog: pytest.LogCaptureFixture) -> None:
        runner, _, peer = await open_pair()
        with caplog.at_level(logging.DEBUG, logger=TRACE):
            await answered_poll(runner, peer)

        link = messages(caplog, LINK)
        assert any(m.startswith("TX UNCONFIRMED_USER_DATA 3->1 DIR PRM len=") and "| 05 64 " in m for m in link)
        assert any(m.startswith("RX UNCONFIRMED_USER_DATA 1->3 PRM len=") and "| 05 64 " in m for m in link)

    async def test_link_reset_and_ack(self, caplog: pytest.LogCaptureFixture) -> None:
        channel_a, channel_b = create_channel_pair()
        await channel_a.open()
        await channel_b.open()
        runner, _ = make_runner(channel_a, link_reset=LinkResetPolicy.ON_OPEN)
        peer = FakeOutstation(channel_b)
        with caplog.at_level(logging.DEBUG, logger=TRACE):
            await runner.open()
            await peer.read_frames(1)
            await peer.send_ack()
            await answered_poll(runner, peer)

        link = messages(caplog, LINK)
        assert any(m.startswith("TX RESET_LINK_STATE 3->1 DIR PRM len=5 | ") for m in link)
        assert any(m.startswith("RX ACK 1->3 len=5 | ") for m in link)

    async def test_skipped_frames_say_why(self, caplog: pytest.LogCaptureFixture) -> None:
        runner, _, peer = await open_pair()

        async def respond() -> None:
            seq = await peer.read_request_seq()
            answer = analog_response(seq=seq, fir=True, fin=True, con=False, index=0, value=1.0)
            await peer.send_raw(frame_app(answer, destination=9))
            await peer.send_raw(frame_app(answer, source=7))
            await peer.send_raw(frame_app(answer, dir_from_master=True))
            await peer.send_raw(frame_app(answer))

        with caplog.at_level(logging.DEBUG, logger=TRACE):
            responder = asyncio.create_task(respond())
            await runner.integrity_poll()
            await responder

        link = messages(caplog, LINK)
        assert "RX skipped: addressed to 9, not this master (3)" in link
        assert "RX skipped: from 7, expected outstation 1" in link
        assert "RX skipped: DIR set (frame from a master)" in link


class TestTransportTrace:
    async def test_tx_segment(self, caplog: pytest.LogCaptureFixture) -> None:
        runner, _, peer = await open_pair()
        with caplog.at_level(logging.DEBUG, logger=TRACE):
            await answered_poll(runner, peer)

        transport = messages(caplog, TRANSPORT)
        assert any(m.startswith("TX segment FIR FIN seq=") for m in transport)

    async def test_multi_segment_fragment(self, caplog: pytest.LogCaptureFixture) -> None:
        runner, _, peer = await open_pair()

        async def respond() -> None:
            seq = await peer.read_request_seq()
            answer = analog_response(seq=seq, fir=True, fin=True, con=False, index=0, value=1.0)
            first = TransportSegment.build(fir=True, fin=False, seq=0, payload=answer[:4])
            last = TransportSegment.build(fir=False, fin=True, seq=1, payload=answer[4:])
            await peer.send_raw(frame_segment(first))
            await peer.send_raw(frame_segment(last))

        with caplog.at_level(logging.DEBUG, logger=TRACE):
            responder = asyncio.create_task(respond())
            await runner.integrity_poll()
            await responder

        transport = messages(caplog, TRANSPORT)
        answer_length = len(analog_response(seq=0, fir=True, fin=True, con=False, index=0, value=1.0))
        assert "RX segment FIR seq=0 4 bytes" in transport
        assert f"RX segment FIN seq=1 {answer_length - 4} bytes" in transport
        assert f"RX fragment complete: 2 segments, {answer_length} bytes" in transport


class TestAppTrace:
    async def test_request_record(self, caplog: pytest.LogCaptureFixture) -> None:
        runner, _, peer = await open_pair()
        with caplog.at_level(logging.DEBUG, logger=TRACE):
            await answered_poll(runner, peer)

        request = next(m for m in messages(caplog, APP) if m.startswith("TX READ"))
        lines = request.split("\n")
        assert re.fullmatch(r"TX READ seq=\d+ FIR FIN", lines[0])
        assert "  g60v1 qual=0x06 all" in lines
        assert lines[-1].startswith("  hex: ")

    async def test_confirm_is_traced(self, caplog: pytest.LogCaptureFixture) -> None:
        runner, _, peer = await open_pair()
        with caplog.at_level(logging.DEBUG, logger=TRACE):
            await answered_poll(runner, peer, con=True)

        assert any(re.match(r"TX CONFIRM seq=\d+ FIR FIN", m) for m in messages(caplog, APP))

    async def test_response_record(self, caplog: pytest.LogCaptureFixture) -> None:
        runner, _, peer = await open_pair()
        with caplog.at_level(logging.DEBUG, logger=TRACE):
            await answered_poll(runner, peer)

        response = next(m for m in messages(caplog, APP) if m.startswith("RX "))
        lines = response.split("\n")
        assert re.fullmatch(
            r"RX RESPONSE seq=\d+ FIR FIN \d+ ms after request IIN=none \(IIN1=0x00 IIN2=0x00\)", lines[0]
        )
        assert lines[1] == "  g30v5 qual=0x17 count 1 (6 bytes)"
        assert lines[-1].startswith("  hex: ")

    async def test_unsolicited_has_no_latency(self, caplog: pytest.LogCaptureFixture) -> None:
        from tests.unit.master.test_tcp_runner import unsolicited_response

        runner, _, peer = await open_pair()

        async def send() -> None:
            await peer.send_fragment(unsolicited_response(seq=2, index=1, value=3.0))
            await peer.read_fragments(1)  # the CONFIRM

        with caplog.at_level(logging.DEBUG, logger=TRACE):
            sender = asyncio.create_task(send())
            info = await runner.listen_unsolicited(timeout=2.0)
            await sender

        assert info is not None
        record = next(m for m in messages(caplog, APP) if m.startswith("RX UNSOLICITED_RESPONSE"))
        assert "ms after request" not in record

    async def test_dropped_fragment_is_traced_before_its_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        runner, _, peer = await open_pair()

        async def respond() -> None:
            seq = await peer.read_request_seq()
            late = (seq - 1) % 16
            await peer.send_fragment(analog_response(seq=late, fir=True, fin=True, con=False, index=0, value=1.0))
            await peer.send_fragment(analog_response(seq=seq, fir=True, fin=True, con=False, index=0, value=2.0))

        with caplog.at_level(logging.DEBUG, logger=TRACE):
            responder = asyncio.create_task(respond())
            await runner.integrity_poll()
            await responder

        names_and_messages = [(r.name, r.getMessage()) for r in caplog.records]
        warning_at = next(i for i, (_, m) in enumerate(names_and_messages) if "Dropping response fragment" in m)
        traced_at = next(i for i, (name, m) in enumerate(names_and_messages) if name == APP and m.startswith("RX "))
        assert traced_at < warning_at

    async def test_formatters_not_called_at_info(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dnp3.master import trace

        def refuse(*_args: object) -> str:
            raise AssertionError("formatter ran with DEBUG off")

        for name in ("describe_link_frame", "describe_segment", "describe_request", "describe_response"):
            monkeypatch.setattr(trace, name, refuse)

        runner, handler, peer = await open_pair()
        with caplog.at_level(logging.INFO):
            await answered_poll(runner, peer, con=True)

        assert handler.analog_inputs[4] == pytest.approx(1.0)
        assert not [r for r in caplog.records if r.name in (LINK, TRANSPORT, APP)]


class TestTimeoutMessage:
    @staticmethod
    async def _poll_expecting_timeout(runner: MasterTcpRunner, respond: Any) -> str:
        responder = asyncio.create_task(respond())
        with pytest.raises(ResponseTimeoutError) as caught:
            await runner.integrity_poll()
        await responder
        return str(caught.value)

    async def test_silent_outstation(self) -> None:
        runner, _, _ = await open_pair(response_timeout=0.2)
        with pytest.raises(ResponseTimeoutError) as caught:
            await runner.integrity_poll()
        assert re.fullmatch(
            r"No response to READ seq=\d+ within 0\.2 s: no frame received since the request", str(caught.value)
        )

    async def test_stalled_mid_fragment(self) -> None:
        runner, _, peer = await open_pair(response_timeout=0.3)

        async def respond() -> None:
            seq = await peer.read_request_seq()
            answer = analog_response(seq=seq, fir=True, fin=True, con=False, index=0, value=1.0)
            await peer.send_raw(frame_segment(TransportSegment.build(fir=True, fin=False, seq=0, payload=answer[:4])))

        message = await self._poll_expecting_timeout(runner, respond)
        assert re.fullmatch(
            r"No response to READ seq=\d+ within 0\.3 s: last frame \d+\.\d s ago; "
            r"1 segment of an incomplete fragment buffered",
            message,
        )

    async def test_wrong_sequence(self) -> None:
        runner, _, peer = await open_pair(response_timeout=0.3)
        seqs: list[int] = []

        async def respond() -> None:
            seq = await peer.read_request_seq()
            seqs.append(seq)
            await peer.send_fragment(
                analog_response(seq=(seq - 1) % 16, fir=True, fin=True, con=False, index=0, value=1.0)
            )

        message = await self._poll_expecting_timeout(runner, respond)
        seq = seqs[0]
        assert re.fullmatch(
            rf"No response to READ seq={seq} within 0\.3 s: last frame \d+\.\d s ago; "
            rf"dropped 1 fragment with sequence {(seq - 1) % 16}, expected {seq}",
            message,
        )

    async def test_burst_without_fin(self) -> None:
        runner, _, peer = await open_pair(response_timeout=0.3)

        async def respond() -> None:
            seq = await peer.read_request_seq()
            await peer.send_fragment(analog_response(seq=seq, fir=True, fin=False, con=False, index=0, value=1.0))

        message = await self._poll_expecting_timeout(runner, respond)
        assert re.fullmatch(
            r"No response to READ seq=\d+ within 0\.3 s: last frame \d+\.\d s ago; 1 fragment received, none with FIN",
            message,
        )

    async def test_facts_combine(self) -> None:
        runner, _, peer = await open_pair(response_timeout=0.3)

        async def respond() -> None:
            seq = await peer.read_request_seq()
            await peer.send_fragment(analog_response(seq=seq, fir=True, fin=False, con=False, index=0, value=1.0))
            second = analog_response(seq=(seq + 1) % 16, fir=False, fin=True, con=False, index=1, value=2.0)
            await peer.send_raw(frame_segment(TransportSegment.build(fir=True, fin=False, seq=1, payload=second[:4])))

        message = await self._poll_expecting_timeout(runner, respond)
        assert message.endswith("; 1 fragment received, none with FIN; 1 segment of an incomplete fragment buffered")

    async def test_dropped_count_is_per_exchange(self) -> None:
        runner, _, peer = await open_pair(response_timeout=0.3)

        async def respond() -> None:
            seq = await peer.read_request_seq()
            await peer.send_fragment(
                analog_response(seq=(seq - 1) % 16, fir=True, fin=True, con=False, index=0, value=1.0)
            )

        await self._poll_expecting_timeout(runner, respond)
        second = await self._poll_expecting_timeout(runner, respond)
        assert "dropped 1 fragment with sequence" in second


class TestDiscardWarnings:
    async def test_unparseable_fragment_while_awaiting(self, caplog: pytest.LogCaptureFixture) -> None:
        runner, _, peer = await open_pair()

        async def respond() -> None:
            seq = await peer.read_request_seq()
            await peer.send_fragment(b"\xff\xff")
            await peer.send_fragment(analog_response(seq=seq, fir=True, fin=True, con=False, index=0, value=1.0))

        with caplog.at_level(logging.WARNING, logger=TRACE):
            responder = asyncio.create_task(respond())
            await runner.integrity_poll()
            await responder

        warning = next(m for m in messages(caplog, TRACE) if m.startswith("Discarding"))
        assert re.match(r"Discarding 2 bytes that did not parse as a response while awaiting READ seq=\d+: ", warning)
        assert warning.endswith(" | ff ff")

    async def test_unparseable_fragment_while_listening(self, caplog: pytest.LogCaptureFixture) -> None:
        runner, _, peer = await open_pair()
        with caplog.at_level(logging.WARNING, logger=TRACE):
            await peer.send_fragment(b"\xff\xff")
            await runner.listen_unsolicited(timeout=0.3)

        warning = next(m for m in messages(caplog, TRACE) if m.startswith("Discarding"))
        assert "while listening for unsolicited responses: " in warning

    async def test_wrong_sequence_names_function_and_iin(self, caplog: pytest.LogCaptureFixture) -> None:
        runner, _, peer = await open_pair()

        async def respond() -> None:
            seq = await peer.read_request_seq()
            await peer.send_fragment(
                analog_response(seq=(seq - 1) % 16, fir=True, fin=True, con=False, index=0, value=1.0)
            )
            await peer.send_fragment(analog_response(seq=seq, fir=True, fin=True, con=False, index=0, value=1.0))

        with caplog.at_level(logging.WARNING, logger=TRACE):
            responder = asyncio.create_task(respond())
            await runner.integrity_poll()
            await responder

        warning = next(m for m in messages(caplog, TRACE) if "Dropping response fragment" in m)
        assert warning.endswith("; RESPONSE, IIN=none (IIN1=0x00 IIN2=0x00)")

    async def test_solicited_while_listening_names_function_and_iin(self, caplog: pytest.LogCaptureFixture) -> None:
        runner, _, peer = await open_pair()
        with caplog.at_level(logging.WARNING, logger=TRACE):
            await peer.send_fragment(analog_response(seq=0, fir=True, fin=True, con=False, index=0, value=1.0))
            await runner.listen_unsolicited(timeout=0.3)

        warning = next(m for m in messages(caplog, TRACE) if "Dropping solicited response fragment" in m)
        assert warning.endswith("; RESPONSE, IIN=none (IIN1=0x00 IIN2=0x00)")
