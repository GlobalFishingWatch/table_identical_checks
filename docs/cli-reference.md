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

## Exit codes

| Code | Meaning |
|---|---|
| `0` | `summary` / `format`: verdict PASS. Other commands: succeeded |
| `1` | `summary` / `format`: verdict FAIL (see [Verdict](#verdict)) |
| `2` | Error: bad options, a missing file, a BigQuery error, any other crash (with a traceback on stderr), Ctrl-C, or output closed early (broken pipe) |

Versions without this section exited 0 whatever the comparison found. Callers that treat any non-zero status as a crash need to accept `1` as "differences found".

A pipx install must be reinstalled (`pipx reinstall table-identical-checks`) to pick up the new entry point. An editable install doesn't regenerate the `table-check` launcher.

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
| `--expect-diff` | TEXT (repeatable) | None | Columns expected to differ. They are still compared and reported (status `EXPECT`), but never fail the verdict. Comma-separated glob patterns over flattened names, e.g. `regions.*`. A bare STRUCT name (`regions`) matches all its fields |
| `--ignore-columns` | TEXT (repeatable) | None | Columns left out of the comparison entirely. Same pattern syntax. A top-level column that is ignored isn't read at all. Ignoring only some fields of a STRUCT still reads the STRUCT, because the other fields are compared. `--write-diffs` copies whole rows regardless |

| `--keyless` | FLAG | False | Compare whole rows without keys (see [Keyless mode](#keyless-mode)) |

### Keyless mode

`--keyless` compares whole rows instead of joining on keys. Use it when a table has no natural key and you only need to know whether two tables hold the same rows.

- **Identical-only.** Values must match exactly: floats have no tolerance, and GEOGRAPHY compares by its text form. Pairing rows needs keys, so there are no per-column deltas.
- **Row form.** Each row is serialised by flattened column path, so STRUCT subfields line up by name. ARRAYs ignore element order, as in keyed mode.
- **Duplicate-aware.** Each distinct row is counted on both sides, and surplus copies count as rows only in that side. `{X, X, Y}` against `{X, Y, Y}` is one row only in A and one only in B, not "identical". Duplicate rows are reported per side.
- **Output.** The summary shows identical / only-in-A / only-in-B row counts and up to 5 sample rows per side with their copy counts. The verdict and exit code work as for keyed runs.
- **Restrictions.** It can't be combined with `--keys`, `--interactive`, `--tolerance` / `--rel-tolerance`, the KLL options, `--write-diffs`, `--legacy`, `--max-diff-pct` or `--expect-diff`. `--ignore-columns`, partition filters and snapshots work.

### Verdict

Every summary ends with `VERDICT: PASS` or `VERDICT: FAIL (...)`. The JSON output has a matching `verdict` field, a `failing_columns` list and a `fail_reasons` list.

The verdict is **FAIL** when any of these holds:
- a row exists on one side only;
- the row totals or the duplicate structure differ, for example an extra copy of a duplicated key whose values all match;
- rows differ in a column not listed in `--expect-diff` (differences within tolerance don't count);
- rows differ but no column shows the difference. That's never assumed to be expected;
- **schema drift**: a column exists in one table only, or has a different type, and isn't matched by `--ignore-columns` or `--expect-diff`. Patterns match the union of both schemas, so a column added in B can be ignored.

Otherwise the verdict is **PASS**, including when the only differences are in expected columns.

Notes:
- **Keys:** key columns are never matched, so a broad glob like `*` skips them. A pattern that matches nothing else is an error, to catch typos.
- **Unsupported types:** unsupported-type columns can't be expected to differ, because they're never compared. Use `--ignore-columns` for them.
- **Expected columns without differences:** a column listed in `--expect-diff` that shows no differences beyond tolerance is reported as a note, not a failure.
- **Aborted runs:** when the circuit breaker aborts, the verdict uses Layer 1's pre-tolerance counts and says so.
- **Older versions:** JSON files written by this version carry `verdict`, `failing_columns` and `fail_reasons`, which older `table-check format` / `verify-query` versions can't load.

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

Both sides select the same explicit column list (keys first), so columns line
up by name rather than by position. `EXCEPT DISTINCT` rejects `ARRAY`,
`STRUCT`, `GEOGRAPHY` and `JSON`, so:

- STRUCT fields are selected by leaf path (`info.score AS info__score`).
- ARRAY columns are compared in the same canonical form the comparison uses
  (elements sorted by their JSON), so element order is ignored.
- Columns with differences, unsupported-type columns, and `GEOGRAPHY` and KLL
  sketch columns (whose equality checks have no set-comparable form) are left
  out and listed in a comment at the top of the emitted query.

| Option | Type | Default | Description |
|---|---|---|---|
| `--input-json` | TEXT | (required) | Path to a summary JSON file |
