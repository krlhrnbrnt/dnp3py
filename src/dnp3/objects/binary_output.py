"""Binary Output objects per IEEE 1815-2012.

Group 10: Binary Output Status
- Variation 1: Packed format (1 bit per point)
- Variation 2: With flags (1 byte per point)

Group 11: Binary Output Event
- Variation 1: Without time (1 byte flags)
- Variation 2: With time (7 bytes: flags + 6-byte timestamp)

Group 12: Control Relay Output Block (CROB)
- Variation 1: Standard CROB (11 bytes)
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import ClassVar

# ControlCode is defined once, in core; this module re-exports it for the object path.
from dnp3.core.enums import ControlCode as ControlCode
from dnp3.core.flags import BinaryQuality
from dnp3.core.timestamp import DNP3Timestamp
from dnp3.objects.base import EventObject, FixedSizeObject, StaticObject
from dnp3.objects.binary_input import _BinaryFlags
from dnp3.objects.registry import register

# Group numbers
BINARY_OUTPUT_STATIC_GROUP = 10
BINARY_OUTPUT_EVENT_GROUP = 11
CROB_GROUP = 12

# Timestamp size
TIMESTAMP_SIZE = 6

# State bit mask
STATE_BIT = 0x80


class CommandStatus(IntEnum):
    """Command status codes (Table 4-2)."""

    SUCCESS = 0  # Command succeeded
    TIMEOUT = 1  # Command timed out
    NO_SELECT = 2  # No matching SELECT
    FORMAT_ERROR = 3  # Format error in control
    NOT_SUPPORTED = 4  # Control not supported
    ALREADY_ACTIVE = 5  # Control already active
    HARDWARE_ERROR = 6  # Hardware error
    LOCAL = 7  # Local control in effect
    TOO_MANY_OPS = 8  # Too many operations
    NOT_AUTHORIZED = 9  # Not authorized
    AUTOMATION_INHIBIT = 10  # Automation inhibit
    PROCESSING_LIMITED = 11  # Processing capacity limited
    OUT_OF_RANGE = 12  # Parameter out of range
    DOWNSTREAM_LOCAL = 13  # Downstream device in local
    ALREADY_COMPLETE = 14  # Operation already complete
    BLOCKED = 15  # Operation blocked
    CANCELLED = 16  # Operation cancelled
    BLOCKED_OTHER_MASTER = 17  # Blocked by other master
    DOWNSTREAM_FAIL = 18  # Downstream device failed
    NON_PARTICIPATING = 126  # Non-participating point
    UNDEFINED = 127  # Undefined error


@register
@dataclass(frozen=True, slots=True)
class BinaryOutputFlags(_BinaryFlags, StaticObject):
    """Binary Output with flags (g10v2).

    Each point is 1 byte containing quality flags and state.

    Attributes:
        quality: Quality flags (bits 0-6).
        state: Binary state (bit 7): False=off, True=on.
    """

    GROUP = BINARY_OUTPUT_STATIC_GROUP
    VARIATION = 2
    FORMAT = "<B"
    _LABEL = "Binary output"

    quality: BinaryQuality
    state: bool

    @property
    def is_online(self) -> bool:
        """Check if point is online."""
        return bool(self.quality & BinaryQuality.ONLINE)


@register
@dataclass(frozen=True, slots=True)
class BinaryOutputEvent(_BinaryFlags, EventObject):
    """Binary Output Event without time (g11v1).

    Each event is 1 byte containing quality flags and state.

    Attributes:
        quality: Quality flags (bits 0-6).
        state: Binary state (bit 7): False=off, True=on.
    """

    GROUP = BINARY_OUTPUT_EVENT_GROUP
    VARIATION = 1
    FORMAT = "<B"
    _LABEL = "Binary output event"

    quality: BinaryQuality
    state: bool


@register
@dataclass(frozen=True, slots=True)
class BinaryOutputEventTime(_BinaryFlags, EventObject):
    """Binary Output Event with absolute time (g11v2).

    Each event is 7 bytes: 1 byte flags + 6 byte timestamp.

    Attributes:
        quality: Quality flags (bits 0-6).
        state: Binary state (bit 7): False=off, True=on.
        timestamp: Time when event occurred.
    """

    GROUP = BINARY_OUTPUT_EVENT_GROUP
    VARIATION = 2
    FORMAT = "<B6s"
    _LABEL = "Binary output event with time"

    quality: BinaryQuality
    state: bool
    timestamp: DNP3Timestamp


@register
@dataclass(frozen=True, slots=True)
class CROB(FixedSizeObject, StaticObject):
    """Control Relay Output Block (g12v1).

    11-byte control command for binary output.

    Attributes:
        control_code: Control-code octet (TCC, Clear, Queue and Op Type fields).
        count: Number of times to execute.
        on_time_ms: Duration of ON state in milliseconds.
        off_time_ms: Duration of OFF state in milliseconds.
        status: Command status (typically 0 for requests).
    """

    GROUP = CROB_GROUP
    VARIATION = 1
    FORMAT = "<BBIIB"
    _LABEL = "CROB"

    control_code: ControlCode
    count: int
    on_time_ms: int
    off_time_ms: int
    status: CommandStatus

    # Constants
    MAX_COUNT: ClassVar[int] = 255
    MAX_TIME_MS: ClassVar[int] = 0xFFFFFFFF

    def __post_init__(self) -> None:
        """Validate CROB fields."""
        if not 0 <= self.count <= self.MAX_COUNT:
            msg = f"Count {self.count} out of range (0-{self.MAX_COUNT})"
            raise ValueError(msg)
        if not 0 <= self.on_time_ms <= self.MAX_TIME_MS:
            msg = f"On time {self.on_time_ms} out of range (0-{self.MAX_TIME_MS})"
            raise ValueError(msg)
        if not 0 <= self.off_time_ms <= self.MAX_TIME_MS:
            msg = f"Off time {self.off_time_ms} out of range (0-{self.MAX_TIME_MS})"
            raise ValueError(msg)

    @classmethod
    def pulse_on(
        cls,
        on_time_ms: int = 1000,
        off_time_ms: int = 0,
        count: int = 1,
    ) -> "CROB":
        """Create a pulse-on CROB command."""
        return cls(
            control_code=ControlCode.PULSE_ON,
            count=count,
            on_time_ms=on_time_ms,
            off_time_ms=off_time_ms,
            status=CommandStatus.SUCCESS,
        )

    @classmethod
    def pulse_off(
        cls,
        on_time_ms: int = 0,
        off_time_ms: int = 1000,
        count: int = 1,
    ) -> "CROB":
        """Create a pulse-off CROB command."""
        return cls(
            control_code=ControlCode.PULSE_OFF,
            count=count,
            on_time_ms=on_time_ms,
            off_time_ms=off_time_ms,
            status=CommandStatus.SUCCESS,
        )

    @classmethod
    def latch_on(cls) -> "CROB":
        """Create a latch-on CROB command."""
        return cls(
            control_code=ControlCode.LATCH_ON,
            count=1,
            on_time_ms=0,
            off_time_ms=0,
            status=CommandStatus.SUCCESS,
        )

    @classmethod
    def latch_off(cls) -> "CROB":
        """Create a latch-off CROB command."""
        return cls(
            control_code=ControlCode.LATCH_OFF,
            count=1,
            on_time_ms=0,
            off_time_ms=0,
            status=CommandStatus.SUCCESS,
        )
