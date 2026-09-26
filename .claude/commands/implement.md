---
description: Implement a plan from docs/plans/
argument-hint: <plan number or path, e.g. 003>
---

Implement the plan: $ARGUMENTS

If no plan is given, take the lowest-numbered plan in `docs/plans/` with `Status: todo` whose `Depends on` plans are all `done`. Read the whole plan before starting.

Plans stay out of the code history: the branch never touches `docs/plans/`, and no code, comment, test or commit message on it mentions a plan or plan number. Plan files change only in their own `docs:` commits on `main`.

1. Check prerequisites. If a `Depends on` plan isn't `done` (or merged), stop and say so unless the user says to proceed.
2. Branch from an up-to-date `main` using the plan's `Branch:` name.
3. Work the steps in order, TDD: write the failing test, run it and see it fail, implement, see it pass. Commit per step with conventional commit messages (`test:`, `feat:`, `fix:`, `refactor:`).
4. If the plan is wrong or incomplete, don't silently deviate: note the deviation and why, for the plan update below. If the work outgrows the plan, stop after a mergeable point and note the remainder for a new plan.
5. Run the full `Done when` checklist: `uv run pytest tests/ --cov=src/dnp3 --cov-fail-under=95`, `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/`. Fix failures; don't skip hooks.

Stop on the branch. Don't merge, push or open a PR unless the user explicitly asks. Finish with: branch name, commits made, check results, and any deviations from the plan, then ask whether to merge.

Only after the user says to merge: squash-merge the branch into `main` as one conventional commit that describes the change itself. Then, as a separate `docs:` commit on `main` that touches only `docs/plans/`, tick the checklist, set `Status: done`, record any deviations, and add any follow-up plan.
