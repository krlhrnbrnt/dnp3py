# 009: Data link parser survives garbage and short LENGTH fields

Status: done
Branch: fix/datalink-parser-hardening
Depends on: none

## Goal
`FrameParser.feed()` never raises on arbitrary input. Today one 4096-byte read of junk from an outstation raises
`RecursionError` out of `MasterTcpRunner` (its `READ_CHUNK_SIZE` is 4096). A CRC-valid header with LENGTH below 5 is
rejected instead of yielding a frame whose `user_data_length` is negative. Upstream issues #64 and #65.

## Context
- `src/dnp3/datalink/parser.py` `FrameParser._try_parse_frame` (`:190`): after a header CRC failure (`:218`) or a
  data-block CRC failure (`:236`) it deletes one byte and calls itself. That is one stack frame per skipped byte.
- `feed()` (`:171`) already loops over `_try_parse_frame()` until it returns None.
- `src/dnp3/datalink/frame.py`: `LENGTH_FIELD_OVERHEAD = 5`, `DataLinkHeader.user_data_length` returns
  `length - 5` unchecked, and `from_bytes` takes `length=data[2]` as is.
- `_calculate_frame_size(-5)` returns 5, so today a LENGTH=0 header consumes only 5 of its 10 bytes.
- IEEE 1815-2012 Clause 9: LENGTH counts CONTROL, DESTINATION and SOURCE plus user data, so it is at least 5.
- Tests: `tests/unit/datalink/test_parser.py`, classes `TestFrameParserErrors` and `TestFrameParserStreaming`.
  Build frames with `DataLinkFrame.build(...).to_bytes()`. Build a header with a chosen LENGTH and a valid CRC as
  `body + compute_crc(body).to_bytes(2, "little")` (`dnp3.core.crc.compute_crc`).

## Steps
1. Test: `TestFrameParserErrors::test_4k_of_start_bytes_does_not_raise` feeds `b"\x05\x64" * 2048` and asserts
   `list(feed(...)) == []` and `bytes_buffered < 10`. Add `test_garbage_then_valid_frame_is_recovered`: 8 KB of
   `b"\x05\x64"` followed by one valid frame yields exactly that frame, with its `user_data` intact.
   Implement: wrap the body of `_try_parse_frame` in `while True:` and replace both `return self._try_parse_frame()`
   with `continue`.
2. Test: `test_data_block_crc_failure_resyncs_iteratively`: a frame with 200 bytes of user data whose last data
   block CRC is corrupted, repeated 50 times back to back, then one good frame. Only the good frame is yielded and
   nothing raises. This covers the second self-call.
   Implement: nothing more if step 1 covered both sites.
3. Test: parametrized `test_length_below_minimum_is_rejected[0..4]`: a CRC-valid header with that LENGTH yields no
   frame. Add a companion case: the same bad header followed by a valid frame yields only the valid frame, and
   `bytes_buffered == 0` afterwards.
   Implement: in `_try_parse_frame`, after the CRC check, `if header.length < LENGTH_FIELD_OVERHEAD:` drop one byte
   and `continue`, the same path as a CRC failure. Add a one-line comment citing Clause 9.
4. Test: `test_length_five_is_smallest_legal`: LENGTH=5 still yields a frame with `user_data == b""`. It passes
   already and guards the boundary.
5. Test: Hypothesis `test_feed_never_raises` with `st.lists(st.binary(max_size=4096), max_size=8)`, feeding every
   chunk to one parser. Nothing raises, and every yielded frame has `header.length >= 5` and
   `len(user_data) == header.user_data_length`.

## Review focus
- A valid frame split across two `feed()` calls, directly after garbage, must still be yielded. The existing
  `TestFrameParserSplit` tests must pass unchanged.
- The loop must not spin when the buffer holds a partial header after the start bytes (fewer than 10 bytes). It
  must return None and wait for more input.

## Out of scope
- Rejecting LENGTH above 255. It can't happen, because the field is one byte.
- Quadratic cost of `del self._buffer[0]` per skipped byte. 4 KB of junk costs about 4000 small deletes, which is
  fine. Revisit only if profiling shows it.

## Done when
- [x] new tests pass
- [x] `uv run pytest tests/` passes, coverage >= 95%
- [x] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean

## Deviations
- Step 2: with plain user data, 50 bad frames recurse only 50 deep, so the test passed on the old code. The user data
  is `START_BYTES * 100`, so each bad frame also triggers about 100 header-CRC resyncs and the test fails before the
  fix.
- Step 3: the LENGTH check shares the header-CRC branch (`... or self._buffer[2] < LENGTH_FIELD_OVERHEAD`) instead
  of a separate `if` after parsing the header.
- Tests were trimmed after review. `test_4k_of_start_bytes_does_not_raise` was folded into
  `test_garbage_then_valid_frame_is_recovered` (which now asserts `bytes_buffered == 0`). The header-only LENGTH case
  was dropped for the header-plus-valid-frame case, which also shows nothing bogus is yielded.
- Added `test_garbage_then_frame_split_across_feeds` for the review focus: a frame after garbage, split at every cut
  point, including every partial header.
- Step 5: `st.binary` alone almost never yields a CRC-valid header, so the property test passed on the old code.
  It draws from random bytes, runs of start bytes, valid frames and CRC-valid headers with any LENGTH, then splits
  the stream at random points. It now fails on the old parser.
- Test helpers `_good_frame` and `_header_with_length` were added; the start bytes are spelled `START_BYTES`.
- Follow-up: `DataLinkFrame.from_bytes` still accepts LENGTH below 5. See 016.
