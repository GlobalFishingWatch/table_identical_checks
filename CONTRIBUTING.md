# Contributing to `table-identical-checks`

Thanks for your interest! This is a focused tool with a narrow remit
(comparing two BigQuery tables). Contributions that fit the existing
direction are welcome.

## Reporting issues

Open a GitHub issue at
<https://github.com/GlobalFishingWatch/table_identical_checks/issues>.
Helpful issues include:

- A short description of what you expected vs. what happened.
- The exact `table-check` command you ran (redact any sensitive table refs).
- The CLI output, or a minimal repro on synthetic data if possible.
- `table-check --version`, Python version, and OS.

For security-sensitive issues see [SECURITY.md](SECURITY.md).

## Development setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
```

## Running tests

```bash
# Fast unit tests (no BigQuery required)
pytest -m 'not bq'

# Full suite including BigQuery integration tests
# (needs a GCP project + sandbox dataset; see tests/conftest.py)
TABLE_CHECK_TEST_PROJECT=<your-project> \
TABLE_CHECK_TEST_DATASET=<your-dataset> \
pytest -m bq
```

## Code style

- Linting via [`ruff`](https://docs.astral.sh/ruff/): `ruff check src tests`.
- Line length: 100.
- Python ≥ 3.10 type hints on all public APIs.
- Prefer `dataclass`-based domain types over dicts where it improves clarity.
- Functional style is welcome but not required; match what's around the
  code you're editing.

## Commit messages

- Subject line in imperative mood, ≤ 70 chars (`Fix duplicate-key fanout
  in diff-split outputs`).
- Body explains the *why*, not the *what* — the diff already shows the
  what.

## Pull requests

- Branch from `master`.
- Each PR should include or update tests where applicable. New features
  generally need at least one BQ integration test.
- Keep PRs focused: one feature or one bug fix per PR makes review faster.
- Update `CHANGELOG.md` under `[Unreleased]`.

## Scope guardrails

What this project intentionally does **not** do:

- Schema migration / drift management — use a schema-diff tool for that.
- Multi-engine query generation — the SQL is BQ-specific by design.
- Row-level edit suggestions — the goal is detection, not remediation.

If you're unsure whether a contribution fits the scope, open an issue
first to discuss.
