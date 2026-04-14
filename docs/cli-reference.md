# CLI Reference

All commands accept `--credentials` (path to service account JSON, defaults to `$GOOGLE_APPLICATION_CREDENTIALS`) and partition filters (`--partition-filter-a`, `--partition-filter-b`) for tables that require partition elimination.

## `table-check diff`

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
| `--max-display-rows` | INT | 20 | Max rows to display in stdout (full result goes to temp file) |

## `table-check count`

Count the number of differing rows between two tables.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--table-a` | TEXT | (required) | First table |
| `--table-b` | TEXT | (required) | Second table |
| `--keys` | TEXT | (required) | Comma-separated key columns |
| `--tolerance` | TEXT | None | Float/geography tolerance |

## `table-check summary`

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

## `table-check breakdown`

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
