# 013: Report what the master could not parse

Status: done
Branch: fix/report-unparsed-response-data
Depends on: 010, 011, 012 (so the groups a Level 2 outstation sends are sized before unknown ones stop being
absorbed)

## Goal
When the master cannot parse part of a response, it says so instead of failing silently. There are two cases:
- The object data stops early: an unknown group or variation, a reserved qualifier, or a truncated block. The values
  parsed so far are still delivered. `ResponseInfo.unparsed` says where parsing stopped and why, and the master logs
  a warning naming the group and variation. An unknown block's bytes are no longer attached to a header that does
  not describe them.
- The whole fragment fails to parse. `process_response` still returns None, but only for `ParseError`, and it logs
  the reason. Any other exception is a bug and propagates.

This lets a test engineer tell "the outstation didn't send it" from "the master couldn't read it". Upstream issues
#71 (observability part) and #66.

## Context
- `src/dnp3/application/parser.py` `parse_response_object_blocks` (`:300`) has three early stops:
  - reserved qualifier (`:330`): `break`, silently;
  - unknown width (`:336`): the block absorbs the rest of the fragment, then `break`;
  - declared count exceeds the data (`:345`): the block is kept with the bytes present, then `break`. Keep this
    behavior; the master already decodes whole objects from such a block.
- `ParseError` is defined twice: `dnp3.application.parser.ParseError(Exception)` (`:67`), which the parser raises, and
  `dnp3.core.exceptions.ParseError(DNP3Error)`, which nothing on this path raises. Catch the parser's.
- `src/dnp3/application/fragment.py` `ResponseFragment(header, objects)`. It is also built by the outstation, so a
  new field needs a default.
- `src/dnp3/master/master.py` `process_response` (`:490`) catches bare `Exception`. `master.py` has no logger yet;
  copy `logger = logging.getLogger(__name__)` from `tcp_runner.py`.
- `src/dnp3/master/handler.py` `ResponseInfo`, a non-frozen dataclass with defaults. The runner and every handler
  callback already receive it.
- Tests that pin today's absorb behavior and must change in step 2 (the names say what they assert):
  `tests/unit/application/test_parser.py::test_unknown_group_absorbs_remainder` (or its replacement from plan 011),
  `test_virtual_address_range_absorbs_remainder` and `test_size_prefix_absorbs_remainder`.

## Steps
1. Test: `tests/unit/application/test_parser.py::TestUnparsed`:
   - `test_complete_response_has_no_unparsed`: two known blocks give `parse_response(...).unparsed is None`;
   - `test_unknown_object_is_reported`: g1v2, then group 200 variation 1, then g30v1. `objects` holds only the g1v2
     block. `unparsed.reason is UnparsedReason.UNKNOWN_OBJECT`, `unparsed.header.group == 200`, `unparsed.offset`
     is the byte offset of the g200 header within the object data, and `unparsed.data` is every byte from that
     header to the end;
   - `test_reserved_qualifier_is_reported`: reason `RESERVED_QUALIFIER`, `header` None (it could not be decoded);
   - `test_truncated_block_is_kept_and_reported`: the block from `test_short_object_data_keeps_block_and_stops` is
     still returned, and `unparsed.reason is UnparsedReason.TRUNCATED` with that block's header.
   Implement:
   - in `fragment.py`, `class UnparsedReason(Enum)` with `UNKNOWN_OBJECT`, `RESERVED_QUALIFIER` and `TRUNCATED`,
     and `@dataclass(frozen=True, slots=True) class UnparsedData` with `offset: int`, `reason: UnparsedReason`,
     `header: ObjectHeader | None` and `data: bytes`;
   - `ResponseFragment.unparsed: UnparsedData | None = None`. Its docstring says `to_bytes()` does not re-emit it;
   - in `parser.py`, a private `_parse_response_blocks(data) -> tuple[list[ObjectBlock], UnparsedData | None]`
     holding today's loop. `parse_response_object_blocks` returns element 0, and `parse_response` sets both fields.
2. Test: rewrite the three absorb tests listed in Context as "reported, not returned". An unknown-width block is no
   longer in `objects`, and `unparsed` names it. Implement: stop appending the absorbing block at the unknown-width
   stop.
3. Test: `tests/unit/master/test_response_parsing.py`:
   - `test_unparsed_reaches_response_info`: g1v2, then g200v1. `on_binary_input` gets the value, and the
     `ResponseInfo` it receives has `unparsed.header.group == 200`;
   - `test_unparsed_is_logged` (pytest `caplog`, logger `dnp3.master.master`): one WARNING naming `g200v1` and the
     reason.
   Implement: `ResponseInfo.unparsed: UnparsedData | None = None` (document it in the class docstring). Fill it in
   `_process_response_fragment`, and log once when it is set.
4. Test: `tests/unit/master/test_master.py`:
   - `test_process_response_logs_parse_error`: `process_response(b"\xc0")` returns None and logs one WARNING whose
     message includes the `ParseError` text;
   - `test_process_response_propagates_bugs`: monkeypatch `dnp3.master.master.parse_response` to raise
     `RuntimeError`. It propagates.
   Implement: `except ParseError as exc:` (imported from `dnp3.application.parser`), then log and return None.
5. Test: Hypothesis `tests/unit/master/test_master.py::test_process_response_total`, with `st.binary(max_size=300)`.
   `process_response` returns a `ResponseInfo` or None and raises nothing. If it finds an input that raises
   something else, fix the parser to raise `ParseError` for it. Keep the failing example as an explicit
   `@example`.

## Review focus
- `unparsed.offset` is relative to the object data (after the 4-byte response header). Say so in the docstring,
  and make the test compute the expected offset from the bytes it built.
- A response whose first block is unknown yields `objects == ()` with `unparsed` set. `_process_response_fragment`
  must still update sequence state and return the `ResponseInfo`. Add this case to step 3.

## Out of scope
- Raising instead of returning partial data. A SCADA master must not discard good values because of one bad block.
- Changing the runner's "Discarding %d bytes" warning. The master's log now carries the reason.
- The unused `dnp3.core.exceptions.ParseError` (upstream #67).

## Done when
- [x] new tests pass
- [x] `uv run pytest tests/` passes, coverage >= 95%
- [x] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean

## Deviations
- A RESERVED_QUALIFIER report carries the object header. The header decodes; only its `range_code` and
  `prefix_code` properties can raise. Keeping it lets the warning name the group and variation. `header` is None
  only when fewer than 3 bytes remain, which is now reported as TRUNCATED instead of dropped silently.
- Reserved qualifiers are checked from the qualifier bits (range 0xA and 0xC-0xF, prefix 7) before the width
  lookup. Choosing the reason by whether `RangeCode` has a member reported 0xA as UNKNOWN_OBJECT. Range codes 3-5
  and 0xB are defined by the spec but have no width here, so they report UNKNOWN_OBJECT.
- A fourth reason, `INVALID_RANGE`: a start-stop range with stop below start has no extent, but the block was
  returned with every later block's bytes as its data. It is now reported and not returned. TRUNCATED blocks are
  still kept with the bytes present.
- `parse_response_object_blocks` is removed rather than kept as a wrapper. Nothing outside the tests called it,
  and it dropped the report. Tests read `parse_response(...).objects`.
- `_ALIASES` is removed from `master.py`. g11v3, g40v5 and g40v6 are not defined by the spec and have no parser
  width, so after step 2 they never reached the master decoder. They are reported as UNKNOWN_OBJECT and their
  `test_decoded_values_golden` entries are gone.
- The step 1 assertion that `objects` holds only the g1v2 block moved to step 2, since step 1 still kept the
  absorbing block. Step 2 also rewrote `test_packed_with_index_prefix_is_unsized`,
  `test_octet_string_variation_zero_is_unsized` and `test_unregistered_trailing_block_is_not_dropped` (now
  `_is_reported`), and renamed the 0x0B test to `test_variable_format_range_is_reported_not_returned`.
- The warning gives the offset out of the object data length rather than a count of bytes not parsed, since a
  truncated block's whole objects are still delivered.
- `test_process_response_total` also draws bodies behind a valid response header, so the object parser is
  reached. A 100,000-example structured run found no exception other than `ParseError`, so there was no parser fix
  and no `@example`.
- Added, from review: `UnparsedData` and `UnparsedReason` are exported from `dnp3.application`; a solicited
  response whose first block is unknown still completes its task; the known-blocks property test also checks
  `unparsed is None`.
- No follow-up plan. Found in review, older than this work and not planned: qualifier bit 7 is ignored, so 0x80
  parses as 0x00; `parse_response` accepts non-response function codes; `RangeCode` names 3-5 RESERVED and puts
  VIRTUAL_ADDRESS on 0xB, which the spec defines as variable format.

## Upstream sync (2026-09-28)
Mostly replaced by upstream #71 and #103. `ResponseFragment.truncation` and `ResponseInfo.truncation`
(`Truncation`, `TruncationReason`) say where framing stopped and why, and the master logs a warning. One behavior
differs from this plan: a block shorter than its declared count now delivers nothing, where this plan delivered its
whole objects. `UnparsedData`, `UnparsedReason` and the removal of `parse_response_object_blocks` are gone with it.

The #66 part was kept: `Master.process_response` returns None only for a `ParseError`, which it logs, and any other
exception propagates (`TestProcessResponse` in `tests/unit/master/test_master.py`). #66 and #67 are still open
upstream.
