"""Stateful byte stream parser for data link frames.

The parser handles:
- Hunting for start bytes (0x05 0x64)
- Buffering partial frames
- CRC validation
- Recovery from corrupted frames
"""

import logging
from collections.abc import Iterator
from enum import Enum, auto

from dnp3.core.crc import compute_crc
from dnp3.datalink.frame import (
    DATA_BLOCK_SIZE,
    HEADER_SIZE,
    HEADER_SIZE_NO_CRC,
    LENGTH_FIELD_OVERHEAD,
    DataLinkFrame,
    DataLinkHeader,
)

logger = logging.getLogger(__name__)

# Start byte values (0x05 0x64)
_START_BYTE_0 = 0x05
_START_BYTE_1 = 0x64


class _ParserState(Enum):
    """Internal parser state."""

    HUNTING = auto()  # Looking for start bytes
    HEADER = auto()  # Collecting header bytes
    DATA = auto()  # Collecting data blocks


def _calculate_frame_size(user_data_length: int) -> int:
    """Calculate total frame size including all CRCs.

    Args:
        user_data_length: Number of user data bytes.

    Returns:
        Total frame size in bytes.
    """
    if user_data_length == 0:
        return HEADER_SIZE

    # Calculate number of full blocks and remaining bytes
    full_blocks = user_data_length // DATA_BLOCK_SIZE
    remainder = user_data_length % DATA_BLOCK_SIZE

    # Each block has 2-byte CRC
    data_size = full_blocks * (DATA_BLOCK_SIZE + 2)
    if remainder > 0:
        data_size += remainder + 2

    return HEADER_SIZE + data_size


def _find_start_bytes(data: bytes | bytearray, start: int = 0) -> int:
    """Find position of start bytes in data.

    Args:
        data: Byte buffer to search.
        start: Starting position.

    Returns:
        Position of start bytes, or -1 if not found.
    """
    pos = start
    while pos <= len(data) - 2:
        if data[pos] == _START_BYTE_0 and data[pos + 1] == _START_BYTE_1:
            return pos
        pos += 1
    return -1


def _validate_header_crc(header_bytes: bytes) -> bool:
    """Validate header CRC.

    Args:
        header_bytes: 10-byte header including CRC.

    Returns:
        True if CRC is valid.
    """
    data = header_bytes[:HEADER_SIZE_NO_CRC]
    crc = int.from_bytes(header_bytes[8:10], byteorder="little")
    return compute_crc(data) == crc


def _validate_header_length(header_bytes: bytes) -> bool:
    """Validate the LENGTH field meets the minimum (IEEE 1815-2012
    9.2.4.1.2: at least 5, the header alone with no user data).

    Args:
        header_bytes: Header bytes, CRC-validated or not; only the LENGTH
            field at offset 2 is read.

    Returns:
        True if LENGTH is at least the minimum.
    """
    return header_bytes[2] >= LENGTH_FIELD_OVERHEAD


def _validate_data_block_crc(block: bytes) -> bool:
    """Validate a data block's CRC.

    Args:
        block: Data block with 2-byte CRC appended.

    Returns:
        True if CRC is valid.
    """
    data = block[:-2]
    crc = int.from_bytes(block[-2:], byteorder="little")
    return compute_crc(data) == crc


def _extract_user_data(data: bytes, user_data_length: int) -> bytes | None:
    """Extract and validate user data from data blocks.

    Args:
        data: Raw data blocks with CRCs.
        user_data_length: Expected user data length.

    Returns:
        Extracted user data, or None if CRC validation fails.
    """
    if user_data_length == 0:
        return b""

    result = bytearray()
    remaining = user_data_length
    pos = 0

    while remaining > 0:
        block_size = min(remaining, DATA_BLOCK_SIZE)
        block_end = pos + block_size + 2  # Include CRC

        if block_end > len(data):
            return None

        block = data[pos:block_end]
        if not _validate_data_block_crc(block):
            return None

        result.extend(block[:-2])
        remaining -= block_size
        pos = block_end

    return bytes(result)


def _bad_data_block(data: bytes, user_data_length: int) -> int:
    """Number, from 1, of the first data block whose CRC fails."""
    pos = 0
    remaining = user_data_length
    block = 1
    while remaining > 0:
        size = min(remaining, DATA_BLOCK_SIZE)
        if not _validate_data_block_crc(data[pos : pos + size + 2]):
            return block
        pos += size + 2
        remaining -= size
        block += 1
    return block


class FrameParser:
    """Stateful byte stream parser for data link frames.

    The parser maintains an internal buffer and yields complete frames
    as they become available. It handles:
    - Partial frames split across multiple feed() calls
    - Garbage bytes between frames
    - CRC validation with automatic recovery

    Example:
        parser = FrameParser()
        for chunk in stream:
            for frame in parser.feed(chunk):
                process(frame)
    """

    def __init__(self) -> None:
        """Initialize parser in hunting state."""
        self._buffer = bytearray()
        self._state = _ParserState.HUNTING
        # Leading bytes of the next skipped run that belong to a rejected candidate, already reported.
        self._quiet = 0

    @property
    def bytes_buffered(self) -> int:
        """Number of bytes currently buffered."""
        return len(self._buffer)

    def reset(self) -> None:
        """Reset parser to initial state, clearing buffer."""
        self._buffer.clear()
        self._state = _ParserState.HUNTING
        self._quiet = 0

    def feed(self, data: bytes) -> Iterator[DataLinkFrame]:
        """Feed bytes to the parser and yield complete frames.

        Args:
            data: Bytes to process.

        Returns:
            Iterator over complete DataLinkFrame instances.
        """
        self._buffer.extend(data)

        frames: list[DataLinkFrame] = []
        while True:
            frame = self._try_parse_frame()
            if frame is None:
                break
            frames.append(frame)
        return iter(frames)

    def _skip(self, count: int) -> None:
        """Drop `count` leading bytes that cannot start a frame."""
        if not count:
            return
        quiet = min(count, self._quiet)
        self._quiet = 0
        if count > quiet:
            logger.warning(
                "Skipped %d bytes before a frame start | %s", count - quiet, self._buffer[quiet:count].hex(" ")
            )
        del self._buffer[:count]

    def _reject(self, reason: str, size: int | None = None) -> None:
        """Report a failed frame candidate and step past its first byte to hunt again.

        Args:
            reason: Why the candidate failed.
            size: Candidate length in bytes, or None when its LENGTH cannot be
                trusted; the warning then shows the header and the whole next
                skipped run is taken as the rest of the candidate.
        """
        shown = HEADER_SIZE if size is None else size
        logger.warning("Rejected link frame: %s | %s", reason, self._buffer[:shown].hex(" "))
        del self._buffer[0]
        self._quiet = len(self._buffer) if size is None else size - 1

    def _try_parse_frame(self) -> DataLinkFrame | None:
        """Try to parse a complete frame from the buffer.

        Loops rather than recursing on CRC failure: a peer sending an
        unbounded run of garbage must not grow the call stack (issue #64).

        Returns:
            A DataLinkFrame if one is complete, None otherwise.
        """
        while True:
            # Hunt for start bytes
            start_pos = _find_start_bytes(self._buffer)
            if start_pos == -1:
                # No start bytes found; keep a trailing first start byte, whose partner may be in the next read.
                keep = 1 if self._buffer and self._buffer[-1] == _START_BYTE_0 else 0
                self._skip(len(self._buffer) - keep)
                return None

            self._skip(start_pos)

            # Need at least header
            if len(self._buffer) < HEADER_SIZE:
                return None

            # Validate header CRC
            if not _validate_header_crc(bytes(self._buffer[:HEADER_SIZE])):
                self._reject("header CRC mismatch")
                continue

            # Validate LENGTH minimum (issue #65): a CRC-valid header can
            # still carry a LENGTH below 5, which would make
            # user_data_length negative. Read the raw byte rather than
            # calling DataLinkHeader.from_bytes, which now raises on this
            # case instead of resyncing.
            if not _validate_header_length(bytes(self._buffer[:HEADER_SIZE])):
                # Same recovery path as a header CRC failure.
                self._reject(f"LENGTH {self._buffer[2]} below minimum {LENGTH_FIELD_OVERHEAD}", HEADER_SIZE)
                continue

            # Parse header to get expected frame size
            header = DataLinkHeader.from_bytes(bytes(self._buffer[:HEADER_SIZE_NO_CRC]))
            frame_size = _calculate_frame_size(header.user_data_length)

            # Wait for complete frame
            if len(self._buffer) < frame_size:
                return None

            # Extract and validate data blocks
            data_start = HEADER_SIZE
            data_bytes = bytes(self._buffer[data_start:frame_size])
            user_data = _extract_user_data(data_bytes, header.user_data_length)

            if user_data is None:
                block = _bad_data_block(data_bytes, header.user_data_length)
                self._reject(f"CRC mismatch in data block {block}", frame_size)
                continue

            # Success - consume frame from buffer and return
            self._quiet = 0
            del self._buffer[:frame_size]
            return DataLinkFrame(header=header, user_data=user_data)
