# Architecture

## Multi-Layer Pipeline (default for `summary`)

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

## `--only-diffs` Pipeline Integration

The `--only-diffs` flag on the `diff` command leverages the pipeline to produce focused output:

1. Runs the pipeline to compute per-column `diff_count` values
2. Calls `differing_columns()` (a pure function) to extract columns with `diff_count > 0`
3. Passes the resulting column list as `columns_filter` to `build_diff_query()` or `build_diff_table_statement()`
4. The generated SQL includes only key columns and value columns with actual differences

This is particularly useful for wide tables where most columns are identical and the full diff output would be unwieldy.

## Legacy Mode (`--legacy`)

The original multi-query approach. Executes 2 + N queries where N is the number of value columns. Each query wraps the FULL OUTER JOIN independently. Useful as a fallback if the pipeline produces unexpected results.

### Query Count Comparison

| Scenario | Legacy | Pipeline |
|----------|--------|----------|
| 20 columns, no tolerance | ~22 queries | 1 job |
| 10 float columns with tolerance | ~25 queries | 1 job |
| Tables identical | ~22 queries | 1 job (Layer 2 is empty) |
| Circuit breaker triggers | N/A | 1 job (stops after Layer 1) |

## STRUCT Handling

Non-repeated STRUCT/RECORD fields are flattened at schema level by `_flatten_fields()` in `schema.py`:

- `address.street`, `address.zip_code` for a single-level STRUCT
- `outer.inner.x` for nested STRUCTs

The pipeline SQL uses `_safe_alias()` to mangle dot-notation into double-underscore aliases (e.g., `address__street__eq`) because dots are illegal in BigQuery temp table column names. The result parser uses a matching `_alias()` helper for column lookup.

REPEATED STRUCTs and REPEATED sub-fields inside a non-repeated STRUCT are marked UNSUPPORTED and auto-excluded.

## Module Layout

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
