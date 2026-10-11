"""Tests for the qualifier 0x5B single-object helpers."""

from __future__ import annotations

import pytest

from dnp3.application.fragment import ObjectBlock
from dnp3.application.qualifiers import ObjectHeader
from dnp3.master.free_format import QUALIFIER, build_block, object_octets


def test_build_block_frames_one_object() -> None:
    block = build_block(70, 4, b"\x01\x02\x03")

    assert (block.header.group, block.header.variation, block.header.qualifier) == (70, 4, QUALIFIER)
    assert block.data == b"\x01\x03\x00\x01\x02\x03"


def test_object_octets_round_trips() -> None:
    assert object_octets(build_block(120, 2, b"abc")) == b"abc"


def _block(qualifier: int, data: bytes) -> ObjectBlock:
    return ObjectBlock(header=ObjectHeader(group=70, variation=4, qualifier=qualifier), data=data)


@pytest.mark.parametrize(
    ("block", "message"),
    [
        (_block(0x07, b"\x01\x01\x00a"), "g70v4 with qualifier 0x07, expected 0x5B"),
        (_block(0x5B, b"\x02\x01\x00a"), "g70v4 must carry exactly one object"),
        (_block(0x5B, b"\x01"), "g70v4 must carry exactly one object"),
        (_block(0x5B, b"\x01\x05\x00ab"), "g70v4 object size 5 does not match its 2 octets"),
    ],
)
def test_object_octets_rejects_malformed(block: ObjectBlock, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        object_octets(block)
