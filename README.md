# Table Identical Checks

[![CI](https://github.com/GlobalFishingWatch/table_identical_checks/actions/workflows/test.yml/badge.svg)](https://github.com/GlobalFishingWatch/table_identical_checks/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

> :warning: **Disclaimer:** This project is entirely maintained by Claude Code with high-level human supervision. Manual guidance is provided up to the following level:
> 1. Choice of infrastructure and architecture,
> 2. Flow of the data and checkpoints,
> 3. Definition of comparability and tolerances,
> 4. Input and output interfaces.

---

Compare BigQuery tables and identify differences.

## Installation

For development in this repo:
```bash
pip install -e ".[dev]"
```

For global use (CLI available in PATH anywhere, tracks this repo — `git pull` updates instantly):
```bash
pipx install --editable /path/to/table_identical_checks
```

## Claude Code integration

### `/compare` skill (available today)

A user-scope Claude Code skill at `~/.claude/skills/compare/SKILL.md` wraps `table-check summary` with sensible defaults (table format, picks up the BQ execution project from the `$GOOGLE_CLOUD_PROJECT` env var). It works in any project once the CLI is on `PATH` (see pipx install above).

Usage in Claude Code:
```
/compare project.ds.table_a project.ds.table_b --keys=id
/compare project.ds.table_a project.ds.table_b --keys=id,date --max-diff-pct=100
```

### MCP server (planned)

A native MCP server wrapping the same CLI (`summary`, `format`, `verify-query`, and likely `diff` / `breakdown`) is on the roadmap. This would let non-Claude-Code clients call the tool through native MCP tool invocations instead of shelling out. Intentionally deferred until the CLI surface stabilises.

## Quick Start

```bash
# Summary with compact table output (recommended starting point)
table-check summary --table-a=project.dataset.table1 --table-b=project.dataset.table2 \
  --keys=id --format=table

# Show all differing rows
table-check diff --table-a=... --table-b=... --keys=id

# Count differing rows
table-check count --table-a=... --table-b=... --keys=id

# Breakdown by dimension (e.g., time series analysis)
table-check breakdown --table-a=... --table-b=... --keys=id --dimension=date_col
```

### Common Options

```bash
# Apply tolerance for float comparisons
table-check summary ... --tolerance=1e-9

# Per-column tolerance
table-check summary ... --tolerance=col1:1e-9,col2:1e-6

# Persist diff results to a BQ table
table-check diff ... --output-table=project.dataset.diff_results

# Show only columns with actual differences
table-check diff ... --only-diffs

# Dry run (print generated SQL only)
table-check diff ... --dry-run
```

See [docs/cli-reference.md](docs/cli-reference.md) for full option details.

## Example Output

Comparing two versions of a vessel info table (623 rows, 29 value columns).
Default tolerances (abs=1e-15, rel=1e-12) apply automatically:

```
$ table-check summary \
    --table-a=your_project.your_dataset.vessel_info_a \
    --table-b=your_project.your_dataset.vessel_info_b \
    --keys=vessel_id --format=table

================================================================================
=============================== table comparison ===============================
...vessel_info vs ...vessel_info
keys: vessel_id | tol: 1e-15 | rel_tol: 1e-12

rows: 623 vs 623 | diffs: 2 (filtered 0)

Identical columns: callsign.count, callsign.freq, callsign.value, ...

--------------------------------------------------------------------------------
Column                   Type           Diffs     Diff%      MaxAbs      MaxRel      AvgAbs      Exc.tol   Within tol  Status
-----------------------------------------------------------------------------------------------------------------------------
n_shipname.freq           FLT               2     0.32%     1.0e-04     2.1e-04     7.1e-05            2            0     NOK
shipname.freq             FLT               2     0.32%     1.0e-04     2.1e-04     7.1e-05            2            0     NOK
================================================================================
============================== DIFFERENCES FOUND ===============================
```

Out of 29 value columns (including flattened STRUCT sub-fields), only 2 rows differ
in 2 columns. The default tolerances are tight enough to filter IEEE 754 noise but
not these real differences (max relative delta 2.1e-04 far exceeds 1e-12).
`Exc.tol = 2` means both diffs exceed tolerance, so `Status = NOK`.

## Features

- **Multi-layer pipeline**: The `summary` command runs as a single BQ multi-statement script with automatic circuit breaker
- **Duplicate key detection**: Automatically checks for non-unique keys before comparison and warns prominently
- **Per-column diff counts**: Summary output shows how many rows differ per column and the percentage
- **Tolerance filtering** with default noise suppression (see [Tolerance](#tolerance) below)
- **Automatic partition filter detection** via dry-run queries (works for views over partitioned tables)
- **STRUCT flattening**: Non-repeated STRUCT/RECORD fields are recursively flattened to dot-notation sub-fields
- **Unsupported column auto-exclusion**: ARRAY, JSON, BYTES, etc. are excluded with a warning
- **Diff persistence** (`--output-table`): Write results to a BQ table with optional TTL
- **Focused diffs** (`--only-diffs`): Restrict output to columns with actual differences
- NULL-safe comparison (NULLs treated as equal)

### Supported Column Types

| Type | Comparison | Delta Calculation | Tolerance |
|------|------------|-------------------|-----------|
| INT64 | Exact (NULL-safe) | `a - b`, `ABS(a - b)`, `SAFE_DIVIDE(a - b, b)` | No |
| FLOAT64 | Delta metrics | Same as INT64 | Yes (absolute and/or relative) |
| STRING | Exact (NULL-safe) | Match flag (boolean) | No |
| TIMESTAMP | Exact (NULL-safe) | `TIMESTAMP_DIFF(a, b, MICROSECOND)` | No |
| DATE | Exact (NULL-safe) | `DATE_DIFF(a, b, DAY)` | No |
| BOOLEAN | Cast to INT64 | Treated as integer (0/1) | No |
| GEOGRAPHY | `ST_EQUALS` (NULL-safe) | `ST_DISTANCE(a, b, TRUE)` in meters | Yes (`ST_DISTANCE(a, b) <= tol`) |
| STRUCT/RECORD | Flattened to sub-fields | Per sub-field (by type) | Per sub-field |
| ARRAY\<scalar\>, ARRAY\<STRUCT\<scalars\>\> | Multiset equality via `TO_JSON_STRING(ARRAY(...ORDER BY TO_JSON_STRING(e)))` | Length delta, mismatch flag | No |
| KLL_FLOAT64 / KLL_INT64 sketches (BYTES) | Opt-in via `--kll-cols` / `--kll-int-cols`; quantile-value comparison at 5 probes `[0.1, 0.25, 0.5, 0.75, 0.9]` | Max/avg abs value diff, mismatch flag | `--kll-abs-tol` (default 0.0) and `--kll-rel-tol` (default 0.05) |
| BYTES, JSON, RANGE, nested ARRAYs | Auto-excluded | N/A | See "Comparing unsupported columns" below |

## Tolerance

Float comparisons use two tolerance thresholds to filter IEEE 754 floating-point
noise. Both apply by default and are combined with OR -- a value is within
tolerance if **either** condition holds:

| Threshold | Default | Formula | Purpose |
|-----------|---------|---------|---------|
| `--tolerance` (absolute) | `1e-15` | `ABS(a - b) <= tol` | Catches noise near zero |
| `--rel-tolerance` (relative) | `1e-12` | `ABS(a - b) / GREATEST(ABS(a), ABS(b)) <= rel_tol` | Catches noise at any scale |

```bash
# Use defaults (recommended for most cases)
table-check summary --table-a=... --table-b=... --keys=id

# Custom thresholds
table-check summary ... --tolerance=1e-9 --rel-tolerance=1e-6

# Disable tolerance entirely (exact comparison)
table-check summary ... --tolerance=0 --rel-tolerance=0
```

Rows are only filtered when **all** toleranced columns are within tolerance **and**
all non-toleranced columns (boolean, string, integer, timestamp) are exactly equal.

**Caveats:**
- Relative tolerance uses `SAFE_DIVIDE`, which returns NULL (not within tolerance) when
  dividing by zero. Values near zero are handled by the absolute tolerance instead.
- There is a narrow gap for small-but-not-tiny values: if one value is 0 and the other is
  between 1e-15 and ~1e-12, neither tolerance catches it. In practice this is rarely
  meaningful for BigQuery analytics data.
- GEOGRAPHY columns only support absolute tolerance (`ST_DISTANCE <= tol` in meters).
  Relative tolerance does not apply to geography comparisons.
- Per-column overrides combine with global defaults via OR:
  `--tolerance=col1:0.01 --rel-tolerance=1e-6` means `col1` is within tolerance
  if `abs_delta <= 0.01` OR `rel_delta <= 1e-6`.

## Comparing unsupported columns

`BYTES`, `JSON`, `RANGE`, and nested / non-scalar-struct `ARRAY` columns are
auto-excluded from `table-check summary` because these types don't have a
universal meaningful equality notion. For these, run a separate semantic
comparison in a hand-written query.

### KLL quantile sketches (BYTES)

KLL sketch byte representations depend on aggregation order and BigQuery
parallelism, so two sketches built from the same input multiset are **not**
required to be byte-identical. KLL provides statistical equivalence within a
documented rank-error bound (~1.33% single-sided at the default `K=200`), not
byte equivalence. This tool compares them semantically by extracting the
quantile *value* at 5 fixed probes (`[0.1, 0.25, 0.5, 0.75, 0.9]`) from both
sketches and checking absolute and relative tolerances on the extracted
values (BigQuery does not provide a rank-from-value function for KLL
sketches).

See [`TABLE_IDENTICAL_CHECKS_QA_KLL.md`](TABLE_IDENTICAL_CHECKS_QA_KLL.md) for
a worked example on the `segments_daily` `speed_sketch` column, including
both comparison strategies, concrete tolerance choices, and edge cases.

### Future: `--custom-eq` escape hatch

A future `--custom-eq "col:<sql>"` flag (on the roadmap, not yet implemented)
will let semantic comparisons like KLL plug directly into the standard
`table-check summary` run so they appear in the per-column breakdown instead
of requiring a separate query.

## Testing

```bash
# Unit tests only (fast, no BQ connection needed, ~1s)
pytest

# BigQuery integration tests only
pytest -m bq

# All tests
pytest -m ""
```

309 tests across the test suite: 219 unit tests (no BQ required) and 90 BigQuery integration tests. BQ-integration tests use a sandbox dataset configurable via the `TABLE_CHECK_TEST_PROJECT` and `TABLE_CHECK_TEST_DATASET` environment variables (see `tests/conftest.py`).

## Documentation

- [CLI Reference](docs/cli-reference.md) -- full option tables for all commands
- [Architecture](docs/architecture.md) -- pipeline design, STRUCT handling, module layout

## Limitations

- No JSON/CSV export (stdout only, unless using `--output-table` on `diff`)
- No schema validation (assumes identical schemas)
- Key columns must be specified manually
- Pipeline mode only available for `summary`; other commands use the SQLAlchemy query path
- Nested arrays and arrays containing STRUCT fields that themselves contain STRUCT/REPEATED/BYTES/JSON/RANGE are auto-excluded. `ARRAY<scalar>` and `ARRAY<STRUCT<scalars>>` are supported via multiset equality.
- `BYTES`, `JSON`, and `RANGE` columns are auto-excluded. See "Comparing unsupported columns" above for semantic-comparison guidance (e.g. KLL sketches).
