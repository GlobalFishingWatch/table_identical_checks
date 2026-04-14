# Table Identical Checks

Compare BigQuery tables and identify differences.

## Installation

```bash
pip install -e ".[dev]"
```

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

## Features

- **Multi-layer pipeline**: The `summary` command runs as a single BQ multi-statement script with automatic circuit breaker
- **Duplicate key detection**: Automatically checks for non-unique keys before comparison and warns prominently
- **Per-column diff counts**: Summary output shows how many rows differ per column and the percentage
- **Tolerance filtering** for FLOAT64 and GEOGRAPHY columns (global or per-column)
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
| FLOAT64 | Delta metrics | Same as INT64 | Yes (`ABS(a - b) <= tol`) |
| STRING | Exact (NULL-safe) | Match flag (boolean) | No |
| TIMESTAMP | Exact (NULL-safe) | `TIMESTAMP_DIFF(a, b, MICROSECOND)` | No |
| BOOLEAN | Cast to INT64 | Treated as integer (0/1) | No |
| GEOGRAPHY | `ST_EQUALS` (NULL-safe) | `ST_DISTANCE(a, b, TRUE)` in meters | Yes (`ST_DISTANCE(a, b) <= tol`) |
| STRUCT/RECORD | Flattened to sub-fields | Per sub-field (by type) | Per sub-field |
| ARRAY, JSON, etc. | Auto-excluded | N/A | N/A |

## Testing

```bash
# Unit tests only (fast, no BQ connection needed, ~1s)
pytest

# BigQuery integration tests only
pytest -m bq

# All tests
pytest -m ""
```

211 tests across 9 test files: 123 unit tests and 88 BigQuery integration tests. BQ tests run against `world-fishing-827.tech_great_expectations`.

## Documentation

- [CLI Reference](docs/cli-reference.md) -- full option tables for all commands
- [Architecture](docs/architecture.md) -- pipeline design, STRUCT handling, module layout

## Limitations

- No JSON/CSV export (stdout only, unless using `--output-table` on `diff`)
- No schema validation (assumes identical schemas)
- Key columns must be specified manually
- Pipeline mode only available for `summary`; other commands use the SQLAlchemy query path
- REPEATED STRUCT/RECORD fields are not supported (auto-excluded)
