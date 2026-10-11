"""File upload and delete in `MasterRunner`, against a scripted outstation."""

from __future__ import annotations

import asyncio
import struct
from typing import Any

import pytest

from dnp3.application.builder import build_null_response, build_response
from dnp3.application.fragment import ObjectBlock
from dnp3.application.qualifiers import ObjectHeader
from dnp3.core.enums import FunctionCode
from dnp3.core.flags import IIN
from dnp3.master import file_transfer
from dnp3.master.file_transfer import LAST_BLOCK, FileStatus
from dnp3.master.runner import (
    FileTransferError,
    LinkError,
    MasterRunnerError,
    RequestRejectedError,
    ResponseTimeoutError,
)
from tests.unit.master.test_tcp_runner import FakeOutstation, Reply, assert_nothing_sent, open_runner

HANDLE = 0x1234


def g70(variation: int, obj: bytes) -> ObjectBlock:
    """One free-format (qualifier 0x5B) group 70 object."""
    header = ObjectHeader(group=70, variation=variation, qualifier=0x5B)
    return ObjectBlock(header=header, data=b"\x01" + struct.pack("<H", len(obj)) + obj)


def g70_object(request: bytes) -> tuple[int, bytes]:
    """Variation and object octets of a request's single g70 object, read from the raw bytes."""
    group, variation, qualifier = request[2:5]
    assert (group, qualifier) == (70, 0x5B)
    assert request[5] == 1
    (size,) = struct.unpack_from("<H", request, 6)
    obj = request[8:]
    assert len(obj) == size
    return variation, obj


def request_id_of(request: bytes) -> int:
    """The request ID of a g70v3 (offset 24) or g70v4 (offset 10) request."""
    variation, obj = g70_object(request)
    (request_id,) = struct.unpack_from("<H", obj, 24 if variation == 3 else 10)
    return request_id


def status_reply(
    status: FileStatus = FileStatus.SUCCESS, *, max_block: int = 4, request_id: int | None = None, text: bytes = b""
) -> Reply:
    """Answer a file command with a g70v4 for `HANDLE`, echoing its request ID unless `request_id` is given."""

    def reply(request: bytes) -> bytes:
        echoed = request_id_of(request) if request_id is None else request_id
        obj = struct.pack("<IIHHB", HANDLE, 0, max_block, echoed, status) + text
        return build_response(objects=(g70(4, obj),), seq=request[0] & 0x0F).to_bytes()

    return reply


def block_reply(
    status: FileStatus = FileStatus.SUCCESS, *, handle: int = HANDLE, number_offset: int = 0, strip_last: bool = False
) -> Reply:
    """Answer a WRITE with a g70v6 echoing its block number."""

    def reply(request: bytes) -> bytes:
        _, obj = g70_object(request)
        _, number = struct.unpack_from("<II", obj)
        if strip_last:
            number &= ~LAST_BLOCK
        answer = struct.pack("<IIB", handle, number + number_offset, status)
        return build_response(objects=(g70(6, answer),), seq=request[0] & 0x0F).to_bytes()

    return reply


def null_answer(iin: IIN | None = None) -> Reply:
    return lambda request: build_null_response(iin=iin, seq=request[0] & 0x0F).to_bytes()


def serve(peer: FakeOutstation, *replies: Reply | None) -> asyncio.Task[list[bytes]]:
    """Answer requests in turn with `replies`, collecting them; None leaves that request unanswered."""

    async def run() -> list[bytes]:
        received: list[bytes] = []
        for reply in replies:
            [request] = await peer.read_fragments(1)
            received.append(request)
            if reply is not None:
                await peer.send_fragment(reply(request))
        return received

    return asyncio.create_task(run())


class TestDelete:
    async def test_delete(self) -> None:
        runner, peer, _ = await open_runner()
        server = serve(peer, status_reply(max_block=0))

        assert await runner.delete_file("old.cfg") is None
        [request] = await server

        assert request[1] == FunctionCode.DELETE_FILE
        variation, obj = g70_object(request)
        assert variation == 3
        assert struct.unpack_from("<H", obj, 20) == (0,)  # operational mode NULL
        assert obj[26:] == b"old.cfg"

    async def test_missing_file_raises(self) -> None:
        runner, peer, _ = await open_runner()
        server = serve(peer, status_reply(FileStatus.FILE_NOT_FOUND, text=b"no such file"))

        with pytest.raises(FileTransferError) as excinfo:
            await runner.delete_file("old.cfg")
        await server

        assert excinfo.value.status is FileStatus.FILE_NOT_FOUND
        assert excinfo.value.text == "no such file"
        assert "DELETE_FILE of 'old.cfg' failed: FILE_NOT_FOUND: no such file" in str(excinfo.value)

    async def test_mismatched_request_id_raises(self) -> None:
        runner, peer, _ = await open_runner()
        server = serve(peer, status_reply(request_id=99))

        with pytest.raises(FileTransferError, match="request ID 99, expected 0") as excinfo:
            await runner.delete_file("old.cfg")
        await server

        assert excinfo.value.status is None

    async def test_deferred_status_raises(self) -> None:
        runner, peer, _ = await open_runner()
        server = serve(peer, null_answer())

        with pytest.raises(FileTransferError, match="defers the file status"):
            await runner.delete_file("old.cfg")
        await server

    async def test_rejected_iin_raises(self) -> None:
        runner, peer, _ = await open_runner()
        server = serve(peer, null_answer(IIN.NO_FUNC_CODE_SUPPORT))

        with pytest.raises(RequestRejectedError, match=r"DELETE_FILE of 'old.cfg'.*NO_FUNC_CODE_SUPPORT"):
            await runner.delete_file("old.cfg")
        await server

    @pytest.mark.parametrize("name", ["", "café", "a" * 3000])
    async def test_invalid_name_sends_nothing(self, name: str) -> None:
        runner, _, channel = await open_runner()

        with pytest.raises(ValueError):
            await runner.delete_file(name)

        await assert_nothing_sent(channel)


DATA = b"abcdefghij"  # three blocks at the 4-octet block size `status_reply` grants


def spy_aborts(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record the handle of every ABORT_FILE the runner builds."""
    aborts: list[int] = []
    real = file_transfer.build_abort

    def spy(handle: int, **kwargs: Any) -> Any:
        aborts.append(handle)
        return real(handle, **kwargs)

    monkeypatch.setattr(file_transfer, "build_abort", spy)
    return aborts


class TestUpload:
    async def test_uploads_in_blocks(self) -> None:
        runner, peer, _ = await open_runner()
        server = serve(peer, status_reply(), block_reply(), block_reply(), block_reply(), status_reply(max_block=0))

        assert await runner.upload_file("cfg.txt", DATA) is None
        requests = await server

        assert [r[1] for r in requests] == [
            FunctionCode.OPEN_FILE,
            FunctionCode.WRITE,
            FunctionCode.WRITE,
            FunctionCode.WRITE,
            FunctionCode.CLOSE_FILE,
        ]
        variation, opened = g70_object(requests[0])
        assert variation == 3
        assert struct.unpack_from("<HH", opened) == (26, 7)
        assert struct.unpack_from("<H", opened, 10) == (0o644,)  # permissions
        assert struct.unpack_from("<I", opened, 12) == (0,)  # authentication key
        assert struct.unpack_from("<IHH", opened, 16) == (10, 2, 2032)  # file size, WRITE mode, proposed block size
        assert opened[26:] == b"cfg.txt"
        writes = [(*struct.unpack_from("<II", g70_object(r)[1]), g70_object(r)[1][8:]) for r in requests[1:4]]
        assert writes == [(HANDLE, 0, b"abcd"), (HANDLE, 1, b"efgh"), (HANDLE, 2 | LAST_BLOCK, b"ij")]
        assert g70_object(requests[4]) == (4, struct.pack("<IIHHB", HANDLE, 0, 0, 1, 0))

    async def test_empty_file_opens_and_closes(self) -> None:
        runner, peer, _ = await open_runner()
        server = serve(peer, status_reply(), status_reply(max_block=0))

        await runner.upload_file("empty.txt", b"")
        requests = await server

        assert [r[1] for r in requests] == [FunctionCode.OPEN_FILE, FunctionCode.CLOSE_FILE]

    async def test_request_id_wraps(self) -> None:
        runner, peer, _ = await open_runner()
        runner._file_request_id = 0xFFFF
        server = serve(peer, status_reply(), status_reply(max_block=0))

        await runner.upload_file("empty.txt", b"")
        opened, closed = await server

        assert (request_id_of(opened), request_id_of(closed)) == (0xFFFF, 0)

    async def test_open_refused_raises_without_abort(self) -> None:
        runner, peer, channel = await open_runner()
        server = serve(peer, status_reply(FileStatus.PERMISSION_DENIED, text=b"read only"))

        with pytest.raises(FileTransferError, match=r"OPEN_FILE of 'cfg.txt' failed: PERMISSION_DENIED") as excinfo:
            await runner.upload_file("cfg.txt", DATA)
        await server

        assert excinfo.value.status is FileStatus.PERMISSION_DENIED
        await assert_nothing_sent(channel)

    async def test_refused_block_aborts(self) -> None:
        runner, peer, _ = await open_runner()
        server = serve(
            peer, status_reply(), block_reply(), block_reply(FileStatus.BUFFER_OVERRUN), status_reply(max_block=0)
        )

        with pytest.raises(FileTransferError, match=r"WRITE of block 1 of 'cfg.txt' failed: BUFFER_OVERRUN") as excinfo:
            await runner.upload_file("cfg.txt", DATA)
        requests = await server

        assert excinfo.value.status is FileStatus.BUFFER_OVERRUN
        assert requests[-1][1] == FunctionCode.ABORT_FILE
        assert struct.unpack_from("<I", g70_object(requests[-1])[1]) == (HANDLE,)

    async def test_iin_rejected_block_aborts(self) -> None:
        runner, peer, _ = await open_runner()
        server = serve(peer, status_reply(), null_answer(IIN.PARAMETER_ERROR), status_reply(max_block=0))

        with pytest.raises(RequestRejectedError):
            await runner.upload_file("cfg.txt", DATA)
        requests = await server

        assert requests[-1][1] == FunctionCode.ABORT_FILE

    async def test_missing_block_status_aborts(self) -> None:
        runner, peer, _ = await open_runner()
        server = serve(peer, status_reply(), null_answer(), status_reply(max_block=0))

        with pytest.raises(FileTransferError, match=r"WRITE of block 0 of 'cfg.txt': .*no g70v6") as excinfo:
            await runner.upload_file("cfg.txt", DATA)
        requests = await server

        assert excinfo.value.status is None
        assert requests[-1][1] == FunctionCode.ABORT_FILE

    async def test_refused_close_aborts(self) -> None:
        runner, peer, _ = await open_runner()
        server = serve(peer, status_reply(), status_reply(FileStatus.FATAL), status_reply(max_block=0))

        with pytest.raises(FileTransferError, match="CLOSE_FILE") as excinfo:
            await runner.upload_file("cfg.txt", b"")
        requests = await server

        assert excinfo.value.status is FileStatus.FATAL
        assert requests[-1][1] == FunctionCode.ABORT_FILE

    async def test_timeout_mid_upload_aborts(self) -> None:
        runner, peer, _ = await open_runner(response_timeout=0.3)
        server = serve(peer, status_reply(), None, status_reply(max_block=0))

        with pytest.raises(ResponseTimeoutError):
            await runner.upload_file("cfg.txt", DATA)
        requests = await server

        assert [r[1] for r in requests] == [FunctionCode.OPEN_FILE, FunctionCode.WRITE, FunctionCode.ABORT_FILE]

    async def test_failed_abort_keeps_original_error(self) -> None:
        runner, peer, _ = await open_runner()
        server = serve(peer, status_reply(), block_reply(FileStatus.FATAL), status_reply(FileStatus.CANNOT_ABORT))

        with pytest.raises(FileTransferError) as excinfo:
            await runner.upload_file("cfg.txt", DATA)
        await server

        assert excinfo.value.status is FileStatus.FATAL

    @pytest.mark.parametrize("max_block", [0, 2033])
    async def test_unusable_block_size_aborts(self, max_block: int) -> None:
        runner, peer, _ = await open_runner()
        server = serve(peer, status_reply(max_block=max_block), status_reply(max_block=0))

        with pytest.raises(FileTransferError, match=f"blocks of {max_block} octets, expected 1 to 2032") as excinfo:
            await runner.upload_file("cfg.txt", DATA)
        requests = await server

        assert excinfo.value.status is None
        assert [r[1] for r in requests] == [FunctionCode.OPEN_FILE, FunctionCode.ABORT_FILE]

    @pytest.mark.parametrize(
        ("reply", "message"),
        [(block_reply(handle=0x9999), "handle 0x9999"), (block_reply(number_offset=1), "block 1, expected")],
    )
    async def test_block_echo_mismatch_aborts(self, reply: Reply, message: str) -> None:
        runner, peer, _ = await open_runner()
        server = serve(peer, status_reply(), reply, status_reply(max_block=0))

        with pytest.raises(FileTransferError, match=message):
            await runner.upload_file("cfg.txt", b"ab")
        requests = await server

        assert requests[-1][1] == FunctionCode.ABORT_FILE

    @pytest.mark.parametrize("strip_last", [False, True])
    async def test_block_echo_with_or_without_last_bit(self, strip_last: bool) -> None:
        runner, peer, _ = await open_runner()
        server = serve(peer, status_reply(), block_reply(strip_last=strip_last), status_reply(max_block=0))

        await runner.upload_file("cfg.txt", b"ab")
        await server

    async def test_link_failure_does_not_abort(self, monkeypatch: pytest.MonkeyPatch) -> None:
        aborts = spy_aborts(monkeypatch)
        runner, peer, channel = await open_runner()

        async def outstation() -> None:
            [request] = await peer.read_fragments(1)
            await peer.send_fragment(status_reply()(request))
            await peer.read_fragments(1)
            await channel.close()  # type: ignore[attr-defined]

        closer = asyncio.create_task(outstation())
        with pytest.raises(LinkError):
            await runner.upload_file("cfg.txt", DATA)
        await closer

        assert aborts == []

    async def test_cancel_does_not_abort(self, monkeypatch: pytest.MonkeyPatch) -> None:
        aborts = spy_aborts(monkeypatch)
        runner, peer, _ = await open_runner()
        upload = asyncio.create_task(runner.upload_file("cfg.txt", DATA))

        [request] = await peer.read_fragments(1)
        await peer.send_fragment(status_reply()(request))
        await peer.read_fragments(1)
        upload.cancel()

        with pytest.raises(asyncio.CancelledError):
            await upload
        assert aborts == []

    async def test_invalid_permissions_sends_nothing(self) -> None:
        runner, _, channel = await open_runner()

        with pytest.raises(ValueError, match="16 bits"):
            await runner.upload_file("cfg.txt", DATA, permissions=0x10000)

        await assert_nothing_sent(channel)

    async def test_invalid_name_sends_nothing(self) -> None:
        runner, _, channel = await open_runner()

        with pytest.raises(ValueError, match="not ASCII"):
            await runner.upload_file("café", DATA)

        await assert_nothing_sent(channel)


def test_exports() -> None:
    import dnp3.master

    assert dnp3.master.FileTransferError is FileTransferError
    assert dnp3.master.FileStatus is FileStatus
    assert issubclass(FileTransferError, MasterRunnerError)
