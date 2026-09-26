"""Analog Input objects per IEEE 1815-2012.

Group 30: Analog Input Static
- Variation 1: 32-bit with flag (5 bytes)
- Variation 2: 16-bit with flag (3 bytes)
- Variation 3: 32-bit without flag (4 bytes)
- Variation 4: 16-bit without flag (2 bytes)
- Variation 5: Single-precision float with flag (5 bytes)
- Variation 6: Double-precision float with flag (9 bytes)

Group 32: Analog Input Event
- Variation 1: 32-bit without time (5 bytes)
- Variation 2: 16-bit without time (3 bytes)
- Variation 3: 32-bit with time (11 bytes)
- Variation 4: 16-bit with time (9 bytes)
- Variation 5: Single-precision float without time (5 bytes)
- Variation 6: Double-precision float without time (9 bytes)
- Variation 7: Single-precision float with time (11 bytes)
- Variation 8: Double-precision float with time (15 bytes)
"""

from dataclasses import dataclass

from dnp3.core.flags import AnalogQuality
from dnp3.core.timestamp import DNP3Timestamp
from dnp3.objects.base import EventObject, FixedSizeObject, StaticObject
from dnp3.objects.registry import register

# Group numbers
ANALOG_INPUT_STATIC_GROUP = 30
ANALOG_INPUT_EVENT_GROUP = 32

# Timestamp size
TIMESTAMP_SIZE = 6

# Size constants for analog objects
SIZE_3_BYTES = 3
SIZE_15_BYTES = 15

# The with-time variations report a short read without the received length.
_LENGTH_MSG_WITH_TIME = "{label} requires {size} {unit}"


@register
@dataclass(frozen=True, slots=True)
class AnalogInput32(FixedSizeObject, StaticObject):
    """Analog Input 32-bit with flag (g30v1).

    Attributes:
        quality: Quality flags.
        value: 32-bit signed analog value.
    """

    GROUP = ANALOG_INPUT_STATIC_GROUP
    VARIATION = 1
    FORMAT = "<Bi"
    _LABEL = "Analog input 32-bit"
    _RANGE_FIELD = "value"

    quality: AnalogQuality
    value: int

    @property
    def is_online(self) -> bool:
        """Check if point is online."""
        return bool(self.quality & AnalogQuality.ONLINE)


@register
@dataclass(frozen=True, slots=True)
class AnalogInput16(FixedSizeObject, StaticObject):
    """Analog Input 16-bit with flag (g30v2).

    Attributes:
        quality: Quality flags.
        value: 16-bit signed analog value.
    """

    GROUP = ANALOG_INPUT_STATIC_GROUP
    VARIATION = 2
    FORMAT = "<Bh"
    _LABEL = "Analog input 16-bit"
    _RANGE_FIELD = "value"

    quality: AnalogQuality
    value: int

    @property
    def is_online(self) -> bool:
        """Check if point is online."""
        return bool(self.quality & AnalogQuality.ONLINE)


@register
@dataclass(frozen=True, slots=True)
class AnalogInput32NoFlag(FixedSizeObject, StaticObject):
    """Analog Input 32-bit without flag (g30v3).

    Attributes:
        value: 32-bit signed analog value.
    """

    GROUP = ANALOG_INPUT_STATIC_GROUP
    VARIATION = 3
    FORMAT = "<i"
    _LABEL = "Analog input 32-bit"
    _RANGE_FIELD = "value"

    value: int


@register
@dataclass(frozen=True, slots=True)
class AnalogInput16NoFlag(FixedSizeObject, StaticObject):
    """Analog Input 16-bit without flag (g30v4).

    Attributes:
        value: 16-bit signed analog value.
    """

    GROUP = ANALOG_INPUT_STATIC_GROUP
    VARIATION = 4
    FORMAT = "<h"
    _LABEL = "Analog input 16-bit"
    _RANGE_FIELD = "value"

    value: int


@register
@dataclass(frozen=True, slots=True)
class AnalogInputFloat(FixedSizeObject, StaticObject):
    """Analog Input single-precision float with flag (g30v5).

    Attributes:
        quality: Quality flags.
        value: Single-precision floating-point value.
    """

    GROUP = ANALOG_INPUT_STATIC_GROUP
    VARIATION = 5
    FORMAT = "<Bf"
    _LABEL = "Analog input float"

    quality: AnalogQuality
    value: float

    @property
    def is_online(self) -> bool:
        """Check if point is online."""
        return bool(self.quality & AnalogQuality.ONLINE)


@register
@dataclass(frozen=True, slots=True)
class AnalogInputDouble(FixedSizeObject, StaticObject):
    """Analog Input double-precision float with flag (g30v6).

    Attributes:
        quality: Quality flags.
        value: Double-precision floating-point value.
    """

    GROUP = ANALOG_INPUT_STATIC_GROUP
    VARIATION = 6
    FORMAT = "<Bd"
    _LABEL = "Analog input double"

    quality: AnalogQuality
    value: float

    @property
    def is_online(self) -> bool:
        """Check if point is online."""
        return bool(self.quality & AnalogQuality.ONLINE)


# Event objects


@register
@dataclass(frozen=True, slots=True)
class AnalogInputEvent32(FixedSizeObject, EventObject):
    """Analog Input Event 32-bit without time (g32v1).

    Attributes:
        quality: Quality flags.
        value: 32-bit signed analog value.
    """

    GROUP = ANALOG_INPUT_EVENT_GROUP
    VARIATION = 1
    FORMAT = "<Bi"
    _LABEL = "Analog input event 32-bit"
    _RANGE_FIELD = "value"

    quality: AnalogQuality
    value: int


@register
@dataclass(frozen=True, slots=True)
class AnalogInputEvent16(FixedSizeObject, EventObject):
    """Analog Input Event 16-bit without time (g32v2).

    Attributes:
        quality: Quality flags.
        value: 16-bit signed analog value.
    """

    GROUP = ANALOG_INPUT_EVENT_GROUP
    VARIATION = 2
    FORMAT = "<Bh"
    _LABEL = "Analog input event 16-bit"
    _RANGE_FIELD = "value"

    quality: AnalogQuality
    value: int


@register
@dataclass(frozen=True, slots=True)
class AnalogInputEvent32Time(FixedSizeObject, EventObject):
    """Analog Input Event 32-bit with time (g32v3).

    Attributes:
        quality: Quality flags.
        value: 32-bit signed analog value.
        timestamp: Time when event occurred.
    """

    GROUP = ANALOG_INPUT_EVENT_GROUP
    VARIATION = 3
    FORMAT = "<Bi6s"
    _LABEL = "Analog input event 32-bit with time"
    _LENGTH_MSG = _LENGTH_MSG_WITH_TIME
    _RANGE_FIELD = "value"

    quality: AnalogQuality
    value: int
    timestamp: DNP3Timestamp


@register
@dataclass(frozen=True, slots=True)
class AnalogInputEvent16Time(FixedSizeObject, EventObject):
    """Analog Input Event 16-bit with time (g32v4).

    Attributes:
        quality: Quality flags.
        value: 16-bit signed analog value.
        timestamp: Time when event occurred.
    """

    GROUP = ANALOG_INPUT_EVENT_GROUP
    VARIATION = 4
    FORMAT = "<Bh6s"
    _LABEL = "Analog input event 16-bit with time"
    _LENGTH_MSG = _LENGTH_MSG_WITH_TIME
    _RANGE_FIELD = "value"

    quality: AnalogQuality
    value: int
    timestamp: DNP3Timestamp


@register
@dataclass(frozen=True, slots=True)
class AnalogInputEventFloat(FixedSizeObject, EventObject):
    """Analog Input Event single-precision float without time (g32v5).

    Attributes:
        quality: Quality flags.
        value: Single-precision floating-point value.
    """

    GROUP = ANALOG_INPUT_EVENT_GROUP
    VARIATION = 5
    FORMAT = "<Bf"
    _LABEL = "Analog input event float"

    quality: AnalogQuality
    value: float


@register
@dataclass(frozen=True, slots=True)
class AnalogInputEventDouble(FixedSizeObject, EventObject):
    """Analog Input Event double-precision float without time (g32v6).

    Attributes:
        quality: Quality flags.
        value: Double-precision floating-point value.
    """

    GROUP = ANALOG_INPUT_EVENT_GROUP
    VARIATION = 6
    FORMAT = "<Bd"
    _LABEL = "Analog input event double"

    quality: AnalogQuality
    value: float


@register
@dataclass(frozen=True, slots=True)
class AnalogInputEventFloatTime(FixedSizeObject, EventObject):
    """Analog Input Event single-precision float with time (g32v7).

    Attributes:
        quality: Quality flags.
        value: Single-precision floating-point value.
        timestamp: Time when event occurred.
    """

    GROUP = ANALOG_INPUT_EVENT_GROUP
    VARIATION = 7
    FORMAT = "<Bf6s"
    _LABEL = "Analog input event float with time"
    _LENGTH_MSG = _LENGTH_MSG_WITH_TIME

    quality: AnalogQuality
    value: float
    timestamp: DNP3Timestamp


@register
@dataclass(frozen=True, slots=True)
class AnalogInputEventDoubleTime(FixedSizeObject, EventObject):
    """Analog Input Event double-precision float with time (g32v8).

    Attributes:
        quality: Quality flags.
        value: Double-precision floating-point value.
        timestamp: Time when event occurred.
    """

    GROUP = ANALOG_INPUT_EVENT_GROUP
    VARIATION = 8
    FORMAT = "<Bd6s"
    _LABEL = "Analog input event double with time"
    _LENGTH_MSG = _LENGTH_MSG_WITH_TIME

    quality: AnalogQuality
    value: float
    timestamp: DNP3Timestamp
