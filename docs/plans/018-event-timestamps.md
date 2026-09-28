# 018: Event timestamps reach the handler

Status: todo
Branch: fix/event-timestamps
Depends on: 017 (adds `HeaderInfo`, which gains the timestamp quality), 012 (g4v2 / g4v3 get the same treatment)

## Upstream sync (2026-09-28)
Mostly done upstream (#81). Every timed layout row sets `timestamp` (a UTC `datetime`); relative-time
objects (g2v3, g4v3) are timed from the last g51 CTO before them in the same fragment; a CTO does not carry across
fragments; g51 blocks are not delivered; and `ResponseInfo.relative_time_without_cto` counts relative-time objects
left untimed. Steps 1 and 3 are done (tests in `tests/unit/master/test_event_time.py`).

Remaining: timestamp quality (step 2, which needs plan 017's `HeaderInfo`; upstream #117 tracks the
unsynchronized-CTO case), the end-to-end counter event test (step 4) and the README example (step 5). Cut the plan
down to those before implementing.

## Goal
Every value decoded from an object that carries a time has `timestamp` set, as a UTC `datetime`. Relative-time
events (g2v3, g4v3) are resolved against the g51 common time of occurrence (CTO) that precedes them in the fragment.
`HeaderInfo.timestamp_quality` says whether that time is synchronized, unsynchronized or absent. Today `timestamp`
is always None, even though the objects decode it.

## Context
- `src/dnp3/master/master.py` `_parse_binary_values` (`:578`), `_parse_analog_values` (`:601`) and
  `_parse_counter_values` (`:615`) build values without `timestamp` (e.g. `:597`).
- Classes with a `timestamp: DNP3Timestamp` field: g2v2, g11v2, g21v5/v6, g22v5/v6, g23v5/v6, g32v3/v4/v7/v8,
  g42v3/v4/v7/v8, g43v3/v4/v7/v8, and g4v2 after plan 012. g2v3 (and g4v3) carry `relative_time_ms: int` instead.
- `src/dnp3/core/timestamp.py` `DNP3Timestamp.to_datetime()` (`:27`) returns a UTC `datetime`.
- `src/dnp3/objects/time.py`: g51v1 `TimeCTO` (`:52`) and g51v2 `TimeCTOUnsync` (`:72`), each with `timestamp`.
  `_parse_response_objects` ignores g51 today. Outstations send it with qualifier 0x07 and count 1.
- IEEE 1815-2012 Annex A, group 51: a CTO applies to the relative-time objects after it in the same fragment. Cite
  the exact subsection in the code comment.
- opendnp3 reference (`MeasurementHandler`): it keeps the last CTO per fragment. Absolute-time variations and g51v1
  give SYNCHRONIZED, g51v2 gives UNSYNCHRONIZED, and everything else gives INVALID.
- The in-repo outstation emits g22v5 counter events stamped at change time (`src/dnp3/outstation/outstation.py:915`).
  `tests/integration/test_tcp_master_runner_e2e.py` has the outstation and runner setup.

## Steps
1. Test: `tests/unit/master/test_response_parsing.py::TestTimestamps`:
   - `test_absolute_time_sets_timestamp`, parametrized over g2v2, g22v5, g32v3 and g42v3 (one per value type), each
     with `DNP3Timestamp(1_700_000_000_123)`. The value's `timestamp == datetime(2023, 11, 14, 22, 13, 20, 123000,
     tzinfo=UTC)`;
   - `test_no_time_variation_has_none`: g2v1 and g1v2 give None.
   Implement: a private `_timestamp_of(obj) -> datetime | None` in `master.py`, passed into every value built.
2. Test: `TestTimestampQuality`: an absolute-time variation gives `header.timestamp_quality is
   TimestampQuality.SYNCHRONIZED`, and a no-time variation gives INVALID.
   Implement in `handler.py`:
   - `TimestampQuality(Enum)` with `SYNCHRONIZED`, `UNSYNCHRONIZED` and `INVALID`. The docstring says SYNCHRONIZED
     means the object carries an absolute time, and that whether the outstation's clock is set is IIN1.4 NEED_TIME;
   - `HeaderInfo.timestamp_quality: TimestampQuality = TimestampQuality.INVALID`;
   - export `TimestampQuality`.
3. Test: `TestCommonTimeOfOccurrence`:
   - `test_relative_time_resolved_against_cto`: g51v1 = T, then g2v3 (0x17) index 4 with relative time 250. The
     timestamp is T + 250 ms, and the quality is SYNCHRONIZED;
   - `test_unsynchronized_cto`: the same with g51v2 gives UNSYNCHRONIZED;
   - `test_latest_cto_applies`: g51v1 T1, g2v3 r1, g51v1 T2, g2v3 r2 gives T1 + r1 and T2 + r2;
   - `test_relative_time_without_cto`: g2v3 alone is still delivered, with timestamp None and quality INVALID;
   - `test_cto_does_not_carry_across_fragments`: one `process_response` call with only g51, then one with only g2v3.
     The timestamp is None;
   - `test_cto_block_not_delivered`: a g51 block causes no handler call.
   Implement: `_parse_response_objects` keeps a local CTO (timestamp and quality), reset per fragment, and updates it
   from each g51 block. Classes with `relative_time_ms` resolve against it. Add a one-line comment citing the Annex A
   rule.
4. Test, end to end: `tests/integration/test_tcp_master_runner_e2e.py::test_counter_event_timestamp`: add a counter,
   record `before`, update it, record `after`, then class-poll. The event value has `header.is_event`, and its
   timestamp lies between `before` minus 1 ms and `after` (the wire resolution is 1 ms).
5. Docs: in the `README.md` master section, add a short handler example reading `value.timestamp` and
   `value.header.is_event`.

## Review focus
- Relative time is added as milliseconds: `cto.to_datetime() + timedelta(milliseconds=relative_time_ms)`. The golden
  values in step 3 must differ enough that seconds-for-milliseconds fails.
- A g51 block with count other than 1 is unusual. Take its first object and ignore the rest, as opendnp3 does.

## Out of scope
- Converting times out of UTC.
- Making the in-repo outstation send CTOs or relative-time events.
- Delivering g50 time values to the handler.

## Done when
- [ ] new tests pass
- [ ] `uv run pytest tests/` passes, coverage >= 95%
- [ ] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean
