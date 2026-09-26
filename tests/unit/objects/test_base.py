"""Tests for DNP3 object base classes."""

import math
from collections.abc import Callable
from dataclasses import astuple, dataclass
from typing import NamedTuple

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from dnp3.core.flags import AnalogQuality, BinaryQuality, CounterQuality
from dnp3.core.timestamp import DNP3Timestamp
from dnp3.objects import (
    CROB,
    AnalogInput16,
    AnalogInput16NoFlag,
    AnalogInput32,
    AnalogInput32NoFlag,
    AnalogInputDouble,
    AnalogInputEvent16,
    AnalogInputEvent16Time,
    AnalogInputEvent32,
    AnalogInputEvent32Time,
    AnalogInputEventDouble,
    AnalogInputEventDoubleTime,
    AnalogInputEventFloat,
    AnalogInputEventFloatTime,
    AnalogInputFloat,
    BinaryInputEvent,
    BinaryInputEventRelativeTime,
    BinaryInputEventTime,
    BinaryInputFlags,
    BinaryOutputEvent,
    BinaryOutputEventTime,
    BinaryOutputFlags,
    ClassData0,
    ClassData1,
    ClassData2,
    ClassData3,
    CommandStatus,
    Counter16,
    Counter16NoFlag,
    Counter32,
    Counter32NoFlag,
    CounterEvent16,
    CounterEvent16Time,
    CounterEvent32,
    CounterEvent32Time,
    FrozenCounter16,
    FrozenCounter16Time,
    FrozenCounter32,
    FrozenCounter32Time,
    TimeAndDate,
    TimeCTO,
    TimeCTOUnsync,
    TimeDelayCoarse,
    TimeDelayFine,
)
from dnp3.objects.base import (
    QUALITY_SIZE,
    SIZE_1_BYTE,
    SIZE_2_BYTES,
    SIZE_4_BYTES,
    SIZE_5_BYTES,
    SIZE_6_BYTES,
    SIZE_7_BYTES,
    SIZE_8_BYTES,
    SIZE_9_BYTES,
    SIZE_11_BYTES,
    DNP3Object,
    EventObject,
    FixedSizeObject,
    GroupVariation,
    PointValue,
    StaticObject,
    TimestampedValue,
)
from dnp3.objects.registry import get_registry_copy


class TestSizeConstants:
    """Tests for size constants."""

    def test_size_1_byte(self) -> None:
        """SIZE_1_BYTE is 1."""
        assert SIZE_1_BYTE == 1

    def test_size_2_bytes(self) -> None:
        """SIZE_2_BYTES is 2."""
        assert SIZE_2_BYTES == 2

    def test_size_4_bytes(self) -> None:
        """SIZE_4_BYTES is 4."""
        assert SIZE_4_BYTES == 4

    def test_size_5_bytes(self) -> None:
        """SIZE_5_BYTES is 5."""
        assert SIZE_5_BYTES == 5

    def test_size_6_bytes(self) -> None:
        """SIZE_6_BYTES is 6."""
        assert SIZE_6_BYTES == 6

    def test_size_7_bytes(self) -> None:
        """SIZE_7_BYTES is 7."""
        assert SIZE_7_BYTES == 7

    def test_size_8_bytes(self) -> None:
        """SIZE_8_BYTES is 8."""
        assert SIZE_8_BYTES == 8

    def test_size_9_bytes(self) -> None:
        """SIZE_9_BYTES is 9."""
        assert SIZE_9_BYTES == 9

    def test_size_11_bytes(self) -> None:
        """SIZE_11_BYTES is 11."""
        assert SIZE_11_BYTES == 11

    def test_quality_size(self) -> None:
        """QUALITY_SIZE is 1."""
        assert QUALITY_SIZE == 1


class TestGroupVariation:
    """Tests for GroupVariation dataclass."""

    def test_create_basic(self) -> None:
        """Create basic group/variation."""
        gv = GroupVariation(group=1, variation=2)
        assert gv.group == 1
        assert gv.variation == 2

    def test_str_format(self) -> None:
        """String format is g{group}v{variation}."""
        gv = GroupVariation(group=30, variation=1)
        assert str(gv) == "g30v1"

    def test_group_negative_raises(self) -> None:
        """Negative group raises error."""
        with pytest.raises(ValueError, match=r"Group.*out of range"):
            GroupVariation(group=-1, variation=0)

    def test_group_too_large_raises(self) -> None:
        """Group > 255 raises error."""
        with pytest.raises(ValueError, match=r"Group.*out of range"):
            GroupVariation(group=256, variation=0)

    def test_variation_negative_raises(self) -> None:
        """Negative variation raises error."""
        with pytest.raises(ValueError, match=r"Variation.*out of range"):
            GroupVariation(group=0, variation=-1)

    def test_variation_too_large_raises(self) -> None:
        """Variation > 255 raises error."""
        with pytest.raises(ValueError, match=r"Variation.*out of range"):
            GroupVariation(group=0, variation=256)

    def test_immutable(self) -> None:
        """GroupVariation is immutable."""
        gv = GroupVariation(group=1, variation=2)
        with pytest.raises(AttributeError):
            gv.group = 10  # type: ignore[misc]

    @given(st.integers(min_value=0, max_value=255), st.integers(min_value=0, max_value=255))
    def test_valid_range(self, group: int, variation: int) -> None:
        """All valid group/variation combinations work."""
        gv = GroupVariation(group=group, variation=variation)
        assert gv.group == group
        assert gv.variation == variation

    def test_equality(self) -> None:
        """GroupVariation equality works."""
        gv1 = GroupVariation(group=1, variation=2)
        gv2 = GroupVariation(group=1, variation=2)
        gv3 = GroupVariation(group=1, variation=3)
        assert gv1 == gv2
        assert gv1 != gv3

    def test_hash(self) -> None:
        """GroupVariation is hashable."""
        gv = GroupVariation(group=1, variation=2)
        d = {gv: "test"}
        assert d[GroupVariation(1, 2)] == "test"


class ConcreteStaticObject(StaticObject):
    """Concrete static object for testing."""

    GROUP = 1
    VARIATION = 1
    SIZE = 1

    def __init__(self, value: int) -> None:
        self._value = value

    def to_bytes(self) -> bytes:
        return bytes([self._value])

    @classmethod
    def from_bytes(cls, data: bytes) -> "ConcreteStaticObject":
        if not data:
            msg = "Empty data"
            raise ValueError(msg)
        return cls(value=data[0])


class ConcreteEventObject(EventObject):
    """Concrete event object for testing."""

    GROUP = 2
    VARIATION = 1
    SIZE = 1

    def __init__(self, value: int) -> None:
        self._value = value

    def to_bytes(self) -> bytes:
        return bytes([self._value])

    @classmethod
    def from_bytes(cls, data: bytes) -> "ConcreteEventObject":
        if not data:
            msg = "Empty data"
            raise ValueError(msg)
        return cls(value=data[0])


class TestDNP3ObjectBase:
    """Tests for DNP3Object base class through concrete implementations."""

    def test_group_variation_property(self) -> None:
        """group_variation() returns correct GroupVariation."""
        gv = ConcreteStaticObject.group_variation()
        assert gv.group == 1
        assert gv.variation == 1

    def test_size_property(self) -> None:
        """size() returns SIZE class variable."""
        assert ConcreteStaticObject.size() == 1

    def test_to_bytes(self) -> None:
        """to_bytes serializes object."""
        obj = ConcreteStaticObject(value=0x81)
        assert obj.to_bytes() == b"\x81"

    def test_from_bytes(self) -> None:
        """from_bytes parses object."""
        obj = ConcreteStaticObject.from_bytes(b"\x81")
        assert obj._value == 0x81


class TestStaticObject:
    """Tests for StaticObject base class."""

    def test_is_dnp3_object(self) -> None:
        """StaticObject is a DNP3Object."""
        obj = ConcreteStaticObject(value=0)
        assert isinstance(obj, DNP3Object)

    def test_is_static_object(self) -> None:
        """ConcreteStaticObject is a StaticObject."""
        obj = ConcreteStaticObject(value=0)
        assert isinstance(obj, StaticObject)


class TestEventObject:
    """Tests for EventObject base class."""

    def test_is_dnp3_object(self) -> None:
        """EventObject is a DNP3Object."""
        obj = ConcreteEventObject(value=0)
        assert isinstance(obj, DNP3Object)

    def test_is_event_object(self) -> None:
        """ConcreteEventObject is an EventObject."""
        obj = ConcreteEventObject(value=0)
        assert isinstance(obj, EventObject)


class TestPointValue:
    """Tests for PointValue dataclass."""

    def test_create_basic(self) -> None:
        """Create basic point value."""
        obj = ConcreteStaticObject(value=0x81)
        pv = PointValue(index=5, value=obj)
        assert pv.index == 5
        assert pv.value == obj

    def test_index_zero_valid(self) -> None:
        """Index 0 is valid."""
        obj = ConcreteStaticObject(value=0)
        pv = PointValue(index=0, value=obj)
        assert pv.index == 0

    def test_index_negative_raises(self) -> None:
        """Negative index raises error."""
        obj = ConcreteStaticObject(value=0)
        with pytest.raises(ValueError, match="cannot be negative"):
            PointValue(index=-1, value=obj)

    def test_immutable(self) -> None:
        """PointValue is immutable."""
        obj = ConcreteStaticObject(value=0)
        pv = PointValue(index=5, value=obj)
        with pytest.raises(AttributeError):
            pv.index = 10  # type: ignore[misc]


class TestTimestampedValue:
    """Tests for TimestampedValue dataclass."""

    def test_create_basic(self) -> None:
        """Create timestamped value."""
        obj = ConcreteEventObject(value=0x81)
        ts = DNP3Timestamp(milliseconds=1000)
        tv = TimestampedValue(value=obj, timestamp=ts)
        assert tv.value == obj
        assert tv.timestamp == ts

    def test_immutable(self) -> None:
        """TimestampedValue is immutable."""
        obj = ConcreteEventObject(value=0)
        ts = DNP3Timestamp(milliseconds=0)
        tv = TimestampedValue(value=obj, timestamp=ts)
        with pytest.raises(AttributeError):
            tv.timestamp = DNP3Timestamp(milliseconds=1000)  # type: ignore[misc]


# Wire contract of every registered object class. Sizes, limits, error messages and sample encodings
# are fixed values, so any change to how a class is declared must reproduce them exactly.
_TS = DNP3Timestamp(1_700_000_000_123)
_AQ = AnalogQuality.ONLINE | AnalogQuality.RESTART
_CQ = CounterQuality.ONLINE | CounterQuality.RESTART
_BQ = BinaryQuality.ONLINE | BinaryQuality.RESTART
_I32 = {"MIN_VALUE": -(2**31), "MAX_VALUE": 2**31 - 1}
_I16 = {"MIN_VALUE": -(2**15), "MAX_VALUE": 2**15 - 1}
_U32 = {"MAX_VALUE": 2**32 - 1}
_U16 = {"MAX_VALUE": 2**16 - 1}
_I32_RANGE = "(-2147483648 to 2147483647)"
_I16_RANGE = "(-32768 to 32767)"


class _Contract(NamedTuple):
    attrs: dict[str, int]
    sample: DNP3Object
    wire: str
    short_msg: str | None
    bad: tuple[Callable[[], object], str] | None = None


_GOLDEN: dict[type[DNP3Object], _Contract] = {
    BinaryInputFlags: _Contract({"SIZE": 1}, BinaryInputFlags(_BQ, True), "83", "Binary input requires 1 byte, got 0"),
    BinaryInputEvent: _Contract(
        {"SIZE": 1}, BinaryInputEvent(_BQ, True), "83", "Binary input event requires 1 byte, got 0"
    ),
    BinaryInputEventTime: _Contract(
        {"SIZE": 7},
        BinaryInputEventTime(_BQ, True, _TS),
        "837b68e5cf8b01",
        "Binary input event with time requires 7 bytes, got 0",
    ),
    BinaryInputEventRelativeTime: _Contract(
        {"SIZE": 3},
        BinaryInputEventRelativeTime(_BQ, True, 0x1234),
        "833412",
        "Binary input event with relative time requires 3 bytes, got 0",
        (lambda: BinaryInputEventRelativeTime(_BQ, True, 65536), "Relative time 65536 out of range (0-65535)"),
    ),
    BinaryOutputFlags: _Contract(
        {"SIZE": 1}, BinaryOutputFlags(_BQ, True), "83", "Binary output requires 1 byte, got 0"
    ),
    BinaryOutputEvent: _Contract(
        {"SIZE": 1}, BinaryOutputEvent(_BQ, True), "83", "Binary output event requires 1 byte, got 0"
    ),
    BinaryOutputEventTime: _Contract(
        {"SIZE": 7},
        BinaryOutputEventTime(_BQ, True, _TS),
        "837b68e5cf8b01",
        "Binary output event with time requires 7 bytes, got 0",
    ),
    CROB: _Contract(
        {"SIZE": 11, "MAX_COUNT": 255, "MAX_TIME_MS": 0xFFFFFFFF},
        CROB.pulse_on(on_time_ms=0x01020304, off_time_ms=5, count=2),
        "0102040302010500000000",
        "CROB requires 11 bytes, got 0",
        (lambda: CROB.pulse_on(count=256), "Count 256 out of range (0-255)"),
    ),
    Counter32: _Contract(
        {"SIZE": 5, **_U32},
        Counter32(_CQ, 2**32 - 2),
        "03feffffff",
        "Counter 32-bit requires 5 bytes, got 0",
        (lambda: Counter32(_CQ, 2**32), "Value 4294967296 out of range (0 to 4294967295)"),
    ),
    Counter16: _Contract(
        {"SIZE": 3, **_U16},
        Counter16(_CQ, 2**16 - 2),
        "03feff",
        "Counter 16-bit requires 3 bytes, got 0",
        (lambda: Counter16(_CQ, -1), "Value -1 out of range (0 to 65535)"),
    ),
    Counter32NoFlag: _Contract(
        {"SIZE": 4, **_U32},
        Counter32NoFlag(2**32 - 2),
        "feffffff",
        "Counter 32-bit requires 4 bytes, got 0",
        (lambda: Counter32NoFlag(2**32), "Value 4294967296 out of range (0 to 4294967295)"),
    ),
    Counter16NoFlag: _Contract(
        {"SIZE": 2, **_U16},
        Counter16NoFlag(2**16 - 2),
        "feff",
        "Counter 16-bit requires 2 bytes, got 0",
        (lambda: Counter16NoFlag(2**16), "Value 65536 out of range (0 to 65535)"),
    ),
    FrozenCounter32: _Contract(
        {"SIZE": 5, **_U32},
        FrozenCounter32(_CQ, 2**32 - 2),
        "03feffffff",
        "Frozen counter 32-bit requires 5 bytes, got 0",
        (lambda: FrozenCounter32(_CQ, 2**32), "Value 4294967296 out of range (0 to 4294967295)"),
    ),
    FrozenCounter16: _Contract(
        {"SIZE": 3, **_U16},
        FrozenCounter16(_CQ, 2**16 - 2),
        "03feff",
        "Frozen counter 16-bit requires 3 bytes, got 0",
        (lambda: FrozenCounter16(_CQ, 2**16), "Value 65536 out of range (0 to 65535)"),
    ),
    FrozenCounter32Time: _Contract(
        {"SIZE": 11, **_U32},
        FrozenCounter32Time(_CQ, 2**32 - 2, _TS),
        "03feffffff7b68e5cf8b01",
        "Frozen counter 32-bit with time requires 11 bytes",
        (lambda: FrozenCounter32Time(_CQ, 2**32, _TS), "Value 4294967296 out of range (0 to 4294967295)"),
    ),
    FrozenCounter16Time: _Contract(
        {"SIZE": 9, **_U16},
        FrozenCounter16Time(_CQ, 2**16 - 2, _TS),
        "03feff7b68e5cf8b01",
        "Frozen counter 16-bit with time requires 9 bytes",
        (lambda: FrozenCounter16Time(_CQ, 2**16, _TS), "Value 65536 out of range (0 to 65535)"),
    ),
    CounterEvent32: _Contract(
        {"SIZE": 5, **_U32},
        CounterEvent32(_CQ, 2**32 - 2),
        "03feffffff",
        "Counter event 32-bit requires 5 bytes, got 0",
        (lambda: CounterEvent32(_CQ, 2**32), "Value 4294967296 out of range (0 to 4294967295)"),
    ),
    CounterEvent16: _Contract(
        {"SIZE": 3, **_U16},
        CounterEvent16(_CQ, 2**16 - 2),
        "03feff",
        "Counter event 16-bit requires 3 bytes, got 0",
        (lambda: CounterEvent16(_CQ, 2**16), "Value 65536 out of range (0 to 65535)"),
    ),
    CounterEvent32Time: _Contract(
        {"SIZE": 11, **_U32},
        CounterEvent32Time(_CQ, 2**32 - 2, _TS),
        "03feffffff7b68e5cf8b01",
        "Counter event 32-bit with time requires 11 bytes",
        (lambda: CounterEvent32Time(_CQ, 2**32, _TS), "Value 4294967296 out of range (0 to 4294967295)"),
    ),
    CounterEvent16Time: _Contract(
        {"SIZE": 9, **_U16},
        CounterEvent16Time(_CQ, 2**16 - 2, _TS),
        "03feff7b68e5cf8b01",
        "Counter event 16-bit with time requires 9 bytes",
        (lambda: CounterEvent16Time(_CQ, 2**16, _TS), "Value 65536 out of range (0 to 65535)"),
    ),
    AnalogInput32: _Contract(
        {"SIZE": 5, **_I32},
        AnalogInput32(_AQ, -2),
        "03feffffff",
        "Analog input 32-bit requires 5 bytes, got 0",
        (lambda: AnalogInput32(_AQ, 2**31), f"Value 2147483648 out of range {_I32_RANGE}"),
    ),
    AnalogInput16: _Contract(
        {"SIZE": 3, **_I16},
        AnalogInput16(_AQ, -2),
        "03feff",
        "Analog input 16-bit requires 3 bytes, got 0",
        (lambda: AnalogInput16(_AQ, -(2**15) - 1), f"Value -32769 out of range {_I16_RANGE}"),
    ),
    AnalogInput32NoFlag: _Contract(
        {"SIZE": 4, **_I32},
        AnalogInput32NoFlag(-2),
        "feffffff",
        "Analog input 32-bit requires 4 bytes, got 0",
        (lambda: AnalogInput32NoFlag(2**31), f"Value 2147483648 out of range {_I32_RANGE}"),
    ),
    AnalogInput16NoFlag: _Contract(
        {"SIZE": 2, **_I16},
        AnalogInput16NoFlag(-2),
        "feff",
        "Analog input 16-bit requires 2 bytes, got 0",
        (lambda: AnalogInput16NoFlag(2**15), f"Value 32768 out of range {_I16_RANGE}"),
    ),
    AnalogInputFloat: _Contract(
        {"SIZE": 5}, AnalogInputFloat(_AQ, 1.5), "030000c03f", "Analog input float requires 5 bytes, got 0"
    ),
    AnalogInputDouble: _Contract(
        {"SIZE": 9},
        AnalogInputDouble(_AQ, 1.5),
        "03000000000000f83f",
        "Analog input double requires 9 bytes, got 0",
    ),
    AnalogInputEvent32: _Contract(
        {"SIZE": 5, **_I32},
        AnalogInputEvent32(_AQ, -2),
        "03feffffff",
        "Analog input event 32-bit requires 5 bytes, got 0",
        (lambda: AnalogInputEvent32(_AQ, 2**31), f"Value 2147483648 out of range {_I32_RANGE}"),
    ),
    AnalogInputEvent16: _Contract(
        {"SIZE": 3, **_I16},
        AnalogInputEvent16(_AQ, -2),
        "03feff",
        "Analog input event 16-bit requires 3 bytes, got 0",
        (lambda: AnalogInputEvent16(_AQ, 2**15), f"Value 32768 out of range {_I16_RANGE}"),
    ),
    AnalogInputEvent32Time: _Contract(
        {"SIZE": 11, **_I32},
        AnalogInputEvent32Time(_AQ, -2, _TS),
        "03feffffff7b68e5cf8b01",
        "Analog input event 32-bit with time requires 11 bytes",
        (lambda: AnalogInputEvent32Time(_AQ, 2**31, _TS), f"Value 2147483648 out of range {_I32_RANGE}"),
    ),
    AnalogInputEvent16Time: _Contract(
        {"SIZE": 9, **_I16},
        AnalogInputEvent16Time(_AQ, -2, _TS),
        "03feff7b68e5cf8b01",
        "Analog input event 16-bit with time requires 9 bytes",
        (lambda: AnalogInputEvent16Time(_AQ, 2**15, _TS), f"Value 32768 out of range {_I16_RANGE}"),
    ),
    AnalogInputEventFloat: _Contract(
        {"SIZE": 5},
        AnalogInputEventFloat(_AQ, 1.5),
        "030000c03f",
        "Analog input event float requires 5 bytes, got 0",
    ),
    AnalogInputEventDouble: _Contract(
        {"SIZE": 9},
        AnalogInputEventDouble(_AQ, 1.5),
        "03000000000000f83f",
        "Analog input event double requires 9 bytes, got 0",
    ),
    AnalogInputEventFloatTime: _Contract(
        {"SIZE": 11},
        AnalogInputEventFloatTime(_AQ, 1.5, _TS),
        "030000c03f7b68e5cf8b01",
        "Analog input event float with time requires 11 bytes",
    ),
    AnalogInputEventDoubleTime: _Contract(
        {"SIZE": 15},
        AnalogInputEventDoubleTime(_AQ, 1.5, _TS),
        "03000000000000f83f7b68e5cf8b01",
        "Analog input event double with time requires 15 bytes",
    ),
    TimeAndDate: _Contract({"SIZE": 6}, TimeAndDate(_TS), "7b68e5cf8b01", "Time and date requires 6 bytes, got 0"),
    TimeCTO: _Contract({"SIZE": 6}, TimeCTO(_TS), "7b68e5cf8b01", "Time CTO requires 6 bytes, got 0"),
    TimeCTOUnsync: _Contract(
        {"SIZE": 6}, TimeCTOUnsync(_TS), "7b68e5cf8b01", "Unsync time CTO requires 6 bytes, got 0"
    ),
    TimeDelayCoarse: _Contract(
        {"SIZE": 2, "MAX_VALUE": 65535},
        TimeDelayCoarse(300),
        "2c01",
        "Coarse time delay requires 2 bytes, got 0",
        (lambda: TimeDelayCoarse(65536), "Delay 65536 out of range (0 to 65535)"),
    ),
    TimeDelayFine: _Contract(
        {"SIZE": 2, "MAX_VALUE": 65535},
        TimeDelayFine(300),
        "2c01",
        "Fine time delay requires 2 bytes, got 0",
        (lambda: TimeDelayFine(-1), "Delay -1 out of range (0 to 65535)"),
    ),
    ClassData0: _Contract({"SIZE": 0}, ClassData0(), "", None),
    ClassData1: _Contract({"SIZE": 0}, ClassData1(), "", None),
    ClassData2: _Contract({"SIZE": 0}, ClassData2(), "", None),
    ClassData3: _Contract({"SIZE": 0}, ClassData3(), "", None),
}


def test_golden_covers_every_registered_class() -> None:
    """Every registered class has a pinned contract."""
    assert set(_GOLDEN) == set(get_registry_copy().values())


@pytest.mark.parametrize("cls", list(_GOLDEN), ids=lambda c: c.__name__)
def test_object_contract_golden(cls: type[DNP3Object]) -> None:
    """Class attributes, error messages and encoding match the pinned contract."""
    contract = _GOLDEN[cls]
    assert {name: getattr(cls, name) for name in contract.attrs} == contract.attrs
    assert cls.size() == contract.attrs["SIZE"]
    assert contract.sample.to_bytes().hex() == contract.wire
    assert cls.from_bytes(bytes.fromhex(contract.wire)) == contract.sample
    if contract.short_msg is None:
        assert cls.from_bytes(b"") == contract.sample
    else:
        with pytest.raises(ValueError) as short:
            cls.from_bytes(b"")
        assert str(short.value) == contract.short_msg
    if contract.bad is not None:
        make, bad_msg = contract.bad
        with pytest.raises(ValueError) as bad:
            make()
        assert str(bad.value) == bad_msg


def _wire(cls: type[DNP3Object]) -> st.SearchStrategy[bytes]:
    """Any SIZE bytes, except that CROB needs a defined Op Type and a defined CommandStatus."""
    size = cls.size()
    assert size is not None
    if cls is CROB:
        # A.8.1.2.2: Op Type is bits 3-0 of the control octet, and only 0-4 are defined.
        return st.builds(
            lambda high, op_type, body, status: bytes([high << 4 | op_type]) + body + bytes([status]),
            st.integers(min_value=0, max_value=0x0F),
            st.integers(min_value=0, max_value=4),
            st.binary(min_size=9, max_size=9),
            st.sampled_from(CommandStatus),
        )
    return st.binary(min_size=size, max_size=size)


@pytest.mark.parametrize("cls", list(_GOLDEN), ids=lambda c: c.__name__)
@given(data=st.data())
def test_decoded_object_round_trips(cls: type[DNP3Object], data: st.DataObject) -> None:
    """Any object decoded from wire bytes re-encodes to SIZE bytes that decode back to it."""
    obj = cls.from_bytes(data.draw(_wire(cls)))
    assume(not any(isinstance(v, float) and math.isnan(v) for v in astuple(obj)))  # NaN never compares equal.
    wire = obj.to_bytes()
    assert len(wire) == cls.size()
    assert cls.from_bytes(wire) == obj


@dataclass(frozen=True, slots=True)
class _Layout(FixedSizeObject, StaticObject):
    """Unregistered object with a flags byte, a signed 16-bit value and a timestamp."""

    GROUP = 200
    VARIATION = 1
    FORMAT = "<Bh6s"
    _LABEL = "Layout"
    _RANGE_FIELD = "value"

    quality: AnalogQuality
    value: int
    timestamp: DNP3Timestamp


@dataclass(frozen=True, slots=True)
class _Delay(FixedSizeObject, StaticObject):
    """Unregistered object with an unsigned 16-bit value and its own range label."""

    GROUP = 200
    VARIATION = 2
    FORMAT = "<H"
    _LABEL = "Delay thing"
    _RANGE_FIELD = "delay"
    _RANGE_LABEL = "Delay"

    delay: int


@dataclass(frozen=True, slots=True)
class _Byte(FixedSizeObject, StaticObject):
    """Unregistered one-byte object with no range check and a message without the received length."""

    GROUP = 200
    VARIATION = 3
    FORMAT = "<B"
    _LABEL = "Byte thing"
    _LENGTH_MSG = "{label} requires {size} {unit}"

    quality: AnalogQuality


class TestStructLayout:
    """FORMAT on a subclass derives size, limits, encoding and validation."""

    def test_size_from_format(self) -> None:
        assert _Layout.SIZE == 9
        assert _Layout.size() == 9

    def test_limits_from_range_field_code(self) -> None:
        assert (_Layout.MIN_VALUE, _Layout.MAX_VALUE) == (-(2**15), 2**15 - 1)
        assert (_Delay.MIN_VALUE, _Delay.MAX_VALUE) == (0, 2**16 - 1)

    def test_no_limits_without_range_field(self) -> None:
        assert not hasattr(_Byte, "MIN_VALUE")
        assert not hasattr(_Byte, "MAX_VALUE")

    def test_to_bytes_packs_fields_in_order(self) -> None:
        obj = _Layout(AnalogQuality.ONLINE, -2, DNP3Timestamp(0x010203))
        assert obj.to_bytes() == bytes.fromhex("01feff030201000000")

    def test_from_bytes_restores_field_types(self) -> None:
        obj = _Layout.from_bytes(bytes.fromhex("01feff030201000000ff"))
        assert obj == _Layout(AnalogQuality.ONLINE, -2, DNP3Timestamp(0x010203))
        assert type(obj.quality) is AnalogQuality
        assert type(obj.timestamp) is DNP3Timestamp

    def test_short_input(self) -> None:
        with pytest.raises(ValueError, match=r"^Layout requires 9 bytes, got 2$"):
            _Layout.from_bytes(b"\x01\x02")

    def test_short_input_single_byte_and_custom_message(self) -> None:
        with pytest.raises(ValueError, match=r"^Byte thing requires 1 byte$"):
            _Byte.from_bytes(b"")

    def test_range_check(self) -> None:
        with pytest.raises(ValueError, match=r"^Value -32769 out of range \(-32768 to 32767\)$"):
            _Layout(AnalogQuality.ONLINE, -(2**15) - 1, DNP3Timestamp(0))

    def test_range_label(self) -> None:
        with pytest.raises(ValueError, match=r"^Delay 65536 out of range \(0 to 65535\)$"):
            _Delay(65536)

    def test_unencodable_field_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            _Byte(AnalogQuality(0x100)).to_bytes()

    def test_plain_subclass_inherits_layout(self) -> None:
        class Sub(_Layout):
            pass

        obj = Sub(AnalogQuality.ONLINE, -2, DNP3Timestamp(0x010203))
        assert Sub.SIZE == 9
        assert (Sub.MIN_VALUE, Sub.MAX_VALUE) == (_Layout.MIN_VALUE, _Layout.MAX_VALUE)
        assert obj.to_bytes() == bytes.fromhex("01feff030201000000")
        assert Sub.from_bytes(obj.to_bytes()) == obj
        with pytest.raises(ValueError, match=r"^Value 32768 out of range"):
            Sub(AnalogQuality.ONLINE, 2**15, DNP3Timestamp(0))

    def test_dataclass_subclass_of_registered_class_inherits_layout(self) -> None:
        @dataclass(frozen=True, slots=True)
        class Scaled(AnalogInputFloat):
            def scaled(self, factor: float) -> float:
                return self.value * factor

        obj = Scaled.from_bytes(AnalogInputFloat(AnalogQuality.ONLINE, 1.5).to_bytes())
        assert obj.scaled(2.0) == 3.0
        assert obj.to_bytes() == AnalogInputFloat(AnalogQuality.ONLINE, 1.5).to_bytes()

    def test_binary_subclass_inherits_flags_packing(self) -> None:
        class Sub(BinaryInputFlags):
            pass

        obj = Sub(BinaryQuality.ONLINE, state=True)
        assert obj.to_bytes() == b"\x81"
        assert Sub.from_bytes(b"\x81") == obj
