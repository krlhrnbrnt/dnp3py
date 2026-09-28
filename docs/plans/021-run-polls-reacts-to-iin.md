# 021: run_polls() reacts to outstation IIN bits

Status: todo
Branch: feat/run-polls-iin-reactions
Depends on: 014 (`time_sync()`, `time_sync_method`), 020 (`clear_restart()`, `enable_unsolicited()`,
`RequestRejectedError`)

## Upstream sync (2026-09-28)
Still todo. The fork kept `run_polls()`, which upstream removed. Read `ConnectionLostError` as
`LinkError`: a `LinkError` ends the loop, and any other `MasterRunnerError` is retried after `poll_retry_delay`. It
depends on plan 020, which now has an open API decision. Line numbers in Context predate the sync.

## Goal
While `run_polls()` drives the master, it reacts to IIN bits as opendnp3 does:
- DEVICE_RESTART: clear the bit, then run an integrity poll, then re-enable unsolicited reporting;
- EVENT_BUFFER_OVERFLOW: run an integrity poll, because events were lost;
- NEED_TIME: sync the outstation's clock. This one is opt-in.

Explicit calls (`request()`, `integrity_poll()`, `listen_unsolicited()` and the others) never send extra requests,
so a script driving the outstation step by step keeps full control. Defaults match opendnp3.

## Context
- `src/dnp3/master/tcp_runner.py` `run_polls()` (`:435`): it takes the next due task from the scheduler, or idles
  listening for unsolicited responses. A failed poll is logged and retried after `poll_retry_delay`, and
  `ConnectionLostError` ends the loop.
- `src/dnp3/master/master.py` `_process_response_fragment` (`:595`) sees every solicited and unsolicited response:
  the runner routes both through `process_response`.
- Reused config (`src/dnp3/master/config.py`): `startup_integrity_poll` (`:77`), `enable_unsolicited_on_startup`
  (`:79`), and `time_sync_method` (from plan 014).
- opendnp3 reference:
  - `MContext::ProcessIIN` runs on every response, unsolicited included;
  - a restart demands clear restart, the startup integrity poll and enable unsolicited;
  - overflow demands an integrity poll (`integrityOnEventOverflowIIN`, default true);
  - NEED_TIME demands a time sync only when a sync mode is configured (default none);
  - task priority order: clear restart, integrity, time sync, enable unsolicited, then user polls.
- The in-repo outstation starts with DEVICE_RESTART and NEED_TIME set (`src/dnp3/outstation/state.py:216`).
- Test helpers in `tests/unit/master/test_tcp_runner.py`: `FakeOutstation`, `make_runner`, `idle_scheduler` and
  `TestScheduledPolls` (`:891`), plus `open_runner` (`:1419`) and `answer_requests` (`:1383`), which answers each
  request in turn by function code, and records them.

## Steps
1. Test: `tests/unit/master/test_config.py::test_iin_reaction_defaults`: `react_to_restart` is True,
   `integrity_on_event_overflow` is True, and `time_sync_on_need_time` is False.
   Implement: those three `MasterConfig` fields, with docstring lines.
2. Test: `tests/unit/master/test_master.py::TestIINActions`, feeding responses to `process_response`:
   - `test_restart_demands_clear_integrity_enable`: DEVICE_RESTART gives `master.pending_actions ==
     IINAction.CLEAR_RESTART | IINAction.INTEGRITY_POLL | IINAction.ENABLE_UNSOLICITED`;
   - `test_restart_respects_startup_flags`: with `startup_integrity_poll=False` and
     `enable_unsolicited_on_startup=False`, only CLEAR_RESTART;
   - `test_restart_ignored_when_disabled`: `react_to_restart=False` gives no actions;
   - `test_overflow_demands_integrity`, and no action with its flag off;
   - `test_need_time_demands_sync_only_when_enabled`;
   - `test_unsolicited_response_demands_too`;
   - `test_complete_action_clears_only_that_action`.
   Implement:
   - `IINAction(Flag)` in `master.py` with `CLEAR_RESTART`, `INTEGRITY_POLL`, `TIME_SYNC` and `ENABLE_UNSOLICITED`;
   - `Master.pending_actions` (read-only) and `Master.complete_action(action)`;
   - record demands in `_process_response_fragment`;
   - export `IINAction` from `dnp3.master`.
3. Test: `tests/unit/master/test_tcp_runner.py::TestIINReactions`. The fake answers each request in turn and
   records function codes:
   - `test_restart_runs_actions_in_priority_order`: a scheduled class poll's answer carries DEVICE_RESTART. The next
     requests are WRITE (g80v1), READ with g60v1 (integrity), then ENABLE_UNSOLICITED, and scheduled polls resume
     after that;
   - `test_overflow_runs_integrity_poll`;
   - `test_need_time_runs_time_sync_when_enabled`: with LAN, RECORD_CURRENT_TIME then WRITE is sent;
   - `test_explicit_request_does_not_react`: `runner.integrity_poll()` answered with DEVICE_RESTART. Nothing more is
     sent within 0.2 s, and `master.pending_actions` is still set;
   - `test_timed_out_action_retried`: the fake ignores the first clear restart. It is sent again after
     `poll_retry_delay`;
   - `test_rejected_action_not_repeated`: the clear restart answer keeps DEVICE_RESTART. One warning is logged, and
     no second WRITE is sent although later answers still carry the bit. After `open()`, the reaction works again.
   Implement in `run_polls()`:
   - before taking a scheduled task, run the highest-priority pending action not in `_disabled_actions`, through
     `clear_restart()`, `integrity_poll()`, `time_sync()` or `enable_unsolicited()`;
   - on success, `master.complete_action(action)`;
   - on `RequestRejectedError` or `TimeSyncError`: log, complete the action, and add it to `_disabled_actions`;
   - on any other `MasterRunnerError`: keep it pending and wait `poll_retry_delay`, as for polls.
     `ConnectionLostError` still propagates;
   - reset `_disabled_actions` in `open()`;
   - the `run_polls()` docstring lists the reactions and says explicit calls do not react.
4. Test, end to end: `tests/integration/test_tcp_master_runner_e2e.py::test_run_polls_clears_restart`: an in-repo
   outstation (starts with DEVICE_RESTART), one scheduled integrity poll, and `run_polls(stop=...)` stopped once the
   outstation's `iin` no longer has DEVICE_RESTART, with a 2 s bound.

## Review focus
- `complete_action` runs after the action returns, so a demand raised by the action's own response is dropped. That
  prevents a loop when an overflow bit is still set in the integrity poll's answer. The next response carrying the
  bit demands it again.
- Existing `run_polls()` tests answer with IIN 0 and should be unaffected. Check every integration test that runs
  `run_polls()` against the in-repo outstation: it will now also see the restart reactions.

## Out of scope
- Running `startup()` automatically on connect, and reconnecting after a lost connection.
- Event scans triggered by the class 1-3 event IIN bits (off by default in opendnp3).
- Exponential retry backoff.

## Done when
- [ ] new tests pass
- [ ] `uv run pytest tests/` passes, coverage >= 95%
- [ ] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean
