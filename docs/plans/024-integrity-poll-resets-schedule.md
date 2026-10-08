# 024: An integrity poll resets the scheduled one

Branch: fix/integrity-poll-resets-schedule
Depends on: none

## Upstream sync (2026-09-28)
Still todo. The fork kept `run_polls()`, and `integrity_poll()` still goes through `request()`. Line
numbers in Context predate the sync.

## Goal
After `startup()`, or any `runner.integrity_poll()`, `run_polls()` waits a full `integrity_poll_interval` before its
next scheduled integrity poll, instead of repeating it at once. A scheduled task that has never run is due at once,
however long the host has been up.

## Context
- `src/dnp3/master/polling.py`:
  - `PollTask.is_due()` (`:64`): an interval task is due when `time.monotonic() - last_poll_time >= interval`, and
    `last_poll_time = 0.0` stands for "never run". `time.monotonic()` has an arbitrary origin (boot time on Windows,
    Linux and macOS), so on a host up for less than `interval` a task that has never run is not due. With the
    default 3600 s, the first scheduled integrity poll can wait up to an hour;
  - `PollScheduler.get_time_until_next()` (`:227`) does the same arithmetic;
  - `mark_executed()` (`:76`). `PollScheduler` (`:173`) exposes tasks only through `get_due_tasks()` and
    `get_next_task()`.
- `src/dnp3/master/master.py` `_setup_polling()` (`:346`) schedules
  `IntegrityPollTask(interval=polling.integrity_poll_interval)`, 3600 s by default.
- `src/dnp3/master/tcp_runner.py`:
  - `startup()` (`:210`) calls `integrity_poll()`, which runs `request(master.build_integrity_poll())` and does
    not touch the scheduler;
  - `poll(task)` marks only its own task executed;
  - `run_polls()` (`:435`) takes `scheduler.get_next_task()`.
- Tests: `tests/unit/master/test_polling.py` patches `time.monotonic` (e.g. `test_is_due_periodic`).
  `tests/unit/master/test_tcp_runner.py` has `TestScheduledPolls`, `TestStartup`, `open_runner`, `answer_requests`
  and `assert_nothing_sent`.

## Steps
1. Test: `tests/unit/master/test_polling.py::TestIntegrityPollTask::test_never_run_task_is_due_on_a_fresh_host`:
   with `time.monotonic` patched to 10.0, `IntegrityPollTask(interval=3600.0).is_due()` is True, and a scheduler
   holding only that task gives `get_time_until_next() == 0.0`.
   Implement: in `is_due()` and `get_time_until_next()`, a task whose `last_poll_time` is 0.0 has never run and is
   due at once.
2. Test: `TestPollScheduler::test_mark_executed_by_type`: an integrity task and a class 1 task, neither run yet.
   `scheduler.mark_executed(PollType.INTEGRITY)` leaves only the class 1 task due.
   Implement: `PollScheduler.mark_executed(poll_type: PollType) -> None`.
3. Test: `tests/unit/master/test_tcp_runner.py`:
   - `TestScheduledPolls::test_integrity_poll_resets_scheduled_integrity`: the scheduler holds an
     `IntegrityPollTask(interval=3600.0)` that has never run. After an answered `runner.integrity_poll()`,
     `scheduler.get_next_task()` is None;
   - `TestScheduledPolls::test_failed_integrity_poll_keeps_schedule`: an unanswered `integrity_poll()` raises
     `ResponseTimeoutError`, and the task is still due;
   - `TestStartup::test_run_polls_after_startup_does_not_repeat_integrity`: default config. After `startup()`,
     `run_polls()` runs for 0.2 s and sends nothing.
   Implement: `integrity_poll()` calls `self.master.scheduler.mark_executed(PollType.INTEGRITY)` once `request()`
   returns.

## Review focus
- Existing `run_polls()` tests that keep the default scheduler depended on the host's uptime for the first integrity
  poll to be due. After step 1 it always is. Check that each test still means what it says.
- An integrity poll that raises must not reset the schedule, so a failed startup poll is retried by `run_polls()`.

## Out of scope
- Class polls resetting scheduled class polls, and a hand-built integrity READ sent with `request()`.
- Making `last_poll_time` optional instead of using 0.0 for "never run".
- IIN-driven integrity polls (plan 021). They go through `integrity_poll()`, so they reset the schedule too.

## Done when
- [ ] new tests pass
- [ ] `uv run pytest tests/` passes, coverage >= 95%
- [ ] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean
