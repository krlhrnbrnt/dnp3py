# 023: The outstation keeps a clock that time sync sets

Status: todo
Branch: feat/outstation-time-sync
Depends on: none

## Upstream sync (2026-09-28)
Still todo. `Outstation.process_request` now also takes a `peer` (#72), and `_runner_over_tcp` in the
end-to-end test exists as described. Line numbers in Context predate the sync.

## Goal
The in-repo outstation keeps its own clock, and the master can set it with either procedure. A WRITE of g50v1 sets
it directly. RECORD_CURRENT_TIME followed by a WRITE of g50v3 sets it to the written time plus the time elapsed since
the request arrived. The time WRITE clears NEED_TIME; DELAY_MEASURE no longer does. Counter events are stamped from
this clock. `MasterTcpRunner.time_sync()` then works against this outstation with the default LAN method, and tests
can check the time it set.

## Context
- `src/dnp3/outstation/outstation.py`:
  - `Outstation` (`:506`), whose `__post_init__` (`:523`) sets NEED_TIME when `config.time_sync_required`;
  - `_process_request_fragment` dispatch (`:578`). RECORD_CURRENT_TIME falls through to the NO_FUNC_CODE_SUPPORT
    null response (`:616`);
  - `_handle_write` (`:994`) handles only g80v1 and always answers a null response with `self.iin`;
  - `_handle_delay_measure` (`:1521`) answers g52v2 = 0 and clears NEED_TIME (`:1535`);
  - `_build_counter_event_blocks` (`:915`) falls back to `DNP3Timestamp.now()` (`:937`).
- `src/dnp3/outstation/state.py` `OutstationStateManager.clear_need_time()` (`:234`).
- `src/dnp3/database/database.py`: `update_counter` (`:371`), `increment_counter` (`:413`) and `freeze_counter`
  (`:449`) default an event's timestamp to `DNP3Timestamp.now()`. Binary and analog events carry only a timestamp
  the caller supplies.
- `src/dnp3/objects/time.py` `TimeAndDate` (g50v1) and `TimeAndDateRecorded` (g50v3). Requests are built with
  `Master.build_write_time(timestamp, *, recorded=False)` and `Master.build_record_current_time()`.
- Clock pattern to copy: `MasterTcpRunner.wall_clock_ms: Callable[[], int]` (`src/dnp3/master/tcp_runner.py:149`),
  replaced in tests with a fake clock.
- IEEE 1815-2012 time synchronization procedures: the outstation clears IIN1.4 NEED_TIME when its time is written.
  For g50v3 it sets its time to the written value plus the time elapsed since RECORD_CURRENT_TIME arrived. Cite the
  clause if the spec is at hand; plan 014 could not confirm its number.
- Tests that assert the old behavior:
  - `tests/unit/outstation/test_outstation.py::TestDelayMeasure::test_delay_measure_clears_need_time` (`:204`);
  - `tests/integration/test_tcp_master_runner_e2e.py::TestTimeSyncOverTcp::test_time_sync_lan_against_outstation_raises`
    (`:235`). `_runner_over_tcp` (`:86`) sets up the outstation and runner.

## Steps
In the unit tests, use a settable fake clock (`now = [1_000]`, `outstation.wall_clock_ms = lambda: now[0]`), so the
number of clock reads is not fixed.
1. Test: `tests/unit/outstation/test_outstation.py::TestClock`:
   - `test_clock_follows_wall_clock`: before any time write, `outstation.now() == DNP3Timestamp(1_000)`;
   - `test_write_g50v1_sets_clock_and_clears_need_time`: with the wall clock at 1_000, WRITE g50v1 = 9_000. The
     answer has no IIN2 error bit and no NEED_TIME. With the wall clock at 1_500, `now()` is 9_500;
   - `test_malformed_time_write_is_parameter_error`, parametrized over count 2 and qualifier 0x00: the answer has
     PARAMETER_ERROR, and neither the clock nor NEED_TIME changes.
   Implement:
   - `Outstation.wall_clock_ms: Callable[[], int]`, defaulting to the system clock in milliseconds, a private
     `_clock_offset_ms`, and `now() -> DNP3Timestamp`;
   - `_handle_write` handles g50v1: qualifier 0x07, count 1, else PARAMETER_ERROR. It sets the offset so that
     `now()` reads the written time, and clears NEED_TIME.
2. Test: `TestRecordCurrentTime`:
   - `test_record_current_time_answers_null`: the answer has no NO_FUNC_CODE_SUPPORT;
   - `test_write_g50v3_adds_elapsed_time`: a g50v1 write first moves the clock 50_000 ahead. Then RECORD_CURRENT_TIME
     at wall 1_000, WRITE g50v3 = 20_000 at wall 1_300, and `now()` at wall 1_400 is 20_400. NEED_TIME is cleared;
   - `test_g50v3_without_record_is_parameter_error`.
   Implement: RECORD_CURRENT_TIME records the wall clock and answers a null response. `_handle_write` handles g50v3
   with the same header checks, setting the offset to the written time minus the recorded wall time.
3. Test: `TestDelayMeasure::test_delay_measure_leaves_need_time` replaces `test_delay_measure_clears_need_time`.
   Implement: `_handle_delay_measure` no longer clears NEED_TIME.
4. Test:
   - `tests/unit/database/test_database.py::test_clock_stamps_counter_events`: with
     `Database(clock=lambda: DNP3Timestamp(42))`, `update_counter`, `increment_counter` and `freeze_counter` without
     a timestamp give events stamped 42;
   - `tests/unit/outstation/test_outstation.py::TestClock::test_counter_event_uses_outstation_clock`: after a g50v1
     write, a counter update without a timestamp produces a g22v5 event carrying the outstation's time.
   Implement: `Database.clock: Callable[[], DNP3Timestamp]` (default `DNP3Timestamp.now`), used by the three
   defaults. `Outstation.__post_init__` points `self.database.clock` at `self.now`, and the `:937` fallback uses
   `self.now()`.
5. Test, end to end: in `tests/integration/test_tcp_master_runner_e2e.py`, `test_time_sync_sets_outstation_clock`,
   parametrized over `TimeSyncMethod`, replaces both `TestTimeSyncOverTcp` tests. The runner's `wall_clock_ms` runs one
   hour ahead of the system clock. After `time_sync()`, `outstation.now()` is one hour ahead of the system clock, to
   within 500 ms, and the outstation's IIN has no NEED_TIME.
   Implement: `_runner_over_tcp` also yields the outstation.

## Review focus
- The g50v3 correction must measure the elapsed time on one clock. Taking the written time minus the recorded `now()`
  mixes the outstation's time with the wall clock, so an earlier offset leaks into the new time. The earlier g50v1
  write in step 2 makes that mistake fail.
- Only a time WRITE clears NEED_TIME. A WRITE carrying only g80v1 still leaves it set, as
  `test_write_g80v1_does_not_clear_other_iin_bits` (`:1769`) already checks.

## Out of scope
- DELAY_MEASURE reporting a real turnaround. It stays 0, since the outstation answers within the same call.
- Setting NEED_TIME again after a restart or after a drift period.
- Unsynchronized-time CTO (g51v2) and timestamped binary and analog events.

## Done when
- [ ] new tests pass
- [ ] `uv run pytest tests/` passes, coverage >= 95%
- [ ] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean
