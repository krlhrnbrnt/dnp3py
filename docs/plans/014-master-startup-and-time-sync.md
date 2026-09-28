# 014: Master startup sequence and time synchronization

Status: done
Branch: feat/master-startup-time-sync
Depends on: none

## Goal
Two new methods on the runner, plus the config and objects behind them. The startup flags on `MasterConfig`
start doing something. Upstream issue #73. The issue says the other startup flags are wired, but in this fork none
of them has a consumer.
- `runner.time_sync()` sets the outstation's clock. It uses either the LAN procedure (RECORD_CURRENT_TIME, then
  WRITE g50v3) or the non-LAN procedure (DELAY_MEASURE, then WRITE g50v1 corrected for propagation delay). A
  rejected sync raises `TimeSyncError`.
- `runner.startup()` runs the sequence the `MasterConfig` startup flags describe: disable unsolicited, time sync,
  integrity poll, enable unsolicited, each only if its flag is set.

## Context
- `src/dnp3/master/config.py` `MasterConfig`: `startup_integrity_poll` (True), `disable_unsolicited_on_startup`
  (False), `enable_unsolicited_on_startup` (True) and `time_sync_on_startup` (False) have no readers in `src/`.
- `src/dnp3/master/master.py`:
  - has `build_delay_measure()`, `build_enable_unsolicited()`, `build_disable_unsolicited()` and
    `build_integrity_poll()`, each drawing the next sequence number;
  - `_parse_response_objects` ignores g52;
  - `GROUP_TIME_DELAY = 52` is already declared.
- `src/dnp3/application/builder.py`:
  - `build_write_request(objects, seq)` and `build_delay_measure_request(seq)` exist;
  - there is no RECORD_CURRENT_TIME builder. `FunctionCode.RECORD_CURRENT_TIME = 0x18` exists in `core/enums.py`.
- `src/dnp3/objects/time.py`: g50v1 `TimeAndDate`, g52v1 `TimeDelayCoarse` (seconds) and g52v2 `TimeDelayFine`
  (milliseconds) are registered. g50v3 (absolute time at last recorded time, 6 bytes) is not.
- `src/dnp3/core/timestamp.py` `DNP3Timestamp(milliseconds)`, with `now()`.
- `src/dnp3/core/flags.py` `IIN`: `NEED_TIME`, `NO_FUNC_CODE_SUPPORT`, `OBJECT_UNKNOWN` and `PARAMETER_ERROR`.
- The procedures are in IEEE 1815-2012's time synchronization clause; cite its section number in the docstrings.
  - Non-LAN: the master notes the send time `t0` and the receive time `t1`, and the outstation reports its turnaround
    `d` in g52. The one-way delay is `((t1 - t0) - d) / 2`. The master then writes g50v1 = its current time plus that
    delay.
  - LAN (the procedure intended for networked links): the master notes `t0`, sends RECORD_CURRENT_TIME, and then
    writes g50v3 = `t0`.
  - Both writes use qualifier 0x07 (1-byte count) with count 1.
- In-repo outstation (`src/dnp3/outstation/outstation.py`):
  - answers DELAY_MEASURE with g52v2 = 0 and clears NEED_TIME;
  - answers WRITE with a null response;
  - answers RECORD_CURRENT_TIME with `NO_FUNC_CODE_SUPPORT` (the fallback at `:616`).
- Test helpers in `tests/unit/master/test_tcp_runner.py`: `FakeOutstation` (`read_fragments`, `send_fragment`) and
  `make_runner`. Build responses with `build_response(objects=..., iin=..., seq=...)`. A g52v2 block is
  `ObjectHeader.build(group=52, variation=2, prefix=PrefixCode.NONE, range_code=RangeCode.UINT8_COUNT)` plus
  `CountRange(count=1).to_bytes_1()` plus 2 delay bytes, as in the outstation's `_handle_delay_measure`.

## Steps
1. Test: `tests/unit/objects/test_time.py::test_g50v3_recorded_time`: size 6, golden decode and round trip.
   Implement `TimeAndDateRecorded` (g50v3) in `objects/time.py`, and export it.
2. Test: `tests/unit/master/test_master.py`:
   - `test_build_record_current_time`: function code 0x18, no objects, and the next sequence;
   - `test_build_write_time_g50v1` / `test_build_write_time_recorded_g50v3`: exact request bytes for timestamp
     `DNP3Timestamp(0x0102_0304_0506)`, i.e. header `32 01 07`, count `01`, then the 6 little-endian bytes;
   - `test_delay_measure_response_sets_time_delay`: g52v2 = 250 gives `info.time_delay_ms == 250`, g52v1 = 2 gives
     2000, and a response without g52 gives None;
   - `test_propagation_delay`: `propagation_delay_ms(sent_ms=1000, received_ms=1300, outstation_delay_ms=100) ==
     100`, and it is clamped to 0 when `d` exceeds the round trip.
   Implement:
   - `build_record_current_time_request(seq)` in `application/builder.py`;
   - `Master.build_record_current_time()` and `Master.build_write_time(timestamp, *, recorded: bool = False)`;
   - `ResponseInfo.time_delay_ms: int | None = None`, filled from a g52 block in `_parse_response_objects`;
   - module function `propagation_delay_ms` in `master.py`.
3. Test: `tests/unit/master/test_tcp_runner.py::TestTimeSync`. Use `make_runner`, a fake clock
   (`runner.wall_clock_ms = iter([...]).__next__`, or a small counter object), and a `FakeOutstation` task that
   answers each request:
   - `test_non_lan_writes_corrected_time`: the clock reads 10_000 before DELAY_MEASURE, 10_300 after, and 10_400 at
     the write. The outstation reports d = 100. The master sends DELAY_MEASURE, then a WRITE whose g50v1 value is
     10_400 + 100. Assert on the decoded timestamp, not only that a WRITE went out;
   - `test_lan_writes_recorded_time`: the clock reads 20_000 before RECORD_CURRENT_TIME. The master sends 0x18,
     then a WRITE of g50v3 = 20_000;
   - `test_lan_unsupported_raises_with_hint`: a RECORD_CURRENT_TIME answer with `NO_FUNC_CODE_SUPPORT` raises
     `TimeSyncError`, whose message names `TimeSyncMethod.NON_LAN`, and no WRITE is sent;
   - `test_write_rejected_raises`: a WRITE answer with `PARAMETER_ERROR` raises `TimeSyncError`;
   - `test_missing_time_delay_raises`: a DELAY_MEASURE answer without g52 raises `TimeSyncError`, and no WRITE is
     sent;
   - `test_method_defaults_to_config`: `MasterConfig(time_sync_method=TimeSyncMethod.NON_LAN)` and `time_sync()`
     with no argument uses DELAY_MEASURE.
   Implement:
   - `TimeSyncMethod(Enum)` with `LAN` and `NON_LAN`, in `master/config.py`;
   - `MasterConfig.time_sync_method = TimeSyncMethod.LAN`;
   - on the runner: `wall_clock_ms: Callable[[], int]`, defaulting to a module function returning
     `DNP3Timestamp.now().milliseconds`, and `TimeSyncError(MasterRunnerError)`;
   - `async def time_sync(self, method: TimeSyncMethod | None = None) -> None`, built from `request()`.
   - Export `TimeSyncMethod` and `TimeSyncError` from `dnp3.master`.
4. Test: `TestStartup`:
   - `test_startup_default_flags`: default `MasterConfig`. The function codes the fake sees, in order, are READ
     (integrity) and ENABLE_UNSOLICITED;
   - `test_startup_all_flags`: all four flags set. DISABLE_UNSOLICITED, the time-sync pair, READ, then
     ENABLE_UNSOLICITED;
   - `test_startup_no_flags_sends_nothing`.
   Implement `async def startup(self) -> None` on the runner, in that order, with a docstring naming each flag.
5. Test, end to end: `tests/integration/test_tcp_master_runner_e2e.py`:
   - `test_time_sync_non_lan_against_outstation`: completes without error against the in-repo outstation;
   - `test_time_sync_lan_against_outstation_raises`: raises `TimeSyncError`, because the in-repo outstation does not
     support RECORD_CURRENT_TIME;
   - `test_startup_against_outstation`: completes, and the handler holds integrity-poll values.

## Review focus
- The WRITE in the non-LAN procedure must use the clock reading taken when the WRITE is built, not `t1`. The step 3
  clock values are distinct so that mistake fails.
- `time_sync()` shares the runner's channel lock through `request()`. `run_polls()` running concurrently must not
  interleave between DELAY_MEASURE and WRITE, but a short gap only costs accuracy. Say so in the docstring rather
  than holding the lock across both.

## Out of scope
- Automatic time sync when a response sets NEED_TIME. For outstation testing, the test decides when to sync;
  `ResponseInfo.iin` already exposes NEED_TIME.
- Clearing DEVICE_RESTART (WRITE g80v1) on startup. That is a candidate follow-up plan.
- `MasterConfig.enable_unsolicited` ("Accept unsolicited responses"). It stays unwired here.

## Done when
- [x] new tests pass
- [x] `uv run pytest tests/` passes, coverage >= 95%
- [x] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean

## Deviations
- No IEEE 1815-2012 section number in the docstrings. Neither the repo nor a web search confirmed the time
  synchronization clause number, so the docstrings name the procedures. Add the number when someone has the spec.
- `time_delay_ms` is decoded in `_process_response_fragment` and passed to the `ResponseInfo` constructor, not set in
  `_parse_response_objects`. The info is not changed after construction, and handlers see the field set.
- The review focus premise was off. The gap between the two requests costs nothing, since neither procedure depends
  on it. What cost accuracy was reading the clock before `request()` waited for the channel lock: the LAN procedure
  wrote a stale send time, and the non-LAN procedure counted the wait as round trip. `request()`'s body is now
  `_exchange()`, and each time sync step reads the clock and exchanges under one `_claim()`. The lock is still not
  held across both requests. `test_clock_read_once_the_channel_is_held` pins this for both methods.
- A rejected DELAY_MEASURE also raises `TimeSyncError`. Rejected means NO_FUNC_CODE_SUPPORT, OBJECT_UNKNOWN or
  PARAMETER_ERROR in the last fragment's IIN (`_REJECTED`).
- `startup()` logs a rejected DISABLE_UNSOLICITED or ENABLE_UNSOLICITED instead of dropping it silently, and its
  docstring says any other failure stops the sequence. Plan 020 turns the log into a raise.
- The LAN NO_FUNC_CODE_SUPPORT message names `MasterConfig.time_sync_method` and `TimeSyncMethod.NON_LAN`. The
  README lists RECORD_CURRENT_TIME as master only.
- Tests: `make_runner` takes `**config_options`, and the e2e `_poll_over_tcp` wraps a new `_runner_over_tcp` context
  manager. Added beyond the steps: rejected DELAY_MEASURE, the default wall clock, the clock read under the lock, g52
  blocks with no value (count 0, qualifier 0x06), a builder-level RECORD_CURRENT_TIME test, the `time_sync_method`
  default, a logged rejection in `startup()`, and a g50v3 entry in `test_base.py`'s golden table, which
  `test_golden_covers_every_registered_class` requires. The two write-time byte tests are one parametrized
  `test_build_write_time`.
- Follow-up plans: 023 (the in-repo outstation keeps a clock that time sync sets, so the default LAN method works
  against it) and 024 (an integrity poll resets the scheduled one, and a task never run is due at once). Not
  planned: the non-LAN round trip is timed on the wall clock, so a clock step during the exchange skews the delay.
  Timing it on `time.monotonic()` would need a second injectable clock.

## Upstream sync (2026-09-28)
Ported onto upstream's runner. It differs from this plan in three ways: link and channel failures raise
upstream's `LinkError` (`ConnectionLostError` does not exist), `startup()` uses the runner's `disable_unsolicited()`
and `enable_unsolicited()`, and g50v3 also has a wire-layout row. `ResponseInfo.time_delay_ms` is read through the
layout table. #73 is still open upstream.
