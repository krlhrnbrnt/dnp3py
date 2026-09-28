# 012: Double-bit binary inputs (g3, g4)

Status: done
Branch: feat/double-bit-inputs
Depends on: 010 (adds the packed-width table this plan extends)

## Goal
A master reads double-bit binary inputs, which are typical for breaker and disconnector positions, with all four
states intact: intermediate, off, on and indeterminate. It also keeps every block after them; today a g3 block
ends response parsing. Values reach a new opt-in handler callback, `on_double_bit_input`, carrying a
`DoubleBitState`. Upstream issue #76, plus the g3/g4 part of #74.

## Context
- `src/dnp3/core/flags.py`: `DoubleBitState` (INTERMEDIATE 0, OFF 1, ON 2, INDETERMINATE 3) and `DoubleBitQuality`
  (ONLINE 0x01 .. CHATTER_FILTER 0x20, STATE_BIT_0 0x40, STATE_BIT_1 0x80) are correct and exported, but unused.
  Their docstrings cite "Table 4-10", which is wrong. The state values are in IEEE 1815-2012 11.9.6 / Table 11-14,
  and the flag octet is in A.4.2.2.2.
- Layouts (IEEE 1815-2012 A.4, g3; A.5, g4):
  - g3v1 is packed, 2 bits per point, 4 points per octet. Point 0 is in bits 0-1, and the last octet is zero padded
    (A.4.1.2.1).
  - g3v2 is one octet: flags in bits 0-5, state in bits 6-7, so `state = octet >> 6` (A.4.2.2.2).
  - g4v1 is the g3v2 octet. g4v2 is the octet plus a 6-byte absolute time. g4v3 is the octet plus a 2-byte relative
    time.
- `src/dnp3/objects/binary_input.py` `_BinaryFlags` shows how to split one flags octet into two dataclass fields
  through `_pack` / `_unpack` overrides. Copy the shape, not the masks.
- `src/dnp3/application/parser.py` `_PACKED_BITS_PER_POINT` (added by plan 010) sizes packed blocks.
- `src/dnp3/master/master.py`:
  - `_parse_response_objects` (`:538`) collects values per point type and calls one handler method per type;
  - `_parse_packed_binary(layout, data)` (`:244`) is the 1-bit model for a 2-bit decoder;
  - `_decode_block(block, "state")` decodes fixed-size objects.
- `src/dnp3/master/handler.py`: `SOEHandler` is a `runtime_checkable` Protocol.
  `tests/unit/master/test_handler.py::test_custom_implementation` asserts that a handler with exactly today's six
  methods is an `SOEHandler`. Adding a seventh method to `SOEHandler` would break that and every user handler, so
  the new callback goes on a separate Protocol.

## Steps
1. Test: `tests/unit/objects/test_double_bit.py`:
   - g3v2 golden decodes, one per state, asserting both fields:
     - `0x81`: ONLINE, `DoubleBitState.ON`;
     - `0x41`: ONLINE, OFF;
     - `0xC1`: ONLINE, INDETERMINATE;
     - `0x01`: ONLINE, INTERMEDIATE;
     - `0x3F`: all six quality flags, INTERMEDIATE.
     Round-trip each through `to_bytes`. These cases are the only thing that catches a swapped state-bit order, so
     check them against A.4.2.2.2 while writing them.
   - Sizes: g3v2 1, g4v1 1, g4v2 7, g4v3 3.
   - g4v2 decodes its timestamp, and g4v3 its relative time.
   Implement `src/dnp3/objects/double_bit.py`:
   - `DOUBLE_BIT_INPUT_STATIC_GROUP = 3`, `DOUBLE_BIT_INPUT_EVENT_GROUP = 4`;
   - a private `_DoubleBitFlags(FixedSizeObject)` base whose `_unpack` sets `quality = DoubleBitQuality(octet &
     0x3F)` and `state = DoubleBitState(octet >> 6)`, and whose `_pack` reverses that;
   - `DoubleBitInputFlags` (g3v2), `DoubleBitInputEvent` (g4v1), `DoubleBitInputEventTime` (g4v2, `"<B6s"`),
     `DoubleBitInputEventRelativeTime` (g4v3, `"<BH"`);
   - register and export them from `dnp3.objects`;
   - fix the two "Table 4-10" docstrings in `core/flags.py`.
2. Test: `tests/unit/application/test_parser.py::test_packed_g3v1_then_g1v2`: g3v1 with 5 points (2 data octets),
   then g1v2. Two blocks, each with exactly its own bytes. Implement: add `(3, 1): 2` to `_PACKED_BITS_PER_POINT`.
3. Test: `tests/unit/master/test_handler.py`:
   - `test_default_handler_stores_double_bit`: `DefaultSOEHandler.on_double_bit_input` stores values by index;
     `double_bit_inputs` and `get_double_bit_input(index)` read them back; `clear()` empties them;
   - `test_default_handler_is_double_bit_handler`: `isinstance(DefaultSOEHandler(), DoubleBitHandler)`;
   - `test_custom_implementation` passes unchanged.
   Implement in `handler.py`:
   - `DoubleBitValue(index: int, value: DoubleBitState, quality: int = 0, timestamp: datetime | None = None)`,
     frozen, like `BinaryValue`;
   - `@runtime_checkable class DoubleBitHandler(Protocol)` with `on_double_bit_input(values, info)`. Its docstring
     says it is separate from `SOEHandler` so existing handlers stay valid;
   - `DefaultSOEHandler` gains the method, the `double_bit_inputs` property, `get_double_bit_input` and the `clear()`
     line.
4. Test: `tests/unit/master/test_response_parsing.py`:
   - `test_g3v1_packed_decodes_all_four_states`: 4 points in one octet `0b11_10_01_00` decode as index 0
     INTERMEDIATE, 1 OFF, 2 ON, 3 INDETERMINATE;
   - `test_g3v1_partial_octet_ignores_padding`: 3 points with the top 2 bits set reports only 3 points;
   - `test_g3v2_then_g1v2_both_delivered`: `on_double_bit_input` and `on_binary_input` both fire;
   - `test_g4v1_event_with_count_qualifier`: 0x17, index 9, octet 0x81 gives index 9, ON, quality 0x01;
   - `test_handler_without_double_bit_callback_is_skipped`: a handler with only today's six methods gets a g3v2 plus
     g1v2 response. No exception, and the binary input still arrives.
   Implement in `master.py`:
   - `GROUP_DOUBLE_BIT_INPUT = 3` and `GROUP_DOUBLE_BIT_INPUT_EVENT = 4`;
   - `_parse_packed_double_bit(layout, data) -> list[DoubleBitValue]`, with quality ONLINE, as the 1-bit packed path
     does;
   - `Master._parse_double_bit_values(block)`: packed for g3v1, otherwise `_decode_block(block, "state")`;
   - in `_parse_response_objects`, collect them and call `on_double_bit_input` only when
     `isinstance(self.handler, DoubleBitHandler)`.
   - Export `DoubleBitValue` and `DoubleBitHandler` from `dnp3.master`.

## Review focus
- g3v1 bit order within the octet: point 0 is the least significant pair. The step 4 octet makes each state appear
  exactly once, so a reversed order fails visibly.
- A start index other than 0 on a packed g3v1 block: index = `first_index + ordinal`, as in `_parse_packed_binary`.

## Out of scope
- Resolving g4v3 relative times against a g51 CTO. The master does not resolve timestamps for g2v3 either.
- Outstation-side support for double-bit points.

## Done when
- [x] new tests pass
- [x] `tests/unit/master/test_handler.py::test_custom_implementation` passes unchanged
- [x] `uv run pytest tests/` passes, coverage >= 95%
- [x] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean

## Deviations
- `tests/unit/objects/test_base.py` gained a contract for each new class; its
  `test_golden_covers_every_registered_class` requires one for every registered class. Those contracts hold the
  step 1 size, g4v2 timestamp and g4v3 relative-time checks, so `test_double_bit.py` keeps only the registry lookups,
  the golden octets and a state-bit property test.
- g4v3 checks `relative_time_ms` in its own `__post_init__`, as g2v3 does. `FixedSizeObject._RANGE_FIELD` maps struct
  codes one-to-one onto fields, and the flags octet holds two fields.
- There is no `_parse_packed_double_bit`. `_iter_packed(layout, data, bits)` walks the bit fields of g1v1, g10v1 and
  g3v1, and `_parse_double_bit_values` builds the g3v1 values inline.
- `ObjectLayout.count` is now `int`. `_decode_object_layout` never set it to None.
- `test_g4v1_event_with_count_qualifier` became `test_g4_event_then_g1v2_both_delivered`, parametrized over g4v1-v3,
  each followed by a g1v2 block. Also added: g3v1 with a nonzero start index, g3v1 then g1v2 end to end, a malformed
  g3v1 block, and the `dnp3.master` exports.
- Added, from review: `_DoubleBitFlags._pack` masks quality to bits 0-5. `DoubleBitQuality` names STATE_BIT_0/1, so a
  quality carrying them could override the state on the wire.
- README and `docs/control-commands.md` list groups 3 and 4.
- Follow-up: 022 fixes the same state-bit leak in the single-bit binary flags (g1, g2, g10, g11).

## Upstream sync (2026-09-28)
Replaced by upstream #76. It differs from this plan's API:
- `DoubleBitValue` lives in `dnp3.master.double_bit`, and its state field is `state`, not `value`;
- the opt-in protocol is `DoubleBitInputHandler.on_double_bit_input`;
- `DefaultSOEHandler.double_bit_inputs` and `get_double_bit_input` exist, and g4v2/g4v3 values carry `timestamp`.
The object classes in `src/dnp3/objects/double_bit.py` were dropped. Plan 022 is unaffected.
