# 025: The master delivers octet strings (g110, g111)

Status: done
Branch: feat/master-octet-string-read
Depends on: none

## Goal
A master handler receives octet string values and events from the outstation as `OctetStringValue(index, value:
bytes)`, through an opt-in `OctetStringHandler.on_octet_string(values, info)`. `DefaultSOEHandler` stores them. Today
the master steps over g110/g111 blocks and drops their contents.

## Context
- IEEE 1815-2012 Annex A, g110 (octet string) and g111 (octet string event): the variation is the string length in
  octets, 1 to 255. Variation 0 only appears in a READ, meaning any length. Neither group carries flags or a
  timestamp. Static responses use a start-stop range (qualifier 0x00/0x01); events use a count with an index prefix
  (0x17/0x28).
- `src/dnp3/application/parser.py`: `_OCTET_STRING_GROUPS` (`:55`) and `_lookup_object_size` (`:301`) already size
  g110/g111 blocks by variation. A variation 0 block has no width, so the parser reports it in
  `ResponseFragment.unparsed` and does not delimit it.
- `src/dnp3/master/master.py`:
  - group constants (`:56-70`);
  - `_decode_object_layout(qualifier, data)` (`:140`) and `_iter_object_slots(layout, data, object_width)` (`:183`)
    handle both range and count+prefix layouts, and stop at the end of the data;
  - `_parse_response_objects` (`:631`) collects per-type lists and calls the handler once per type. The double-bit
    gate `isinstance(self.handler, DoubleBitHandler)` (`:669`) is the pattern to copy;
  - `_parse_double_bit_values` (`:703`) is the method-style helper to mirror.
- `src/dnp3/master/handler.py`: `DoubleBitValue` (`:34`), `DoubleBitHandler` (`:201`), `DefaultSOEHandler` (`:243`,
  its `clear()` at `:371`). `SOEHandler` must not change:
  `tests/unit/master/test_handler.py::test_custom_implementation` (`:302`) pins its six methods.
- `src/dnp3/master/__init__.py` exports the handler types.
- opendnp3 reference: `ISOEHandler::Process(HeaderInfo, ICollection<Indexed<OctetString>>)` takes g110 and g111 alike.
- Tests to copy: `tests/unit/master/test_handler.py::TestDoubleBitHandlerProtocol` (`:333`);
  `tests/unit/master/test_response_parsing.py` `RESPONSE_HEADER`, `CollectingHandler` (`:39`),
  `DoubleBitCollectingHandler` (`:524`), `TestDoubleBitInputs` (`:535`).

## Steps
1. Test: `tests/unit/master/test_handler.py::TestOctetStringValue`: `test_creation` (index and bytes kept),
   `test_is_frozen`, `test_fields_are_index_and_value` (`dataclasses.fields` names are exactly `index`, `value`).
   Implement: `@dataclass(frozen=True) class OctetStringValue` in `handler.py`. Its docstring says there is no
   quality or timestamp because g110/g111 carry neither (Annex A).
2. Test: `TestOctetStringHandlerProtocol`: `DefaultSOEHandler()` is an `OctetStringHandler`; a class with only
   `on_binary_input` is not; `OctetStringHandler` and `OctetStringValue` are in `dnp3.master.__all__`. In
   `TestDefaultSOEHandler`: `test_on_octet_string` stores by index, `get_octet_string` returns the value or None,
   `octet_strings` returns a copy, `clear()` empties it, and `last_response` is the `info` passed.
   Implement: `@runtime_checkable class OctetStringHandler(Protocol)` with `on_octet_string(values, info)`, docstring
   worded like `DoubleBitHandler`'s; `_octet_strings`, `octet_strings`, `on_octet_string`, `get_octet_string` and the
   `clear()` line on `DefaultSOEHandler`; exports.
3. Test: `tests/unit/master/test_response_parsing.py::TestOctetStrings`, with `OctetStringCollectingHandler
   (CollectingHandler)` recording `(index, value)` pairs in call order:
   - `test_g110_start_stop_delivers_each_string`: g110v5, qualifier 0x00, start 2 stop 3, `b"hello" + b"world"`
     gives `[(2, b"hello"), (3, b"world")]`;
   - `test_g110_uint16_start_stop`: qualifier 0x01, start 300 stop 300;
   - `test_g111_uint8_count_uint8_index`: g111v3, qualifier 0x17, count 2, indices 7 then 1. Wire order is kept;
   - `test_g111_uint16_count_uint16_index`: qualifier 0x28;
   - `test_g110_then_g30_both_delivered`: the strings and the analog value both arrive;
   - `test_truncated_block_delivers_whole_strings`: g110v4, start 0 stop 2, 9 data octets. Indices 0 and 1 arrive,
     and `info.unparsed.reason` is `UnparsedReason.TRUNCATED`;
   - `test_hostile_count_reads_nothing_past_block`: g111v255, qualifier 0x28, count 0xFFFF, one indexed string
     present. Exactly that string arrives, no exception;
   - `test_variation_zero_delivers_nothing`: g110v0 in a response gives no `on_octet_string` call and a non-None
     `info.unparsed`;
   - `test_size_prefix_yields_no_values`: `master._parse_octet_string_values` on a qualifier 0x47 block returns `[]`;
   - `test_handler_without_octet_string_callback_is_skipped`: `CollectingHandler`, g110v3 then g1v2. The binary
     input arrives, no error.
   Implement in `master.py`: `GROUP_OCTET_STRING = 110`, `GROUP_OCTET_STRING_EVENT = 111`;
   `_parse_octet_string_values(block)`: width is `block.header.variation`, `[]` when 0 or when
   `_decode_object_layout` returns None, else one `OctetStringValue(index, bytes(...))` per `_iter_object_slots` slot;
   in `_parse_response_objects`, an `octet_strings` list and the `isinstance(self.handler, OctetStringHandler)` gate.
4. Test: Hypothesis, in `TestOctetStrings`:
   - `test_event_block_round_trips`: a length 1..255 and 1..10 `(index 0..65535, bytes of that length)` pairs, built
     as a g111 block with qualifier 0x28. The handler receives exactly those pairs, in order;
   - `test_static_block_round_trips`: a start index 0..65000 and 1..10 strings of one length, qualifier 0x01. The
     handler receives consecutive indices from start with those strings.
5. Docs: in `README.md`, the Object Groups row `| 110, 111 | Octet String (static, event; master decoding only) |`,
   and a `### Octet strings (master)` subsection after `### Master (Client)`: a `DefaultSOEHandler`, a
   `MasterTcpRunner(master=..., host=..., port=...)` used as `async with`, `runner.request(master.build_range_poll(
   group=110, variation=0, start=0, stop=3))`, then `handler.get_octet_string(2)` and `.value.decode("ascii")`, with
   one sentence saying the bytes carry no encoding.

## Review focus
- The string width comes from the variation (at most 255) and the count from the wire. `_iter_object_slots` must be
  what bounds the read, not the count.
- A static value and an event for the same index in one fragment are both delivered, in wire order.

## Out of scope
- Writing octet strings (plan 026).
- The outstation side: serving g110/g111 or accepting their writes.
- Decoding the bytes as text.
- Header info per value and per-block delivery (plan 017).

## Done when
- [x] new tests pass
- [x] `tests/unit/master/test_handler.py::test_custom_implementation` passes unchanged
- [x] `uv run pytest tests/` passes, coverage >= 95%
- [x] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean

## Deviations
- Tests dropped from the steps: `test_is_frozen` (it tests `@dataclass(frozen=True)` itself),
  `test_g110_uint16_start_stop` and `test_g111_uint16_count_uint16_index` (the Hypothesis round trips build the
  same 0x01 and 0x28 layouts), and `test_variation_zero_delivers_nothing` (it passed before any octet string code
  existed, because the parser rejects variation 0 first).
- Tests added beyond the steps: `test_variation_zero_block_yields_no_values` calls `_parse_octet_string_values`
  directly. Without the `width == 0` guard, a zero width never exhausts the data, so a uint32 range would yield up to
  2^32 empty strings. `test_bytearray_data_yields_bytes` pins the `bytes(...)` copy, since `ObjectBlock` is public and
  its data can be a `bytearray`. `test_static_and_event_for_same_index_both_delivered_in_wire_order` pins the second
  review focus point.
