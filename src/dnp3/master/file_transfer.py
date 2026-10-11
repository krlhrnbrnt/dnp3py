"""File transfer, master side: the group 70 objects that upload and delete outstation files (IEEE 1815-2012).

Sans-I/O, like `secauth`: `MasterRunner` sends what this module builds and
passes it what arrives. Decoders raise `ValueError`; the runner reports those
as `FileTransferError`.

No file authentication: every g70v3 carries authentication key 0, which an
outstation without file authentication accepts.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from enum import IntEnum

from dnp3.application.fragment import ObjectBlock, RequestFragment
from dnp3.application.header import RequestHeader
from dnp3.core.enums import FunctionCode
from dnp3.core.timestamp import DNP3Timestamp
from dnp3.master import free_format

__all__ = [
    "GROUP",
    "LAST_BLOCK",
    "FileCommandStatus",
    "FileStatus",
    "FileTransportStatus",
    "blocks",
    "build_abort",
    "build_close",
    "build_delete",
    "build_open_write",
    "build_write_block",
    "command_status",
    "encode_name",
    "proposed_block_size",
    "transport_status",
]

GROUP = 70
LAST_BLOCK = 0x8000_0000
"""Bit 31 of a g70v5 block number marks the file's last block."""

_COMMAND = struct.Struct("<HH6sHIIHHH")  # g70v3 up to the file name
_COMMAND_STATUS = struct.Struct("<IIHHB")  # g70v4 up to the optional text
_TRANSPORT = struct.Struct("<II")  # g70v5 up to the file data
_TRANSPORT_STATUS = struct.Struct("<IIB")  # g70v6 up to the optional text
_REQUEST_HEADER_SIZE = 2  # application control and function code
_OBJECT_PREFIX_SIZE = 6  # group, variation, qualifier, count and 2-octet object size
_MAX_U16 = 0xFFFF
_MAX_U32 = 0xFFFF_FFFF
_MODE_NULL = 0
_MODE_WRITE = 2


class FileStatus(IntEnum):
    """Status codes of the g70v4 and g70v6 status field."""

    SUCCESS = 0
    PERMISSION_DENIED = 1
    INVALID_MODE = 2
    FILE_NOT_FOUND = 3
    FILE_LOCKED = 4
    TOO_MANY_OPEN = 5
    INVALID_HANDLE = 6
    WRITE_BLOCK_SIZE = 7
    COMM_LOST = 8
    CANNOT_ABORT = 9
    NOT_OPENED = 16
    HANDLE_EXPIRED = 17
    BUFFER_OVERRUN = 18
    FATAL = 19
    BLOCK_SEQ = 20
    UNDEFINED = 255


@dataclass(frozen=True)
class FileCommandStatus:
    """g70v4: the outstation's answer to an open, close, delete or abort."""

    handle: int
    size: int
    max_block: int
    request_id: int
    status: FileStatus
    text: str


@dataclass(frozen=True)
class FileTransportStatus:
    """g70v6: the outstation's answer to one written block."""

    handle: int
    block_number: int
    status: FileStatus
    text: str


def encode_name(name: str, max_fragment_size: int) -> bytes:
    """`name` as the octets a g70v3 carries.

    Raises:
        ValueError: `name` is not ASCII, or empty, or too long for an open
            request to fit in one fragment of `max_fragment_size` octets.
    """
    limit = min(
        max_fragment_size - _REQUEST_HEADER_SIZE - _OBJECT_PREFIX_SIZE - _COMMAND.size, _MAX_U16 - _COMMAND.size
    )
    try:
        encoded = name.encode("ascii")
    except UnicodeEncodeError as exc:
        msg = f"File name {name!r} is not ASCII"
        raise ValueError(msg) from exc
    if not 1 <= len(encoded) <= limit:
        msg = f"File name must be 1 to {limit} octets, got {len(encoded)}"
        raise ValueError(msg)
    return encoded


def proposed_block_size(max_fragment_size: int) -> int:
    """The largest g70v5 data block one WRITE fragment of `max_fragment_size` octets holds."""
    return min(
        max_fragment_size - _REQUEST_HEADER_SIZE - _OBJECT_PREFIX_SIZE - _TRANSPORT.size, _MAX_U16 - _TRANSPORT.size
    )


def build_open_write(
    name: bytes,
    *,
    size: int,
    permissions: int,
    created: DNP3Timestamp,
    max_block: int,
    request_id: int,
    seq: int,
) -> RequestFragment:
    """OPEN_FILE g70v3 in write mode, which creates the file or replaces one of that name.

    Raises:
        ValueError: `permissions` does not fit 16 bits, or `size` 32 bits.
    """
    if not 0 <= permissions <= _MAX_U16:
        msg = f"Permissions {permissions:#o} do not fit 16 bits"
        raise ValueError(msg)
    if size > _MAX_U32:
        msg = f"File size {size} does not fit 32 bits"
        raise ValueError(msg)
    command = _command(
        name,
        created=created,
        permissions=permissions,
        size=size,
        mode=_MODE_WRITE,
        max_block=max_block,
        request_id=request_id,
    )
    return _request(FunctionCode.OPEN_FILE, command, seq)


def build_delete(name: bytes, *, request_id: int, seq: int) -> RequestFragment:
    """DELETE_FILE g70v3. Only the name and request ID carry meaning."""
    command = _command(
        name,
        created=DNP3Timestamp(0),
        permissions=0,
        size=0,
        mode=_MODE_NULL,
        max_block=0,
        request_id=request_id,
    )
    return _request(FunctionCode.DELETE_FILE, command, seq)


def build_write_block(handle: int, number: int, chunk: bytes, *, last: bool, seq: int) -> RequestFragment:
    """WRITE of one g70v5 block of an open file."""
    wire_number = (number | LAST_BLOCK) if last else number
    obj = _TRANSPORT.pack(handle, wire_number) + chunk
    return _request(FunctionCode.WRITE, free_format.build_block(GROUP, 5, obj), seq)


def build_close(handle: int, *, request_id: int, seq: int) -> RequestFragment:
    """CLOSE_FILE g70v4: keep what was written."""
    return _request(FunctionCode.CLOSE_FILE, _handle_block(handle, request_id), seq)


def build_abort(handle: int, *, request_id: int, seq: int) -> RequestFragment:
    """ABORT_FILE g70v4: drop what was written."""
    return _request(FunctionCode.ABORT_FILE, _handle_block(handle, request_id), seq)


def command_status(objects: Sequence[ObjectBlock]) -> FileCommandStatus:
    """The g70v4 that answers an open, close, delete or abort.

    Raises:
        ValueError: No g70v4, more than one group 70 object, or a malformed one.
    """
    obj = _only_object(objects, 4)
    _check_length(obj, _COMMAND_STATUS, 4)
    handle, size, max_block, request_id, code = _COMMAND_STATUS.unpack_from(obj)
    return FileCommandStatus(handle, size, max_block, request_id, _status(code), _text(obj[_COMMAND_STATUS.size :]))


def transport_status(objects: Sequence[ObjectBlock]) -> FileTransportStatus:
    """The g70v6 that answers a written block.

    Raises:
        ValueError: No g70v6, more than one group 70 object, or a malformed one.
    """
    obj = _only_object(objects, 6)
    _check_length(obj, _TRANSPORT_STATUS, 6)
    handle, block_number, code = _TRANSPORT_STATUS.unpack_from(obj)
    return FileTransportStatus(handle, block_number, _status(code), _text(obj[_TRANSPORT_STATUS.size :]))


def blocks(data: bytes, size: int) -> Iterator[tuple[int, bytes, bool]]:
    """`data` as `(block number, chunk, last)` in order, numbered from 0. Empty data has no blocks."""
    count = -(-len(data) // size)
    for number in range(count):
        yield number, data[number * size : (number + 1) * size], number == count - 1


def _command(
    name: bytes, *, created: DNP3Timestamp, permissions: int, size: int, mode: int, max_block: int, request_id: int
) -> ObjectBlock:
    auth_key = 0
    head = _COMMAND.pack(
        _COMMAND.size, len(name), created.to_bytes(), permissions, auth_key, size, mode, max_block, request_id
    )
    return free_format.build_block(GROUP, 3, head + name)


def _handle_block(handle: int, request_id: int) -> ObjectBlock:
    obj = _COMMAND_STATUS.pack(handle, 0, 0, request_id, FileStatus.SUCCESS)
    return free_format.build_block(GROUP, 4, obj)


def _request(function: FunctionCode, block: ObjectBlock, seq: int) -> RequestFragment:
    return RequestFragment(header=RequestHeader.build(function=function, seq=seq), objects=(block,))


def _only_object(objects: Sequence[ObjectBlock], variation: int) -> bytes:
    found = [block for block in objects if block.header.group == GROUP]
    if not found:
        # An outstation may answer with a null response and report the status later as a g70v4/g70v6 event
        # (deferred status). That is not supported: it would mean waiting for the event in a class poll.
        msg = (
            f"Answer carries no g70v{variation}; an outstation that defers the file status to an event is not supported"
        )
        raise ValueError(msg)
    if len(found) > 1:
        msg = f"Answer carries {len(found)} group 70 objects, expected one g70v{variation}"
        raise ValueError(msg)
    [block] = found
    if block.header.variation != variation:
        msg = f"Answer carries g70v{block.header.variation}, expected g70v{variation}"
        raise ValueError(msg)
    return free_format.object_octets(block)


def _check_length(obj: bytes, fields: struct.Struct, variation: int) -> None:
    if len(obj) < fields.size:
        msg = f"g70v{variation} is {len(obj)} octets, shorter than its {fields.size} octets of fixed fields"
        raise ValueError(msg)


def _status(code: int) -> FileStatus:
    try:
        return FileStatus(code)
    except ValueError:
        return FileStatus.UNDEFINED


def _text(octets: bytes) -> str:
    # The outstation's text reaches exception messages and logs; a control character could forge a log line.
    return "".join(c if c.isprintable() else "�" for c in octets.decode("ascii", errors="replace"))
