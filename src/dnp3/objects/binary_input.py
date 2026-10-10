"""Binary Input objects per IEEE 1815-2012.

Group 1: Binary Input Static
- Variation 1: Packed format (1 bit per point)
- Variation 2: With flags (1 byte per point)

Group 2: Binary Input Event
- Variation 1: Without time (1 byte flags)
- Variation 2: With absolute time (7 bytes: flags + 6-byte timestamp)
- Variation 3: With relative time (3 bytes: flags + 2-byte relative time)
"""

from dataclasses import dataclass
from typing import Any, Self

from dnp3.core.flags import BinaryQuality
from dnp3.core.timestamp import DNP3Timestamp
from dnp3.objects.base import EventObject, FixedSizeObject, StaticObject
from dnp3.objects.registry import register

# Group numbers
BINARY_INPUT_STATIC_GROUP = 1
BINARY_INPUT_EVENT_GROUP = 2

# Timestamp size
TIMESTAMP_SIZE = 6
RELATIVE_TIME_SIZE = 2

# State bit mask
STATE_BIT = 0x80


class _BinaryFlags(FixedSizeObject):
    """Packs the quality field (bits 0-6) and the state field (bit 7) into the leading flags byte."""

    def _pack(self) -> tuple[Any, ...]:
        quality, state, *rest = super()._pack()
        # BinaryQuality also names bit 7 (STATE); the state field alone decides it.
        return ((int(quality) & ~STATE_BIT) | (STATE_BIT if state else 0), *rest)

    @classmethod
    def _unpack(cls, values: tuple[Any, ...]) -> Self:
        flags, *rest = values
        return super()._unpack((flags & ~STATE_BIT, bool(flags & STATE_BIT), *rest))


@register
@dataclass(frozen=True, slots=True)
class BinaryInputFlags(_BinaryFlags, StaticObject):
    """Binary Input with flags (g1v2).

    Each point is 1 byte containing quality flags and state.

    Attributes:
        quality: Quality flags (bits 0-6).
        state: Binary state (bit 7): False=off, True=on.
    """

    GROUP = BINARY_INPUT_STATIC_GROUP
    VARIATION = 2
    FORMAT = "<B"
    _LABEL = "Binary input"

    quality: BinaryQuality
    state: bool

    @property
    def is_online(self) -> bool:
        """Check if point is online."""
        return bool(self.quality & BinaryQuality.ONLINE)


@register
@dataclass(frozen=True, slots=True)
class BinaryInputEvent(_BinaryFlags, EventObject):
    """Binary Input Event without time (g2v1).

    Each event is 1 byte containing quality flags and state.

    Attributes:
        quality: Quality flags (bits 0-6).
        state: Binary state (bit 7): False=off, True=on.
    """

    GROUP = BINARY_INPUT_EVENT_GROUP
    VARIATION = 1
    FORMAT = "<B"
    _LABEL = "Binary input event"

    quality: BinaryQuality
    state: bool


@register
@dataclass(frozen=True, slots=True)
class BinaryInputEventTime(_BinaryFlags, EventObject):
    """Binary Input Event with absolute time (g2v2).

    Each event is 7 bytes: 1 byte flags + 6 byte timestamp.

    Attributes:
        quality: Quality flags (bits 0-6).
        state: Binary state (bit 7): False=off, True=on.
        timestamp: Time when event occurred.
    """

    GROUP = BINARY_INPUT_EVENT_GROUP
    VARIATION = 2
    FORMAT = "<B6s"
    _LABEL = "Binary input event with time"

    quality: BinaryQuality
    state: bool
    timestamp: DNP3Timestamp


@register
@dataclass(frozen=True, slots=True)
class BinaryInputEventRelativeTime(_BinaryFlags, EventObject):
    """Binary Input Event with relative time (g2v3).

    Each event is 3 bytes: 1 byte flags + 2 byte relative time.
    Relative time is milliseconds since CTO (Common Time of Occurrence).

    Attributes:
        quality: Quality flags (bits 0-6).
        state: Binary state (bit 7): False=off, True=on.
        relative_time_ms: Milliseconds since CTO (0-65535).
    """

    GROUP = BINARY_INPUT_EVENT_GROUP
    VARIATION = 3
    FORMAT = "<BH"
    _LABEL = "Binary input event with relative time"

    quality: BinaryQuality
    state: bool
    relative_time_ms: int

    def __post_init__(self) -> None:
        """Validate relative time range."""
        max_relative_time = 65535
        if not 0 <= self.relative_time_ms <= max_relative_time:
            msg = f"Relative time {self.relative_time_ms} out of range (0-{max_relative_time})"
            raise ValueError(msg)
