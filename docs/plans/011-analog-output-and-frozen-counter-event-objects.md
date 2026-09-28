# 011: Analog output objects (g40-g43) and frozen counter events (g23)

Status: done
Branch: feat/analog-output-objects
Depends on: none (010 is independent; merge in either order)

## Goal
A master reading an outstation that reports analog outputs or frozen counter events gets those values, and every
block after them. `on_analog_output` fires from a real response for the first time: today the dispatch exists
but the parser stops before reaching it. `on_frozen_counter` also receives g23 frozen counter events. Upstream issue
#74. Analog output readback matters for DER outstations, where IEEE 1815.2 requires g40v1, g40v2, g41v2 and g42v1.

## Context
- `src/dnp3/objects/analog_input.py` is the pattern to follow: `@register @dataclass(frozen=True, slots=True)`
  classes on `FixedSizeObject`, one struct `FORMAT` per class, `_LABEL`, and `_RANGE_FIELD = "value"` for integer
  values. `src/dnp3/objects/counter.py` shows a no-flag class exposing a `quality` property (`:109`).
- `src/dnp3/objects/__init__.py` imports each object module and re-exports its classes in `__all__`.
- `src/dnp3/master/master.py`:
  - `_ALIASES` (`:209`) maps g40 and g42 keys onto g30 and g32 classes of the same layout. It exists only because
    g40/g42 are not registered. `(11, 3)`, `(40, 5)` and `(40, 6)` are not defined by the spec but are pinned by
    `tests/unit/master/test_response_parsing.py::test_decoded_values_golden`.
  - `_parse_response_objects` (`:538`) dispatches `{40, 42}` to `on_analog_output` and `21` to `on_frozen_counter`.
    Group 23 has no branch.
  - `_decode_block(block, field)` skips a class that has no `quality` or no `field` attribute, so g41 and g43 (which
    carry a status, not a quality) are skipped.
- Layouts (IEEE 1815-2012 Annex A; A.19 g40, A.20 g41, A.21 g42, A.22 g43, A.13 g23):

  | Key | Layout | Size |
  |-----|--------|------|
  | g40v1 / v2 | flags, INT32 / INT16 | 5 / 3 |
  | g40v3 / v4 | flags, FLT32 / FLT64 | 5 / 9 |
  | g41v1 / v2 | INT32 / INT16, status | 5 / 3 |
  | g41v3 / v4 | FLT32 / FLT64, status | 5 / 9 |
  | g42v1..v8 | same as g32v1..v8 | 5, 3, 11, 9, 5, 9, 11, 15 |
  | g43v1..v8 | status, then value (and time) in g42's order | 5, 3, 11, 9, 5, 9, 11, 15 |
  | g23v1 / v2 | flags, UINT32 / UINT16 | 5 / 3 |
  | g23v5 / v6 | flags, UINT32 / UINT16, time | 11 / 9 |

  The g41/g43 status octet holds the command status in bits 0-6; bit 7 is reserved.
- `src/dnp3/core/enums.py` `CommandStatus` has `UNDEFINED = 127`. `objects/binary_output.py` has a duplicate
  `CommandStatus` that must not be used here.

## Steps
1. Test: `tests/unit/objects/test_analog_output.py`:
   - parametrized over every new key: `registry.get_size(g, v)` equals the table above;
   - for each variation, a golden-bytes decode asserting each field. For example, g40v1 `01 D2 04 00 00` is ONLINE
     with value 1234, and g41v2 `39 30 00` is value 12345 with status 0. Round-trip with `to_bytes`;
   - `test_g41_status_ignores_reserved_bit`: status octet `0x84` decodes as status 4;
   - `test_g41_unknown_status_maps_to_undefined`: status 99 gives `command_status is CommandStatus.UNDEFINED` and
     does not raise.
   Implement `src/dnp3/objects/analog_output.py`:
   - `ANALOG_OUTPUT_STATUS_GROUP = 40`, `ANALOG_OUTPUT_COMMAND_GROUP = 41`, `ANALOG_OUTPUT_EVENT_GROUP = 42`,
     `ANALOG_OUTPUT_COMMAND_EVENT_GROUP = 43`;
   - g40: `AnalogOutputStatus32`, `AnalogOutputStatus16`, `AnalogOutputStatusFloat`, `AnalogOutputStatusDouble`, with
     `quality: AnalogQuality` and `value`;
   - g41: `AnalogOutputCommand32`, `AnalogOutputCommand16`, `AnalogOutputCommandFloat`, `AnalogOutputCommandDouble`,
     with `value` and `status: int` (the raw octet masked to 7 bits on decode), plus a `command_status` property
     returning `CommandStatus`, or `UNDEFINED` when the code is not in the enum. A raw int keeps decoding total: an
     outstation may send a status this library does not know;
   - g42: `AnalogOutputEvent32`, `AnalogOutputEvent16`, `AnalogOutputEvent32Time`, `AnalogOutputEvent16Time`,
     `AnalogOutputEventFloat`, `AnalogOutputEventDouble`, `AnalogOutputEventFloatTime`, `AnalogOutputEventDoubleTime`;
   - g43: `AnalogOutputCommandEvent32` ... `AnalogOutputCommandEventDoubleTime`, the same eight names with `Command`
     inserted, and fields `status: int`, `value` and `timestamp` where present;
   - register all of them and export them from `dnp3.objects`.
2. Test: `tests/unit/objects/test_counter.py::test_frozen_counter_event_*` for g23v1, v2, v5 and v6: size and a
   golden decode. Implement in `counter.py`: `FROZEN_COUNTER_EVENT_GROUP = 23` and `FrozenCounterEvent32`,
   `FrozenCounterEvent16`, `FrozenCounterEvent32Time`, `FrozenCounterEvent16Time`, as `EventObject`s. Export them.
3. Test: `tests/unit/master/test_response_parsing.py`:
   - `test_g40_then_g30_both_reach_handler`: one g40v1 block (qualifier 0x00, indices 0-1), then a g30v1 block, sent
     through `Master.process_response`. `on_analog_output` gets both indices with their values and quality, and
     `on_analog_input` gets the g30 value. Extend the local `RecordingHandler` with `on_analog_output` and
     `on_frozen_counter`;
   - `test_g42_event_reaches_on_analog_output`: g42v3 with count qualifier 0x17, index 7;
   - `test_g23_event_reaches_on_frozen_counter`: g23v1 with 0x17, index 3, value 42;
   - `test_g41_echo_does_not_stop_parsing`: a g41v2 block followed by a g1v2 block. The binary value arrives, and no
     analog output is reported from the g41 block.
   Implement in `master.py`:
   - add `GROUP_FROZEN_COUNTER_EVENT = 23` to the frozen-counter branch;
   - delete the `_ALIASES` entries for keys that are now registered. Keep `(11, 3)`, `(40, 5)` and `(40, 6)`, and
     update the comment above the table to say why only those remain.
4. Update the tests that used g40 as the example unknown group, so they still test what their names say:
   - `test_parser.py::test_unknown_group_absorbs_remainder` and
     `test_response_parsing.py::test_unregistered_group_block_still_parses` / `test_unregistered_trailing_block_is_not_dropped`:
     switch these to an unassigned group such as 200, or delete them when g40 coverage in step 3 supersedes them.
   - Update `_lookup_object_size`'s docstring in `application/parser.py`: it names groups 40/42 as unregistered.
5. Test: `tests/integration/test_tcp_master_values_e2e.py` (or the closest existing e2e with an in-repo outstation):
   if the outstation database can hold analog outputs, an integrity poll reports them through `on_analog_output`.
   If the in-repo outstation cannot emit g40, skip this step and say so in the plan's deviation notes.

## Review focus
- Endianness and signedness: g40v1/g41v1 are signed INT32 (`i`), and g23v1 is unsigned UINT32 (`I`). A negative
  analog output setpoint must decode negative. Add a golden case with value -1 for g40v1 and g41v1.
- g41 and g43 put the status in different positions: after the value in g41, before it in g43. The golden tests
  must use distinct bytes for status and value so a swapped layout fails.

## Out of scope
- g13 binary output command events, and g21v9/v10. Plan 013 makes any such block visible if it appears.
- An SOE callback for g43 command events.
- Master-side encoding of g41 requests beyond what `commands.py` already does.

## Done when
- [x] new tests pass
- [x] `test_decoded_values_golden` passes unchanged
- [x] `uv run pytest tests/` passes, coverage >= 95%
- [x] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean

## Deviations
- Step 5 skipped: the in-repo outstation `Database` has no analog output points, so it cannot emit g40.
- The step 4 parser change (`test_unknown_group_absorbs_remainder` to group 200, `_lookup_object_size` docstring)
  landed with step 1: registering g40 is what broke that test.
- `tests/unit/objects/test_base.py` gained a contract for each new class; its
  `test_golden_covers_every_registered_class` requires one for every registered class.
- The handler in `test_response_parsing.py` is `CollectingHandler`, not `RecordingHandler`; it was extended.
- `test_unregistered_group_block_still_parses` was deleted (the g40 dispatch tests supersede it);
  `test_unregistered_trailing_block_is_not_dropped` uses group 200.
- Step 3's g40, g42 and g41 tests passed on first run, because step 1 already let the parser size those blocks. Only
  the g23 test failed first.
- The step 1 size table was folded into the golden tests: each golden row is keyed by (group, variation), looked up
  in the registry, and checks `get_size` against its byte length. Step 2's g23 tests are one parametrized
  `test_frozen_counter_event_decode`.
- g40 classes share `is_online` through a private `_AnalogOutputStatus` base; g41 and g43 share a
  `_CommandStatusObject` base that masks the status on decode. g41 classes are `StaticObject`s, like `CROB`.
- Added, from review: building a g41 or g43 object with `status` outside 0-127 raises `ValueError`; a master test
  shows a g43 block does not stop parsing and is not reported; README and `docs/control-commands.md` list groups 23
  and 43 and read g41 echo statuses with the new classes.
- No follow-up plan. g11v3 and g40v5/v6 still have no registered width, so such a block takes the rest of the
  fragment. Plan 001 chose that, and plan 013 makes such blocks visible.

## Upstream sync (2026-09-28)
Replaced by upstream. #74 added wire-layout rows for g40-g43 and g23; the master delivers g40/g42 on
`on_analog_output` and g23 on `on_frozen_counter`, and frames g41/g43 without delivering them. The object classes this
plan added (`src/dnp3/objects/analog_output.py` and the g23 classes in `counter.py`) were dropped, because nothing
reads them any more. Plan 019 encoded through the g41 classes and is marked for revision.
