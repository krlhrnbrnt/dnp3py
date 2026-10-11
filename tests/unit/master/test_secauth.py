"""Tests for the sans-I/O Secure Authentication module."""

from __future__ import annotations

import hashlib
import hmac
import struct

import pytest
from cryptography.hazmat.primitives.keywrap import aes_key_unwrap, aes_key_wrap

from dnp3.application.fragment import ObjectBlock
from dnp3.application.parser import parse_request, parse_request_header
from dnp3.application.qualifiers import ObjectHeader
from dnp3.core.enums import FunctionCode
from dnp3.master.secauth import (
    Challenge,
    ErrorReport,
    KeyStatus,
    KeyStatusMessage,
    MacAlgorithm,
    SecureAuthError,
    SessionKeys,
    build_challenge_reply,
    build_key_change,
    build_key_status_request,
    check_key_change_reply,
    decode,
    enum_name,
    free_format_block,
    mac,
    wrap_session_keys,
)

# RFC 4231 test case 2 and RFC 2202 test case 2.
JEFE = b"Jefe"
WHAT = b"what do ya want for nothing?"
SHA256 = bytes.fromhex("5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843")
SHA1 = bytes.fromhex("effcdf6ae5eb2fa2d27416d5f184df9c259a7c79")


class TestMac:
    @pytest.mark.parametrize(
        ("algorithm", "expected"),
        [
            (MacAlgorithm.HMAC_SHA1_10, SHA1[:10]),
            (MacAlgorithm.HMAC_SHA256_8, SHA256[:8]),
            (MacAlgorithm.HMAC_SHA256_16, SHA256[:16]),
            (MacAlgorithm.HMAC_SHA1_8, SHA1[:8]),
        ],
    )
    def test_truncated_hmac(self, algorithm: MacAlgorithm, expected: bytes) -> None:
        assert mac(algorithm, JEFE, WHAT) == expected

    def test_parts_are_concatenated(self) -> None:
        assert mac(MacAlgorithm.HMAC_SHA256_16, JEFE, WHAT[:4], WHAT[4:]) == SHA256[:16]

    @pytest.mark.parametrize("algorithm", [0, 1, 6, 255])
    def test_unsupported_algorithm(self, algorithm: int) -> None:
        with pytest.raises(SecureAuthError, match=f"Unsupported MAC algorithm {algorithm}"):
            mac(algorithm, JEFE, WHAT)


class TestKeyWrap:
    def test_rfc3394_128_bit_kek(self) -> None:
        """RFC 3394 4.1, through the same call `wrap_session_keys` makes."""
        kek = bytes.fromhex("000102030405060708090A0B0C0D0E0F")
        data = bytes.fromhex("00112233445566778899AABBCCDDEEFF")

        assert aes_key_wrap(kek, data) == bytes.fromhex("1FA68B0A8112B447AEF34BD8FB5A7B829D3E862371D2CFE5")

    def test_rfc3394_256_bit_kek(self) -> None:
        """RFC 3394 4.6."""
        data = bytes.fromhex("00112233445566778899AABBCCDDEEFF000102030405060708090A0B0C0D0E0F")
        expected = bytes.fromhex("28C9F404C4B810F4CBCCB35CFB87F8263F5786E2D80ED326CBC7F0E71A99F43BFB988B9B7A02DD21")

        assert aes_key_wrap(bytes(range(32)), data) == expected

    def test_layout_and_padding(self) -> None:
        """Key length, control key, monitoring key, key status, then zeros to a multiple of 8."""
        update_key = bytes(range(16))
        control, monitor, status = b"C" * 16, b"M" * 16, b"S" * 15

        plain = aes_key_unwrap(update_key, wrap_session_keys(update_key, control, monitor, status))

        assert plain == b"\x10\x00" + control + monitor + status + bytes(7)  # 49 octets padded to 56
        assert len(plain) % 8 == 0


def test_enum_name() -> None:
    assert enum_name(KeyStatus, 4) == "AUTH_FAIL"
    assert enum_name(KeyStatus, 9) == "9"


def g120(variation: int, obj: bytes, *, count: int = 1) -> ObjectBlock:
    """A g120 block as the parser frames it: count, then a 2-octet size and the object."""
    header = ObjectHeader(group=120, variation=variation, qualifier=0x5B)
    return ObjectBlock(header=header, data=bytes([count]) + struct.pack("<H", len(obj)) + obj)


class TestDecode:
    def test_challenge(self) -> None:
        obj = struct.pack("<IHBB", 0x01020304, 1, 4, 1) + b"\xa5" * 4

        assert decode([g120(1, obj)]) == Challenge(sequence=0x01020304, user=1, mac_algorithm=4)

    def test_challenge_data_too_short(self) -> None:
        with pytest.raises(SecureAuthError, match="challenge"):
            decode([g120(1, struct.pack("<IHBB", 1, 1, 4, 1) + b"\xa5" * 3)])

    def test_key_status(self) -> None:
        obj = struct.pack("<IHBBBH", 7, 1, 1, 2, 4, 4) + b"\xa5" * 4 + b"\x99" * 16

        message = decode([g120(5, obj)])

        assert message == KeyStatusMessage(
            key_change_sequence=7,
            user=1,
            key_wrap_algorithm=1,
            status=2,
            mac_algorithm=4,
            mac_value=b"\x99" * 16,
            raw=obj[:-16],  # the key change wraps the object without its MAC
        )

    def test_key_status_challenge_length_past_end(self) -> None:
        with pytest.raises(SecureAuthError, match="key status"):
            decode([g120(5, struct.pack("<IHBBBH", 7, 1, 1, 2, 4, 9) + b"\xa5" * 4)])

    def test_error(self) -> None:
        obj = struct.pack("<IHHB", 3, 1, 0, 1) + bytes(6) + b"bad mac"

        assert decode([g120(7, obj)]) == ErrorReport(code=1)

    @pytest.mark.parametrize(
        ("objects", "match"),
        [
            ([], "no object"),
            ([ObjectBlock(header=ObjectHeader(group=1, variation=2, qualifier=0x5B), data=b"\x01\x00\x00")], "group 1"),
            ([g120(1, b"", count=2)], "one object"),
            ([g120(7, bytes(13)), g120(7, bytes(13))], "one object"),
            (
                [ObjectBlock(header=ObjectHeader(group=120, variation=1, qualifier=0x5B), data=b"\x01\x09\x00ab")],
                "size",
            ),
            (
                [ObjectBlock(header=ObjectHeader(group=120, variation=1, qualifier=0x5B), data=b"\x01\x01\x00ab")],
                "size",
            ),
            ([ObjectBlock(header=ObjectHeader(group=120, variation=1, qualifier=0x07), data=b"\x01")], "qualifier"),
            ([g120(9, b"\x00" * 16)], "g120v9"),
            ([g120(5, b"\x00" * 10)], "key status"),
            ([g120(7, b"\x00" * 14)], "error"),
        ],
    )
    def test_malformed(self, objects: list[ObjectBlock], match: str) -> None:
        with pytest.raises(SecureAuthError, match=match):
            decode(objects)


def test_key_status_request() -> None:
    request = build_key_status_request(user=1, seq=5)

    data = request.to_bytes()
    header, consumed = parse_request_header(data)
    assert header.function == FunctionCode.AUTHENTICATE_REQUEST
    assert header.control.seq == 5
    # parse_request cannot frame g120v4: the object table has no width for it.
    assert data[consumed:] == bytes([120, 4, 0x07, 1]) + struct.pack("<H", 1)


def test_free_format_block_round_trips() -> None:
    block = free_format_block(2, b"\x01\x02\x03")

    assert block.header.qualifier == 0x5B
    assert block.data == b"\x01\x03\x00\x01\x02\x03"


UPDATE_KEY = bytes(range(16))


def key_status(*, status: int = 2, key_wrap: int = 1, user: int = 1, mac_value: bytes = b"") -> KeyStatusMessage:
    obj = struct.pack("<IHBBBH", 7, user, key_wrap, status, 4, 4) + b"\xa5" * 4 + mac_value
    message = decode([g120(5, obj)])
    assert isinstance(message, KeyStatusMessage)
    return message


def hmac_sha256_16(key: bytes, *parts: bytes) -> bytes:
    return hmac.new(key, b"".join(parts), hashlib.sha256).digest()[:16]


class TestKeyChange:
    def test_wraps_fresh_keys_with_the_key_status(self) -> None:
        status = key_status()

        request, keys = build_key_change(UPDATE_KEY, status, user=1, seq=3, now=12.5)

        parsed = parse_request(request.to_bytes())
        assert parsed.header.function == FunctionCode.AUTHENTICATE_REQUEST
        assert parsed.header.control.seq == 3
        [block] = parsed.objects
        assert (block.header.variation, block.header.qualifier) == (6, 0x5B)
        obj = block.data[3:]
        assert struct.unpack_from("<IH", obj) == (7, 1)
        plain = aes_key_unwrap(UPDATE_KEY, obj[6:])
        assert plain[:2] == b"\x10\x00"
        assert plain[2:18] == keys.control and plain[18:34] == keys.monitor
        assert plain[34:].startswith(status.raw)
        assert keys.control != keys.monitor and keys.set_at == 12.5
        assert keys.control.hex() not in repr(keys)

    def test_wraps_key_status_without_its_mac(self) -> None:
        """On renewal the key status carries a MAC under the old keys; it is not wrapped."""
        status = key_status(status=1, mac_value=b"\x99" * 16)

        request, _ = build_key_change(UPDATE_KEY, status, user=1, seq=0, now=0.0)

        plain = aes_key_unwrap(UPDATE_KEY, request.objects[0].data[3 + 6 :])
        assert plain[34:] == status.raw + bytes(-len(plain[:34] + status.raw) % 8)
        assert b"\x99" * 16 not in plain

    def test_fresh_keys_each_time(self) -> None:
        _, first = build_key_change(UPDATE_KEY, key_status(), user=1, seq=0, now=0.0)
        _, second = build_key_change(UPDATE_KEY, key_status(), user=1, seq=0, now=0.0)

        assert first.control != second.control

    def test_key_wrap_mismatch(self) -> None:
        with pytest.raises(SecureAuthError, match=r"key wrap algorithm 2.*16-octet update key needs 1"):
            build_key_change(UPDATE_KEY, key_status(key_wrap=2), user=1, seq=0, now=0.0)

    def test_other_user(self) -> None:
        with pytest.raises(SecureAuthError, match="user 2"):
            build_key_change(UPDATE_KEY, key_status(user=2), user=1, seq=0, now=0.0)

    def test_reply_accepted(self) -> None:
        request, keys = build_key_change(UPDATE_KEY, key_status(), user=1, seq=0, now=0.0)
        reply = key_status(status=1, mac_value=hmac_sha256_16(keys.monitor, request.to_bytes()))

        assert check_key_change_reply(keys, request, reply) is None

    def test_reply_status_not_ok(self) -> None:
        request, keys = build_key_change(UPDATE_KEY, key_status(), user=1, seq=0, now=0.0)

        with pytest.raises(SecureAuthError, match="AUTH_FAIL"):
            check_key_change_reply(keys, request, key_status(status=4))

    def test_reply_mac_wrong(self) -> None:
        request, keys = build_key_change(UPDATE_KEY, key_status(), user=1, seq=0, now=0.0)

        with pytest.raises(SecureAuthError, match="MAC"):
            check_key_change_reply(keys, request, key_status(status=1, mac_value=bytes(16)))


def test_challenge_reply() -> None:
    keys = SessionKeys(control=b"C" * 16, monitor=b"M" * 16, set_at=0.0)
    challenge = Challenge(sequence=9, user=1, mac_algorithm=4)

    request = build_challenge_reply(
        keys, challenge, user=1, challenge_fragment=b"challenge", request_fragment=b"request", seq=6
    )

    parsed = parse_request(request.to_bytes())
    assert parsed.header.function == FunctionCode.AUTHENTICATE_REQUEST
    assert parsed.header.control.seq == 6
    [block] = parsed.objects
    assert (block.header.variation, block.header.qualifier) == (2, 0x5B)
    assert block.data[3:] == struct.pack("<IH", 9, 1) + hmac_sha256_16(b"C" * 16, b"challenge", b"request")


def test_challenge_reply_unsupported_mac() -> None:
    keys = SessionKeys(control=b"C" * 16, monitor=b"M" * 16, set_at=0.0)
    challenge = Challenge(sequence=9, user=1, mac_algorithm=1)

    with pytest.raises(SecureAuthError, match="Unsupported MAC algorithm 1"):
        build_challenge_reply(keys, challenge, user=1, challenge_fragment=b"", request_fragment=b"", seq=0)


def test_challenge_reply_other_user() -> None:
    keys = SessionKeys(control=b"C" * 16, monitor=b"M" * 16, set_at=0.0)
    challenge = Challenge(sequence=9, user=2, mac_algorithm=4)

    with pytest.raises(SecureAuthError, match="Challenge is for user 2, not user 1"):
        build_challenge_reply(keys, challenge, user=1, challenge_fragment=b"", request_fragment=b"", seq=0)
