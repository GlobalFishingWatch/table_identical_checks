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

# Summary statistics (pipeline mode -- single BQ job)
table-check summary --table-a=... --table-b=... --keys=id

# Summary with compact table output
table-check summary --table-a=... --table-b=... --keys=id --format=table

# Summary with legacy multi-query path
table-check summary --table-a=... --table-b=... --keys=id --legacy

# Set circuit breaker threshold (abort detailed stats if >20% of rows differ)
table-check summary --table-a=... --table-b=... --keys=id --max-diff-pct=20

# Breakdown by dimension (e.g., time series analysis)
table-check breakdown --table-a=... --table-b=... --keys=id --dimension=date_col

# Apply tolerance for float/geography comparisons
table-check diff --table-a=... --table-b=... --keys=id --tolerance=1e-9

# Per-column tolerance
table-check diff --table-a=... --table-b=... --keys=id --tolerance=col1:1e-9,col2:1e-6

# Sort columns by significance (most different first)
table-check summary --table-a=... --table-b=... --keys=id --sort-columns=significance

# Dry run (print generated SQL only)
table-check diff --table-a=... --table-b=... --keys=id --dry-run
```

## Features

- **Multi-layer pipeline** executes the `summary` command as a single BQ multi-statement script (see [Architecture](#architecture) below)
- **SQLAlchemy Core** for type-safe, composable SQL generation (legacy path and `diff`/`count`/`breakdown` commands)
- FULL OUTER JOIN comparison on specified key columns
- NULL-safe comparison (NULLs treated as equal)
- Automatic partition filter detection and injection
- **Unsupported column auto-exclusion**: ARRAY, STRUCT, RECORD, JSON, BYTES, RANGE columns are automatically excluded with a prominent CLI warning
- **Tolerance-based filtering** for FLOAT64 and GEOGRAPHY columns
  - Global tolerance: `--tolerance=1e-9`
  - Per-column tolerance: `--tolerance=col1:1e-9,col2:1e-6`
  - Excludes rows where ALL toleranced columns are within tolerance
  - Summary shows both pre-tolerance and post-tolerance row counts
  - Per-column statistics include `within_tolerance_count` and `outside_tolerance_count`
- **Sortable column statistics** in summary output
  - Alphabetical (default): `--sort-columns=alphabetical`
  - By significance: `--sort-columns=significance` (sorts by SUM(ABS(rel_delta)))
- **Output formats** for the `summary` command (`--format`):
  - `verbose` (default): Multi-line detailed output
  - `table`: Compact tabular format with one row per column, OK/NOK status
- Delta metrics for numeric columns: `delta`, `abs_delta`, `rel_delta`

### Supported Column Types

| Type | Comparison | Delta Calculation | Tolerance |
|------|------------|-------------------|-----------|
| INT64 | Exact (NULL-safe) | `a - b`, `ABS(a - b)`, `SAFE_DIVIDE(a - b, b)` | No |
| FLOAT64 | Delta metrics | Same as INT64 | Yes (`ABS(a - b) <= tol`) |
| STRING | Exact (NULL-safe) | Match flag (boolean) | No |
| TIMESTAMP | Exact (NULL-safe) | `TIMESTAMP_DIFF(a, b, MICROSECOND)` | No |
| BOOLEAN | Cast to INT64 | Treated as integer (0/1) | No |
| GEOGRAPHY | `ST_EQUALS` (NULL-safe) | `ST_DISTANCE(a, b, TRUE)` in meters | Yes (`ST_DISTANCE(a, b) <= tol`) |
| ARRAY, STRUCT, JSON, etc. | Auto-excluded | N/A | N/A |

## Architecture

### Multi-Layer Pipeline (default for `summary`)

The `summary` command executes a **single BigQuery multi-statement script** that materialises intermediate results via `CREATE TEMP TABLE`. This replaces the legacy approach of running 2+N separate queries (each wrapping the full FULL OUTER JOIN).

```
DECLARE total_rows_a, total_rows_b, rows_in_both_diff

SET total_rows_a = COUNT(*) FROM table_a
SET total_rows_b = COUNT(*) FROM table_b

--- Layer 1: Identify non-identical rows ---
CREATE TEMP TABLE _l1 AS
  SELECT keys, in_a, in_b, col1__eq, col2__eq, ...
  FROM table_a FULL OUTER JOIN table_b ON keys
  WHERE any column differs OR row only in one table

SET rows_in_both_diff = COUNT(*) FROM _l1 WHERE in_a AND in_b

--- Circuit breaker ---
IF rows_in_both_diff > GREATEST(total_rows_a, total_rows_b) * max_diff_pct THEN
  SELECT 'ABORTED', Layer 1 counts only
ELSE
  --- Layer 2: Compute deltas for non-identical rows ---
  CREATE TEMP TABLE _l2 AS
    SELECT deltas, tolerance flags, ...
    FROM _l1
    JOIN table_a ON keys   -- INNER JOIN: only non-identical rows
    JOIN table_b ON keys
    WHERE in_a AND in_b

  --- Layer 3: Aggregate statistics ---
  SELECT 'COMPLETED',
    Layer 1 summary (CROSS JOIN) Layer 2 aggregated stats
END IF
```

Key design points:

- **Single BQ job**: The entire pipeline runs as one `client.query(script)` call
- **Temp tables**: `_l1` and `_l2` are `CREATE TEMP TABLE` -- automatically scoped to the script, no cleanup needed
- **Layer 2 uses INNER JOIN**: Since Layer 1 identified which keys differ, Layer 2 only re-reads those rows (~10% of data in typical comparisons)
- **Circuit breaker**: Controlled by `--max-diff-pct` (default: 10%). If too many rows differ, returns Layer 1 counts only with `pipeline_status = 'ABORTED'`
- **Identical column detection**: Layer 1 per-column `diff_count` identifies columns that are exactly identical; these are excluded from Layer 2/3 stats and listed separately in output

### Legacy Mode (`--legacy`)

The original multi-query approach. Executes 2 + N queries where N is the number of value columns. Each query wraps the FULL OUTER JOIN independently. Useful as a fallback if the pipeline produces unexpected results.

### Query Count Comparison

| Scenario | Legacy | Pipeline |
|----------|--------|----------|
| 20 columns, no tolerance | ~22 queries | 1 job |
| 10 float columns with tolerance | ~25 queries | 1 job |
| Tables identical | ~22 queries | 1 job (Layer 2 is empty) |
| Circuit breaker triggers | N/A | 1 job (stops after Layer 1) |

### Module Layout

```
src/table_identical_checks/
  backend/
    __init__.py          # Public API exports
    query_builder.py     # SQL generation (SQLAlchemy + raw SQL for pipeline)
    pipeline.py          # PipelineConfig, PipelineResult, run_pipeline()
    summary.py           # ComparisonSummary, formatters, generate_summary()
    schema.py            # Column type detection, partition field detection
    tolerance.py         # ToleranceConfig parsing
  cli.py                 # Click CLI (diff, count, summary, breakdown)
```

## Testing

```bash
pytest tests/
```

141 tests across 7 test files. All tests run against real BigQuery (no mocking). Test dataset: `world-fishing-827.tech_great_expectations`.

### Test Files

| File | Tests | Coverage |
|------|-------|---------|
| `test_numeric.py` | 18 | INT64/FLOAT64 comparisons, deltas |
| `test_string.py` | 11 | STRING matching, NULL handling |
| `test_tolerance.py` | 19 | Global/per-column tolerance, edge cases |
| `test_table_formatter.py` | 54 | Compact table output formatting |
| `test_geography.py` | 13 | GEOGRAPHY ST_DISTANCE, ST_EQUALS, tolerance |
| `test_unsupported.py` | 19 | Auto-exclusion of unsupported types |
| `test_pipeline.py` | 18 | Pipeline execution, circuit breaker, tolerance |

## Limitations

- No JSON/CSV export (stdout only)
- No schema validation (assumes identical schemas)
- Key columns must be specified manually
- Results are ephemeral (not persisted to BQ tables)
- Pipeline mode only available for the `summary` command; `diff`, `count`, and `breakdown` use the legacy query approach
