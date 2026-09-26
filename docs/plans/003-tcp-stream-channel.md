# 003: One stream implementation for TCP client and server channels

Status: todo
Branch: refactor/tcp-stream-channel
Depends on: none

## Goal
`TcpClientChannel` and `TcpServerChannel` share a single read/write/close implementation, so a fix to one applies to
both. Their constructors, fields, dataclass behavior and exceptions are unchanged.

## Context
- `src/dnp3/transport_io/tcp_client.py` `TcpClientChannel` and `src/dnp3/transport_io/tcp_server.py`
  `TcpServerChannel` repeat each other line for line:
  - `state`, `is_open`, `statistics`, `local_address` and `remote_address`;
  - `close`, `read`, `write`, `read_exactly` and `write_all`;
  - the socket-option code in `_configure_socket`. `TcpServer._configure_socket` is also a copy.
- Both classes are public dataclasses and must keep their current fields and dataclass `__eq__`/`__repr__`:
  - `TcpServerChannel(reader, writer, config)` has public `reader` and `writer` fields, used in
    `tests/unit/test_coverage_gaps.py:711+`. Its initial state is `OPEN` with `connect_count == 1`.
  - `TcpClientChannel(config)` keeps private `_reader` and `_writer`.
  - `TcpServer` uses `channel in self._connections`, which relies on dataclass equality. Do not change it.
- The only difference in I/O is where the streams come from and the "not open" check:
  - the client also checks that `_reader`/`_writer` are not `None`;
  - the server checks state only.
- `connect()` (`tcp_client.py:318-333`) and `serve()` (`tcp_server.py:447-465`) copy every config field by hand into
  `TcpConfig` or `TcpServerConfig`. `TcpServer._handle_connection` deliberately copies a subset of fields; leave it
  as is so channel config values don't change.
- `SimulatorChannel.open` and `SimulatorServer.start` set `_state = OPENING` and then immediately `_state = OPEN` in
  the same synchronous step, so nothing can observe the first assignment.

## Steps
1. Test: `tests/unit/transport_io/test_tcp_server.py::test_channels_share_stream_io` asserts that `TcpServerChannel`
   and `TcpClientChannel` both subclass `StreamIO` and that neither class's `__dict__` defines `read`, `write`,
   `read_exactly`, `write_all` or `close`. It fails.
2. Implement: create a plain mixin, not a dataclass, `class StreamIO` in `tcp_client.py`. It has the single copy of
   the properties, `close`, `read`, `write`, `read_exactly` and `write_all`, written against an abstract hook
   `_streams() -> tuple[StreamReader, StreamWriter]` that raises `ChannelClosedError("Channel is not open")`.
   - The client's hook returns `_reader`/`_writer`.
   - The server's hook returns `reader`/`writer`.

   Both dataclasses keep their fields and add `StreamIO` as a base. Error messages, statistics updates and the
   `write_all` short-write check stay exactly as they are.
3. Implement: move socket options into a module-level `configure_socket(sock, config)` in `tcp_client.py`.
   `TcpClientChannel` and `TcpServer` both call it.
4. Test: `test_connect_copies_config` and `test_serve_copies_config` assert that the returned channel/server config
   has the exact type (`TcpConfig` or `TcpServerConfig`), the new host and port, and every other field copied.
   Implement: private `_copy_config(cls, config, **overrides)` builds
   `cls(**{f.name: getattr(config, f.name) for f in dataclasses.fields(cls)} | overrides)`. It replaces the manual
   copies in `connect()` and `serve()`.
5. Implement: delete the `OPENING` assignments in the simulator that are immediately overwritten.
6. Test: all existing `tests/unit/transport_io/*`, `tests/unit/test_coverage_gaps.py` and
   `tests/integration/test_tcp_*.py` pass unchanged.

## Out of scope
- Removing or renaming any config field or protocol, including the unused `read_buffer_size` and `write_buffer_size`,
  because removal would be breaking.
- `tcp_runner.py` typing (plan 005).

## Done when
- [ ] new tests pass
- [ ] `uv run pytest tests/` passes, coverage >= 95%
- [ ] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean
