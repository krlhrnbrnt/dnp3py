# 026: The master writes octet strings (g110)

Status: todo
Branch: feat/master-octet-string-write
Depends on: 025 (the README section this extends)

## Goal
`Master.build_write_octet_string(index, value)` builds a WRITE of one g110 string, and
`await MasterTcpRunner.write_octet_string(index, value)` sends it and raises `RequestRejectedError` when the outstation
answers that it did not carry out the write.

## Context
- IEEE 1815-2012 Annex A, g110: the variation is the string length, 1 to 255 octets; variation 0 is not a length.
  IIN2.0 NO_FUNC_CODE_SUPPORT, IIN2.1 OBJECT_UNKNOWN and IIN2.2 PARAMETER_ERROR say a request was not carried out.
- Wire form chosen: one object header g110v{len}, start-stop range with start = stop = index (qualifier 0x00 up to
  index 255, 0x01 up to 65535), then the string. A start-stop range is the usual form for writing one static value;
  index-prefix qualifiers are not offered.
- `src/dnp3/master/master.py` `build_write_time` (`:540`): pattern for a WRITE builder, taking the sequence from
  `self._state.get_next_request_sequence()`. `build_write_request` is already imported.
- `src/dnp3/application/qualifiers.py`: `ObjectHeader.build(group, variation, prefix, range_code)` (`:144`),
  `StartStopRange.to_bytes_1/2` (`:208`), `RangeCode.UINT8_START_STOP` / `UINT16_START_STOP`.
- `src/dnp3/master/polling.py`: `MAX_UINT8_RANGE`, `MAX_UINT16_RANGE` (`:21-22`), the range limits
  `RangePollTask.build_request` uses.
- `src/dnp3/master/tcp_runner.py`: `_REJECTED` (`:74`), `MasterRunnerError` (`:82`), `TimeSyncError` (`:99`),
  `request()` (`:252`). `_raise_if_rejected` (`:785`) raises `TimeSyncError` and stays as it is.
- `src/dnp3/master/__init__.py` exports the runner errors.
- Plan 020 (todo) also raises `RequestRejectedError` with an `iin: IIN` attribute. It is created here in that shape.
- `src/dnp3/application/parser.py` `parse_request` gives a single-block request's object data whole, so a test can
  read the header, range and string back.
- Tests to copy: `tests/unit/master/test_master.py::test_build_write_time` (`:243`, exact bytes);
  `tests/unit/master/test_tcp_runner.py` `open_runner` (`:1419`), `answer_requests` (`:1383`), `null_reply`
  (`:1375`), `written_object` (`:1408`), `assert_nothing_sent` (`:1414`), `TestTimeSync` (`:1429`).

## Steps
1. Test: `tests/unit/master/test_master.py::TestMasterRequestBuilding`:
   - `test_build_write_octet_string_uint8_index`: `build_write_octet_string(3, b"abc").to_bytes()` is
     `C0|seq 02 6E 03 00 03 03 61 62 63`;
   - `test_build_write_octet_string_uint16_index`: index 300 gives qualifier 0x01 and range `2C 01 2C 01`;
   - `test_build_write_octet_string_index_boundaries`: 255 gives 0x00, 256 and 65535 give 0x01;
   - `test_build_write_octet_string_max_length`: a 255-octet value gives variation 255;
   - `test_build_write_octet_string_rejects`, parametrized over `(0, b"")`, `(0, bytes(256))`, `(-1, b"a")`,
     `(65536, b"a")`: `ValueError`, and the next request still gets the sequence it would have had.
   Implement: `OCTET_STRING_MAX_LENGTH = 255` and `Master.build_write_octet_string(index: int, value: bytes)`,
   validating before taking a sequence. The docstring cites Annex A for the length limits.
2. Test: Hypothesis `test_build_write_octet_string_round_trips`: index 0..65535 and value `st.binary(min_size=1,
   max_size=255)`. `parse_request(fragment.to_bytes())` has function WRITE and one block with group 110, variation
   `len(value)`, qualifier 0x00 or 0x01 by index, and data equal to the range bytes plus `value`.
3. Test: `tests/unit/master/test_tcp_runner.py::TestWriteOctetString`:
   - `test_accepted_write_returns`: `answer_requests(peer, 1)`. `write_octet_string(3, b"hello")` returns None; the
     request is a WRITE whose `written_object` has header `(110, 5, 0x00)` and data `bytes([3, 3]) + b"hello"`;
   - `test_rejected_write_raises`, parametrized over the three `_REJECTED` bits: `RequestRejectedError` whose message
     contains `g110v5 index 3` and the bit's name, and whose `.iin` has the bit;
   - `test_other_iin_bits_accepted`: DEVICE_RESTART | CLASS_1_EVENTS does not raise;
   - `test_invalid_value_sends_nothing`: `write_octet_string(3, b"")` raises `ValueError`, then
     `assert_nothing_sent`;
   - `test_request_rejected_error_exported`: `RequestRejectedError` is in `dnp3.master.__all__` and subclasses
     `MasterRunnerError`.
   Implement in `tcp_runner.py`:
   - `class RequestRejectedError(MasterRunnerError)` with `__init__(self, message: str, iin: IIN)` storing `self.iin`;
   - private `async def _request_accepted(self, request: RequestFragment, what: str) -> list[ResponseInfo]`: runs
     `request()` and raises `RequestRejectedError(f"Outstation rejected {what}: {bits.name}", iin=...)` when the last
     fragment has a `_REJECTED` bit;
   - `async def write_octet_string(self, index: int, value: bytes) -> None`, building with
     `self.master.build_write_octet_string` and calling `_request_accepted` with
     `what=f"WRITE of g110v{len(value)} index {index}"`. The docstring lists ValueError, RequestRejectedError,
     ResponseTimeoutError, ConnectionLostError and MasterRunnerError, and says a WRITE response carries no value,
     so reading the string back is the caller's job.
   Export `RequestRejectedError` from `dnp3.master`.
4. Docs: `README.md` Object Groups row becomes `| 110, 111 | Octet String (static, event; master read and write) |`.
   The `### Octet strings (master)` subsection gains `await runner.write_octet_string(2, b"Feeder 7")` and one sentence
   on `RequestRejectedError`.

## Review focus
- Validation runs before the sequence is taken and before the channel is claimed, so a bad value sends nothing and
  leaves the sequence untouched.
- `TimeSyncError` behavior is unchanged: `TestTimeSync` passes as it is.

## Out of scope
- Several strings, indices or lengths in one request.
- Index-prefix (0x17/0x28) write qualifiers.
- Reading the string back after the write.
- `clear_restart()`, `enable_unsolicited()`, `disable_unsolicited()` and a `must_clear` check (plan 020).

## Done when
- [ ] new tests pass
- [ ] `uv run pytest tests/` passes, coverage >= 95%
- [ ] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean
