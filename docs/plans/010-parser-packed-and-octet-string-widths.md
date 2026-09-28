# 010: Response parser sizes packed and octet-string blocks

Status: done
Branch: fix/parser-packed-widths
Depends on: none

## Goal
A response containing a g1v1 (packed binary input) or g10v1 (packed binary output status) block, or an octet
string (g110/g111), no longer ends parsing at that block. Today every later block in the fragment is lost silently.
Many outstations report binary inputs as g1v1 by default, near the start of a Class 0 response, so a master can see
the binary inputs and nothing after them. This is part of upstream issue #71; the issue text lists the missing groups
but not these.

## Context
- `src/dnp3/application/parser.py`:
  - `parse_response_object_blocks` (`:300`) calls `_lookup_object_size(header)` (`:269`). When that returns None for
    a block that carries objects, the block takes the rest of the fragment and parsing stops (`:336-341`).
  - `_lookup_object_size` defers to `registry.get_size`. g1v1 and g10v1 are not registered, because their width is
    bits per point, not bytes per object.
  - `_parse_object_block(data, object_size)` (`:139`) computes `(prefix_size + object_size) * count`.
    `_parse_range(data, range_code)` returns `ParsedRange(start, stop, count, bytes_consumed)`.
- Packed layout (IEEE 1815-2012 Annex A, g1v1 and g10v1): one bit per point, point 0 in bit 0 of the first octet,
  and the last octet padded with zeros. The block's data length is `ceil(count / 8)`. Packed objects are never index
  prefixed.
- Octet strings (IEEE 1815-2012 Annex A, groups 110 and 111): the variation is the string length in octets, and
  variation 0 is not valid in a response.
- The master already decodes g1v1 and g10v1 from a correctly bounded block: `Master._parse_binary_values` and
  `_parse_packed_binary` in `src/dnp3/master/master.py`. It ignores g110/g111. That is fine; the point here is to
  step past them.
- Tests: `tests/unit/application/test_parser.py::TestParseResponseObjectBlocks` (raw-byte style, e.g.
  `test_two_blocks_are_delimited_by_object_size`) and `tests/unit/master/test_response_parsing.py` (`RESPONSE_HEADER`,
  `FLAGS_ON`, `indexed_values`).

## Steps
1. Test: `TestParseResponseObjectBlocks::test_packed_g1v1_then_g30v1`: g1v1 start 0 stop 9 (10 points, 2 data
   octets), then a g30v1 block with one point. Assert two blocks, in order. Assert the g1v1 block's `data` is exactly
   the 2 range bytes plus 2 data octets, and the g30v1 block's `data` holds its own range and 5 object bytes.
   Implement:
   - a module constant `_PACKED_BITS_PER_POINT: dict[tuple[int, int], int] = {(1, 1): 1, (10, 1): 1}`, with a
     comment citing the Annex A packed layout;
   - a helper `_packed_block_size(header, parsed_range) -> int` returning `(parsed_range.count * bits + 7) // 8`;
   - in `parse_response_object_blocks`, handle a packed key before the `_lookup_object_size` path: parse the range,
     require `PrefixCode.NONE`, and bound the block by range bytes plus the packed size.
   Keep `_parse_object_block`'s signature unchanged. If the cleanest route is an optional `data_size` parameter on
   it, that is fine, but request parsing (`parse_object_headers`) must not change.
2. Test: `test_packed_g10v1_then_g1v2`: the same shape for group 10. `test_packed_exact_octet_boundary`: 8 points
   is exactly 1 data octet, and 16 points is 2.
3. Test: `test_packed_block_short_data_keeps_block_and_stops`: a g1v1 block that declares 20 points but carries 1
   data octet behaves like `test_short_object_data_keeps_block_and_stops`: the block is kept with the bytes present,
   and parsing stops.
   Implement: route the short case through the existing truncated-block branch.
4. Test: `test_packed_with_index_prefix_is_unsized`: g1v1 with qualifier 0x17 (count plus 1-byte index prefix)
   absorbs the rest, as today. No spec layout exists for it, so it stays unsized.
5. Test: `test_octet_string_g110_is_delimited`: g110v4 with 2 objects (8 data octets), then g30v1. Two blocks.
   `test_octet_string_variation_zero_is_unsized`: g110v0 absorbs the rest, as today.
   Implement: in `_lookup_object_size`, `if header.group in _OCTET_STRING_GROUPS and header.variation: return
   header.variation`, with `_OCTET_STRING_GROUPS = frozenset({110, 111})` and a comment citing Annex A.
6. Test, end to end through the master: `tests/unit/master/test_response_parsing.py::TestMultiBlock` (or the class
   holding `test_multi_block_values_reach_handler`) `test_packed_binary_then_analog_reach_handler`:
   `Master.process_response(RESPONSE_HEADER + g1v1 block + g30v1 block)`. The recording handler gets both the binary
   values (index and state for each of the 10 points) and the analog value.
7. Test: Hypothesis `test_known_blocks_round_trip` in `tests/unit/application/test_parser.py`. Generate a list of 1
   to 6 blocks, each drawn from g1v1 (random count 1..40, random bits), g1v2, g30v1, g110vN (N 1..8). Concatenate
   their bytes and assert `parse_response_object_blocks` returns every header, in order, with each block's `data`
   equal to the bytes that were generated for it.

## Review focus
- Packed blocks with a start index other than 0, e.g. start 5 stop 14. Count is `stop - start + 1`. Add that case to
  step 1's test data.
- A count of 0 (a UINT8_COUNT of 0). It must consume only the range bytes.
- Stop below start. Each field is unsigned, but the wire can still carry stop < start, which gives a negative count.

## Out of scope
- Double-bit packed g3v1 (plan 012 adds `(3, 1): 2` to the same table).
- Missing object groups 3, 4, 23 and 40-43 (plans 011 and 012).
- Reporting a block whose width stays unknown (plan 013).

## Done when
- [x] new tests pass
- [x] `uv run pytest tests/` passes, coverage >= 95%
- [x] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean

## Deviations
- No `_packed_block_size` helper: the one-line size calculation sits inline in `_parse_object_block`, which gained
  an optional `bits_per_point` parameter. A separate `_lookup_packed_bits(header)` does the table lookup and returns
  None unless the block has no prefix and a decodable range.
- `_range_is_decodable(range_code)` was extracted so `_lookup_object_size` and `_lookup_packed_bits` share the range
  check. Without it in the packed lookup, g1v1 with an undecodable range (e.g. qualifier 0x0B) would consume only
  its header.
- Steps 2-4 passed on first run: step 1's implementation already covered them.
- Added, from review: a start-stop range with stop < start raises `ParseError` on the sized paths of
  `_parse_object_block`, so the block is kept and parsing stops. The plan said this range was impossible; it is not.
  Once packed blocks had a width, such a range let the parser read the next header from inside the block and hand
  invented points to the handler. Fixed-width blocks (g1v2, g30v1) had the same fault before this work.
- Added tests: g111v2 with qualifier 0x28 (index-prefixed octet-string events), and g10v1 in the round-trip
  property.

## Upstream sync (2026-09-28)
Replaced by upstream. #74 frames response blocks from the wire-layout table, which sizes the packed
variations (g1v1, g3v1, g10v1) by count and stops with `TruncationReason.PACKED_WITH_INDEX_PREFIX` on a packed block
with an index prefix. Octet strings are sized by g110/g111 rows in that table (the variation is the length, 1-255; no
row for variation 0), added when plan 025 was ported.
