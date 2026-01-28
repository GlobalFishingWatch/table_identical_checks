# Table Identical Checks

[![Coverage](https://codecov.io/gh/chr96er/table-identical-checks/branch/main/graph/badge.svg)](https://codecov.io/gh/chr96er/table-identical-checks)

Compare BigQuery tables and identify differences.

## Installation

```bash
pip install -e ".[dev]"
```

## Commands

```bash
# Show all differing rows
table-check diff --table-a=project.dataset.table1 --table-b=project.dataset.table2 --keys=id

# Count differing rows
table-check count --table-a=... --table-b=... --keys=id

# Summary statistics (min/max/avg delta per column)
table-check summary --table-a=... --table-b=... --keys=id

# Breakdown by dimension (e.g., time series analysis)
table-check breakdown --table-a=... --table-b=... --keys=id --dimension=date_col

# Dry run (print generated SQL only)
table-check diff --table-a=... --table-b=... --keys=id --dry-run
```

## Features

- **SQLAlchemy Core** for type-safe, composable SQL generation
- FULL OUTER JOIN comparison on specified key columns
- NULL-safe comparison (NULLs treated as equal)
- Automatic partition filter detection and injection
- Delta metrics for numeric columns: `delta`, `abs_delta`, `rel_delta`
- Supported types: **INT64**, **FLOAT64**, **STRING**, **TIMESTAMP**, **BOOLEAN**
  - TIMESTAMP: Uses `TIMESTAMP_DIFF()` for second-precision deltas
  - BOOLEAN: Casts to INT64 for consistent delta calculation

## Limitations

- No tolerance filtering (e.g., `abs_delta < X`)
- No JSON/CSV export (stdout only)
- No ARRAY, STRUCT support
- No schema validation (assumes identical schemas)
- Key columns must be specified manually
- Results are ephemeral (not persisted to BQ tables)

## Architecture

Built on **SQLAlchemy Core** for programmatic SQL query construction:
- Type-safe column expressions
- Composable query building
- BigQuery dialect support via `sqlalchemy-bigquery`
- Custom NULL-safe comparison logic (BigQuery doesn't support `IS NOT DISTINCT FROM`)

## Testing

```bash
export GOOGLE_APPLICATION_CREDENTIALS=sa.json
pytest tests/
```

All tests run against real BigQuery (no mocking).
