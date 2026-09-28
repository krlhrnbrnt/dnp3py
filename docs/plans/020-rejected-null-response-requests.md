# 020: Clear restart, and raise when the outstation rejects a request

Status: todo
Branch: feat/runner-null-response-requests
Depends on: 014 (adds `startup()`, which this plan makes use the new methods), 026 (adds `RequestRejectedError`)

## Goal
Three runner methods: `clear_restart()`, `enable_unsolicited()` and `disable_unsolicited()`. Each sends its request
and raises `RequestRejectedError` when the answer carries a request-error IIN bit (function not supported, object
unknown, parameter error). `clear_restart()` also raises if DEVICE_RESTART is still set afterwards. `startup()` uses
them, so a rejected startup step raises instead of only being logged.

## Context
- `src/dnp3/master/tcp_runner.py` `MasterTcpRunner.request()` (`:252`) returns `list[ResponseInfo]` and does not
  judge IIN. Keep it that way: a READ can return good data alongside OBJECT_UNKNOWN. Its body is `_exchange()`
  (`:275`), which runs with the channel already claimed.
- From plan 014, in `tcp_runner.py`:
  - `startup()` (`:210`) sends DISABLE_UNSOLICITED and ENABLE_UNSOLICITED through `request()` and logs a rejection
    with `_warn_if_rejected()` (`:793`);
  - `time_sync()` raises `TimeSyncError(MasterRunnerError)` through `_raise_if_rejected()` (`:785`);
  - `_REJECTED` (`:74`) holds the three request-error bits.
- From plan 026, in `tcp_runner.py`: `RequestRejectedError(MasterRunnerError)` with `iin: IIN`, exported from
  `dnp3.master`, and a private `_request_accepted(request, what)`, which runs `request()` and raises
  `RequestRejectedError` on a `_REJECTED` bit in the last fragment. `write_octet_string()` uses it.
- `src/dnp3/master/master.py` has `build_enable_unsolicited()` and `build_disable_unsolicited()`, but no clear-restart
  builder. `src/dnp3/application/builder.py` has the request builders.
- Clear restart is a WRITE of g80v1 with qualifier 0x00, start 7, stop 7 and one data octet 0x00: IIN bit 7 is
  DEVICE_RESTART (IIN1.7). Cite the IEEE 1815-2012 IIN1.7 clause in the docstring. opendnp3's
  `build::ClearRestartIIN` writes exactly these bytes.
- `src/dnp3/core/flags.py` `IIN`: `DEVICE_RESTART` 0x0080, `NO_FUNC_CODE_SUPPORT` 0x0100, `OBJECT_UNKNOWN` 0x0200,
  `PARAMETER_ERROR` 0x0400.
- opendnp3 reference: `IMasterTask::ValidateNullResponse` fails a task on any of the three request-error bits, and
  `ClearRestartTask` fails if the response still has DEVICE_RESTART.
- The in-repo outstation starts with DEVICE_RESTART set (`src/dnp3/outstation/state.py:216`), clears it on WRITE
  g80v1 with qualifier 0x00 (`outstation.py:1009`), and answers with a null response. `Outstation.iin` (`:534`)
  exposes its current IIN.
- Test helpers: `FakeOutstation`, `make_runner` in `tests/unit/master/test_tcp_runner.py`. Build answers with
  `build_null_response(iin=..., seq=...)`.

## Steps
1. Test: `tests/unit/master/test_master.py::test_build_clear_restart`: the exact request bytes are
   `C0|seq 02 50 01 00 07 07 00`, with the master's next sequence.
   Implement: `build_clear_restart_request(seq)` in `application/builder.py`, and `Master.build_clear_restart()`.
2. Test: `tests/unit/master/test_tcp_runner.py::TestNullResponseRequests`:
   - `test_clear_restart_accepted`: a null answer with IIN 0 returns None, and the fake saw one WRITE;
   - `test_clear_restart_still_set_raises`: the answer keeps DEVICE_RESTART. `RequestRejectedError` is raised, and
     its `.iin` includes DEVICE_RESTART;
   - `test_request_error_bits_raise`, parametrized over the three bits, for `enable_unsolicited()`;
   - `test_enable_unsolicited_classes`: `enable_unsolicited(class_1=True, class_2=False, class_3=True)` sends g60v2
     and g60v4 headers only;
   - `test_other_iin_bits_accepted`: DEVICE_TROUBLE | CLASS_1_EVENTS does not raise.
   Implement:
   - a keyword `must_clear: IIN = IIN(0)` on `_request_accepted`, which then also raises `RequestRejectedError` when
     any `must_clear` bit is still set in the last fragment;
   - the three public methods on it, each passing a `what` that names its request.
3. Test: `TestStartup::test_rejected_unsolicited_control_raises` replaces `test_rejected_unsolicited_control_is_logged`,
   keeping its parametrization over DISABLE_UNSOLICITED and ENABLE_UNSOLICITED: the fake answers with
   NO_FUNC_CODE_SUPPORT, and `startup()` raises `RequestRejectedError`.
   Implement: `startup()` calls `disable_unsolicited()` and `enable_unsolicited()`. Delete `_warn_if_rejected()` and
   the `startup()` docstring sentence saying those rejections are logged, not raised.
4. Test, end to end: `tests/integration/test_tcp_master_runner_e2e.py::test_clear_restart_against_outstation`: the
   first integrity poll's `info.iin` has DEVICE_RESTART. `clear_restart()` returns, and the next poll's `info.iin`
   does not have it.

## Out of scope
- Reacting to IIN bits automatically (plan 021).
- Making `request()`, `integrity_poll()` or polls raise on IIN bits.
- Assign class (function code 22).

## Done when
- [ ] new tests pass
- [ ] `uv run pytest tests/` passes, coverage >= 95%
- [ ] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean
