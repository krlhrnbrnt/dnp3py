# 017: SOE values say which object header they came from

Status: todo
Branch: feat/soe-header-info
Depends on: 012 (its double-bit dispatch is restructured here), 025 (likewise its octet string dispatch)

## Goal
A handler can tell an event from a current value, and receives values in the order the outstation sent them. Every
value carries `header: HeaderInfo` (group, variation, qualifier, position, event or static, flags sent or assumed).
Handler methods are called once per object block, in wire order, instead of once per point type per fragment. An
opt-in `FragmentHandler` gets begin and end calls around each fragment. `SOEHandler` signatures do not change, so
existing handlers keep working.

## Context
- `src/dnp3/master/master.py`:
  - `_parse_response_objects` (`:534`) collects values into six per-type lists for the whole fragment, then calls
    one handler method per non-empty list. The order across types is lost, and so is whether a value came from a
    static block (g1) or an event block (g2).
  - `_decode_block(block, field)` (`:217`) resolves the object class through `registry.lookup` and `_ALIASES`, and
    returns `(index, object)` pairs. It has the class in hand.
  - `_parse_packed_binary` (`:240`) reports g1v1 / g10v1 points with quality ONLINE: those flags are assumed, not
    sent.
  - Plan 012 adds `_parse_double_bit_values` and an `isinstance(self.handler, DoubleBitHandler)` gate.
  - Plan 025 adds `_parse_octet_string_values` and an `isinstance(self.handler, OctetStringHandler)` gate. g110/g111
    have no registry class: the variation is the string length, and neither group carries flags (IEEE 1815-2012
    Annex A).
- `src/dnp3/objects/base.py`: `StaticObject` (`:188`) and `EventObject` (`:196`) mark every registered class.
  `FixedSizeObject._FIELD_NAMES` lists the wire fields. Flag-less variations such as g20v5 and g30v3 expose `quality`
  as a property, not a field (`counter.py:116`, `analog_input.py:112`), so `"quality" in cls._FIELD_NAMES` says
  whether flags were on the wire.
- `src/dnp3/master/handler.py`: `BinaryValue`, `AnalogValue`, `CounterValue` (`:16`, `:33`, `:50`) are frozen
  dataclasses whose fields after `value` all have defaults. `SOEHandler` (`:112`) is a `runtime_checkable`
  Protocol, and `tests/unit/master/test_handler.py::test_custom_implementation` pins its six methods. New callbacks
  therefore go on a separate Protocol, as plan 012 does for `DoubleBitHandler`.
- opendnp3 reference (`ISOEHandler`, `HeaderInfo`, `MeasurementHandler`): `Process(HeaderInfo, values)` is called
  once per object header, with `gv`, `qualifier`, `isEventVariation`, `flagsValid` and `headerIndex`.
  `BeginFragment` / `EndFragment` wrap a fragment's values and are called only when it has at least one.
- `tests/unit/master/test_response_parsing.py` `CollectingHandler` (`:37`). No test pins one call per type per
  fragment.

## Steps
1. Test: `tests/unit/master/test_handler.py::test_value_header_defaults_to_none`: `BinaryValue(index=0,
   value=True).header is None`, likewise for the other value classes, and a `HeaderInfo` is frozen.
   Implement in `handler.py`:
   - `@dataclass(frozen=True, slots=True) class HeaderInfo` with `group: int`, `variation: int`, `qualifier: int`,
     `header_index: int` (0-based position of the block among the fragment's parsed blocks), `is_event: bool`, and
     `flags_valid: bool`;
   - `header: HeaderInfo | None = None` as the last field of `BinaryValue`, `AnalogValue`, `CounterValue`,
     `DoubleBitValue` and `OctetStringValue`;
   - export `HeaderInfo` from `dnp3.master`.
2. Test: `tests/unit/master/test_response_parsing.py::TestHeaderInfo`:
   - `test_event_and_static_blocks_marked`: g2v1 (qualifier 0x17, index 3), then g1v2 (0x00, indices 0-1). The
     event value has `header.is_event`, group 2, variation 1, qualifier 0x17 and `header_index` 0. The static values
     have `is_event` False and `header_index` 1;
   - `test_packed_binary_flags_not_valid`: g1v1 gives `flags_valid` False, g1v2 gives True;
   - `test_flagless_counter_flags_not_valid`: g20v5 gives False, g20v1 gives True;
   - `test_header_index_counts_undelivered_blocks`: a g41v2 block, then g1v2. The g1 values have `header_index` 1;
   - `test_octet_string_blocks_marked`: g111v3 (0x17) gives `is_event` True, g110v3 (0x00) gives False, and both
     give `flags_valid` False.
   Implement:
   - a private `_header_info(block, position, cls)` in `master.py`, with `is_event = issubclass(cls, EventObject)`
     and `flags_valid = "quality" in cls._FIELD_NAMES`. For octet string blocks `cls` is None, and then
     `is_event = group == 111` and `flags_valid` is False;
   - `_decode_block` also returns the resolved class;
   - each `_parse_*_values` helper takes the header and sets it on every value it builds. The packed paths set
     `is_event` False and `flags_valid` False.
3. Test: `TestDeliveryOrder`:
   - `test_one_call_per_block_in_wire_order`: a handler that appends `(method name, [indices])` per call. A response
     with g32v1 (index 5), g2v1 (index 1), then g30v1 (indices 0-1) gives exactly `on_analog_input [5]`,
     `on_binary_input [1]`, `on_analog_input [0, 1]`, in that order;
   - `test_default_handler_keeps_static_value`: `DefaultSOEHandler` fed an event and then the static value for the
     same index ends with the static value.
   Implement: `_parse_response_objects` dispatches each block as soon as it is decoded, instead of batching per type.
   Keep the double-bit and octet string gates.
4. Test: `TestFragmentHandler`:
   - `test_begin_and_end_wrap_value_calls`: a handler implementing both Protocols records `begin`, the value calls,
     then `end`, each with the fragment's `ResponseInfo`;
   - `test_no_begin_or_end_without_values`: a null response gives no calls;
   - `test_end_called_when_handler_raises`: a value callback raises. `on_fragment_end` still runs, and the exception
     propagates out of `process_response`;
   - `test_plain_soe_handler_unaffected`: a handler with only the six methods gets its values and no error.
   Implement:
   - `@runtime_checkable class FragmentHandler(Protocol)` with `on_fragment_begin(info)` and
     `on_fragment_end(info)`. Its docstring says it is separate so existing `SOEHandler`s stay valid;
   - `_parse_response_objects` calls begin before the first value call and end, in a `finally`, after the last. Only
     when there is at least one value call and `isinstance(self.handler, FragmentHandler)`;
   - export `FragmentHandler`.

## Review focus
- `header_index` counts parsed blocks, including blocks the master does not deliver (g52, g41 echoes). Document it
  that way in the `HeaderInfo` docstring.
- `_ALIASES` entries resolve to a class from another group (g11v3 to g2v3). The header must report the block's own
  group and variation, not the alias target's.

## Out of scope
- Timestamps and timestamp quality (plan 018).
- Delivering command events (g13/g43) or time values (g50).
- Changing any `SOEHandler` method signature.

## Done when
- [ ] new tests pass
- [ ] `tests/unit/master/test_handler.py::test_custom_implementation` passes unchanged
- [ ] `uv run pytest tests/` passes, coverage >= 95%
- [ ] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean
