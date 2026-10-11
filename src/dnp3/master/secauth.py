"""Secure Authentication version 5, master side (IEEE 1815-2012 clause 7).

Builds and checks the group 120 objects a master exchanges with an outstation
that challenges critical requests. Sans-I/O: `MasterRunner` sends what this
module builds and passes it what arrives.

The only module that needs the optional `cryptography` package, for AES key
wrap (RFC 3394), which the standard library lacks.
"""

from __future__ import annotations

import hmac
import secrets
import struct
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import IntEnum

from dnp3.application.fragment import ObjectBlock, RequestFragment
from dnp3.application.header import RequestHeader
from dnp3.application.qualifiers import ObjectHeader, PrefixCode, RangeCode
from dnp3.core.enums import FunctionCode
from dnp3.master import free_format

try:
    from cryptography.hazmat.primitives.keywrap import aes_key_wrap
except ImportError as exc:  # pragma: no cover - covered in a subprocess without cryptography
    msg = "Secure Authentication needs the 'secauth' extra: pip install \"dnp3py[secauth]\""
    raise ImportError(msg) from exc

__all__ = [
    "GROUP",
    "MIN_CHALLENGE_DATA_SIZE",
    "SESSION_KEY_SIZE",
    "AuthErrorCode",
    "AuthMessage",
    "Challenge",
    "ErrorReport",
    "KeyStatus",
    "KeyStatusMessage",
    "KeyWrapAlgorithm",
    "MacAlgorithm",
    "SecureAuthError",
    "SessionKeys",
    "build_challenge_reply",
    "build_key_change",
    "build_key_status_request",
    "check_key_change_reply",
    "decode",
    "enum_name",
    "free_format_block",
    "mac",
    "wrap_session_keys",
]

SESSION_KEY_SIZE = 16
"""Octets in each session key; 16 is the spec minimum and what opendnp3 uses."""

_AES_128_KEY_SIZE = 16


class SecureAuthError(ValueError):
    """A group 120 object is malformed or cannot be answered."""


class MacAlgorithm(IntEnum):
    """MAC algorithm codes of the g120v1 and g120v5 MAL field.

    Code 1, HMAC-SHA-1 truncated to 4 octets, is obsolete in SAv5 and not
    supported; neither is 6, AES-GMAC.
    """

    HMAC_SHA1_10 = 2
    HMAC_SHA256_8 = 3
    HMAC_SHA256_16 = 4
    HMAC_SHA1_8 = 5


_MACS: dict[int, tuple[str, int]] = {
    MacAlgorithm.HMAC_SHA1_10: ("sha1", 10),
    MacAlgorithm.HMAC_SHA256_8: ("sha256", 8),
    MacAlgorithm.HMAC_SHA256_16: ("sha256", 16),
    MacAlgorithm.HMAC_SHA1_8: ("sha1", 8),
}


class KeyWrapAlgorithm(IntEnum):
    """Key wrap algorithm codes of the g120v5 KWA field."""

    AES_128 = 1
    AES_256 = 2


class KeyStatus(IntEnum):
    """Session key status codes of the g120v5 KST field."""

    OK = 1
    NOT_INIT = 2
    COMM_FAIL = 3
    AUTH_FAIL = 4


class AuthErrorCode(IntEnum):
    """Error codes of the g120v7 ERR field."""

    AUTHENTICATION_FAILED = 1
    AGGRESSIVE_MODE_UNSUPPORTED = 4
    MAC_NOT_SUPPORTED = 5
    KEY_WRAP_NOT_SUPPORTED = 6
    AUTHORIZATION_FAILED = 7
    UPDATE_KEY_METHOD_NOT_PERMITTED = 8
    INVALID_SIGNATURE = 9
    INVALID_CERTIFICATION_DATA = 10
    UNKNOWN_USER = 11
    MAX_SESSION_KEY_STATUS_REQUESTS_EXCEEDED = 12


def enum_name(enum: type[IntEnum], value: int) -> str:
    """The member name for `value`, or the number when the spec defines no such code."""
    try:
        return enum(value).name
    except ValueError:
        return str(value)


def mac(algorithm: int, key: bytes, *parts: bytes) -> bytes:
    """HMAC of the concatenated `parts`, truncated as `algorithm` says.

    Raises:
        SecureAuthError: `algorithm` is not one this module supports.
    """
    try:
        digest, length = _MACS[algorithm]
    except KeyError:
        msg = f"Unsupported MAC algorithm {algorithm}"
        raise SecureAuthError(msg) from None
    return hmac.new(key, b"".join(parts), digest).digest()[:length]


def wrap_session_keys(update_key: bytes, control: bytes, monitor: bytes, key_status: bytes) -> bytes:
    """Wrap new session keys for a g120v6 key change.

    The wrapped data is the 2-octet key length, the control and monitoring
    keys, and the g120v5 object the outstation last sent, without its MAC,
    which ties the keys to its challenge. RFC 3394 wraps whole 8-octet
    blocks, so the data is zero-padded first.
    """
    data = struct.pack("<H", len(control)) + control + monitor + key_status
    return aes_key_wrap(update_key, data + bytes(-len(data) % 8))


GROUP = 120
MIN_CHALLENGE_DATA_SIZE = 4
"""Clause 7 requires at least 4 octets of challenge data."""

_CHALLENGE = struct.Struct("<IHBB")
_KEY_STATUS = struct.Struct("<IHBBBH")
_ERROR = struct.Struct("<IHHB")
_ERROR_TIME_SIZE = 6


@dataclass(frozen=True)
class Challenge:
    """g120v1: the outstation will not act on a request until the master proves it holds the session key."""

    sequence: int
    user: int
    mac_algorithm: int


@dataclass(frozen=True)
class KeyStatusMessage:
    """g120v5: the outstation's session key status for a user.

    Attributes:
        raw: The object up to its MAC; the next key change wraps it with the keys.
    """

    key_change_sequence: int
    user: int
    key_wrap_algorithm: int
    status: int
    mac_algorithm: int
    mac_value: bytes
    raw: bytes


@dataclass(frozen=True)
class ErrorReport:
    """g120v7: the outstation refused a request or a key change."""

    code: int


AuthMessage = Challenge | KeyStatusMessage | ErrorReport


def free_format_block(variation: int, obj: bytes) -> ObjectBlock:
    """One g120 object behind qualifier 0x5B: a count of 1, the 2-octet object size, the object."""
    return free_format.build_block(GROUP, variation, obj)


def build_key_status_request(user: int, seq: int) -> RequestFragment:
    """AUTHENTICATE_REQUEST g120v4: ask for the user's session key status, the first step of a key change."""
    header = ObjectHeader.build(group=GROUP, variation=4, prefix=PrefixCode.NONE, range_code=RangeCode.UINT8_COUNT)
    return _auth_request(ObjectBlock(header=header, data=b"\x01" + struct.pack("<H", user)), seq)


def _auth_request(block: ObjectBlock, seq: int) -> RequestFragment:
    return RequestFragment(
        header=RequestHeader.build(function=FunctionCode.AUTHENTICATE_REQUEST, seq=seq), objects=(block,)
    )


def decode(objects: Sequence[ObjectBlock]) -> AuthMessage:
    """Decode the g120 object an AUTHENTICATE_RESPONSE carries.

    Exactly one object, and nothing after it: a challenge reply's MAC covers
    the whole fragment, so every byte of it must be one the master parsed.

    Raises:
        SecureAuthError: The response carries no g120v1, v5 or v7 object, more
            than one object, or a malformed one.
    """
    if not objects:
        msg = "Authentication response carries no object"
        raise SecureAuthError(msg)
    if len(objects) != 1:
        msg = f"Authentication response must carry exactly one object, not {len(objects)}"
        raise SecureAuthError(msg)
    [block] = objects
    if block.header.group != GROUP:
        msg = f"Authentication response carries group {block.header.group}, not group {GROUP}"
        raise SecureAuthError(msg)
    obj = _free_format_object(block)
    match block.header.variation:
        case 1:
            return _decode_challenge(obj)
        case 5:
            return _decode_key_status(obj)
        case 7:
            return _decode_error(obj)
    msg = f"Unexpected g120v{block.header.variation} in an authentication response"
    raise SecureAuthError(msg)


def _free_format_object(block: ObjectBlock) -> bytes:
    try:
        return free_format.object_octets(block)
    except ValueError as exc:
        raise SecureAuthError(str(exc)) from exc


def _decode_challenge(obj: bytes) -> Challenge:
    if len(obj) < _CHALLENGE.size + MIN_CHALLENGE_DATA_SIZE:
        msg = f"g120v1 challenge of {len(obj)} octets is too short"
        raise SecureAuthError(msg)
    sequence, user, mac_algorithm, _ = _CHALLENGE.unpack_from(obj)
    return Challenge(sequence, user, mac_algorithm)


def _decode_key_status(obj: bytes) -> KeyStatusMessage:
    if len(obj) < _KEY_STATUS.size:
        msg = f"g120v5 key status of {len(obj)} octets is too short"
        raise SecureAuthError(msg)
    ksq, user, key_wrap, status, mac_algorithm, length = _KEY_STATUS.unpack_from(obj)
    end = _KEY_STATUS.size + length
    if len(obj) < end:
        msg = f"g120v5 key status challenge data of {length} octets runs past the object"
        raise SecureAuthError(msg)
    return KeyStatusMessage(ksq, user, key_wrap, status, mac_algorithm, obj[end:], obj[:end])


def _decode_error(obj: bytes) -> ErrorReport:
    text_start = _ERROR.size + _ERROR_TIME_SIZE
    if len(obj) < text_start:
        msg = f"g120v7 error of {len(obj)} octets is too short"
        raise SecureAuthError(msg)
    *_, code = _ERROR.unpack_from(obj)
    return ErrorReport(code)


@dataclass(frozen=True)
class SessionKeys:
    """The session keys in force, and the event-loop time they were set."""

    control: bytes = field(repr=False)
    monitor: bytes = field(repr=False)
    set_at: float


def build_key_change(
    update_key: bytes, status: KeyStatusMessage, *, user: int, seq: int, now: float
) -> tuple[RequestFragment, SessionKeys]:
    """AUTHENTICATE_REQUEST g120v6 carrying fresh session keys wrapped with the update key.

    Raises:
        SecureAuthError: The key status is for another user, or the outstation
            expects a key wrap algorithm the update key's length does not fit.
    """
    if status.user != user:
        msg = f"Key status is for user {status.user}, not user {user}"
        raise SecureAuthError(msg)
    needed = KeyWrapAlgorithm.AES_128 if len(update_key) == _AES_128_KEY_SIZE else KeyWrapAlgorithm.AES_256
    if status.key_wrap_algorithm != needed:
        msg = (
            f"Outstation uses key wrap algorithm {status.key_wrap_algorithm}; "
            f"a {len(update_key)}-octet update key needs {needed.value} ({needed.name})"
        )
        raise SecureAuthError(msg)
    keys = SessionKeys(
        control=secrets.token_bytes(SESSION_KEY_SIZE), monitor=secrets.token_bytes(SESSION_KEY_SIZE), set_at=now
    )
    wrapped = wrap_session_keys(update_key, keys.control, keys.monitor, status.raw)
    obj = struct.pack("<IH", status.key_change_sequence, user) + wrapped
    return _auth_request(free_format_block(6, obj), seq), keys


def check_key_change_reply(keys: SessionKeys, key_change: RequestFragment, status: KeyStatusMessage) -> None:
    """Check the key status that answers a key change.

    The outstation proves it unwrapped the same keys by sending the MAC of the
    key change under the monitoring key.

    Raises:
        SecureAuthError: The status is not OK, or the MAC does not match.
    """
    if status.status != KeyStatus.OK:
        msg = f"Outstation reports key status {enum_name(KeyStatus, status.status)} after the key change"
        raise SecureAuthError(msg)
    expected = mac(status.mac_algorithm, keys.monitor, key_change.to_bytes())
    if not hmac.compare_digest(expected, status.mac_value):
        msg = "Key status MAC does not match: the outstation holds different session keys"
        raise SecureAuthError(msg)


def build_challenge_reply(
    keys: SessionKeys,
    challenge: Challenge,
    *,
    user: int,
    challenge_fragment: bytes,
    request_fragment: bytes,
    seq: int,
) -> RequestFragment:
    """AUTHENTICATE_REQUEST g120v2 answering a challenge.

    The MAC covers the challenge fragment, then the challenged request, under
    the control key. `seq` is the challenged request's sequence, which the
    challenge carries too.

    Raises:
        SecureAuthError: The challenge is for another user, or asks for an
            unsupported MAC algorithm.
    """
    if challenge.user != user:
        msg = f"Challenge is for user {challenge.user}, not user {user}"
        raise SecureAuthError(msg)
    value = mac(challenge.mac_algorithm, keys.control, challenge_fragment, request_fragment)
    obj = struct.pack("<IH", challenge.sequence, user) + value
    return _auth_request(free_format_block(2, obj), seq)
