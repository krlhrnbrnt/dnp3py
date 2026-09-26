# 002: One control-block builder for all command tasks

Status: done
Branch: refactor/command-task-block-builders
Depends on: none

## Goal
SELECT, OPERATE and DIRECT_OPERATE build their CROB and analog-output blocks through one shared implementation.
A future fix to the wire encoding then applies to all three request types at once. Wire output and public API are
unchanged.

## Context
- `src/dnp3/master/commands.py`: `SelectTask`, `OperateTask` and `DirectOperateTask` each carry an identical copy of
  `_build_control_blocks`, `_build_crob_block` and `_build_analog_block` (~150 duplicated lines). The only difference
  between them is the request builder they call: `build_select_request`, `build_operate_request` or
  `build_direct_operate_request`.
- `CommandTask(ABC)` is public, and so are its subclasses, which are exported from `dnp3.master`. `build_request`
  stays abstract on `CommandTask`, so user subclasses keep the same contract.
- Public module constants stay, even where unused: `CROB_GROUP`, `CROB_VARIATION`, `ANALOG_OUTPUT_*`,
  `QUALIFIER_1BYTE_INDEX`, `QUALIFIER_2BYTE_INDEX`, `MAX_1BYTE_INDEX`.
- The CROB body layout (control, count, on, off, status) can be encoded with `struct.pack("<BBIIB", ...)`.

## Steps
1. Test (characterization, passes before and after):
   `tests/unit/master/test_commands.py::TestCommandTaskPolymorphism::test_all_task_types_encode_identical_blocks`
   (inside the existing class, matching the file's layout). For a mixed operation list
   (binary and analog, indices 3 and 300), assert that the object block bytes from all three task types are equal to
   each other and to pinned golden bytes captured from `main`.
2. Implement: move `_build_control_blocks`, `_build_crob_block` and `_build_analog_block` into `CommandTask` as
   concrete private methods. Each subclass keeps its `mode` field and a one-line `build_request` that calls
   `build_x_request(objects=tuple(self._build_control_blocks()), seq=seq)`.
3. Implement: extract private `_prefixed_block(group, variation, items: list[tuple[int, bytes]]) -> ObjectBlock`. It
   picks qualifier 0x17 or 0x28 from the existing constants and writes the count, index prefixes and bodies. CROB and
   analog building both call it. The CROB body is encoded with `struct.pack("<BBIIB", ...)`.
4. Test: the existing `tests/unit/master/test_commands.py` and `tests/integration/test_commands.py` pass unchanged.

## Out of scope
- The 1-byte count written under qualifier 0x28 (the spec requires 2 bytes). Found while pinning golden
  bytes; kept byte-identical here so this stays a pure refactor, and fixed in plan 008.
- Supporting 16-bit, float or double analog-output variations on the master side.
- The outstation-side CROB parsing (plan 004).

## Done when
- [x] new tests pass
- [x] `uv run pytest tests/` passes, coverage >= 95%
- [x] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean

Note: on Windows, three tests unrelated to this plan fail on `main` as well:
`test_server_start_port_in_use` and `test_open_connection_refused` (socket semantics) and
`test_full_json_resolves_and_parses_inside_as_file_context` (`read_text()` without `encoding`). CI runs on
Linux and macOS only.
