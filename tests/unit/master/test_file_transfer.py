"""Tests for the group 70 file transfer codec."""

from __future__ import annotations

import struct
from collections.abc import Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st

from dnp3.application.fragment import ObjectBlock, RequestFragment
from dnp3.application.qualifiers import ObjectHeader
from dnp3.core.enums import FunctionCode
from dnp3.core.timestamp import DNP3Timestamp
from dnp3.master.file_transfer import (
    LAST_BLOCK,
    FileCommandStatus,
    FileStatus,
    FileTransportStatus,
    blocks,
    build_abort,
    build_close,
    build_delete,
    build_open_write,
    build_write_block,
    command_status,
    encode_name,
    proposed_block_size,
    transport_status,
)


def g70(variation: int, obj: bytes) -> ObjectBlock:
    header = ObjectHeader(group=70, variation=variation, qualifier=0x5B)
    return ObjectBlock(header=header, data=b"\x01" + struct.pack("<H", len(obj)) + obj)


def only_object(request: RequestFragment, function: FunctionCode, variation: int) -> bytes:
    """The object octets of a request's single g70 block, after checking its function and header."""
    assert request.header.function == function
    [block] = request.objects
    assert (block.header.group, block.header.variation, block.header.qualifier) == (70, variation, 0x5B)
    assert block.data[0] == 1
    (size,) = struct.unpack_from("<H", block.data, 1)
    assert len(block.data) == 3 + size
    return block.data[3:]


class TestStatusCodes:
    def test_codes(self) -> None:
        assert [(s.name, s.value) for s in FileStatus] == [
            ("SUCCESS", 0),
            ("PERMISSION_DENIED", 1),
            ("INVALID_MODE", 2),
            ("FILE_NOT_FOUND", 3),
            ("FILE_LOCKED", 4),
            ("TOO_MANY_OPEN", 5),
            ("INVALID_HANDLE", 6),
            ("WRITE_BLOCK_SIZE", 7),
            ("COMM_LOST", 8),
            ("CANNOT_ABORT", 9),
            ("NOT_OPENED", 16),
            ("HANDLE_EXPIRED", 17),
            ("BUFFER_OVERRUN", 18),
            ("FATAL", 19),
            ("BLOCK_SEQ", 20),
            ("UNDEFINED", 255),
        ]


class TestNamesAndSizes:
    def test_encode_name(self) -> None:
        assert encode_name("cfg.txt", 2048) == b"cfg.txt"

    def test_longest_name_fits_one_fragment(self) -> None:
        """249 octets less 2 of application header, 6 of object prefix and 26 of g70v3 fields."""
        assert len(encode_name("a" * 215, 249)) == 215

    def test_name_bounded_by_the_16_bit_object_size(self) -> None:
        """A g70v3 is 26 octets of fields plus the name inside a 16-bit object size."""
        assert len(encode_name("a" * 65509, 100_000)) == 65509
        with pytest.raises(ValueError, match="1 to 65509 octets"):
            encode_name("a" * 65510, 100_000)

    @pytest.mark.parametrize(
        ("name", "message"),
        [("", "1 to 215 octets"), ("a" * 216, "1 to 215 octets"), ("café", "not ASCII")],
    )
    def test_bad_name(self, name: str, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            encode_name(name, 249)

    @pytest.mark.parametrize(("fragment", "block"), [(249, 233), (2048, 2032), (100_000, 65527)])
    def test_proposed_block_size(self, fragment: int, block: int) -> None:
        """A WRITE spends 2 octets of application header, 6 of object prefix and 8 of handle and block number."""
        assert proposed_block_size(fragment) == block


class TestBuilders:
    def test_open_write(self) -> None:
        request = build_open_write(
            b"cfg.txt",
            size=10,
            permissions=0o644,
            created=DNP3Timestamp(1),
            max_block=2032,
            request_id=5,
            seq=3,
        )

        assert request.header.control.seq == 3
        assert (
            only_object(request, FunctionCode.OPEN_FILE, 3)
            == bytes.fromhex("1a000700010000000000a401000000000a0000000200f0070500") + b"cfg.txt"
        )

    @pytest.mark.parametrize("permissions", [-1, 0x10000])
    def test_open_write_rejects_permissions(self, permissions: int) -> None:
        with pytest.raises(ValueError, match="16 bits"):
            build_open_write(
                b"x", size=0, permissions=permissions, created=DNP3Timestamp(0), max_block=1, request_id=0, seq=0
            )

    def test_open_write_rejects_size_over_32_bits(self) -> None:
        with pytest.raises(ValueError, match="32 bits"):
            build_open_write(
                b"x", size=0x1_0000_0000, permissions=0, created=DNP3Timestamp(0), max_block=1, request_id=0, seq=0
            )

    def test_delete(self) -> None:
        """Mode NULL, no size, no block size: only the name and request ID matter."""
        request = build_delete(b"x", request_id=7, seq=1)

        assert (
            only_object(request, FunctionCode.DELETE_FILE, 3)
            == bytes.fromhex("1a00010000000000000000000000000000000000000000000700") + b"x"
        )

    @pytest.mark.parametrize(("last", "number"), [(False, "02000000"), (True, "02000080")])
    def test_write_block(self, last: bool, number: str) -> None:
        request = build_write_block(0x1234, 2, b"ij", last=last, seq=4)

        assert only_object(request, FunctionCode.WRITE, 5) == bytes.fromhex("34120000" + number) + b"ij"

    @pytest.mark.parametrize(
        ("build", "function"), [(build_close, FunctionCode.CLOSE_FILE), (build_abort, FunctionCode.ABORT_FILE)]
    )
    def test_close_and_abort(self, build: Callable[..., RequestFragment], function: FunctionCode) -> None:
        request = build(0x1234, request_id=6, seq=2)

        assert only_object(request, function, 4) == bytes.fromhex("34120000000000000000060000")


class TestDecoders:
    def test_command_status(self) -> None:
        obj = struct.pack("<IIHHB", 0x1234, 10, 512, 5, 0) + b"ok"

        assert command_status([g70(4, obj)]) == FileCommandStatus(
            handle=0x1234, size=10, max_block=512, request_id=5, status=FileStatus.SUCCESS, text="ok"
        )

    def test_transport_status(self) -> None:
        obj = struct.pack("<IIB", 0x1234, 2 | LAST_BLOCK, 18)

        assert transport_status([g70(6, obj)]) == FileTransportStatus(
            handle=0x1234, block_number=2 | LAST_BLOCK, status=FileStatus.BUFFER_OVERRUN, text=""
        )

    def test_unknown_code_is_undefined(self) -> None:
        obj = struct.pack("<IIB", 1, 0, 99)

        assert transport_status([g70(6, obj)]).status is FileStatus.UNDEFINED

    def test_status_text_not_ascii(self) -> None:
        obj = struct.pack("<IIHHB", 1, 0, 0, 0, 19) + b"bad \xff"

        assert command_status([g70(4, obj)]).text == "bad �"

    def test_status_text_control_characters_replaced(self) -> None:
        """The text goes into exception messages and logs, where a line break would forge a log line."""
        obj = struct.pack("<IIB", 1, 0, 19) + b"bad\r\nWARNING forged\x1b[2J"

        assert transport_status([g70(6, obj)]).text == "bad��WARNING forged�[2J"

    def test_other_groups_ignored(self) -> None:
        other = ObjectBlock(header=ObjectHeader(group=80, variation=1, qualifier=0x00), data=b"\x07\x07\x00")
        obj = struct.pack("<IIHHB", 1, 0, 0, 0, 0)

        assert command_status([other, g70(4, obj)]).handle == 1

    @pytest.mark.parametrize(
        ("objects", "message"),
        [
            ([], "no g70v4.*defer"),
            ([g70(6, bytes(9))], "g70v6, expected g70v4"),
            ([g70(4, bytes(13)), g70(4, bytes(13))], "2 group 70 objects"),
            ([g70(4, bytes(12))], "12 octets, shorter than"),
            ([ObjectBlock(header=ObjectHeader(group=70, variation=4, qualifier=0x07), data=b"")], "qualifier 0x07"),
        ],
    )
    def test_command_status_rejects(self, objects: list[ObjectBlock], message: str) -> None:
        with pytest.raises(ValueError, match=message):
            command_status(objects)

    def test_transport_status_rejects_short(self) -> None:
        with pytest.raises(ValueError, match="8 octets, shorter than"):
            transport_status([g70(6, bytes(8))])


class TestBlocks:
    def test_blocks(self) -> None:
        assert list(blocks(b"abcdefghij", 4)) == [(0, b"abcd", False), (1, b"efgh", False), (2, b"ij", True)]

    def test_blocks_exact_multiple(self) -> None:
        assert list(blocks(b"abcdefgh", 4)) == [(0, b"abcd", False), (1, b"efgh", True)]

    def test_no_blocks_for_empty_data(self) -> None:
        assert list(blocks(b"", 4)) == []

    @given(st.binary(max_size=300), st.integers(min_value=1, max_value=64))
    def test_blocks_rejoin(self, data: bytes, size: int) -> None:
        parts = list(blocks(data, size))

        assert b"".join(chunk for _, chunk, _ in parts) == data
        assert [number for number, _, _ in parts] == list(range(len(parts)))
        assert [last for _, _, last in parts] == [i == len(parts) - 1 for i in range(len(parts))]
        assert all(1 <= len(chunk) <= size for _, chunk, _ in parts)
