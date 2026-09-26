# 005: Single link-layer dispatch in the outstation TCP runner

Status: done
Branch: refactor/tcp-runner-link-dispatch
Depends on: none (merge after 003 to type against the final channel classes)

## Goal
`OutstationTcpRunner` handles link-layer frames in one place and is type-checked against the `Channel` protocol. Its
`type: ignore` comments, its mypy override and its ruff per-file ignores are removed. Behavior is unchanged.

## Context
- `src/dnp3/outstation/tcp_runner.py`:
  - `_handle_connection` and `_wait_for_confirm` both contain the same `LinkFunctionCode` ladder: reset, request
    link status and test link each send an ACK or status; confirmed user data sends an ACK and falls through;
    unconfirmed user data falls through; anything else is skipped.
  - `channel` is typed `TcpServerChannel | object`, with 12 `# type: ignore[union-attr]` comments.
  - Magic literal `0x20` for the CON bit; `func_code == 0x00` for CONFIRM (use `FunctionCode.CONFIRM`).
- `src/dnp3/transport_io/channel.py` `Channel` Protocol. It is already satisfied by `TcpServerChannel` and
  `SimulatorChannel`.
- `pyproject.toml`:
  - `[[tool.mypy.overrides]] module = "dnp3.outstation.tcp_runner"` disables `type-arg`, `assignment` and
    `arg-type`;
  - ruff per-file ignores `SIM105`, `PLR0915` and `PLR2004` apply to this file (DNP-016).

## Steps
1. Test: `tests/unit/outstation/test_tcp_runner.py::test_link_frames_answered_while_awaiting_confirm`. During a
   multi-fragment response, send RESET_LINK_STATE and TEST_LINK_STATE before the CONFIRM, and assert that each gets
   an ACK and the next fragment follows. This pins both code paths. It should pass on `main`; if it doesn't, fix the
   divergence first.
2. Implement: add `async def _answer_link_frame(channel, frame, master, outstation) -> bool`. It returns `True` when
   the frame carries user data to process, and `False` when the frame was fully handled or should be skipped. Both
   loops call it.
   - Deviation: it is a module-level function rather than a method, since it uses no runner state.
3. Implement:
   - Type `channel: Channel`.
   - Type `_connection_task: asyncio.Task[None] | None`.
   - Replace `0x20` with `ApplicationControl`'s CON bit and `0x00` with `FunctionCode.CONFIRM`.
   - Replace the `try/except: pass` teardowns with `contextlib.suppress`.
   - Delete every `type: ignore`.
4. Implement: delete the mypy override and the ruff per-file ignores for `tcp_runner.py` in `pyproject.toml`. If
   `PLR0915` still fires, split `_handle_connection` at the "complete fragment → send responses" boundary into
   `_send_responses`.
   - `PLR0915` did fire, so `_send_responses` was split out. `_wait_for_confirm` also lost its `reassembler`
     parameter: it always built its own, so the connection reassembler was passed through unused.
5. Test: the existing `tests/unit/outstation/test_tcp_runner.py`, `tests/integration/test_tcp_outstation_e2e.py` and
   `test_mesa_tcp_e2e.py` pass.
   - Added: tests for the reply and user-data decision of every primary link function code, including an
     unsupported one, and a CONFIRM carried in confirmed user data while a confirm is awaited.

## Out of scope
- Any change to confirm timeout or sequence semantics.
- `_handle_connection` keeps its signature (tests call it with a `SimulatorChannel`).

## Done when
- [x] new tests pass
- [x] `uv run pytest tests/` passes, coverage >= 95%
- [x] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean, with the
      tcp_runner override and ignores removed
