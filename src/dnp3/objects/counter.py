"""Counter objects per IEEE 1815-2012.

Group 20: Counter Static
- Variation 1: 32-bit with flag (5 bytes)
- Variation 2: 16-bit with flag (3 bytes)
- Variation 5: 32-bit without flag (4 bytes)
- Variation 6: 16-bit without flag (2 bytes)

Group 21: Frozen Counter
- Variation 1: 32-bit with flag (5 bytes)
- Variation 2: 16-bit with flag (3 bytes)
- Variation 5: 32-bit with flag and time (11 bytes)
- Variation 6: 16-bit with flag and time (9 bytes)

Group 22: Counter Event
- Variation 1: 32-bit with flag (5 bytes)
- Variation 2: 16-bit with flag (3 bytes)
- Variation 5: 32-bit with flag and time (11 bytes)
- Variation 6: 16-bit with flag and time (9 bytes)
"""

from dataclasses import dataclass

from dnp3.core.flags import CounterQuality
from dnp3.core.timestamp import DNP3Timestamp
from dnp3.objects.base import EventObject, FixedSizeObject, StaticObject
from dnp3.objects.registry import register

# Group numbers
COUNTER_STATIC_GROUP = 20
FROZEN_COUNTER_GROUP = 21
COUNTER_EVENT_GROUP = 22

# Size constants
SIZE_3_BYTES = 3

# The with-time variations report a short read without the received length.
_LENGTH_MSG_WITH_TIME = "{label} requires {size} {unit}"


@register
@dataclass(frozen=True, slots=True)
class Counter32(FixedSizeObject, StaticObject):
    """Counter 32-bit with flag (g20v1).

    Attributes:
        quality: Quality flags.
        value: 32-bit unsigned counter value.
    """

    GROUP = COUNTER_STATIC_GROUP
    VARIATION = 1
    FORMAT = "<BI"
    _LABEL = "Counter 32-bit"
    _RANGE_FIELD = "value"

    quality: CounterQuality
    value: int

    @property
    def is_online(self) -> bool:
        """Check if point is online."""
        return bool(self.quality & CounterQuality.ONLINE)


@register
@dataclass(frozen=True, slots=True)
class Counter16(FixedSizeObject, StaticObject):
    """Counter 16-bit with flag (g20v2).

    Attributes:
        quality: Quality flags.
        value: 16-bit unsigned counter value.
    """

    GROUP = COUNTER_STATIC_GROUP
    VARIATION = 2
    FORMAT = "<BH"
    _LABEL = "Counter 16-bit"
    _RANGE_FIELD = "value"

    quality: CounterQuality
    value: int

    @property
    def is_online(self) -> bool:
        """Check if point is online."""
        return bool(self.quality & CounterQuality.ONLINE)


@register
@dataclass(frozen=True, slots=True)
class Counter32NoFlag(FixedSizeObject, StaticObject):
    """Counter 32-bit without flag (g20v5).

    Attributes:
        value: 32-bit unsigned counter value.
    """

    GROUP = COUNTER_STATIC_GROUP
    VARIATION = 5
    FORMAT = "<I"
    _LABEL = "Counter 32-bit"
    _RANGE_FIELD = "value"

    value: int


@register
@dataclass(frozen=True, slots=True)
class Counter16NoFlag(FixedSizeObject, StaticObject):
    """Counter 16-bit without flag (g20v6).

    Attributes:
        value: 16-bit unsigned counter value.
    """

    GROUP = COUNTER_STATIC_GROUP
    VARIATION = 6
    FORMAT = "<H"
    _LABEL = "Counter 16-bit"
    _RANGE_FIELD = "value"

    value: int


# Frozen Counter objects


@register
@dataclass(frozen=True, slots=True)
class FrozenCounter32(FixedSizeObject, StaticObject):
    """Frozen Counter 32-bit with flag (g21v1).

    Attributes:
        quality: Quality flags.
        value: 32-bit unsigned counter value.
    """

    GROUP = FROZEN_COUNTER_GROUP
    VARIATION = 1
    FORMAT = "<BI"
    _LABEL = "Frozen counter 32-bit"
    _RANGE_FIELD = "value"

    quality: CounterQuality
    value: int

    @property
    def is_online(self) -> bool:
        """Check if point is online."""
        return bool(self.quality & CounterQuality.ONLINE)


@register
@dataclass(frozen=True, slots=True)
class FrozenCounter16(FixedSizeObject, StaticObject):
    """Frozen Counter 16-bit with flag (g21v2).

    Attributes:
        quality: Quality flags.
        value: 16-bit unsigned counter value.
    """

    GROUP = FROZEN_COUNTER_GROUP
    VARIATION = 2
    FORMAT = "<BH"
    _LABEL = "Frozen counter 16-bit"
    _RANGE_FIELD = "value"

    quality: CounterQuality
    value: int

    @property
    def is_online(self) -> bool:
        """Check if point is online."""
        return bool(self.quality & CounterQuality.ONLINE)


@register
@dataclass(frozen=True, slots=True)
class FrozenCounter32Time(FixedSizeObject, StaticObject):
    """Frozen Counter 32-bit with flag and time (g21v5).

    Attributes:
        quality: Quality flags.
        value: 32-bit unsigned counter value.
        timestamp: Time when counter was frozen.
    """

    GROUP = FROZEN_COUNTER_GROUP
    VARIATION = 5
    FORMAT = "<BI6s"
    _LABEL = "Frozen counter 32-bit with time"
    _LENGTH_MSG = _LENGTH_MSG_WITH_TIME
    _RANGE_FIELD = "value"

    quality: CounterQuality
    value: int
    timestamp: DNP3Timestamp


@register
@dataclass(frozen=True, slots=True)
class FrozenCounter16Time(FixedSizeObject, StaticObject):
    """Frozen Counter 16-bit with flag and time (g21v6).

    Attributes:
        quality: Quality flags.
        value: 16-bit unsigned counter value.
        timestamp: Time when counter was frozen.
    """

    GROUP = FROZEN_COUNTER_GROUP
    VARIATION = 6
    FORMAT = "<BH6s"
    _LABEL = "Frozen counter 16-bit with time"
    _LENGTH_MSG = _LENGTH_MSG_WITH_TIME
    _RANGE_FIELD = "value"

    quality: CounterQuality
    value: int
    timestamp: DNP3Timestamp


# Counter Event objects


@register
@dataclass(frozen=True, slots=True)
class CounterEvent32(FixedSizeObject, EventObject):
    """Counter Event 32-bit with flag (g22v1).

    Attributes:
        quality: Quality flags.
        value: 32-bit unsigned counter value.
    """

    GROUP = COUNTER_EVENT_GROUP
    VARIATION = 1
    FORMAT = "<BI"
    _LABEL = "Counter event 32-bit"
    _RANGE_FIELD = "value"

    quality: CounterQuality
    value: int


@register
@dataclass(frozen=True, slots=True)
class CounterEvent16(FixedSizeObject, EventObject):
    """Counter Event 16-bit with flag (g22v2).

    Attributes:
        quality: Quality flags.
        value: 16-bit unsigned counter value.
    """

    GROUP = COUNTER_EVENT_GROUP
    VARIATION = 2
    FORMAT = "<BH"
    _LABEL = "Counter event 16-bit"
    _RANGE_FIELD = "value"

    quality: CounterQuality
    value: int


@register
@dataclass(frozen=True, slots=True)
class CounterEvent32Time(FixedSizeObject, EventObject):
    """Counter Event 32-bit with flag and time (g22v5).

    Attributes:
        quality: Quality flags.
        value: 32-bit unsigned counter value.
        timestamp: Time when event occurred.
    """

    GROUP = COUNTER_EVENT_GROUP
    VARIATION = 5
    FORMAT = "<BI6s"
    _LABEL = "Counter event 32-bit with time"
    _LENGTH_MSG = _LENGTH_MSG_WITH_TIME
    _RANGE_FIELD = "value"

    quality: CounterQuality
    value: int
    timestamp: DNP3Timestamp


@register
@dataclass(frozen=True, slots=True)
class CounterEvent16Time(FixedSizeObject, EventObject):
    """Counter Event 16-bit with flag and time (g22v6).

    Attributes:
        quality: Quality flags.
        value: 16-bit unsigned counter value.
        timestamp: Time when event occurred.
    """

    GROUP = COUNTER_EVENT_GROUP
    VARIATION = 6
    FORMAT = "<BH6s"
    _LABEL = "Counter event 16-bit with time"
    _LENGTH_MSG = _LENGTH_MSG_WITH_TIME
    _RANGE_FIELD = "value"

    quality: CounterQuality
    value: int
    timestamp: DNP3Timestamp
