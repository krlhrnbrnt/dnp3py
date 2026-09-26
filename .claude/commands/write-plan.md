---
description: Write an implementation plan to docs/plans/ (split into several if large)
argument-hint: <what to build or fix>
---

Write an implementation plan for: $ARGUMENTS

Do not write any code. Research first, then plan.

1. Read the code the change touches and trace the real flow end to end. Look for existing helpers and patterns to reuse. Ask only if a decision is genuinely the user's to make.
2. Size it. If the work is more than one reviewable PR, split it into several plans. Each plan should aim to be mergeable on its own (tests pass, nothing half-wired on `main`). That is a goal, not a hard rule: if a split can't stand alone, say so under Dependencies.
3. Write each plan to `docs/plans/NNN-short-slug.md`, where NNN continues the highest existing number (start at `001`). Split plans get consecutive numbers in merge order.

Use this template:

```markdown
# NNN: Title

Status: todo
Branch: <type>/<slug>
Depends on: none | NNN

## Goal
One or two sentences. What changes for the user of the library.

## Context
Files and functions involved, with paths. Relevant DNP3 spec sections (IEEE 1815-2012) if any.

## Steps
TDD order: each step names the failing test to write first, then the change that makes it pass.
1. Test: `tests/unit/...::test_...` asserts ... Implement: ...
2. ...

## Out of scope
What this plan deliberately does not do (and which plan does, if any).

## Done when
- [ ] new tests pass
- [ ] `uv run pytest tests/` passes, coverage >= 95%
- [ ] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean
```

Keep plans short and concrete: paths, names, and behavior, not prose. Finish by listing the plan files you wrote, in merge order.
