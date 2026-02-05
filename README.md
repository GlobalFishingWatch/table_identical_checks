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

# Persist diff results to a BQ table (CREATE OR REPLACE TABLE)
table-check diff --table-a=... --table-b=... --keys=id --output-table=project.dataset.diff_results

# Persist with IF NOT EXISTS and a 24-hour TTL
table-check diff --table-a=... --table-b=... --keys=id \
  --output-table=project.dataset.diff_results \
  --write-mode=if_not_exists \
  --expiration-hours=24

# Show only columns with actual differences (runs pipeline first)
table-check diff --table-a=... --table-b=... --keys=id --only-diffs

# Combine: persist a focused diff with only differing columns
table-check diff --table-a=... --table-b=... --keys=id --only-diffs --output-table=project.dataset.diff_results

# Dry run (print generated SQL only)
table-check diff --table-a=... --table-b=... --keys=id --dry-run
```

## Features

- **Multi-layer pipeline** executes the `summary` command as a single BQ multi-statement script (see [Architecture](#architecture) below)
- **SQLAlchemy Core** for type-safe, composable SQL generation (legacy path and `diff`/`count`/`breakdown` commands)
- FULL OUTER JOIN comparison on specified key columns
- NULL-safe comparison (NULLs treated as equal)
- **Automatic partition filter detection** using dry-run queries -- works for both base tables and views over partitioned tables
- **STRUCT flattening**: Non-repeated STRUCT/RECORD fields are recursively flattened to dot-notation sub-fields (e.g., `address.street`, `outer.inner.x`). Sub-fields are compared as regular columns. REPEATED STRUCTs remain unsupported.
- **Unsupported column auto-exclusion**: ARRAY, REPEATED STRUCT, JSON, BYTES, RANGE columns are automatically excluded with a prominent CLI warning
- **Diff output persistence** (`--output-table`): Write diff results to a BigQuery table using `CREATE OR REPLACE TABLE` (or `CREATE TABLE IF NOT EXISTS` with `--write-mode=if_not_exists`). Optional TTL via `--expiration-hours`.
- **Focused diffs** (`--only-diffs`): Runs the pipeline to identify which columns actually differ, then restricts the diff query to only those columns. Much more useful output for wide tables.
- **Composable options**: `--only-diffs` and `--output-table` combine naturally -- persist a focused diff containing only key columns and differing value columns.
- **Tolerance-based filtering** for FLOAT64 and GEOGRAPHY columns
  - Global tolerance: `--tolerance=1e-9`
  - Per-column tolerance: `--tolerance=col1:1e-9,col2:1e-6`
  - Tolerance works on FLOAT64 sub-fields inside STRUCTs: `--tolerance=address.lat:1e-9`
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
| STRUCT/RECORD | Flattened to dot-notation sub-fields | Per sub-field (by sub-field type) | Per sub-field |
| ARRAY, JSON, etc. | Auto-excluded | N/A | N/A |

## CLI Reference

### `table-check diff`

Compare two tables and show differing rows.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--table-a` | TEXT | (required) | First table (project.dataset.table) |
| `--table-b` | TEXT | (required) | Second table (project.dataset.table) |
| `--keys` | TEXT | (required) | Comma-separated key columns for joining |
| `--tolerance` | TEXT | None | Float/geography tolerance (e.g., `1e-9` or `col1:1e-9,col2:1e-6`) |
| `--dry-run` | FLAG | False | Print generated SQL without executing |
| `--limit` | INT | 100 | Max rows to return (stdout mode only) |
| `--output-table` | TEXT | None | Persist diff to this BQ table (DDL) |
| `--write-mode` | CHOICE | replace | DDL mode: `replace` or `if_not_exists` |
| `--expiration-hours` | INT | None | TTL in hours for the output table |
| `--only-diffs` | FLAG | False | Restrict output to columns with actual differences |
| `--credentials` | TEXT | `$GOOGLE_APPLICATION_CREDENTIALS` | Path to SA JSON |
| `--partition-filter-a` | TEXT | None | Partition filter for table A |
| `--partition-filter-b` | TEXT | None | Partition filter for table B |

### `table-check count`

Count the number of differing rows between two tables.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--table-a` | TEXT | (required) | First table |
| `--table-b` | TEXT | (required) | Second table |
| `--keys` | TEXT | (required) | Comma-separated key columns |
| `--tolerance` | TEXT | None | Float/geography tolerance |
| `--credentials` | TEXT | `$GOOGLE_APPLICATION_CREDENTIALS` | Path to SA JSON |
| `--partition-filter-a` | TEXT | None | Partition filter for table A |
| `--partition-filter-b` | TEXT | None | Partition filter for table B |

### `table-check summary`

Generate a comprehensive comparison summary (single BQ job via pipeline).

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--table-a` | TEXT | (required) | First table |
| `--table-b` | TEXT | (required) | Second table |
| `--keys` | TEXT | (required) | Comma-separated key columns |
| `--tolerance` | TEXT | None | Float/geography tolerance |
| `--format` | CHOICE | verbose | Output format: `verbose` or `table` |
| `--sort-columns` | CHOICE | alphabetical | Sort: `alphabetical` or `significance` |
| `--max-diff-pct` | FLOAT | 10.0 | Circuit breaker: abort if >X% of rows differ |
| `--legacy` | FLAG | False | Use legacy multi-query path instead of pipeline |
| `--credentials` | TEXT | `$GOOGLE_APPLICATION_CREDENTIALS` | Path to SA JSON |
| `--partition-filter-a` | TEXT | None | Partition filter for table A |
| `--partition-filter-b` | TEXT | None | Partition filter for table B |

### `table-check breakdown`

Generate comparison summary broken down by a dimension.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--table-a` | TEXT | (required) | First table |
| `--table-b` | TEXT | (required) | Second table |
| `--keys` | TEXT | (required) | Comma-separated key columns |
| `--dimension` | TEXT | (required) | Column to break down by (e.g., date) |
| `--delta-col` | TEXT | None | Numeric column to track max deltas for |
| `--limit` | INT | None | Limit number of dimension buckets |
| `--tolerance` | TEXT | None | Float/geography tolerance |
| `--credentials` | TEXT | `$GOOGLE_APPLICATION_CREDENTIALS` | Path to SA JSON |
| `--partition-filter-a` | TEXT | None | Partition filter for table A |
| `--partition-filter-b` | TEXT | None | Partition filter for table B |

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

### `--only-diffs` Pipeline Integration

The `--only-diffs` flag on the `diff` command leverages the pipeline to produce focused output:

1. Runs the pipeline to compute per-column `diff_count` values
2. Calls `differing_columns()` (a pure function) to extract columns with `diff_count > 0`
3. Passes the resulting column list as `columns_filter` to `build_diff_query()` or `build_diff_table_statement()`
4. The generated SQL includes only key columns and value columns with actual differences

This is particularly useful for wide tables where most columns are identical and the full diff output would be unwieldy.

### Legacy Mode (`--legacy`)

The original multi-query approach. Executes 2 + N queries where N is the number of value columns. Each query wraps the FULL OUTER JOIN independently. Useful as a fallback if the pipeline produces unexpected results.

### Query Count Comparison

| Scenario | Legacy | Pipeline |
|----------|--------|----------|
| 20 columns, no tolerance | ~22 queries | 1 job |
| 10 float columns with tolerance | ~25 queries | 1 job |
| Tables identical | ~22 queries | 1 job (Layer 2 is empty) |
| Circuit breaker triggers | N/A | 1 job (stops after Layer 1) |

### STRUCT Handling

Non-repeated STRUCT/RECORD fields are flattened at schema level by `_flatten_fields()` in `schema.py`:

- `address.street`, `address.zip_code` for a single-level STRUCT
- `outer.inner.x` for nested STRUCTs

The pipeline SQL uses `_safe_alias()` to mangle dot-notation into double-underscore aliases (e.g., `address__street__eq`) because dots are illegal in BigQuery temp table column names. The result parser uses a matching `_alias()` helper for column lookup.

REPEATED STRUCTs and REPEATED sub-fields inside a non-repeated STRUCT are marked UNSUPPORTED and auto-excluded.

### Module Layout

```
src/table_identical_checks/
  backend/
    __init__.py          # Public API exports
    query_builder.py     # SQL generation (SQLAlchemy + raw SQL for pipeline)
    pipeline.py          # PipelineConfig, PipelineResult, run_pipeline(), differing_columns()
    summary.py           # ComparisonSummary, formatters, generate_summary()
    schema.py            # Column type detection, partition field detection, STRUCT flattening
    tolerance.py         # ToleranceConfig parsing
  cli.py                 # Click CLI (diff, count, summary, breakdown)
```

## Testing

```bash
pytest tests/
```

200 tests across 9 test files. All tests run against real BigQuery (no mocking). Test dataset: `world-fishing-827.tech_great_expectations`.

### Test Files

| File | Tests | Coverage |
|------|-------|---------|
| `test_numeric.py` | 9 | INT64/FLOAT64 comparisons, deltas |
| `test_string.py` | 9 | STRING matching, NULL handling |
| `test_tolerance.py` | 19 | Global/per-column tolerance, edge cases |
| `test_table_formatter.py` | 54 | Compact table output formatting |
| `test_geography.py` | 13 | GEOGRAPHY ST_DISTANCE, ST_EQUALS, tolerance |
| `test_unsupported.py` | 19 | Auto-exclusion of unsupported types |
| `test_pipeline.py` | 18 | Pipeline execution, circuit breaker, tolerance |
| `test_struct.py` | 25 | STRUCT flattening, nested structs, dot-notation |
| `test_diff_output.py` | 34 | `--output-table`, `--only-diffs`, composability, display helpers |

## Limitations

- No JSON/CSV export (stdout only, unless using `--output-table` on `diff`)
- No schema validation (assumes identical schemas)
- Key columns must be specified manually
- Pipeline mode only available for the `summary` command; `diff`, `count`, and `breakdown` use the legacy query approach
- REPEATED STRUCT/RECORD fields are not supported (auto-excluded)
