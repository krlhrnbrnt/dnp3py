# 027: The master reports per-point command status

Status: todo
Branch: feat/master-command-status
Depends on: none

## Goal
`await runner.direct_operate(task)` and `await runner.select_and_operate(task)` return a `CommandTaskResult` with one
`CommandPointResult(header_index, index, state, status)` per commanded point, so a caller can see that point 3 came back
`OUT_OF_RANGE`. `select_and_operate` sends the OPERATE only if every point was selected. Today the g12/g41 echo is
dropped and nothing reports whether a control was carried out.

## Context
- IEEE 1815-2012: a control response echoes the request's object headers and objects, with only each object's status
  octet set (status in bits 0-6, Table 4-2). An OPERATE must follow its SELECT with identical objects and the next
  sequence number (select-before-operate, Clause 4.4.3; cite the exact subclause in the docstring).
- opendnp3 reference (`cpp/lib/src/master/CommandTask.cpp`, `CommandSetOps.cpp`, `TypedCommandHeader.h`,
  `include/opendnp3/master/CommandPointResult.h`, `gen/CommandPointState.h`):
  - command results never go to the SOE handler; only the command task reads the echo;
  - each point ends in `CommandPointState` `INIT`, `SELECT_SUCCESS`, `SELECT_MISMATCH`, `SELECT_FAIL`,
    `OPERATE_FAIL` or `SUCCESS`; `status` is meaningful only for `SUCCESS` and `SELECT_FAIL`, else `UNDEFINED`;
  - echo header *i* answers request header *i*; points match by position. A qualifier other than the one sent, or an
    echo header with more points than were sent, is ignored whole (its points stay `INIT`). An index that differs
    leaves that point `INIT`. Values that differ give `SELECT_MISMATCH` on SELECT, `OPERATE_FAIL` on OPERATE;
  - OPERATE is sent only when every point is `SELECT_SUCCESS`; point failures are reported, never thrown.
  - `cpp/lib/src/master/MasterContext.cpp`: the command task stays the active task from SELECT to OPERATE
    (`OK_REPEAT` then `StartTask_TaskReady`), so no other task starts between them. `solSeq` is stamped when a request
    is transmitted (`ResumeActiveTask`) and incremented on every response or timeout, so the OPERATE carries the
    SELECT's sequence + 1. A queued CONFIRM, solicited or for an unsolicited response, goes out first and the OPERATE
    waits for it (`isSending` gives `TASK_READY`);
  - `cpp/lib/src/outstation/ControlState.h`: an OPERATE succeeds only when its sequence is the SELECT's + 1, its object
    bytes equal the SELECT's (length and CRC) and the select timeout has not passed; otherwise `NO_SELECT` or
    `TIMEOUT`. A CONFIRM does not touch the selection.
- Deviations from opendnp3, on purpose:
  - echo headers beyond the request's are ignored with a warning (opendnp3 fails the whole task). Unmatched points
    cannot reach `SELECT_SUCCESS`, so OPERATE stays gated;
  - `CommandTaskResult` also carries the response `iin`, so a request the outstation could not parse (points `INIT`,
    `IIN.PARAMETER_ERROR`) says why;
  - `select_and_operate` takes the SELECT's and the OPERATE's sequences before sending the SELECT, so after a failed or
    timed-out SELECT the next request carries n+2 where opendnp3 sends n+1. Only the SELECT/OPERATE pair must be
    consecutive.
- `src/dnp3/master/handler.py`: `CommandResponse` (`:101`) is constructed nowhere; it is removed, not aliased.
  `dnp3.outstation.handler.CommandResult` already exists, so the master's result is named `CommandTaskResult`
  (opendnp3's name).
- `src/dnp3/master/commands.py`: `CROB_GROUP`, `ANALOG_OUTPUT_GROUP` (`:22-30`); `CommandTask.operations`,
  `_build_control_blocks` (`:101`, g12v1 and g41v1 blocks with qualifier 0x17 or 0x28); `SelectTask`, `OperateTask`,
  `DirectOperateTask` (`:131-169`). `commands.py` must not import `master.py` (`master.py` imports it).
- `src/dnp3/master/master.py`: `_decode_object_layout` (`:144`) and `_iter_object_slots` (`:187`) walk count+prefix
  blocks and stop at the end of the data; `process_response` (`:582`) parses then calls `_process_response_fragment`
  (`:599`); `build_select` / `build_operate` / `build_direct_operate` (`:441-476`) take the next sequence;
  `_parse_response_objects` stays as it is: g12/g41 are not SOE data.
- `src/dnp3/objects/registry`: `registry.lookup(group, variation).SIZE` gives the object width (g12v1 11, g41v1 5,
  v2 3, v3 5, v4 9). Plan 019 adds g41v2-v4 requests; comparing raw bytes keeps this plan variation-agnostic.
- `src/dnp3/master/tcp_runner.py`: `_Burst` (`:108`), `request()` (`:252`), `_exchange()` (`:275`),
  `_next_solicited()` (`:511`, calls `master.process_response`, which logs a parse failure that the runner then
  logs again), `_handle_unsolicited()` (`:584`), `_claim()` (`:752`), `time_sync()` holding the channel across
  an exchange (`:349`).
- Sequences are drawn when a request is built, not when it is sent: `Master.build_*` and `next_request_sequence()`
  (`master.py:834`) share one counter, and `poll()` (`tcp_runner.py:504`) and `integrity_poll()` (`:250`) draw before
  `_claim()`. Holding `_claim()` across both exchanges does not by itself keep n+1 for the OPERATE: a poll built while
  the SELECT awaits its echo takes it.
- `src/dnp3/master/__init__.py` exports; `docs/control-commands.md` "Reading the command result" (`:149`) and the
  last paragraph of "CommandStatus and IIN" (`:384`) describe the gap and hand-decode bytes.
- The outstation answers g41 SELECT/OPERATE with `NOT_SUPPORTED` on every point (`docs/control-commands.md:139`), so
  select-before-operate integration tests use CROBs. It matches an OPERATE to its SELECT per point index
  (`outstation.py:1133`) and ignores the sequence, so only the runner unit tests can catch a sequence gap.
- Tests to copy: `tests/unit/master/test_handler.py::TestCommandResponse` (`:184`, replaced);
  `tests/unit/master/test_tcp_runner.py` `answer_requests` (`:1383`), `Reply` (`:1371`), `null_reply`,
  `open_runner` (`:1419`), `assert_nothing_sent` (`:1414`), `test_clock_read_once_the_channel_is_held` (`:1522`, a
  second task waiting on the channel); `tests/integration/test_tcp_master_runner_e2e.py` `_runner_over_tcp` (`:86`);
  `tests/integration/test_commands.py` `TrackingHandler` (`:166`).

## Steps
1. Test: `tests/unit/master/test_handler.py::TestCommandPointResult` and `TestCommandTaskResult`:
   - a `CommandPointResult` keeps `header_index`, `index`, `state`, `status`;
   - `CommandTaskResult.is_success` is True when every point is `SUCCESS` with status `SUCCESS`, False when one is
     `SUCCESS` with `OUT_OF_RANGE`, False when one is `SELECT_SUCCESS`, False with no points;
   - `CommandPointState`, `CommandPointResult`, `CommandTaskResult` are in `dnp3.master.__all__`;
     `CommandResponse` is not an attribute of `dnp3.master.handler`.
   Implement in `handler.py`: `class CommandPointState(Enum)` with opendnp3's six members and values 0-5, each with a
   one-line doc of when a point ends there; frozen `CommandPointResult`; frozen
   `CommandTaskResult(points: tuple[CommandPointResult, ...], iin: IIN)` with `is_success`. Delete `CommandResponse`
   and `TestCommandResponse`. Export the three.
2. Test: `tests/unit/master/test_command_status.py`, calling
   `command_point_results(request: RequestFragment, response: Sequence[ObjectBlock])`. Requests come from
   `SelectTask` / `OperateTask` / `DirectOperateTask(...).build_request()`; echoes are the request's blocks with status
   octets set by a local `echo(request, statuses)` helper. Exact cases:
   - `test_direct_operate_all_success`: two CROBs, both `SUCCESS` / `SUCCESS`, `header_index` 0;
   - `test_out_of_range_on_one_point`: statuses `[SUCCESS, OUT_OF_RANGE]` give `SUCCESS` for both points and
     statuses as sent;
   - `test_select_success_and_fail`: SELECT echo with `[SUCCESS, LOCAL]` gives `SELECT_SUCCESS` (status
     `UNDEFINED`) and `SELECT_FAIL` (status `LOCAL`);
   - `test_select_value_mismatch`: echo with a changed on-time gives `SELECT_MISMATCH`;
   - `test_operate_value_mismatch`: same on OPERATE gives `OPERATE_FAIL`;
   - `test_index_mismatch_leaves_point_init`;
   - `test_qualifier_mismatch_leaves_header_init`: 0x17 sent, 0x28 echoed;
   - `test_group_or_variation_mismatch_leaves_header_init`;
   - `test_short_echo_leaves_missing_points_init`: count 1 of 2 sent;
   - `test_echo_with_more_points_than_sent_is_ignored`;
   - `test_missing_echo_header_leaves_points_init`: CROB and g41 sent, only the CROB echoed;
   - `test_extra_echo_header_is_ignored_and_logged` (`caplog`);
   - `test_unknown_status_code_is_undefined`: status octet 0x55;
   - `test_status_reserved_bit_is_masked`: status octet 0x8C gives `OUT_OF_RANGE`;
   - `test_two_byte_indexes`: index 300, qualifier 0x28;
   - `test_crob_and_analog_headers`: header indices 0 and 1, in request order;
   - `test_empty_response_leaves_all_init`.
   Implement `src/dnp3/master/command_status.py` with public `command_point_results`: mode from
   `request.header.function` (SELECT vs OPERATE / DIRECT_OPERATE); walk request and echo slots with
   `_decode_object_layout` / `_iter_object_slots` from `master.py`; width from `registry.lookup(...).SIZE`; compare
   index, then the object bytes except the last; status from the last byte `& 0x7F`, unknown codes `UNDEFINED`.
   Export it from `dnp3.master`.
3. Test: Hypothesis in `test_command_status.py`: 1-10 operations (CROB or g41v1, index 0-65535, random control code,
   times and value) and a random `CommandStatus` per point. Echoing a DIRECT_OPERATE request with those statuses gives
   `SUCCESS` for every point with exactly those statuses, in request order.
4. Test: `tests/unit/master/test_master.py`: `process_fragment(parse_response(data))` returns the same
   `ResponseInfo` as `process_response(data)`, and a response the parser rejects makes `process_response` return None
   with one warning. Implement: rename `_process_response_fragment` to public `process_fragment`; `process_response`
   calls it.
5. Test: `tests/unit/master/test_tcp_runner.py::TestCommands`. First change `Reply` to take the request bytes
   (`null_reply` and `delay_reply` read the sequence from `request[0] & 0x0F`), and add `echo_reply(*statuses)`,
   which answers with the request's objects and those statuses. Cases:
   - `test_direct_operate_returns_point_statuses`: `[SUCCESS, OUT_OF_RANGE]` comes back in the result, and
     `result.iin` is the response's;
   - `test_select_and_operate_sends_operate_after_select`: two requests, SELECT then OPERATE, the OPERATE's sequence is
     the SELECT's + 1 and its object blocks equal the SELECT's byte for byte; all points `SUCCESS`;
   - `test_failed_select_sends_no_operate`: SELECT echo `[SUCCESS, LOCAL]`, the result has `SELECT_SUCCESS` and
     `SELECT_FAIL`, and `assert_nothing_sent`;
   - `test_rejected_select_sends_no_operate`: null response with `PARAMETER_ERROR`, points `INIT`, `iin` carries the
     bit, nothing sent after;
   - `test_poll_during_select_waits_and_keeps_operate_sequence`: a `poll()` started once the peer has read the SELECT
     draws its sequence while the SELECT awaits its echo; the wire carries SELECT n, OPERATE n+1, then READ n+2, and
     the READ is read only after the OPERATE is answered;
   - `test_select_and_operate_sequence_wraps`: with the counter drawn to 15 first, the SELECT carries 15 and the
     OPERATE 0;
   - `test_unsolicited_during_select_is_confirmed_before_operate`: an `unsolicited_response` sent before the SELECT
     echo gets its CONFIRM between the SELECT and the OPERATE, and the OPERATE still carries the SELECT's sequence + 1;
   - `test_multi_fragment_command_response_raises`: a two-fragment answer raises `MasterRunnerError`;
   - `test_empty_task_raises_before_sending`: `ValueError`, `assert_nothing_sent`;
   - `test_parse_failure_is_logged_once`: garbage bytes then a valid answer give exactly one warning.
   Implement in `tcp_runner.py`:
   - `_Burst.objects: Sequence[ObjectBlock]`, the objects of the latest fragment;
   - `_next_solicited` and `_handle_unsolicited` parse with `parse_response`, log and skip on `ParseError`, then call
     `master.process_fragment`; `_next_solicited` sets `burst.objects`;
   - `_exchange_burst(request) -> _Burst`, with `_exchange` returning its `fragments`;
   - `_command(request) -> CommandTaskResult` (caller holds the channel): raise `MasterRunnerError` unless the burst is
     one fragment, else `CommandTaskResult(command_point_results(request, burst.objects), iin)`;
   - `direct_operate(task: DirectOperateTask)` and `select_and_operate(task: SelectTask)`, each raising `ValueError`
     on a task with no operations. `select_and_operate` holds one `_claim()` across both exchanges. Inside it, it
     builds the SELECT with `master.build_select(task)` and at once the OPERATE with
     `master.build_operate(OperateTask(operations=list(task.operations)))`, with no `await` between them, so they
     carry n and n+1. It sends the OPERATE only when every point is `SELECT_SUCCESS`; otherwise it returns the SELECT
     result. Docstrings say point failures are returned, no other request goes out between SELECT and OPERATE while a
     CONFIRM may, and a timeout on OPERATE leaves unknown whether it took effect.
6. Test: `tests/integration/test_tcp_master_runner_e2e.py::TestCommands`, with `_runner_over_tcp` taking an
   outstation `command_handler`. A `DefaultCommandHandler` subclass accepts CROB index 0 and returns
   `CommandResult.out_of_range()` for index 1:
   - `test_direct_operate_reports_out_of_range`: latch on 0 and 1 gives `SUCCESS` and `OUT_OF_RANGE`;
   - `test_select_and_operate_success`: latch on 0, `is_success`, and `operate_binary_output` was called once;
   - `test_select_fail_skips_operate`: latch on 1, `SELECT_FAIL` / `OUT_OF_RANGE`, `operate_binary_output` never
     called.
7. Docs: `docs/control-commands.md`: replace "Reading the command result" (drop `decode_crob_statuses`) with a
   runner example showing `select_and_operate`, a loop over `result.points`, and a table of the six states; mention
   `command_point_results` for callers driving `Master` over another transport. Update the last paragraph of
   "CommandStatus and IIN" to point at `CommandPointResult.status`.

## Review focus
- OPERATE never goes out unless every point is `SELECT_SUCCESS`, including when the echo is short, reordered, has
  the wrong qualifier or extra headers.
- A hostile echo count (0xFFFF) reads nothing past the block; `_iter_object_slots` bounds the walk.
- No request goes out between SELECT and OPERATE, and the OPERATE carries the SELECT's sequence + 1 even when another
  task builds a request meanwhile. Only CONFIRMs may go between them.

## Out of scope
- DIRECT_OPERATE_NO_ACK (opendnp3's master does not offer it either).
- g41v2-v4 requests (plan 019); the matcher already handles their widths.
- Routing control echoes to the SOE handler, or changing `request()` / `ResponseInfo`.
- The outstation's g41 SELECT/OPERATE wiring, and the unused `Master._pending_select`.
- Retrying a failed command.
- Numbering every request when it is sent, as opendnp3 does. Only the SELECT/OPERATE pair needs consecutive sequences.
- Making the outstation check that an OPERATE carries its SELECT's sequence + 1.

## Done when
- [ ] new tests pass
- [ ] `uv run pytest tests/` passes, coverage >= 95%
- [ ] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean
