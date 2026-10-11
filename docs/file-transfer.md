# Uploading and deleting outstation files

A dnp3py master can write a file to an outstation and delete one, using DNP3
file transfer (IEEE 1815-2012, object group 70). `upload_file()` and
`delete_file()` are on every `MasterRunner` and on `SyncMaster`.

```python
from pathlib import Path

from dnp3.master import FileStatus, FileTransferError, Master, MasterTcpRunner

async with MasterTcpRunner(master=Master(), host="10.0.0.5", port=20000) as runner:
    await runner.upload_file("settings.cfg", Path("settings.cfg").read_bytes())
    try:
        await runner.delete_file("old.cfg")
    except FileTransferError as exc:
        if exc.status is not FileStatus.FILE_NOT_FOUND:
            raise
```

## Upload

`upload_file(name, data, *, permissions=0o644)` opens `name` for writing
(OPEN_FILE), sends `data` in blocks (WRITE), and closes the file (CLOSE_FILE).
An existing file of that name is replaced. Empty `data` creates an empty file.

Blocks have the size the outstation grants in its answer to the open. The master
proposes as much as one request fragment holds (`MasterConfig.max_fragment_size`
less 16 octets, at most 65527), and the grant must be 1 to that size; 0 or
larger raises `FileTransferError` and aborts. Polls and commands may run between
blocks.

After the open, a refusal, a bad or mismatched answer, an IIN rejection or a
timeout makes the master send ABORT_FILE so the outstation drops the partial
file, then raise the original error. A failed link, a cancelled call or another
runner error (for example a Secure Authentication failure) sends no abort; the
outstation drops the file when its handle expires.

## Delete

`delete_file(name)` sends DELETE_FILE. A missing file raises
`FileTransferError` with `status` `FileStatus.FILE_NOT_FOUND`.

## Errors

| Exception | Meaning |
|---|---|
| `ValueError` | The name is empty, not ASCII or too long for one fragment, `permissions` does not fit 16 bits, or the data is 4 GiB or more. Nothing was sent. |
| `FileTransferError` | The outstation refused a step (`status` holds its code, `text` its optional message), or its answer was missing, malformed or for another request (`status` is None). |
| `RequestRejectedError` | The outstation's IIN says it did not carry out the request, for example because it has no file transfer. |
| `AuthenticationError` | Secure Authentication is configured and failed. |
| `MasterRunnerError` | The runner is not open, or a response broke the burst's sequence walk. |
| `ResponseTimeoutError`, `LinkError` | As for any other request. |

## Limits

- **No file authentication.** Requests carry authentication key 0. An outstation
  that requires a file login (AUTHENTICATE_FILE) answers PERMISSION_DENIED.
- **Immediate status only.** An outstation may answer a file request with a null
  response and report the result later as an event. dnp3py raises
  `FileTransferError` for that instead of waiting for the event.
- **No download**, directory listing or file information requests.
- File names are ASCII.

Secure Authentication applies as for any request: when the outstation
challenges a file request, the master answers and the transfer continues.
