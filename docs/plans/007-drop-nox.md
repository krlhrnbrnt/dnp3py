# 007: Drop nox in favor of uv

Status: todo
Branch: chore/drop-nox
Depends on: none

## Goal
Remove the `nox` dev dependency and `noxfile.py`. Multi-version testing uses `uv run --python X.Y`, which CI already
does through its GitHub matrix. There is no library change.

## Context
- `noxfile.py`: `tests`, `tests_cov`, `lint` and `typecheck` sessions. It installs unpinned `ruff`, which can drift
  from the `ruff==0.15.18` pin in `pyproject.toml`.
- `.github/workflows/ci.yml` does not call nox.
- References:
  - `pyproject.toml` `[dependency-groups] dev` (`"nox>=2024.0"`);
  - `pyproject.toml` `[tool.bandit] exclude_dirs` (`.nox`);
  - `README.md:235-236`;
  - `CLAUDE.md` (Test Commands, Key Files);
  - `uv.lock`.

## Steps
1. No test (tooling only). Implement: delete `noxfile.py`. Remove `nox` from the dev group and `.nox` from the bandit
   excludes. Run `uv lock`.
2. Implement: in `README.md` and `CLAUDE.md`, replace `uv run nox` with a loop over `uv run --python 3.1x pytest
   tests/` for 3.11 to 3.14, and drop `noxfile.py` from Key Files.
3. Verify: `uv sync` and `uv run pytest tests/` pass.

## Out of scope
- CI workflow changes (none needed).

## Done when
- [ ] `uv lock` committed with `pyproject.toml`
- [ ] `uv run pytest tests/` passes, coverage >= 95%
- [ ] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` clean
