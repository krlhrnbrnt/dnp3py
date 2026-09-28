# 015: One owner for master timeouts, no dead retry settings

Status: todo
Branch: refactor/master-retry-timeout-config
Depends on: none

## Upstream sync (2026-09-28)
Still todo; upstream #75 is still open. Line numbers in Context predate the sync. The runner's
`response_timeout` is still 10.0, `_deadline()` is unchanged, and `run_polls()` still retries after
`poll_retry_delay`.

## Goal
Every master configuration field either does what it says or is gone.
- `PollingConfig.retry_count` and `MasterConfig.task_retry_count` are removed. So is the retry path in the task
  state they appear to configure, which no production code reaches.
- `MasterConfig.response_timeout` becomes the runner's default response timeout. `PollingConfig.response_timeout`,
  a third timeout that nothing reads, is removed.

This is a breaking change to public dataclasses: squash-merge with a `BREAKING CHANGE:` footer. Upstream issue #75.

## Context
- `src/dnp3/master/config.py`:
  - `PollingConfig.response_timeout` (`:32`) and `retry_count` (`:33`) have no readers. `Master._setup_polling`
    reads only the four interval fields;
  - `MasterConfig.task_retry_count` (`:60`) is only range-checked (`:86`);
  - `MasterConfig.response_timeout` (`:58`) is validated but has no readers.
- `src/dnp3/master/tcp_runner.py` `MasterTcpRunner.response_timeout: float = 10.0` (`:129`) is the timeout actually
  used, through `_deadline()` (`:615`).
- `src/dnp3/master/state.py`:
  - `TaskInfo.retry_count`, `can_retry()` and `decrement_retry()` (`:84`, `:115-126`);
  - `MasterStateManager.create_task(..., retry_count=2, ...)` (`:173`);
  - `check_task_timeout()`, which retries or fails (`:235`).
  `create_task` and `start_task` have no production callers, so `check_task_timeout()` always returns False in
  practice. `Master.check_timeout()` (`master.py:694`) is its public wrapper.
- Retry policy that does exist: `run_polls()` retries a failed scheduled poll after `poll_retry_delay`, with no
  limit. That stays. Retrying a READ is harmless.
- Tests pinning the removed fields: `tests/unit/master/test_config.py` (`:28`, `:38`, `:46`, `:93`, `:110`, `:121`,
  `:166`, `:210`) and `tests/unit/master/test_state.py` (`:123-140`, `:210-234`).

## Steps
1. Test: `tests/unit/master/test_config.py`:
   - `test_removed_retry_fields_rejected`: `PollingConfig(retry_count=1)`, `PollingConfig(response_timeout=1.0)` and
     `MasterConfig(task_retry_count=1)` each raise `TypeError`;
   - delete or rewrite the assertions on those fields, including the hypothesis case at `:210`.
   Implement: delete the three fields, their docstring lines and the `task_retry_count` validation.
2. Test: `tests/unit/master/test_state.py`:
   - `test_expired_task_fails`: a started task past its timeout makes `check_task_timeout()` return True, sets the
     task FAILED and makes the manager IDLE;
   - delete the `can_retry` / `decrement_retry` tests and the `retry_count` assertions.
   Implement: delete `TaskInfo.retry_count`, `can_retry`, `decrement_retry` and the `create_task` parameter.
   `check_task_timeout` fails an expired task. Leave the rest of `TaskInfo` / `MasterStateManager` alone, since
   `Master` still calls `complete_current_task()`.
3. Test: `tests/unit/master/test_tcp_runner.py::TestTimeouts`:
   - `test_timeout_defaults_to_master_config`: `MasterConfig(response_timeout=0.2)`, and a runner built without
     `response_timeout` against a silent peer. `ResponseTimeoutError` is raised within 1 s (wrap in
     `asyncio.timeout(1)`);
   - `test_runner_timeout_overrides_config`: `MasterConfig(response_timeout=30)` and runner `response_timeout=0.2`.
     It raises within 1 s.
   Implement: `response_timeout: float | None = None` on the runner. `_deadline()` uses
   `self.master.config.response_timeout` when it is None. Update the field docstring.
   `make_runner` in the tests passes an explicit value, so existing tests are unaffected.
4. Docs: grep `docs/`, `README.md` and `src/` docstrings for `retry_count`, `task_retry_count` and
   `response_timeout`, and update every mention.

## Review focus
- The runner's effective default timeout drops from 10 s to `MasterConfig`'s 5 s. Call this out in the commit body,
  and check that no integration test relied on 10 s against a slow in-repo outstation.
- A Python snippet in `README.md` or `docs/*.md` that passes a removed keyword would now fail for anyone who copies
  it. The grep in step 4 must cover Markdown, not just `.py` files.

## Out of scope
- A bounded retry count for scheduled polls. Add one only when a real need shows up.
- `MasterConfig.confirm_timeout` and `enable_unsolicited`, also unread. They are left for a separate decision,
  because each may become live (outstation confirm handling, unsolicited gating).
- Deleting the rest of the task-state machinery.

## Done when
- [ ] new tests pass
- [ ] `uv run pytest tests/` passes, coverage >= 95%
- [ ] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean
