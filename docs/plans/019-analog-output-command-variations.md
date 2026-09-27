# 019: Analog output commands in every g41 variation

Status: todo
Branch: fix/analog-output-command-variations
Depends on: none

## Goal
`CommandBuilder.add_analog(index, value, variation=...)` sends the setpoint as g41v1 (32-bit integer, the default,
as today), g41v2 (16-bit integer), g41v3 (32-bit float) or g41v4 (64-bit float). A value the chosen variation cannot
represent raises `ValueError` when the operation is created. Today 12.7 is silently sent as 12, and 2**31 fails with
`struct.error` only at build time.

## Context
- `src/dnp3/master/commands.py`:
  - `ControlOperation` (`:47`): frozen, with `analog_value: float` and `is_analog: bool`;
  - `_build_control_blocks` (`:101`) packs every analog operation as `struct.pack("<iB", int(op.analog_value), 0)`
    into one g41v1 block;
  - `ANALOG_OUTPUT_32_VARIATION` .. `ANALOG_OUTPUT_DOUBLE_VARIATION` (`:26-30`) are public constants, used only for
    v1;
  - `_prefixed_block(group, variation, items)` builds one index-prefixed block and picks 0x17 or 0x28;
  - `CommandBuilder.add_analog(index, value)` (`:214`).
- `src/dnp3/objects/analog_output.py`: `AnalogOutputCommand32`, `AnalogOutputCommand16`, `AnalogOutputCommandFloat`
  and `AnalogOutputCommandDouble` (`:174`, `:194`, `:214`, `:233`), each with `value` and `status`. The integer ones
  range-check `value` and raise `ValueError`. Encode through their `to_bytes()` rather than a second struct table.
- Level 2 outstations are only required to parse g41v1 and g41v2 (IEEE 1815-2012 subset definitions). The float
  variations are optional, so int32 stays the default.
- opendnp3 has one command type per variation (`AnalogOutputInt16`, `Int32`, `Float32`, `Double64`) and writes one
  header per type.
- The in-repo outstation decodes all four variations on DIRECT_OPERATE (`src/dnp3/outstation/outstation.py:1203`) and
  passes the value to `CommandHandler.direct_operate_analog_output`.
- Tests and docs touching this:
  - `tests/unit/master/test_commands.py` (`TestCommandBuilder::test_add_analog` `:316`,
    `TestCommandTaskPolymorphism::test_analog_block_structure` `:536`);
  - `tests/integration/test_commands.py::TestAnalogOutput` (`:192`);
  - `docs/control-commands.md` `:131-137`, which documents the truncation.

## Steps
1. Test: `tests/unit/master/test_commands.py::TestAnalogVariations`:
   - `test_default_is_int32`: `add_analog(3, 42)` gives a g41v1 block whose object body is `2A 00 00 00 00`;
   - `test_variation_encoding`, parametrized with exact bytes after the index: INT16 -2 gives `FE FF 00`, FLOAT32
     12.5 gives `00 00 48 41 00`, and DOUBLE64 12.5 gives `00 00 00 00 00 00 29 40 00`;
   - `test_mixed_variations_one_block_each`: INT32 at index 0, FLOAT32 at 1, INT32 at 2. The result is one g41v1
     block with indices [0, 2], then one g41v3 block with [1] (first-appearance order), after any CROB block.
   Implement:
   - `AnalogOutputVariation(IntEnum)` in `commands.py`: `INT32 = 1`, `INT16 = 2`, `FLOAT32 = 3`, `DOUBLE64 = 4`;
   - `ControlOperation.analog_variation: AnalogOutputVariation = AnalogOutputVariation.INT32`;
   - `add_analog(index, value, variation=AnalogOutputVariation.INT32)`;
   - `_build_control_blocks` groups analog operations by variation and encodes each with its g41 class;
   - keep the `ANALOG_OUTPUT_*_VARIATION` constants, and export `AnalogOutputVariation` from `dnp3.master`.
2. Test: `TestAnalogValueChecks`:
   - `test_fractional_value_rejected_for_integer_variation`: `add_analog(0, 12.7)` raises `ValueError` whose message
     names FLOAT32. `add_analog(0, 12.0)` is accepted and sends 12;
   - `test_out_of_range_rejected_on_creation`: INT16 40000 and INT32 2**31 raise `ValueError` from `add_analog`, not
     from `build_*`;
   - `test_float32_overflow_rejected`: FLOAT32 1e39 raises `ValueError`;
   - `test_direct_control_operation_checked`: `ControlOperation(index=0, analog_value=12.7, is_analog=True)` raises
     too.
   Implement: `ControlOperation.__post_init__` validates analog operations by building the g41 object, and turns
   `OverflowError` / `struct.error` into `ValueError`.
3. Test: `tests/integration/test_commands.py::TestAnalogOutput`:
   - `test_direct_operate_float32`: DIRECT_OPERATE with FLOAT32 12.5 through `Outstation.process_request`. The
     tracking command handler receives 12.5;
   - `test_direct_operate_int16`: INT16 -2 arrives as -2.
4. Docs: replace the truncation paragraph in `docs/control-commands.md` (`:131-137`) with variation selection and
   the `ValueError` rule.

## Review focus
- Rejecting 12.7 for INT32 changes behavior for callers that relied on truncation. Say so in the squash commit body.
- `test_all_task_types_encode_identical_blocks` (`test_commands.py:569`) must still pass: SELECT, OPERATE and
  DIRECT_OPERATE encode the same blocks.

## Out of scope
- The 1-byte versus 2-byte index qualifier choice.
- Decoding the g41 echo and select-before-operate.
- Analog SELECT / OPERATE on the in-repo outstation, which only dispatches CROB for those.

## Done when
- [ ] new tests pass
- [ ] `uv run pytest tests/` passes, coverage >= 95%
- [ ] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean
