"""Tests for octet string (g110, g111) delivery to the master handler.

Object bytes follow IEEE 1815-2012 Annex A: the variation is the string length,
and a string is that many raw octets with no flag or time field.
"""

import dataclasses
import struct

import pytest
from hypothesis import given
from hypothesis import strategies as st

import dnp3.master
from dnp3.application.fragment import ObjectBlock, TruncationReason
from dnp3.application.qualifiers import ObjectHeader
from dnp3.core.enums import FunctionCode
from dnp3.core.flags import IIN
from dnp3.master.handler import AnalogValue, BinaryValue, DefaultSOEHandler, ResponseInfo
from dnp3.master.master import Master
from dnp3.master.octet_string import OctetStringHandler, OctetStringValue
from tests.unit.master.delivery import RecordingHandler

# Response header: app control (FIR+FIN, seq 1), RESPONSE function, 2-byte IIN.
RESPONSE_HEADER = bytes([0xC1, 0x81, 0x00, 0x00])
FLAGS_ON = 0x81


class OctetStringRecorder(RecordingHandler):
    """RecordingHandler that also records octet strings as (index, value), in call order."""

    def __init__(self) -> None:
        super().__init__()
        self.octet_strings: list[tuple[int, bytes]] = []

    def on_octet_string(self, values: list[OctetStringValue], info: ResponseInfo) -> None:
        self.octet_strings.extend((v.index, v.value) for v in values)


def _process(body: bytes) -> tuple[OctetStringRecorder, ResponseInfo]:
    handler = OctetStringRecorder()
    info = Master(handler=handler).process_response(RESPONSE_HEADER + body)
    assert info is not None
    return handler, info


class TestOctetStringValue:
    def test_fields_are_index_value_and_header(self) -> None:
        """No quality or timestamp field: g110/g111 carry neither."""
        value = OctetStringValue(index=3, value=b"\x00abc")

        assert [f.name for f in dataclasses.fields(OctetStringValue)] == ["index", "value", "header"]
        assert (value.index, value.value, value.timestamp) == (3, b"\x00abc", None)


class TestOctetStringHandlerProtocol:
    def test_default_handler_is_octet_string_handler(self) -> None:
        assert isinstance(DefaultSOEHandler(), OctetStringHandler)

    def test_exported_from_master_package(self) -> None:
        assert dnp3.master.OctetStringHandler is OctetStringHandler
        assert dnp3.master.OctetStringValue is OctetStringValue
        assert {"OctetStringHandler", "OctetStringValue"} <= set(dnp3.master.__all__)

    def test_handler_without_callback_is_not_octet_string_handler(self) -> None:
        class BinaryOnly:
            def on_binary_input(self, values: list[BinaryValue], info: ResponseInfo) -> None:
                pass

        assert not isinstance(BinaryOnly(), OctetStringHandler)

    def test_default_handler_stores_by_index(self) -> None:
        """Octet strings are stored by index, read back as a copy and cleared."""
        handler = DefaultSOEHandler()
        info = ResponseInfo(function=FunctionCode.RESPONSE, iin=IIN(0), sequence=0)
        values = [OctetStringValue(index=2, value=b"hello"), OctetStringValue(index=9, value=b"x")]

        assert handler.octet_strings == {}
        handler.on_octet_string(values, info)

        assert handler.octet_strings == {2: values[0], 9: values[1]}
        assert handler.get_octet_string(2) == values[0]
        assert handler.get_octet_string(3) is None
        assert handler.last_response is info

        copy = handler.octet_strings
        copy[99] = OctetStringValue(index=99, value=b"z")
        assert 99 not in handler.octet_strings

        handler.clear()

        assert handler.octet_strings == {}


class TestOctetStringDelivery:
    def test_g110_start_stop_delivers_each_string(self) -> None:
        handler, _ = _process(bytes([110, 5, 0x00, 2, 3]) + b"hello" + b"world")

        assert handler.octet_strings == [(2, b"hello"), (3, b"world")]

    def test_g111_uint8_count_uint8_index(self) -> None:
        """Events keep wire order, not index order."""
        handler, _ = _process(bytes([111, 3, 0x17, 2, 7]) + b"sev" + bytes([1]) + b"one")

        assert handler.octet_strings == [(7, b"sev"), (1, b"one")]

    def test_g110_then_g30_both_delivered(self) -> None:
        handler, _ = _process(
            bytes([110, 2, 0x00, 0, 0]) + b"ok" + bytes([30, 2, 0x00, 5, 5, 0x01]) + struct.pack("<h", 123)
        )

        assert handler.octet_strings == [(0, b"ok")]
        assert handler.calls == [("on_analog_input", [AnalogValue(index=5, value=123.0, quality=0x01)])]

    def test_static_and_event_for_same_index_both_delivered_in_wire_order(self) -> None:
        handler, _ = _process(bytes([110, 3, 0x00, 4, 4]) + b"old" + bytes([111, 3, 0x17, 1, 4]) + b"new")

        assert handler.octet_strings == [(4, b"old"), (4, b"new")]

    def test_handler_without_octet_string_callback_is_skipped(self) -> None:
        handler = RecordingHandler()
        body = bytes([110, 3, 0x00, 0, 0]) + b"abc" + bytes([0x01, 0x02, 0x00, 0x02, 0x02, FLAGS_ON])

        Master(handler=handler).process_response(RESPONSE_HEADER + body)

        assert handler.calls == [("on_binary_input", [BinaryValue(index=2, value=True, quality=0x01)])]

    def test_bytearray_data_yields_bytes(self) -> None:
        handler = OctetStringRecorder()
        block = ObjectBlock(header=ObjectHeader(group=110, variation=2, qualifier=0x00), data=bytearray([0, 0]) + b"ab")
        info = ResponseInfo(function=FunctionCode.RESPONSE, iin=IIN(0), sequence=1)

        Master(handler=handler)._parse_response_objects([block], info)

        assert [type(value) for _, value in handler.octet_strings] == [bytes]

    @pytest.mark.parametrize(
        ("body", "reason"),
        [
            (bytes([110, 4, 0x00, 0, 2]) + b"aaaabbbbc", TruncationReason.DATA_SHORTER_THAN_DECLARED),
            (
                bytes([111, 255, 0x28]) + struct.pack("<HH", 0xFFFF, 9) + bytes(range(255)),
                TruncationReason.DATA_SHORTER_THAN_DECLARED,
            ),
            (bytes([110, 2, 0x47, 1, 2]) + b"ab", TruncationReason.SIZE_PREFIX),
            (bytes([110, 0, 0x00, 0, 3]), TruncationReason.UNKNOWN_WIDTH),
        ],
        ids=["short-block", "hostile-count", "size-prefix", "variation-zero"],
    )
    def test_unframed_block_delivers_nothing(self, body: bytes, reason: TruncationReason) -> None:
        """A block that cannot be framed whole delivers no string, not even the ones present."""
        handler, info = _process(body)

        assert handler.octet_strings == []
        assert info.truncation is not None
        assert info.truncation.reason is reason

    @given(data=st.data(), length=st.integers(min_value=1, max_value=255))
    def test_event_block_round_trips(self, data: st.DataObject, length: int) -> None:
        events = data.draw(
            st.lists(
                st.tuples(
                    st.integers(min_value=0, max_value=65535),
                    st.binary(min_size=length, max_size=length),
                ),
                min_size=1,
                max_size=10,
            )
        )
        body = bytes([111, length, 0x28]) + struct.pack("<H", len(events))
        body += b"".join(struct.pack("<H", index) + value for index, value in events)

        handler, _ = _process(body)

        assert handler.octet_strings == events

    @given(
        data=st.data(),
        start=st.integers(min_value=0, max_value=65000),
        length=st.integers(min_value=1, max_value=255),
    )
    def test_static_block_round_trips(self, data: st.DataObject, start: int, length: int) -> None:
        strings = data.draw(st.lists(st.binary(min_size=length, max_size=length), min_size=1, max_size=10))
        body = bytes([110, length, 0x01]) + struct.pack("<HH", start, start + len(strings) - 1) + b"".join(strings)

        handler, _ = _process(body)

        assert handler.octet_strings == list(enumerate(strings, start=start))
