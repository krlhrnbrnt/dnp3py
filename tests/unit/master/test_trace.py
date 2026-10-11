"""Unit tests for the master's protocol trace formatters."""

from __future__ import annotations

from dnp3.application.builder import build_response
from dnp3.application.fragment import ObjectBlock, RequestFragment
from dnp3.application.header import ApplicationControl, RequestHeader
from dnp3.application.qualifiers import ObjectHeader
from dnp3.core.enums import FunctionCode
from dnp3.core.flags import IIN
from dnp3.datalink.builder import build_ack, build_reset_link_state
from dnp3.datalink.control import ControlByte
from dnp3.datalink.frame import DataLinkFrame, DataLinkHeader
from dnp3.master.trace import (
    app_logger,
    describe_iin,
    describe_link_frame,
    describe_object_block,
    describe_request,
    describe_response,
    describe_segment,
    link_logger,
    transport_logger,
)
from dnp3.transport.segment import TransportSegment


class TestLoggers:
    def test_trace_loggers_are_children_of_the_runner_logger(self) -> None:
        assert link_logger.name == "dnp3.master.runner.link"
        assert transport_logger.name == "dnp3.master.runner.transport"
        assert app_logger.name == "dnp3.master.runner.app"


class TestLinkFrame:
    def test_primary_frame_from_master(self) -> None:
        frame = build_reset_link_state(destination=1, source=3, dir_from_master=True)
        text = describe_link_frame(frame)
        assert text.startswith("RESET_LINK_STATE 3->1 DIR PRM len=5 | 05 64 05 c0 01 00 03 00 ")
        assert text.endswith(frame.to_bytes().hex(" "))

    def test_secondary_frame_uses_secondary_name(self) -> None:
        # ACK shares value 0 with RESET_LINK_STATE; PRM picks the name.
        assert describe_link_frame(build_ack(3, 1, False)).startswith("ACK 1->3 len=5 | ")

    def test_secondary_frame_shows_dfc(self) -> None:
        # DFC is bit 4 of a secondary control octet (IEEE 1815-2012 9.2.4.1.3.4), the FCV position.
        control = ControlByte(dir_from_master=False, prm=False, fcb=False, fcv=True, function_code=0)
        frame = DataLinkFrame(header=DataLinkHeader(length=5, control=control, destination=3, source=1), user_data=b"")
        assert describe_link_frame(frame).startswith("ACK 1->3 DFC len=5 | 05 64 05 10 ")

    def test_primary_frame_shows_fcb_fcv(self) -> None:
        control = ControlByte(dir_from_master=True, prm=True, fcb=True, fcv=True, function_code=3)
        frame = DataLinkFrame(header=DataLinkHeader(length=5, control=control, destination=1, source=3), user_data=b"")
        assert describe_link_frame(frame).startswith("CONFIRMED_USER_DATA 3->1 DIR PRM FCB FCV len=5 | ")

    def test_unknown_function_code(self) -> None:
        control = ControlByte(dir_from_master=False, prm=True, fcb=False, fcv=False, function_code=7)
        frame = DataLinkFrame(header=DataLinkHeader(length=5, control=control, destination=3, source=1), user_data=b"")
        assert describe_link_frame(frame).startswith("FUNC_7 1->3 PRM len=5 | ")


class TestSegment:
    def test_first_segment(self) -> None:
        segment = TransportSegment.build(fir=True, fin=False, seq=5, payload=b"\x01\x02")
        assert describe_segment(segment) == "segment FIR seq=5 2 bytes"

    def test_only_segment(self) -> None:
        segment = TransportSegment.build(fir=True, fin=True, seq=0, payload=b"\x01")
        assert describe_segment(segment) == "segment FIR FIN seq=0 1 bytes"


class TestIin:
    def test_no_bits(self) -> None:
        assert describe_iin(IIN(0)) == "none (IIN1=0x00 IIN2=0x00)"

    def test_bits_in_both_octets(self) -> None:
        iin = IIN.DEVICE_RESTART | IIN.OBJECT_UNKNOWN
        assert describe_iin(iin) == "DEVICE_RESTART|OBJECT_UNKNOWN (IIN1=0x80 IIN2=0x02)"


class TestObjectBlock:
    def test_start_stop_range(self) -> None:
        block = ObjectBlock(header=ObjectHeader(1, 2, 0x00), data=bytes([0, 9]) + bytes(10))
        assert describe_object_block(block) == "g1v2 qual=0x00 range 0-9 (10 bytes)"

    def test_all_objects(self) -> None:
        assert describe_object_block(ObjectBlock(header=ObjectHeader(60, 1, 0x06))) == "g60v1 qual=0x06 all"

    def test_count_with_index_prefix(self) -> None:
        block = ObjectBlock(header=ObjectHeader(30, 5, 0x17), data=bytes([1, 4, 0x01]) + bytes(4))
        assert describe_object_block(block) == "g30v5 qual=0x17 count 1 (6 bytes)"

    def test_reserved_range_code_is_unreadable(self) -> None:
        block = ObjectBlock(header=ObjectHeader(1, 2, 0x0F), data=b"\x00")
        assert describe_object_block(block) == "g1v2 qual=0x0f range unreadable"

    def test_short_range_is_unreadable(self) -> None:
        block = ObjectBlock(header=ObjectHeader(1, 2, 0x01), data=b"\x00")
        assert describe_object_block(block) == "g1v2 qual=0x01 range unreadable"


class TestRequest:
    def test_read_request(self) -> None:
        request = RequestFragment(
            header=RequestHeader(
                control=ApplicationControl(fir=True, fin=True, con=False, uns=False, seq=3),
                function=FunctionCode.READ,
            ),
            objects=(ObjectBlock(header=ObjectHeader(60, 2, 0x06)), ObjectBlock(header=ObjectHeader(60, 1, 0x06))),
        )
        assert describe_request(request) == (
            "READ seq=3 FIR FIN\n  g60v2 qual=0x06 all\n  g60v1 qual=0x06 all\n  hex: c3 01 3c 02 06 3c 01 06"
        )


def _binary_response(*, seq: int, iin: IIN) -> bytes:
    block = ObjectBlock(header=ObjectHeader(1, 2, 0x00), data=bytes([0, 1, 0x01, 0x81]))
    return build_response(objects=(block,), iin=iin, seq=seq).to_bytes()


class TestResponse:
    def test_solicited_response(self) -> None:
        data = _binary_response(seq=3, iin=IIN.NEED_TIME)
        lines = describe_response(data, 182.4).split("\n")
        assert lines[0] == "RESPONSE seq=3 FIR FIN 182 ms after request IIN=NEED_TIME (IIN1=0x10 IIN2=0x00)"
        assert lines[1] == "  g1v2 qual=0x00 range 0-1 (2 bytes)"
        assert lines[-1] == f"  hex: {data.hex(' ')}"

    def test_unsolicited_omits_latency(self) -> None:
        data = bytearray(_binary_response(seq=3, iin=IIN(0)))
        data[0] |= 0x10  # UNS
        data[1] = FunctionCode.UNSOLICITED_RESPONSE.value
        first = describe_response(bytes(data), 50.0).split("\n")[0]
        assert first == "UNSOLICITED_RESPONSE seq=3 FIR FIN UNS IIN=none (IIN1=0x00 IIN2=0x00)"

    def test_no_latency_given(self) -> None:
        first = describe_response(_binary_response(seq=1, iin=IIN(0)), None).split("\n")[0]
        assert "ms after request" not in first

    def test_unknown_function_code_traces_as_unparseable(self) -> None:
        lines = describe_response(b"\xc0\xff\x00\x00", 5.0).split("\n")
        assert lines[0] == "unparseable response header"
        assert lines[1].startswith("  unparseable: ")
        assert lines[-1] == "  hex: c0 ff 00 00"

    def test_too_short_for_a_header(self) -> None:
        lines = describe_response(b"\xff", None).split("\n")
        assert lines[0] == "unparseable response header"
        assert lines[-1] == "  hex: ff"
