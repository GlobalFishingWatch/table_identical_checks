# CLI Reference

The `table-check` CLI has six commands. Shared conventions:

- Most commands read GCP credentials via Application Default Credentials.
  Pass `--credentials=<path-to-sa.json>` (or set `$GOOGLE_APPLICATION_CREDENTIALS`)
  to override.
- `--partition-filter-a` / `--partition-filter-b` are used for tables that
  require a partition filter. When omitted, a NULL-safe dummy filter
  (`col IS NULL OR DATE(col) != '1979-01-01'`) is auto-injected for tables
  that declare a partition.
- Float comparisons apply default tolerance `--tolerance=1e-15` (absolute)
  and `--rel-tolerance=1e-12` (relative). Pass `0` to either to disable.
  See [Tolerance](../README.md#tolerance) for details.
- `--snapshot-a` / `--snapshot-b` (ISO 8601 timestamps) read either side via
  BigQuery time travel. For deleted tables, restoration uses the scratch
  dataset specified by `--scratch-dataset` (or `$BQ_SCRATCH_DATASET`).

## `table-check summary`

Generate a comprehensive comparison summary as a single BigQuery
multi-statement job.

| Option | Type | Default | Description |
|---|---|---|---|
| `--table-a` | TEXT | (required) | First table (`project.dataset.table`) |
| `--table-b` | TEXT | (required) | Second table |
| `--keys` | TEXT | (required) | Comma-separated key columns for joining |
| `--credentials` | TEXT | `$GOOGLE_APPLICATION_CREDENTIALS` | Path to service-account JSON |
| `--partition-filter-a` / `--partition-filter-b` | TEXT | (auto-detected) | Custom partition filter |
| `--tolerance` | TEXT | `1e-15` | Absolute float tolerance |
| `--rel-tolerance` | TEXT | `1e-12` | Relative float tolerance |
| `--sort-columns` | `alphabetical` \| `significance` | `alphabetical` | Column sort in the per-column breakdown |
| `--format` | `verbose` \| `table` | `verbose` | Output format |
| `--max-diff-pct` | FLOAT | `100.0` | Circuit breaker; abort detailed stats if more than X% of rows differ. Default is effectively disabled; lower it to re-enable (e.g. `10`). |
| `--legacy` | FLAG | False | Use legacy multi-query path instead of the pipeline |
| `--output-json` | TEXT | (deterministic cache path) | Write the `ComparisonSummary` to a JSON file consumable by `format` and `verify-query`. Default path: `$XDG_CACHE_HOME/table-check/<sha1-of-keys>.json` |
| `--kll-cols` | TEXT | None | Comma-separated BYTES columns to treat as `KLL_QUANTILES.INIT_FLOAT64` sketches |
| `--kll-int-cols` | TEXT | None | Same, for `KLL_QUANTILES.INIT_INT64` sketches |
| `--kll-abs-tol` | FLOAT | `0.0` | Absolute tolerance on extracted KLL quantile values |
| `--kll-rel-tol` | FLOAT | `0.05` | Relative tolerance on extracted KLL quantile values |
| `--write-diffs` | FLAG | False | Materialise two filtered BQ tables, one per input, containing only rows that contribute to the diff after tolerance |
| `--output-a` / `--output-b` | TEXT | (derived from `$TABLE_CHECK_OUTPUT_DATASET`) | Fully-qualified table refs for the filtered diff copies. Both must be set together |
| `--write-mode` | `error` \| `replace` | `error` | Behaviour when diff-output tables exist. `replace` only works on names starting with `DIFF_` (TIC-generated convention) |
| `--expiration-hours` | INT | `168` | TTL for diff-output tables (7 days). Pass `0` to disable expiration |
| `--snapshot-a` / `--snapshot-b` | TEXT | None | ISO 8601 timestamp; read the side via BQ time travel |
| `--scratch-dataset` | TEXT | `$BQ_SCRATCH_DATASET` | `project.dataset` for restoring deleted snapshot tables |

## `table-check diff`

Compare two tables and show differing rows (or write them to a BQ table).

| Option | Type | Default | Description |
|---|---|---|---|
| `--table-a` / `--table-b` / `--keys` | TEXT | (required) | As above |
| `--credentials` | TEXT | env | Path to SA JSON |
| `--partition-filter-a` / `--partition-filter-b` | TEXT | (auto-detected) | Partition filter |
| `--tolerance` / `--rel-tolerance` | TEXT | `1e-15` / `1e-12` | Float tolerance |
| `--dry-run` | FLAG | False | Print generated SQL without executing |
| `--limit` | INT | `100` | Max rows to return (stdout mode) |
| `--output-table` | TEXT | None | Persist diff to this BQ table (DDL) |
| `--write-mode` | `replace` \| `if_not_exists` | `replace` | DDL mode for `--output-table` |
| `--expiration-hours` | INT | None | TTL (hours) for the output table |
| `--only-diffs` | FLAG | False | Restrict output to columns with actual differences (runs the pipeline first to identify them) |
| `--max-display-rows` | INT | `20` | Rows to display in stdout (full result goes to a temp file) |
| `--kll-cols` / `--kll-int-cols` / `--kll-abs-tol` / `--kll-rel-tol` | (as `summary`) | | KLL sketch comparison opt-in |
| `--snapshot-a` / `--snapshot-b` / `--scratch-dataset` | TEXT | (as `summary`) | BQ time-travel reads |

## `table-check count`

Count differing rows between two tables. Cheap; does not materialise the diff.

| Option | Type | Default | Description |
|---|---|---|---|
| `--table-a` / `--table-b` / `--keys` | TEXT | (required) | |
| `--credentials` | TEXT | env | Path to SA JSON |
| `--partition-filter-a` / `--partition-filter-b` | TEXT | (auto-detected) | Partition filter |
| `--tolerance` / `--rel-tolerance` | TEXT | `1e-15` / `1e-12` | Float tolerance |
| `--snapshot-a` / `--snapshot-b` / `--scratch-dataset` | TEXT | None | BQ time-travel reads |

## `table-check breakdown`

Generate a comparison summary broken down by a dimension column (e.g. one
row per `date` value).

| Option | Type | Default | Description |
|---|---|---|---|
| `--table-a` / `--table-b` / `--keys` | TEXT | (required) | |
| `--dimension` | TEXT | (required) | Column to break down by |
| `--delta-col` | TEXT | None | Numeric column to track max deltas for |
| `--limit` | INT | None | Limit number of dimension buckets |
| `--credentials` | TEXT | env | Path to SA JSON |
| `--partition-filter-a` / `--partition-filter-b` | TEXT | (auto-detected) | Partition filter |
| `--tolerance` / `--rel-tolerance` | TEXT | `1e-15` / `1e-12` | Float tolerance |
| `--snapshot-a` / `--snapshot-b` / `--scratch-dataset` | TEXT | None | BQ time-travel reads |

## `table-check format`

Re-render a saved `ComparisonSummary` from JSON without rerunning BigQuery.
The summary command writes its output JSON to a deterministic cache path on
every run, so `format` is the cheap way to switch between `--format=verbose`
and `--format=table` after the fact.

| Option | Type | Default | Description |
|---|---|---|---|
| `--input-json` | TEXT | (required) | Path to a summary JSON file |
| `--format` | `verbose` \| `table` | (value saved in JSON) | Override output format |
| `--sort-columns` | `alphabetical` \| `significance` | (value saved in JSON) | Override column sort order |

## `table-check verify-query`

Emit an `EXCEPT DISTINCT` / `UNION ALL` SQL query that probes the comparison's
equality verdict for all columns the saved summary found equal (pre-tolerance).
Useful as an independent sanity check: paste the emitted query into BigQuery,
and a zero-row result confirms the summary's equality verdict on those
columns.

Columns with differences, unsupported-type columns, and `GEOGRAPHY` columns
(not groupable in BQ) are excluded from the probe.

| Option | Type | Default | Description |
|---|---|---|---|
| `--input-json` | TEXT | (required) | Path to a summary JSON file |
