# dnp3py: Claude Code Instructions

## Project Overview

Pure Python implementation of the DNP3 protocol (IEEE 1815-2012). Zero C/C++ dependencies.
Async I/O via asyncio, strict type annotations, Level 2 subset for SCADA/RTU applications.

- **Owner:** craig8 (GitHub)
- **License:** MIT
- **Python:** 3.11, 3.12, 3.13, 3.14
- **Build system:** Hatchling
- **Package manager:** uv (dev dependencies in the `dev` dependency group in pyproject.toml, locked in `uv.lock`)

## Setup

Always use a virtual environment. Never install packages system wide.

```bash
uv sync                       # create .venv, install dev group + editable package
uv run pre-commit install
```

## Build and Run

```bash
uv sync                       # editable install (re-run after dependency changes)
uv build                      # build sdist and wheel
uvx twine check dist/*        # verify package metadata
```

## Test Commands

```bash
uv run pytest tests/ -v                     # all tests, verbose
uv run pytest tests/unit/ -v                # unit tests only
uv run pytest tests/integration/ -v         # integration tests only
uv run pytest tests/ --cov=src/dnp3 --cov-report=html --cov-fail-under=95   # coverage (HTML report)
for v in 3.11 3.12 3.13 3.14; do uv run --python $v pytest tests/; done   # all supported Pythons
uv run --python 3.11 pytest tests/ -v       # test against specific Python version
```

Test dependencies: pytest, pytest-asyncio, pytest-cov, hypothesis.

Coverage threshold is **95%** (configured in pyproject.toml `tool.coverage.report.fail_under`).

## Quality Checks

```bash
uv run ruff check src/ tests/               # lint
uv run ruff format src/ tests/              # format (applies fixes)
uv run ruff format --check src/ tests/      # format (check only)
uv run mypy src/                            # mypy strict mode
```

Pre-commit hooks enforce: trailing whitespace, ruff lint/format, mypy strict, bandit security.

## Architecture

```
src/dnp3/
  core/             # CRC, types, enums, flags
  datalink/         # Data link layer (frames, parsing)
  transport/        # Transport layer (segmentation, reassembly)
  application/      # Application layer (messages, function codes)
  objects/          # DNP3 object group definitions (Groups 1,2,10,11,12,20,21,22,30,32,40,41,42,50,51,52,60)
  database/         # Point database and event buffering
  outstation/       # Outstation (server) implementation
  master/           # Master (client) implementation
  mesa/             # MESA profile support
  transport_io/     # TCP server/client channels, simulator
tests/
  unit/             # Fast, isolated unit tests
  integration/      # Multi-layer integration tests
  cross_repo/       # Cross-repository compatibility tests
```

Layers follow the DNP3 stack: datalink (bottom) > transport > application (top).
Outstation and master sit above the application layer.

## Conventions

- **TDD mandatory.** Write failing tests first, then implement, then refactor.
- **Commit messages:** conventional commits format (`feat:`, `fix:`, `test:`, `refactor:`, `chore:`, `docs:`).
- **Never squash merge.** Preserve the TDD commit trail.
- **Immutability preferred.** Create new objects rather than mutating.
- **Line length:** 120 characters (ruff config).
- **Quote style:** double quotes.
- **Type annotations:** required on all public APIs. mypy strict mode enforced.
- **No console.log/print debug statements** in committed code.
- **No hardcoded secrets.**

## CI/CD

GitHub Actions workflow (`.github/workflows/ci.yml`):
- Test matrix: Python 3.11 to 3.14 on ubuntu-latest and macos-latest
- Quality job: ruff format check, ruff lint, mypy
- Build job: package build and twine check
- Coverage uploaded to Codecov (Python 3.14 on Ubuntu only)
- Release workflow in `.github/workflows/release.yml`

Note: This is an open source library. CI uses GitHub hosted runners (ubuntu-latest, macos-latest), not self-hosted.

## Team Assignments (WRS)

| Member | Scope |
|--------|-------|
| Raj | Protocol layer implementation, async networking |
| Frank | TDD, test coverage, hypothesis property tests |
| Dutch | Code review on every PR |
| Leon | Security review (SCADA protocol, input parsing) |
| Ferris | N/A (pure Python project) |

## Key Files

- `pyproject.toml`: project metadata, dev dependency group, tool config, coverage thresholds
- `uv.lock`: locked dev environment (commit changes alongside pyproject.toml)
- `.python-version`: default Python for `uv sync` (3.14)
- `.pre-commit-config.yaml`: pre-commit hook definitions
- `src/dnp3/__init__.py`: version string
