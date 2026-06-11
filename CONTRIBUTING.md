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

We use [Conventional Commits](https://www.conventionalcommits.org/), which
drives our automated versioning and CHANGELOG via
[release-please](https://github.com/googleapis/release-please).

**Format:** `<type>: <imperative subject ≤ 70 chars>`

| Type | Use for | Version bump (in 0.x) |
|---|---|---|
| `feat:` | New user-visible feature or flag | MINOR (0.2.0 → 0.3.0) |
| `fix:` | Bug fix | PATCH (0.2.0 → 0.2.1) |
| `perf:` | Performance change (no behaviour change) | PATCH |
| `refactor:` | Code restructuring (no behaviour change) | none |
| `docs:` | Documentation only | none |
| `test:` | Test changes only | none |
| `chore:` | Build / tooling / housekeeping | none |
| `ci:` | CI workflow changes | none |
| `<type>!:` | Breaking change (e.g. `feat!:`) | MAJOR (will bump 0.x → 1.0 when ready) |

**Examples:**

```
feat: add --snapshot-a / --snapshot-b for BQ time-travel reads
fix: drop world-fishing-827 fallback from BQ test config
feat!: remove deprecated --legacy mode on summary
docs: clarify when to use --max-diff-pct
```

The body of the commit should explain the *why*, not the *what* — the diff
already shows the what.

## How releases work

You don't cut releases manually. After your PR merges to `master`, the
release-please bot opens (or updates) a Release PR titled
`chore(master): release X.Y.Z`. That PR bumps `pyproject.toml`'s version,
appends a new `## [X.Y.Z]` section to `CHANGELOG.md`, and stays open until
a maintainer merges it. Merging that PR creates the tag, the GitHub release,
and the release notes -- in one click.

If you want to know what would be in the next release at any point: look at
the open Release PR.

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
