# 004: Table-driven outstation static and event blocks

Status: todo
Branch: refactor/outstation-table-driven-blocks
Depends on: none

## Upstream sync (2026-09-28)
Still todo. Line numbers in Context predate the sync; find symbols by name. Upstream changed the
outstation since this was written: responses can set CON (#77), event buffer entries have serials (#77), and
`process_request` takes a `peer` (#72). The five static builders, three event builders and `_read_*` wrappers are
still there. `dnp3.objects.layout.object_width(group, variation)` now gives each object's width and could replace the
per-type widths in `_read_class_events`.

## Goal
The outstation builds READ responses from one static-block path and one event-block path driven by a table, instead
of 13 near-identical private methods. Wire output and public API are unchanged.

## Context
- `src/dnp3/outstation/outstation.py`, all private:
  - Static block builders `_build_binary_input_blocks`, `_build_binary_output_blocks`, `_build_analog_input_blocks`,
    `_build_counter_blocks` and `_build_frozen_counter_blocks` (740-813) differ only in `(group, variation)`,
    serializer and per-point width.
  - `_read_binary_inputs`, `_read_binary_outputs`, `_read_analog_inputs`, `_read_counters` and
    `_read_frozen_counters` (947-992) are all the same "get_all_*; empty → []; else build" wrapper. None of them uses
    its `block` argument.
  - `_read_all_static_data` (709-738) repeats the same five calls.
  - Event builders `_build_binary_event_blocks`, `_build_analog_event_blocks` and `_build_counter_event_blocks`
    (869-945) differ only in group/variation and serializer. `_read_class_events` (815-846) carries the per-type
    widths 2, 6 and 12.
  - `_handle_read` (624-683) is an if/elif chain over groups.
  - `_parse_crob_block` (403-503) unpacks the 11-byte body by hand.
- Helpers to reuse: `_build_static_blocks`, `_static_block_capacity`, `_event_block_capacity`, `_chunk_run` and
  `_event_framing`.
- Public module constants stay, even where unused: every `GV_*`, `GROUP_*` and `QUALIFIER_CROB_*`, and
  `MAX_1_BYTE_INDEX`/`MAX_2_BYTE_INDEX`.
- Preserve the current behavior: reading g2, g32 or g22 returns all class 1, 2 or 3 events respectively, as today.
- These tests call private methods that will go away:
  - `tests/unit/test_coverage_gaps.py` calls the five `_build_*_blocks` methods;
  - `tests/unit/outstation/test_outstation.py` calls `_read_binary_inputs`.

  Rewrite them to go through `Outstation.process_request` with the same assertions.

## Steps
1. Test (characterization, passes before and after):
   `tests/unit/outstation/test_outstation.py::test_read_responses_golden`. Build a database with sparse points of all
   five static types and events of all three types, with indices on both sides of 255. For class 0, class 1/2/3 and
   each group READ, pin the exact response bytes captured from `main`, including with a small `max_fragment_size`
   so blocks are chunked.
2. Implement: add private `_STATIC_KINDS`, a dict keyed by read group, with values
   `((group, variation), getter: Callable[[Database], list[Any]], serialize: Callable[[Any], bytes], width)`. Replace
   the five static builders and five `_read_*` wrappers with `_static_blocks(group) -> list[ObjectBlock]`.
   `_read_all_static_data` iterates the table in its current order.
3. Implement: add private `_EVENT_KINDS`, a list of `(event_type, (group, variation), serialize, width)` for
   `BinaryEvent`, `AnalogEvent` and `CounterEvent`. Replace the three event builders with one
   `_event_blocks(gv, events, serialize)`.
4. Implement: in `_handle_read`, dispatch static groups through `_STATIC_KINDS` and event groups through a private
   `{2: CLASS_1, 32: CLASS_2, 22: CLASS_3}` map. Keep g60 handling and the `OBJECT_UNKNOWN` fallback. Rewrite the six
   private-method tests listed under Context.
5. Test: the existing CROB format-error tests in `tests/unit/outstation/test_outstation.py` and
   `test_direct_operate_response_format.py` pass. Implement: in `_parse_crob_block`, unpack the body with
   `struct.unpack_from("<BBII", data, offset)`. `ParsedCrob` is unchanged.

## Out of scope
- `tcp_runner.py` (plan 005).
- Whether reading g2 should return only binary events. That would be a behavior change.

## Done when
- [ ] new tests pass
- [ ] `uv run pytest tests/` passes, coverage >= 95%
- [ ] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean
