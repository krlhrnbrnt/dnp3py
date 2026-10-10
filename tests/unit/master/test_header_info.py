"""Each delivered value carries the object header it was decoded from.

Mirrors opendnp3's HeaderInfo: group, variation, qualifier, the header's position
in the fragment, whether the variation is an event, and whether flags were on the
wire rather than assumed.
"""

import dataclasses

import pytest

from dnp3.core.flags import DoubleBitState
from dnp3.master import DoubleBitValue, HeaderInfo, Master, OctetStringValue
from dnp3.master.handler import AnalogValue, BinaryValue, CounterValue, ResponseInfo
from tests.unit.master.delivery import RecordingHandler

# Response header: app control (FIR+FIN, seq 1), RESPONSE function, 2-byte IIN.
RESPONSE_HEADER = bytes([0xC1, 0x81, 0x00, 0x00])

# Qualifier 0x00: 1-octet start and stop indices. 0x17: 1-octet count, 1-octet index prefix.
RANGE_8 = 0x00
COUNT_8_INDEX_8 = 0x17
# Qualifier 0x07: 1-octet count, no index prefix.
COUNT_8 = 0x07

ONLINE = 0x01

Value = BinaryValue | AnalogValue | CounterValue | DoubleBitValue | OctetStringValue


class ValueCollector(RecordingHandler):
    """Every delivered value from every callback, in delivery order."""

    def __init__(self) -> None:
        super().__init__()
        self.values: list[Value] = []

    def on_binary_input(self, values: list[BinaryValue], info: ResponseInfo) -> None:
        self.values.extend(values)

    def on_counter(self, values: list[CounterValue], info: ResponseInfo) -> None:
        self.values.extend(values)

    def on_double_bit_input(self, values: list[DoubleBitValue], info: ResponseInfo) -> None:
        self.values.extend(values)

    def on_octet_string(self, values: list[OctetStringValue], info: ResponseInfo) -> None:
        self.values.extend(values)


def _headers(body: bytes) -> list[HeaderInfo | None]:
    handler = ValueCollector()
    assert Master(handler=handler).process_response(RESPONSE_HEADER + body) is not None
    return [value.header for value in handler.values]


def _range(group: int, variation: int, start: int, stop: int, objects: bytes) -> bytes:
    return bytes([group, variation, RANGE_8, start, stop]) + objects


def _event(group: int, variation: int, index: int, octets: bytes) -> bytes:
    return bytes([group, variation, COUNT_8_INDEX_8, 1, index]) + octets


class TestHeaderInfoField:
    def test_header_defaults_to_none(self) -> None:
        assert BinaryValue(index=0, value=True).header is None
        assert AnalogValue(index=0, value=1.0).header is None
        assert CounterValue(index=0, value=1).header is None
        assert DoubleBitValue(index=0, state=DoubleBitState.OFF).header is None
        assert OctetStringValue(index=0, value=b"").header is None

    def test_header_is_left_out_of_equality(self) -> None:
        header = HeaderInfo(group=1, variation=2, qualifier=RANGE_8, header_index=0, is_event=False, flags_valid=True)
        assert BinaryValue(index=0, value=True, header=header) == BinaryValue(index=0, value=True)

    def test_header_info_is_frozen(self) -> None:
        header = HeaderInfo(group=1, variation=2, qualifier=RANGE_8, header_index=0, is_event=False, flags_valid=True)
        with pytest.raises(dataclasses.FrozenInstanceError):
            header.group = 2  # type: ignore[misc]


class TestHeaderInfoDelivery:
    def test_event_and_static_blocks_marked(self) -> None:
        body = _event(2, 1, 3, bytes([ONLINE])) + _range(1, 2, 0, 1, bytes([ONLINE, ONLINE]))

        assert _headers(body) == [
            HeaderInfo(
                group=2, variation=1, qualifier=COUNT_8_INDEX_8, header_index=0, is_event=True, flags_valid=True
            ),
            HeaderInfo(group=1, variation=2, qualifier=RANGE_8, header_index=1, is_event=False, flags_valid=True),
            HeaderInfo(group=1, variation=2, qualifier=RANGE_8, header_index=1, is_event=False, flags_valid=True),
        ]

    def test_packed_binary_flags_not_valid(self) -> None:
        # A.2.1: g1v1 packs state bits and carries no flags; ONLINE is assumed.
        headers = _headers(_range(1, 1, 0, 0, bytes([0x01])))

        assert headers == [
            HeaderInfo(group=1, variation=1, qualifier=RANGE_8, header_index=0, is_event=False, flags_valid=False)
        ]

    @pytest.mark.parametrize(
        ("variation", "objects", "flags_valid"),
        [
            (1, bytes([ONLINE]) + (7).to_bytes(4, "little"), True),
            (5, (7).to_bytes(4, "little"), False),
        ],
    )
    def test_counter_flags_follow_variation(self, variation: int, objects: bytes, flags_valid: bool) -> None:
        (header,) = _headers(_range(20, variation, 0, 0, objects))

        assert header is not None
        assert header.flags_valid is flags_valid

    def test_header_index_counts_undelivered_blocks(self) -> None:
        # g52v2 (time delay fine) is parsed but not delivered; it still takes position 0.
        body = bytes([52, 2, COUNT_8, 1]) + (100).to_bytes(2, "little") + _range(1, 2, 0, 0, bytes([ONLINE]))

        (header,) = _headers(body)

        assert header is not None
        assert header.header_index == 1

    def test_double_bit_blocks_marked(self) -> None:
        body = _event(4, 1, 5, bytes([ONLINE])) + _range(3, 1, 0, 0, bytes([0x01]))

        assert _headers(body) == [
            HeaderInfo(
                group=4, variation=1, qualifier=COUNT_8_INDEX_8, header_index=0, is_event=True, flags_valid=True
            ),
            HeaderInfo(group=3, variation=1, qualifier=RANGE_8, header_index=1, is_event=False, flags_valid=False),
        ]

    def test_octet_string_blocks_marked(self) -> None:
        # Annex A g110 and g111 carry no flags.
        body = _event(111, 3, 2, b"abc") + _range(110, 3, 0, 0, b"xyz")

        assert _headers(body) == [
            HeaderInfo(
                group=111, variation=3, qualifier=COUNT_8_INDEX_8, header_index=0, is_event=True, flags_valid=False
            ),
            HeaderInfo(group=110, variation=3, qualifier=RANGE_8, header_index=1, is_event=False, flags_valid=False),
        ]
