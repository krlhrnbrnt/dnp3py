"""The link parser warns about every frame it rejects and every byte it skips."""

from __future__ import annotations

import logging

import pytest

from dnp3.core.crc import compute_crc
from dnp3.datalink.builder import build_unconfirmed_user_data
from dnp3.datalink.parser import FrameParser

PARSER = "dnp3.datalink.parser"


def valid_frame() -> bytes:
    """A frame with two data blocks: 16 user-data bytes, then 4."""
    return build_unconfirmed_user_data(
        destination=1, source=3, dir_from_master=True, user_data=bytes(range(20))
    ).to_bytes()


def warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == PARSER and r.levelno == logging.WARNING]


def test_clean_stream_is_silent(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=PARSER):
        frames = list(FrameParser().feed(valid_frame() + valid_frame()))
    assert len(frames) == 2
    assert warnings(caplog) == []


def test_header_crc_mismatch(caplog: pytest.LogCaptureFixture) -> None:
    frame = bytearray(valid_frame())
    frame[8] ^= 0xFF
    with caplog.at_level(logging.WARNING, logger=PARSER):
        list(FrameParser().feed(bytes(frame)))
    assert warnings(caplog) == [f"Rejected link frame: header CRC mismatch | {bytes(frame[:10]).hex(' ')}"]


def test_length_below_minimum(caplog: pytest.LogCaptureFixture) -> None:
    header = bytes([0x05, 0x64, 0x03, 0xC4, 0x01, 0x00, 0x03, 0x00])
    data = header + compute_crc(header).to_bytes(2, "little")
    with caplog.at_level(logging.WARNING, logger=PARSER):
        list(FrameParser().feed(data))
    assert warnings(caplog)[0] == f"Rejected link frame: LENGTH 3 below minimum 5 | {data.hex(' ')}"


def test_data_block_crc_mismatch(caplog: pytest.LogCaptureFixture) -> None:
    frame = bytearray(valid_frame())
    frame[29] ^= 0xFF  # Inside block 2: header is 10 bytes, block 1 is 16 + 2 CRC.
    with caplog.at_level(logging.WARNING, logger=PARSER):
        list(FrameParser().feed(bytes(frame)))
    assert warnings(caplog) == [f"Rejected link frame: CRC mismatch in data block 2 | {bytes(frame).hex(' ')}"]


def test_junk_before_a_frame(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=PARSER):
        frames = list(FrameParser().feed(b"\x01\x02\x03" + valid_frame()))
    assert len(frames) == 1
    assert warnings(caplog) == ["Skipped 3 bytes before a frame start | 01 02 03"]


def test_junk_with_no_frame_start_keeps_a_trailing_start_byte(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=PARSER):
        list(FrameParser().feed(b"\x01\x02\x05"))
    assert warnings(caplog) == ["Skipped 2 bytes before a frame start | 01 02"]


def test_rejected_frame_then_good_frame(caplog: pytest.LogCaptureFixture) -> None:
    corrupt = bytearray(valid_frame())
    corrupt[8] ^= 0xFF
    with caplog.at_level(logging.WARNING, logger=PARSER):
        frames = list(FrameParser().feed(bytes(corrupt) + valid_frame()))
    assert len(frames) == 1
    assert len(warnings(caplog)) == 1, "the rest of the rejected frame is not reported again as skipped bytes"


def test_junk_after_a_rejected_frame_is_still_reported(caplog: pytest.LogCaptureFixture) -> None:
    corrupt = bytearray(valid_frame())
    corrupt[29] ^= 0xFF
    junk = bytes(range(0x10, 0x10 + 40))
    with caplog.at_level(logging.WARNING, logger=PARSER):
        frames = list(FrameParser().feed(bytes(corrupt) + junk + valid_frame()))
    assert len(frames) == 1
    assert warnings(caplog) == [
        f"Rejected link frame: CRC mismatch in data block 2 | {bytes(corrupt).hex(' ')}",
        f"Skipped 40 bytes before a frame start | {junk.hex(' ')}",
    ]
