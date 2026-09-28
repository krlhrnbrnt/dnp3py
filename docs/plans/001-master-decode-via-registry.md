# 001: Master decodes measurements through the object classes

Status: done
Branch: fix/master-decode-via-registry
Depends on: none

## Goal
`Master` stops using its private decoding tables and parses every non-packed measurement object with the registered
object classes. This fixes two misparses users hit today: frozen counters with time (g21v5/v6) and float/double
analog outputs (g40v3/v4). It is not a breaking change: no public name is removed and `parse_response` output is
unchanged.

## Context
- `src/dnp3/master/master.py`:
  - Private duplicate decoding, deleted by this plan: `_binary_object_width`, `_STATIC_BINARY_WIDTHS`,
    `_EVENT_BINARY_WIDTHS`, `_BINARY_EVENT_GROUPS`, `_BINARY_FLAGS_WIDTH`, `_RELATIVE_TIME_WIDTH`,
    `_decode_signed_int`, `_decode_float32`, `_decode_float64`, `_STATIC_ANALOG_SPECS`, `_EVENT_ANALOG_SPECS`,
    `_STATIC_COUNTER_SPECS`, `_EVENT_COUNTER_SPECS`, `_analog_value_spec`, `_counter_value_spec`, `_read_quality`.
  - Public names that must stay even if they become unused: `AnalogValueSpec`, `CounterValueSpec`, `ObjectLayout`,
    `PACKED_FORMAT_GROUPS`, `VARIATION_PACKED`, and every `GROUP_*`, `QUALITY_*`, `QUALIFIER_*` and `RANGE_*` constant.
  - Consumers: `_parse_binary_values`, `_parse_analog_values`, `_parse_counter_values`.
  - Keep `_decode_object_layout`, `_iter_object_slots` and `_parse_packed_binary` (g1v1/g10v1 packed).
- Bugs, per IEEE 1815-2012 Clause 4 object tables:
  - g21v5 and g21v6 are "frozen counter with flag and time" (11 and 9 bytes). The static counter table decodes them as
    4- and 2-byte values with no flag.
  - g40v3 and g40v4 are float and double "with flag" (5 and 9 bytes). The static analog table decodes them as int32
    and int16 with no flag.
- The registry has no group 11 v3, 40 or 42 classes.
  - Registering new ones would change `application/parser.py` (`_lookup_object_size` → `registry.get_size`). Today it
    gives a block of unknown size the rest of the fragment, so registering them changes `parse_response` output.
  - It could also collide with users' own `@register` classes for those group/variations.
  - Instead, alias them inside the master only:
    - g40 v1, v2, v3 and v4 → g30 v1, v2, v5 and v6;
    - g42 vN → g32 vN;
    - g11v3 → g2v3. This is not in the spec, but the master decodes it today, so keep it.
    - g40v5 → g30v5 and g40v6 → g30v6, for the same reason. (Added during implementation: the old static analog
      table covered v5/v6 for g40 too, so the golden test pins them.)
  - `_ABSOLUTE_TIMESTAMP_WIDTH` and the `struct` import also become unused and are deleted.
- No-flag classes have no `quality`: `Counter32NoFlag`, `Counter16NoFlag`, `AnalogInput32NoFlag` and
  `AnalogInput16NoFlag`.
- Ruff B009 forbids `getattr(obj, "value")`. Read fields through a `typing.Protocol` and `cast`.

## Steps
1. Test (characterization, passes before and after):
   `tests/unit/master/test_response_parsing.py::test_decoded_values_golden`. For every group/variation the master
   decodes today, except g21v5/v6 and g40v3/v4, feed a two-point block and pin the resulting `BinaryValue`,
   `AnalogValue` and `CounterValue` lists, captured from `main`. This proves nothing that works today changes.
2. Test: `test_frozen_counter_with_time_g21v5` and `..._g21v6` pin correct values, qualities and index alignment.
   Both fail today.
3. Test: `test_analog_output_float_g40v3` and `test_analog_output_double_g40v4` feed 5- and 9-byte objects and assert
   float values and qualities. Both fail today.
4. Implement: add a `quality` property to the four no-flag classes that returns the ONLINE flag. This only adds an
   attribute; no existing one changes.
5. Implement in `master.py`:
   - Add private `_ALIASES: dict[tuple[int, int], tuple[int, int]]` and `_object_class(g, v)`, which applies the
     alias and then calls `registry.lookup`.
   - Add private `_Measurement(Protocol)` with `quality: int` and `value: float`, and `_Binary(Protocol)` with
     `quality: int` and `state: bool`.
   - Each `_parse_*` method gets `cls` from `_object_class`, skips the block when it is `None`, takes the width from
     `cls.SIZE`, calls `cls.from_bytes(slot)` for each slot, and reads fields via `cast`.
   - Delete the private tables and helpers listed under Context.
6. Test: all existing `tests/unit/master/*` and `tests/integration/test_tcp_master_values_e2e.py` pass unchanged.

## Implementation notes
- The three `_parse_*` methods share one private `_decode_block(block)` that returns `(index, object)` pairs.
- `tests/unit/objects/test_registry.py`'s autouse `clean_registry` fixture emptied the global registry and never
  restored it, so master tests running after it saw no classes. It now swaps in an empty dict with `monkeypatch`.
- On Windows, three unrelated tests fail before and after this plan (port reuse, cp1252 file read, connection
  refused timing). CI runs on Linux and macOS.

## Out of scope
- Deprecating the now-unused public `AnalogValueSpec`, `CounterValueSpec` and `GROUP_TIME_DELAY`. Removing them would
  be breaking.
- Registering g40/g42 classes in the registry, which would change parser output.
- Rewriting the object classes (plan 006).

## Done when
- [x] new tests pass (golden, g21v5/v6, g40v3/v4)
- [x] `uv run pytest tests/` passes, coverage >= 95% (97.01%; see Windows note above)
- [x] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean

## Upstream sync (2026-09-28)
Replaced by upstream. craigpnnl/dnp3py #74 decodes every master value from the wire-layout table
(`src/dnp3/objects/layout.py`, `_DELIVERIES` in `src/dnp3/master/master.py`), so the registry-based `_decode_block`
this plan added was dropped when the fork was rebuilt on upstream. The goal still holds: the master decodes each
object from one description of its layout.
