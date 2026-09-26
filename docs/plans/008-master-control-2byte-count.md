# 008: Two-byte count for master control blocks with qualifier 0x28

Status: done
Branch: fix/master-control-2byte-count
Depends on: 002

## Goal
SELECT, OPERATE and DIRECT_OPERATE requests that address a point index above 255 are well formed. Today the master
tags such blocks with qualifier 0x28 (2-byte count, 2-byte index prefix) but writes a 1-byte count. An outstation
reads the first index byte as the count's high byte and misparses the whole block.

## Context
- `src/dnp3/master/commands.py`: `_prefixed_block` writes `bytes([len(items)])` for both qualifiers. IEEE
  1815-2012 Table 4-3 makes the range field of 0x28 a 2-byte little-endian count.
- The outstation already parses 0x28 with a 2-byte count (`src/dnp3/outstation/outstation.py`, `QUALIFIER_CROB_2BYTE`),
  so master-to-outstation control with index > 255 does not round-trip today.
- `tests/unit/master/test_commands.py::TestCommandTaskPolymorphism::test_all_task_types_encode_identical_blocks` is
  parametrized: case `0x28` pins the current 1-byte count, with a comment noting it; case `0x17` pins the 0x17
  bytes, which this plan must not change.
- A block of more than 255 operations with all indices <= 255 overflows the 1-byte count of 0x17 (`ValueError` from
  `bytes`). Choosing 0x28 when `len(items) > 255` fixes that in the same place.

## Steps
1. Test: in the `0x28` case of `test_all_task_types_encode_identical_blocks`, change the golden bytes to a 2-byte
   count (`0200`) and drop the comment. Run it and see it fail.
2. Test: master-to-outstation round trip, a DIRECT_OPERATE on CROB index 300 is parsed by the outstation as one
   operation on index 300. Run it and see it fail.
3. Implement: `_prefixed_block` writes the count with `index_size` bytes. Update the comment above it.
4. Test: 256 operations with indices <= 255 build a 0x28 block with a 2-byte count of 256. Run it and see it fail.
5. Implement: pick 0x28 when the largest index or the item count exceeds `MAX_1BYTE_INDEX`.

## Out of scope
- Qualifier 0x5B or 4-byte counts; a 65535-operation request does not fit in one fragment anyway.
- Splitting large requests across fragments: 256 CROBs (13 bytes each) already exceed a 2048-byte fragment. Step 5
  only removes the `ValueError`.

## Done when
- [x] new tests pass
- [x] `test_all_task_types_encode_identical_blocks[0x17]` passes unchanged
- [x] `uv run pytest tests/` passes, coverage >= 95%
- [x] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean

## Deviations
- `_prefixed_block` encodes an empty item list as a 0x17 block with a zero count instead of raising `TypeError`.
  No caller passes an empty list today.
- Removed `test_direct_operate_high_index`: it asserted only that a response came back, and passed while the encoding
  was broken. The step 2 round-trip test covers the same 0x28 path.
- Follow-up candidate: a master-to-outstation round trip for an analog output with qualifier 0x28. The outstation
  handles analog outputs on a separate path, and only unit golden bytes cover it.
