# 022: Binary flags encode the state field, not a STATE bit in quality

Status: todo
Branch: fix/binary-flags-state-bit
Depends on: none

## Upstream sync (2026-09-28)
Still todo. `src/dnp3/objects/double_bit.py` no longer exists (upstream decodes double-bit inputs in
`dnp3.master.double_bit`); the fix is still one line in `_BinaryFlags._pack` (`src/dnp3/objects/binary_input.py:36`).
The outstation encodes g1v2 and g10v2 in `_build_binary_input_blocks` / `_build_binary_output_blocks`
(`outstation.py:757`, `:772`) and g2v1 in `_build_binary_event_blocks` (`:886`).

## Goal
A binary point's reported state always comes from its `state` / `value`, never from its quality. Today an outstation
point updated with `value=False, quality=BinaryQuality.ONLINE | BinaryQuality.STATE` is sent to the master as ON.

## Context
- `src/dnp3/objects/binary_input.py` `_BinaryFlags._pack` (`:36`) ORs `int(quality)` with `STATE_BIT` (0x80) and does
  not mask quality. `BinaryQuality` names `STATE = 0x80`, so a quality that carries it sets bit 7 whatever `state` is.
  `_unpack` already strips bit 7 from quality.
- `_BinaryFlags` is the base of g1v2, g2v1-v3 (`binary_input.py`) and g10v2, g11v1-v2 (`binary_output.py`). One fix in
  `_pack` covers all seven.
- The outstation encodes through these classes with the database point's quality:
  - `src/dnp3/outstation/outstation.py:751` (g1v2) and `:766` (g10v2) for static data;
  - `:883` (g2v1) for events.
  `Database.update_binary_input` / `update_binary_output` (`src/dnp3/database/database.py:270`, `:304`) store the
  quality as given.
- `_DoubleBitFlags._pack` in `src/dnp3/objects/double_bit.py` already masks quality (`QUALITY_MASK`). Copy that shape.
- IEEE 1815-2012 Annex A, g1v2 and g10v2: bit 7 is the state and bits 0-6 are flags.

## Steps
1. Tests, all failing first:
   - `tests/unit/objects/test_binary_input.py::test_state_field_wins_over_quality_state_bit`: a hypothesis property
     over any quality 0-255 and either state. Encode a `BinaryInputFlags`, then decode it: the decoded `state` equals
     the input state, and the decoded quality equals `quality & 0x7F`.
   - The same property for `BinaryOutputFlags` in `test_binary_output.py`.
   - `tests/unit/outstation/test_outstation.py`: a binary input updated with `value=False` and quality
     `ONLINE | STATE` reads back from an integrity poll as g1v2 octet `0x01`.
   Implement: `_BinaryFlags._pack` returns `(int(quality) & ~STATE_BIT) | (STATE_BIT if state else 0)`, with a
   one-line comment that `BinaryQuality` also names the state bit.

## Out of scope
- Stripping `STATE` in the database. The encoder is the one place every path goes through.
- `BinaryQuality.RESERVED` (0x40): it is a flag bit on the wire, so it passes through unchanged.
- Other quality enums' spec citations ("Table 4-7" and similar).

## Done when
- [ ] new tests pass
- [ ] `tests/unit/objects/test_base.py` golden contracts pass unchanged
- [ ] `uv run pytest tests/` passes, coverage >= 95%
- [ ] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean
