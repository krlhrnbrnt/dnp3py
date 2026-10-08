# Contributing to dnp3py

Thanks for looking at dnp3py. This file covers how to set up a dev
environment, run the checks CI runs, and the conventions pull requests
follow.

## Setting up

dnp3py uses [uv](https://docs.astral.sh/uv/) to manage the dev environment.

```bash
uv sync
uv run pre-commit install
```

`uv sync` creates `.venv` with the `dev` dependency group from
`pyproject.toml` and an editable install of the package. Its default Python
comes from `.python-version` (3.14). CI tests Python 3.11 through 3.13 only,
so add `--python 3.13` to any command below to run it on a CI-tested
version.

## Running the checks CI runs

`CI Check` is the required status check on `main`. It is the aggregate of
the `test`, `quality`, and `build` jobs, and a pull request merges only once
it passes. Review is expected on every pull request; it is a project
practice, not a required approval enforced by GitHub. Run the same commands
`test`, `quality`, and `build` run:

```bash
uv run pytest tests/ -v --tb=short
```

```bash
uv run ruff format --check src/ tests/
uv run ruff check src/ tests/
```

```bash
uv run mypy src/
```

`build` also runs:

```bash
uv build
uvx twine check dist/*
```

The `dev` group pins `ruff==0.15.18`, matching CI's `quality` job and
`.pre-commit-config.yaml`; update all three together when upgrading ruff.

## Conventions

- Commit subjects: `type(scope): description (#NNN)`, ending with the
  issue number when there is one, conventional-commits style. The release
  tooling parses these to compute the next version and the changelog, so
  the type (`feat`, `fix`, `docs`, and so on) matters.
- Branches: `type/NNN-slug`.
- One property per pull request. A pull request that closes part of a
  larger issue, and says so, is normal.
- Pull requests land by merge commit or rebase merge, never squash: the
  commit trail is kept.
- Security vulnerabilities go through [SECURITY.md](SECURITY.md), never a
  public issue, pull request, or discussion.

## Standards text

IEEE 1815 and IEEE 1815.2 are copyrighted standards. Cite a clause or
table number (for example "1815.2 clause 5.6.3") in code, comments,
commits, and pull requests; never paste text from the standard itself.

## Where to start

Issues labeled `good first issue` are scoped for a first contribution.
For larger work, [ROADMAP.md](ROADMAP.md) lays out the path to IEEE 1815.2
conformance; issues labeled `1815.2-conformance` are that body of work,
and each roadmap milestone links to its own epic issue.
