# 016: DataLinkFrame.from_bytes rejects LENGTH below 5

Status: todo
Branch: fix/frame-from-bytes-validation
Depends on: 009

## Goal
`DataLinkFrame.from_bytes` raises `ValueError` for any frame it can't represent, as its docstring promises. Today a
CRC-valid header with LENGTH below 5 returns a frame whose `user_data_length` is negative.

## Context
- `src/dnp3/datalink/frame.py` `DataLinkFrame.from_bytes` (`:156`): checks the header CRC, then loops over data blocks
  while `remaining > 0`. For LENGTH 0..4, `remaining` is negative, the loop is skipped, and a frame comes back.
- Nothing in `src/` calls it. `FrameParser` uses `DataLinkHeader.from_bytes` plus its own LENGTH check. It is public
  API, and tests call it: `tests/unit/datalink/test_frame.py`, `test_builder.py`, `tests/unit/test_coverage_gaps.py`.
- IEEE 1815-2012 Clause 9: LENGTH counts CONTROL, DESTINATION and SOURCE, so it is at least 5.
- Keep the check out of `DataLinkHeader` itself: `tests/unit/datalink/test_parser.py` `_header_with_length` builds
  headers with LENGTH 0..4 on purpose.

## Steps
1. Test: parametrized `test_from_bytes_rejects_length_below_minimum[0..4]` in `test_frame.py`: a CRC-valid 10-byte
   header with that LENGTH raises `ValueError`.
   Implement: after the header CRC check, raise `ValueError` if `header.length < LENGTH_FIELD_OVERHEAD`, with a
   one-line comment citing Clause 9.
2. Test: `test_from_bytes_rejects_truncated_input`: a valid frame with 20 bytes of user data, cut at every length from
   0 to one byte short, raises `ValueError` each time. It passes already, because every short slice fails a CRC check
   (checked at all 34 cut points). The test guards that.
3. Test: `test_from_bytes_ignores_trailing_bytes` pins today's behavior: extra bytes after a valid frame are ignored.

## Out of scope
- Changing `FrameParser`; it already rejects LENGTH below 5.
- Validation in `DataLinkHeader.__init__`.

## Done when
- [ ] new tests pass
- [ ] `uv run pytest tests/` passes, coverage >= 95%
- [ ] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean
