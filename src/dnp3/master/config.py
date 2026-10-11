"""Master station configuration per IEEE 1815-2012.

Configuration options for DNP3 master behavior, timeouts,
and polling settings.
"""

from dataclasses import dataclass, field
from enum import Enum, auto

# DNP3 address limits (IEEE 1815-2012)
MAX_MASTER_ADDRESS = 65519  # 0xFFEF - addresses 0xFFF0-0xFFFF reserved
MAX_OUTSTATION_ADDRESS = 65535  # 0xFFFF - can be any 16-bit value
MIN_FRAGMENT_SIZE = 249  # Minimum required by spec
MAX_USER_NUMBER = 65535
_AES_KEY_LENGTHS = (16, 32)


class TimeSyncMethod(Enum):
    """Time synchronization procedure of IEEE 1815-2012.

    LAN suits networked links, where transmission delay is small and variable;
    the outstation corrects by the time elapsed since it received
    RECORD_CURRENT_TIME. NON_LAN measures the link delay with DELAY_MEASURE
    and corrects by it, which suits serial links, where it is fixed.
    """

    LAN = auto()
    NON_LAN = auto()


@dataclass(frozen=True)
class PollingConfig:
    """Configuration for polling operations.

    Attributes:
        integrity_poll_interval: Seconds between integrity polls (0=disabled).
        class_1_poll_interval: Seconds between Class 1 polls (0=disabled).
        class_2_poll_interval: Seconds between Class 2 polls (0=disabled).
        class_3_poll_interval: Seconds between Class 3 polls (0=disabled).
    """

    integrity_poll_interval: float = 3600.0  # 1 hour
    class_1_poll_interval: float = 0.0  # Disabled
    class_2_poll_interval: float = 0.0  # Disabled
    class_3_poll_interval: float = 0.0  # Disabled


@dataclass(frozen=True)
class SecureAuthConfig:
    """Secure Authentication version 5 settings (IEEE 1815-2012 clause 7).

    Attributes:
        update_key: The user's pre-shared update key. 16 octets for AES-128
            key wrap, 32 for AES-256; the outstation must be configured with
            the same key and algorithm.
        user_number: The user the master acts as. 1 is the spec's default user.
        session_key_change_interval: Seconds between session key changes.
            opendnp3 uses 10 minutes, 5 inside the spec's default 15-minute
            session key expiry.
    """

    update_key: bytes = field(repr=False)
    user_number: int = 1
    session_key_change_interval: float = 600.0

    def __post_init__(self) -> None:
        """Validate configuration values."""
        if len(self.update_key) not in _AES_KEY_LENGTHS:
            msg = f"Update key must be 16 or 32 octets, got {len(self.update_key)}"
            raise ValueError(msg)
        if not 1 <= self.user_number <= MAX_USER_NUMBER:
            msg = f"User number must be 1-{MAX_USER_NUMBER}, got {self.user_number}"
            raise ValueError(msg)
        if not self.session_key_change_interval > 0:
            msg = f"Session key change interval must be > 0, got {self.session_key_change_interval}"
            raise ValueError(msg)


@dataclass(frozen=True)
class MasterConfig:
    """Master station configuration.

    Attributes:
        address: DNP3 address of this master.
        outstation_address: Address of the outstation to communicate with.
        max_fragment_size: Maximum fragment size in bytes.
        response_timeout: Seconds to wait for each response fragment, counted
            from the request or from the previous fragment.
        confirm_timeout: Timeout waiting for confirmations (seconds).
        enable_unsolicited: Accept unsolicited responses.
        startup_integrity_poll: Perform integrity poll on startup.
        disable_unsolicited_on_startup: Send DISABLE_UNSOLICITED on startup.
        enable_unsolicited_on_startup: Send ENABLE_UNSOLICITED on startup.
        time_sync_on_startup: Perform time sync on startup.
        time_sync_method: Procedure `time_sync()` uses when not given one.
        react_to_restart: When a response carries IIN1.7 DEVICE_RESTART,
            `run_polls()` clears it, then runs the startup integrity poll and
            ENABLE_UNSOLICITED as their startup flags say.
        integrity_on_event_overflow: When a response carries IIN2.3
            EVENT_BUFFER_OVERFLOW, `run_polls()` runs an integrity poll to
            recover the current values of points whose events were lost.
        time_sync_on_need_time: When a response carries IIN1.4 NEED_TIME,
            `run_polls()` runs `time_sync()`.
        secure_auth: Secure Authentication settings, or None for an outstation
            without it. Needs the `secauth` extra.
        polling: Polling configuration.
    """

    address: int = 1
    outstation_address: int = 10
    max_fragment_size: int = 2048
    response_timeout: float = 5.0
    confirm_timeout: float = 5.0
    enable_unsolicited: bool = True
    startup_integrity_poll: bool = True
    disable_unsolicited_on_startup: bool = False
    enable_unsolicited_on_startup: bool = True
    time_sync_on_startup: bool = False
    time_sync_method: TimeSyncMethod = TimeSyncMethod.LAN
    react_to_restart: bool = True
    integrity_on_event_overflow: bool = True
    time_sync_on_need_time: bool = False
    secure_auth: SecureAuthConfig | None = None
    polling: PollingConfig = field(default_factory=PollingConfig)

    def __post_init__(self) -> None:
        """Validate configuration values."""
        if not 0 <= self.address <= MAX_MASTER_ADDRESS:
            msg = f"Address must be 0-{MAX_MASTER_ADDRESS}, got {self.address}"
            raise ValueError(msg)
        if not 0 <= self.outstation_address <= MAX_OUTSTATION_ADDRESS:
            msg = f"Outstation address must be 0-{MAX_OUTSTATION_ADDRESS}"
            msg += f", got {self.outstation_address}"
            raise ValueError(msg)
        if self.max_fragment_size < MIN_FRAGMENT_SIZE:
            msg = f"Max fragment size must be >= {MIN_FRAGMENT_SIZE}, got {self.max_fragment_size}"
            raise ValueError(msg)
        if self.response_timeout <= 0:
            msg = f"Response timeout must be > 0, got {self.response_timeout}"
            raise ValueError(msg)
        if self.confirm_timeout <= 0:
            msg = f"Confirm timeout must be > 0, got {self.confirm_timeout}"
            raise ValueError(msg)
