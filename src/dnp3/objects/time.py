"""Time objects per IEEE 1815-2012.

Group 50: Time and Date
- Variation 1: Absolute time (6 bytes)
- Variation 3: Absolute time at last recorded time (6 bytes)
- Variation 4: Indexed absolute time

Group 51: Time and Date CTO (Common Time of Occurrence)
- Variation 1: Absolute time CTO
- Variation 2: Unsynchronized CTO

Group 52: Time Delay
- Variation 1: Coarse time delay (2 bytes, seconds)
- Variation 2: Fine time delay (2 bytes, milliseconds)
"""

from dataclasses import dataclass

from dnp3.core.timestamp import DNP3Timestamp
from dnp3.objects.base import FixedSizeObject, StaticObject
from dnp3.objects.registry import register

# Group numbers
TIME_AND_DATE_GROUP = 50
TIME_CTO_GROUP = 51
TIME_DELAY_GROUP = 52

# Timestamp size
TIMESTAMP_SIZE = 6


@register
@dataclass(frozen=True, slots=True)
class TimeAndDate(FixedSizeObject, StaticObject):
    """Time and Date (g50v1).

    6 bytes: 48-bit milliseconds since epoch.

    Attributes:
        timestamp: Absolute time value.
    """

    GROUP = TIME_AND_DATE_GROUP
    VARIATION = 1
    FORMAT = "<6s"
    _LABEL = "Time and date"

    timestamp: DNP3Timestamp


@register
@dataclass(frozen=True, slots=True)
class TimeAndDateRecorded(FixedSizeObject, StaticObject):
    """Time and Date at Last Recorded Time (g50v3).

    6 bytes: 48-bit milliseconds since epoch. In the LAN time synchronization
    procedure the master writes the time at which it sent RECORD_CURRENT_TIME,
    and the outstation corrects its clock by the time elapsed since it recorded
    that request's arrival.

    Attributes:
        timestamp: Master time when RECORD_CURRENT_TIME was sent.
    """

    GROUP = TIME_AND_DATE_GROUP
    VARIATION = 3
    FORMAT = "<6s"
    _LABEL = "Recorded time and date"

    timestamp: DNP3Timestamp


@register
@dataclass(frozen=True, slots=True)
class TimeCTO(FixedSizeObject, StaticObject):
    """Time and Date CTO - Common Time of Occurrence (g51v1).

    6 bytes: 48-bit milliseconds since epoch.
    Used as a reference time for relative time values.

    Attributes:
        timestamp: Absolute time value.
    """

    GROUP = TIME_CTO_GROUP
    VARIATION = 1
    FORMAT = "<6s"
    _LABEL = "Time CTO"

    timestamp: DNP3Timestamp


@register
@dataclass(frozen=True, slots=True)
class TimeCTOUnsync(FixedSizeObject, StaticObject):
    """Unsynchronized Time and Date CTO (g51v2).

    6 bytes: 48-bit milliseconds since epoch.
    Indicates time source is not synchronized.

    Attributes:
        timestamp: Absolute time value (unsynchronized).
    """

    GROUP = TIME_CTO_GROUP
    VARIATION = 2
    FORMAT = "<6s"
    _LABEL = "Unsync time CTO"

    timestamp: DNP3Timestamp


@register
@dataclass(frozen=True, slots=True)
class TimeDelayCoarse(FixedSizeObject, StaticObject):
    """Coarse Time Delay (g52v1).

    2 bytes: Delay in seconds.
    Used in delay measurement procedure.

    Attributes:
        delay_seconds: Delay value in seconds (0-65535).
    """

    GROUP = TIME_DELAY_GROUP
    VARIATION = 1
    FORMAT = "<H"
    _LABEL = "Coarse time delay"
    _RANGE_FIELD = "delay_seconds"
    _RANGE_LABEL = "Delay"

    delay_seconds: int


@register
@dataclass(frozen=True, slots=True)
class TimeDelayFine(FixedSizeObject, StaticObject):
    """Fine Time Delay (g52v2).

    2 bytes: Delay in milliseconds.
    Used in delay measurement procedure.

    Attributes:
        delay_ms: Delay value in milliseconds (0-65535).
    """

    GROUP = TIME_DELAY_GROUP
    VARIATION = 2
    FORMAT = "<H"
    _LABEL = "Fine time delay"
    _RANGE_FIELD = "delay_ms"
    _RANGE_LABEL = "Delay"

    delay_ms: int
