"""Base classes for DNP3 objects per IEEE 1815-2012.

DNP3 objects are identified by group and variation numbers:
- Group: Defines the type of data (binary input, analog output, etc.)
- Variation: Defines the format/encoding of the data

Object categories:
- Static: Current point values (e.g., g1v1 Binary Input packed format)
- Event: Change events with optional timestamps (e.g., g2v1 Binary Input event)
"""

import inspect
import re
import struct
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar, Self

from dnp3.core.timestamp import DNP3Timestamp

# Maximum byte value for group/variation validation
MAX_BYTE_VALUE = 255

# One struct format code with its optional repeat count, such as "B" or "6s"
_FORMAT_CODE = re.compile(r"\d*[a-zA-Z?]")


def _int_range(code: str) -> tuple[int, int]:
    """Range of a struct integer code: signed for lowercase codes, unsigned for uppercase."""
    bits = 8 * struct.calcsize("<" + code)
    if code.islower():
        return -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    return 0, (1 << bits) - 1


@dataclass(frozen=True, slots=True)
class GroupVariation:
    """Identifies an object type by group and variation.

    Attributes:
        group: Object group number (1-120).
        variation: Object variation number (0-255).
    """

    group: int
    variation: int

    def __post_init__(self) -> None:
        """Validate group and variation ranges."""
        if not 0 <= self.group <= MAX_BYTE_VALUE:
            msg = f"Group {self.group} out of range (0-{MAX_BYTE_VALUE})"
            raise ValueError(msg)
        if not 0 <= self.variation <= MAX_BYTE_VALUE:
            msg = f"Variation {self.variation} out of range (0-{MAX_BYTE_VALUE})"
            raise ValueError(msg)

    def __str__(self) -> str:
        """Format as g{group}v{variation}."""
        return f"g{self.group}v{self.variation}"


class DNP3Object(ABC):
    """Base class for all DNP3 data objects.

    Each object type must define:
    - GROUP: Object group number
    - VARIATION: Object variation number
    - SIZE: Fixed size in bytes (or None for variable size)
    """

    GROUP: ClassVar[int]
    VARIATION: ClassVar[int]
    SIZE: ClassVar[int | None]

    @classmethod
    def group_variation(cls) -> GroupVariation:
        """Get the group/variation identifier."""
        return GroupVariation(group=cls.GROUP, variation=cls.VARIATION)

    @abstractmethod
    def to_bytes(self) -> bytes:
        """Serialize the object to bytes.

        Raises:
            ValueError: If a field does not fit its wire format.
        """

    @classmethod
    @abstractmethod
    def from_bytes(cls, data: bytes) -> Self:
        """Parse an object from the start of data.

        Raises:
            ValueError: If data is invalid or too short.
        """

    @classmethod
    def size(cls) -> int | None:
        """Get the fixed size of this object type.

        Returns:
            Size in bytes, or None if variable size.
        """
        return cls.SIZE


class FixedSizeObject(DNP3Object):
    """Base for dataclass objects whose wire layout is one little-endian ``struct`` format.

    A subclass declares FORMAT with one code per field in declaration order (``6s`` for a DNP3Timestamp).
    SIZE, to_bytes and from_bytes then follow from it, and _LABEL names the object in the too-short error.
    Naming an integer field in _RANGE_FIELD sets MIN_VALUE and MAX_VALUE to the range of its format code
    and checks the field against them on construction.
    """

    SIZE: ClassVar[int]
    FORMAT: ClassVar[str]
    MIN_VALUE: ClassVar[int]
    MAX_VALUE: ClassVar[int]
    _LABEL: ClassVar[str]
    _LENGTH_MSG: ClassVar[str] = "{label} requires {size} {unit}, got {got}"
    _RANGE_FIELD: ClassVar[str | None] = None
    _RANGE_LABEL: ClassVar[str] = "Value"
    _STRUCT: ClassVar[struct.Struct]
    _DECODERS: ClassVar[tuple[Callable[[Any], Any], ...]]
    _FIELD_NAMES: ClassVar[tuple[str, ...]]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # A subclass that does not restate FORMAT keeps its parent's layout; its own annotations hold no fields.
        if "FORMAT" not in cls.__dict__:
            return
        cls._STRUCT = struct.Struct(cls.FORMAT)
        cls.SIZE = cls._STRUCT.size
        # Dataclass fields are annotated with plain classes; ClassVar annotations are not classes.
        field_types = {name: hint for name, hint in inspect.get_annotations(cls).items() if isinstance(hint, type)}
        cls._FIELD_NAMES = tuple(field_types)
        cls._DECODERS = tuple(DNP3Timestamp.from_bytes if t is DNP3Timestamp else t for t in field_types.values())
        if cls._RANGE_FIELD is not None:
            code = _FORMAT_CODE.findall(cls.FORMAT)[cls._FIELD_NAMES.index(cls._RANGE_FIELD)]
            cls.MIN_VALUE, cls.MAX_VALUE = _int_range(code)

    def __post_init__(self) -> None:
        """Check _RANGE_FIELD against MIN_VALUE and MAX_VALUE."""
        if self._RANGE_FIELD is None:
            return
        value = getattr(self, self._RANGE_FIELD)
        if not self.MIN_VALUE <= value <= self.MAX_VALUE:
            msg = f"{self._RANGE_LABEL} {value} out of range ({self.MIN_VALUE} to {self.MAX_VALUE})"
            raise ValueError(msg)

    def to_bytes(self) -> bytes:
        """Serialize the object to bytes.

        Raises:
            ValueError: If a field does not fit its wire format.
        """
        try:
            return self._STRUCT.pack(*self._pack())
        except struct.error as exc:
            raise ValueError(str(exc)) from exc

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        """Parse an object from the start of data.

        Raises:
            ValueError: If data is invalid or too short.
        """
        size = cls._STRUCT.size
        if len(data) < size:
            unit = "byte" if size == 1 else "bytes"
            raise ValueError(cls._LENGTH_MSG.format(label=cls._LABEL, size=size, unit=unit, got=len(data)))
        return cls._unpack(cls._STRUCT.unpack_from(data))

    def _pack(self) -> tuple[Any, ...]:
        """Field values in FORMAT order, as struct.pack takes them."""
        values = (getattr(self, name) for name in self._FIELD_NAMES)
        return tuple(value.to_bytes() if isinstance(value, DNP3Timestamp) else value for value in values)

    @classmethod
    def _unpack(cls, values: tuple[Any, ...]) -> Self:
        """Build an instance from the values struct.unpack returns for FORMAT."""
        return cls(*(decode(value) for decode, value in zip(cls._DECODERS, values, strict=True)))


class StaticObject(DNP3Object):
    """Base class for static (current value) objects.

    Static objects represent the current state of a point.
    They are typically returned in response to READ requests.
    """


class EventObject(DNP3Object):
    """Base class for event objects.

    Event objects represent changes that occurred at a point.
    They may include timestamps and are buffered until read.
    """


@dataclass(frozen=True, slots=True)
class PointValue:
    """A point value with its index.

    Attributes:
        index: Point index (0-65535 typically).
        value: The data object.
    """

    index: int
    value: DNP3Object

    def __post_init__(self) -> None:
        """Validate index range."""
        if self.index < 0:
            msg = f"Point index {self.index} cannot be negative"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class TimestampedValue:
    """A value with timestamp.

    Attributes:
        value: The data value.
        timestamp: When the value was recorded.
    """

    value: DNP3Object
    timestamp: DNP3Timestamp


# Common size constants for object definitions
SIZE_1_BYTE = 1
SIZE_2_BYTES = 2
SIZE_4_BYTES = 4
SIZE_5_BYTES = 5
SIZE_6_BYTES = 6
SIZE_7_BYTES = 7
SIZE_8_BYTES = 8
SIZE_9_BYTES = 9
SIZE_11_BYTES = 11

# Quality flag byte size
QUALITY_SIZE = 1
