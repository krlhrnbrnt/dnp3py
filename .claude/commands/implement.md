---
description: Implement a plan from docs/plans/
argument-hint: <plan number or path, e.g. 003>
---

Implement the plan: $ARGUMENTS

If no plan is given, take the lowest-numbered plan in `docs/plans/` with `Status: todo` whose `Depends on` plans are all `done`. Read the whole plan before starting.

1. Check prerequisites. If a `Depends on` plan isn't `done` (or merged), stop and say so unless the user says to proceed.
2. Branch from an up-to-date `main` using the plan's `Branch:` name. Set `Status: in progress` in the plan.
3. Work the steps in order, TDD: write the failing test, run it and see it fail, implement, see it pass. Commit per step with conventional commit messages (`test:`, `feat:`, `fix:`, `refactor:`).
4. If the plan is wrong or incomplete, fix the plan file in the same branch and note why, rather than silently deviating. If the work outgrows the plan, stop after a mergeable point and write the remainder as a new plan in `docs/plans/`.
5. Run the full `Done when` checklist: `uv run pytest tests/ --cov=src/dnp3 --cov-fail-under=95`, `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/`. Fix failures; don't skip hooks.
6. Tick the checklist, set `Status: done`, commit.

Don't push or open a PR unless asked. When the plan's branch is merged, squash-merge it into `main` as one conventional commit named after the plan (e.g. `feat: 003 add group 43 analog output events`). Finish with: branch name, commits made, check results, and any deviations from the plan.
