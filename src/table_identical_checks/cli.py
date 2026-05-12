"""Command-line interface for table identical checks."""

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Sequence

import click
import google.auth
from google.cloud import bigquery
from tabulate import tabulate

from .backend import (
    ColumnInfo,
    PipelineConfig,
    QueryBuilder,
    ToleranceConfig,
    build_verify_query,
    from_json_dict,
    generate_dimension_summary,
    generate_summary,
    get_table_schema,
    to_json_dict,
)


# Default tolerance values for filtering IEEE 754 float noise.
# Relative handles large values; absolute handles near-zero values.
# Combined via OR: a value is within tolerance if EITHER condition holds.
DEFAULT_ABS_TOLERANCE = "1e-15"
DEFAULT_REL_TOLERANCE = "1e-12"


def _default_summary_json_path(table_a: str, table_b: str, keys: Sequence[str]) -> str:
    """Return a deterministic cache path for the summary JSON.

    The path identity is keyed on table_a, table_b, and the (sorted) key set,
    so re-running the same comparison overwrites the same file. Tolerance and
    formatting flags do *not* enter the hash: those describe how to interpret
    the comparison, not which comparison it is.
    """
    fingerprint = f"{table_a}|{table_b}|{','.join(sorted(keys))}"
    h = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:12]
    cache_root = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    out_dir = os.path.join(cache_root, "table-check")
    os.makedirs(out_dir, exist_ok=True)
    return os.path.join(out_dir, f"{h}.json")


def _parse_csv_names(raw: str | None) -> list[str] | None:
    """Parse a comma-separated list of column names (strip, drop empty).

    Returns None for None/empty input so callers can distinguish "not set".
    """
    if not raw:
        return None
    names = [part.strip() for part in raw.split(",") if part.strip()]
    return names or None


def _parse_tolerance(tolerance: str | None, rel_tolerance: str | None) -> ToleranceConfig | None:
    """Parse and merge absolute and relative tolerance CLI arguments.

    When neither is specified, applies sensible defaults (abs=1e-15, rel=1e-12)
    to filter IEEE 754 floating-point noise. Pass '0' to either to disable.
    """
    # Apply defaults when neither is specified
    if tolerance is None and rel_tolerance is None:
        tolerance = DEFAULT_ABS_TOLERANCE
        rel_tolerance = DEFAULT_REL_TOLERANCE

    # '0' disables that tolerance type
    abs_config = ToleranceConfig.parse(tolerance) if tolerance and tolerance != "0" else None
    rel_config = (
        ToleranceConfig.parse_rel(rel_tolerance)
        if rel_tolerance and rel_tolerance != "0"
        else None
    )

    if abs_config and rel_config:
        return abs_config.merge(rel_config)
    return abs_config or rel_config


# Sentinel date used by the auto-generated dummy partition filter. Any value
# *other* than this date passes the filter — picking a date in the distant past
# minimises the chance of dropping real rows. NULLs are preserved separately
# via an explicit IS NULL branch (see _build_dummy_partition_filter).
DEFAULT_PARTITION_SENTINEL_DATE = "1979-01-01"


def _build_dummy_partition_filter(
    partition_col: str,
    sentinel_date: str = DEFAULT_PARTITION_SENTINEL_DATE,
) -> str:
    """Build a NULL-safe dummy filter satisfying BQ's partition requirement.

    BigQuery requires queries against partition-filtered tables to reference
    the partition column in WHERE. A naive ``DATE(col) != 'sentinel'`` works
    for non-null partition values but silently drops rows in the ``__NULL__``
    partition, because ``NULL != 'sentinel'`` evaluates to NULL, not TRUE.

    The ``IS NULL`` branch is recognised by BQ's partition elimination as
    targeting the NULL partition, so this stays cheap.
    """
    return (
        f"({partition_col} IS NULL "
        f"OR DATE({partition_col}) != '{sentinel_date}')"
    )


def get_partition_filters(
    client: bigquery.Client,
    table_a: str,
    table_b: str,
    partition_filter_a: str | None,
    partition_filter_b: str | None,
) -> tuple[str | None, str | None]:
    """
    Get partition filters for both tables.

    If user provides filters, use those. Otherwise, auto-detect partition fields
    and create dummy filters that satisfy partition elimination requirements
    while preserving rows in the NULL partition.

    Auto-detection is best-effort: if it fails (e.g. due to cross-project
    permissions), we proceed without a filter and warn the user.
    """
    from table_identical_checks.backend import get_partition_field

    # Handle table A
    final_filter_a = partition_filter_a
    if final_filter_a is None:
        try:
            partition_col_a = get_partition_field(client, table_a)
            if partition_col_a:
                final_filter_a = _build_dummy_partition_filter(partition_col_a)
                click.echo(f"Auto-detected partition column '{partition_col_a}' for table A")
        except Exception as e:
            click.echo(
                f"Warning: Could not auto-detect partition for table A "
                f"({e.__class__.__name__}). Proceeding without partition filter.",
                err=True,
            )

    # Handle table B
    final_filter_b = partition_filter_b
    if final_filter_b is None:
        try:
            partition_col_b = get_partition_field(client, table_b)
            if partition_col_b:
                final_filter_b = _build_dummy_partition_filter(partition_col_b)
                click.echo(f"Auto-detected partition column '{partition_col_b}' for table B")
        except Exception as e:
            click.echo(
                f"Warning: Could not auto-detect partition for table B "
                f"({e.__class__.__name__}). Proceeding without partition filter.",
                err=True,
            )

    return final_filter_a, final_filter_b


def _log_client_info(client: bigquery.Client) -> None:
    """Log BigQuery client info for debugging."""
    click.secho("--- BQ Client Info ---", fg="cyan")
    click.secho(f"  Project: {client.project}", fg="cyan")

    creds = client._credentials
    cred_type = type(creds).__name__
    click.secho(f"  Credential type: {cred_type}", fg="cyan")

    # Try to extract identity info depending on credential type
    if hasattr(creds, "service_account_email"):
        click.secho(
            f"  Service account: {creds.service_account_email}", fg="cyan"
        )
    elif hasattr(creds, "signer_email"):
        click.secho(f"  Signer email: {creds.signer_email}", fg="cyan")

    # Check what the default credentials resolve to
    env_creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    click.secho(
        f"  GOOGLE_APPLICATION_CREDENTIALS: {env_creds or '(not set)'}",
        fg="cyan",
    )

    # Check for ADC
    try:
        default_creds, default_project = google.auth.default()
        click.secho(
            f"  ADC credential type: {type(default_creds).__name__}",
            fg="cyan",
        )
        click.secho(
            f"  ADC project: {default_project or '(none)'}",
            fg="cyan",
        )
        if hasattr(default_creds, "service_account_email"):
            click.secho(
                f"  ADC service account: {default_creds.service_account_email}",
                fg="cyan",
            )
    except Exception as e:
        click.secho(f"  ADC lookup failed: {e}", fg="yellow")

    click.secho("----------------------", fg="cyan")


def _warn_excluded_columns(builder: QueryBuilder) -> None:
    """Print a prominent warning if any columns were excluded due to unsupported types."""
    excluded = builder.excluded_columns
    if not excluded:
        return

    click.echo("")
    click.secho("!" * 60, fg="yellow", bold=True)
    click.secho("!! WARNING: Excluded columns (unsupported types) !!", fg="yellow", bold=True)
    click.secho("!" * 60, fg="yellow", bold=True)
    for col_info in excluded:
        click.secho(f"  {col_info.name:<30} {col_info.bq_type}", fg="yellow")
    click.secho("!" * 60, fg="yellow", bold=True)
    click.echo("")


def _intersect_table_schemas(
    client: bigquery.Client,
    table_a: str,
    table_b: str,
    key_columns: Sequence[str],
    kll_float64_cols: Sequence[str] | None = None,
    kll_int64_cols: Sequence[str] | None = None,
) -> list[ColumnInfo]:
    """Fetch schemas from both tables, return the intersection on name+type.

    Columns present in only one table, and columns whose ColumnType or bq_type
    differs between the two, are dropped from the comparison and reported via
    a yellow warning. Key columns are required on both sides — if any key is
    missing from either schema, a ``click.ClickException`` is raised.

    Returns:
        The list of ``ColumnInfo`` (from table A's schema) whose names appear
        on both sides with matching types. KLL reclassification has already
        been applied to A's columns.
    """
    click.echo(f"Fetching schema from {table_a}...")
    cols_a = get_table_schema(
        client,
        table_a,
        kll_float64_cols=kll_float64_cols,
        kll_int64_cols=kll_int64_cols,
    )
    click.echo(f"Fetching schema from {table_b}...")
    cols_b = get_table_schema(
        client,
        table_b,
        kll_float64_cols=kll_float64_cols,
        kll_int64_cols=kll_int64_cols,
    )

    by_name_a = {c.name: c for c in cols_a}
    by_name_b = {c.name: c for c in cols_b}

    only_in_a = [c for c in cols_a if c.name not in by_name_b]
    only_in_b = [c for c in cols_b if c.name not in by_name_a]
    type_mismatches: list[tuple[ColumnInfo, ColumnInfo]] = []
    common: list[ColumnInfo] = []
    for col in cols_a:
        other = by_name_b.get(col.name)
        if other is None:
            continue
        if col.column_type != other.column_type or col.bq_type != other.bq_type:
            type_mismatches.append((col, other))
            continue
        common.append(col)

    # Hard error: every requested key must exist on both sides.
    missing_keys_a = [k for k in key_columns if k not in by_name_a]
    missing_keys_b = [k for k in key_columns if k not in by_name_b]
    if missing_keys_a or missing_keys_b:
        msg_parts = []
        if missing_keys_a:
            msg_parts.append(f"missing in {table_a}: {missing_keys_a}")
        if missing_keys_b:
            msg_parts.append(f"missing in {table_b}: {missing_keys_b}")
        raise click.ClickException(
            "Key column(s) not found in both schemas — " + "; ".join(msg_parts)
        )

    if only_in_a or only_in_b or type_mismatches:
        click.echo("")
        click.secho("!" * 60, fg="yellow", bold=True)
        click.secho(
            "!! WARNING: Schema mismatch — columns excluded from comparison !!",
            fg="yellow",
            bold=True,
        )
        click.secho("!" * 60, fg="yellow", bold=True)
        for c in only_in_a:
            click.secho(f"  only in A    {c.name:<30} {c.bq_type}", fg="yellow")
        for c in only_in_b:
            click.secho(f"  only in B    {c.name:<30} {c.bq_type}", fg="yellow")
        for ca, cb in type_mismatches:
            click.secho(
                f"  type diff    {ca.name:<30} A={ca.bq_type}  B={cb.bq_type}",
                fg="yellow",
            )
        click.secho("!" * 60, fg="yellow", bold=True)
        click.echo("")

    return common


def _rows_to_dicts(rows: Sequence[bigquery.Row]) -> list[dict[str, object]]:
    """Convert BigQuery Row objects to plain dicts, preserving full float precision."""
    return [dict(row) for row in rows]


def _format_rows_table(rows: Sequence[dict[str, object]]) -> str:
    """Format a sequence of row dicts as an aligned table string using tabulate.

    Float values are rendered with repr() to preserve full precision.
    """
    if not rows:
        return ""

    headers = list(rows[0].keys())

    def _format_value(val: object) -> str:
        if isinstance(val, float):
            return repr(val)
        return str(val)

    table_rows = [
        [_format_value(row.get(h)) for h in headers]
        for row in rows
    ]

    return tabulate(table_rows, headers=headers, tablefmt="simple", disable_numparse=True)


def _write_diff_tempfile(formatted_table: str, row_count: int) -> str:
    """Write the full formatted diff table to a temp file.

    Returns the file path.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = f"/tmp/table-check-diff-{timestamp}.txt"
    with open(path, "w") as f:
        f.write(f"Full diff output ({row_count} rows)\n")
        f.write(f"Generated: {datetime.now(timezone.utc).isoformat()}\n\n")
        f.write(formatted_table)
        f.write("\n")
    return path


def _display_diff_results(
    rows: Sequence[bigquery.Row],
    max_display_rows: int,
) -> None:
    """Display diff results as a formatted table.

    - Prints up to max_display_rows to stdout.
    - Writes ALL rows to a temp file for full inspection.
    - Prints the temp file path.
    """
    all_dicts = _rows_to_dicts(rows)
    total = len(all_dicts)

    # Format ALL rows for the temp file
    full_table = _format_rows_table(all_dicts)
    temp_path = _write_diff_tempfile(full_table, total)

    # Format and display limited rows to stdout
    display_dicts = all_dicts[:max_display_rows]
    display_table = _format_rows_table(display_dicts)

    click.echo(display_table)

    if total > max_display_rows:
        click.echo(
            f"\n... {total - max_display_rows} more row(s) not shown "
            f"(showing {max_display_rows} of {total})"
        )

    click.echo(f"\nFull result written to: {temp_path}")
    click.echo("Inspect with: less -S " + temp_path)


@click.group()
@click.version_option()
def main():
    """Table Identical Checks - Compare BigQuery tables."""
    pass


@main.command()
@click.option("--table-a", required=True, help="First table (project.dataset.table)")
@click.option("--table-b", required=True, help="Second table (project.dataset.table)")
@click.option("--keys", required=True, help="Comma-separated key columns for joining")
@click.option("--credentials", envvar="GOOGLE_APPLICATION_CREDENTIALS", help="Path to SA JSON")
@click.option("--partition-filter-a", default=None, help="Partition filter for table A")
@click.option("--partition-filter-b", default=None, help="Partition filter for table B")
@click.option(
    "--tolerance",
    default=None,
    help="Absolute tolerance for floats (default: 1e-15). Pass '0' to disable. (e.g., '1e-9' or 'col1:1e-9')",
)
@click.option(
    "--rel-tolerance",
    default=None,
    help="Relative tolerance for floats (default: 1e-12). Pass '0' to disable. (e.g., '1e-9' or 'col1:1e-9')",
)
@click.option("--dry-run", is_flag=True, help="Print query without executing")
@click.option("--limit", default=100, help="Max rows to return")
@click.option("--output-table", default=None, help="Write diff to this BQ table")
@click.option(
    "--write-mode",
    default="replace",
    type=click.Choice(["replace", "if_not_exists"]),
    help="DDL mode for --output-table",
)
@click.option("--expiration-hours", default=None, type=int, help="TTL for output table (hours)")
@click.option(
    "--only-diffs",
    is_flag=True,
    help="Show only key columns and columns with actual differences",
)
@click.option(
    "--max-display-rows",
    default=20,
    type=int,
    help="Max rows to display in stdout (default 20). Full result goes to a temp file.",
)
@click.option(
    "--kll-cols",
    default=None,
    help=(
        "Comma-separated BYTES columns to treat as KLL_QUANTILES.INIT_FLOAT64 "
        "sketches (compared via quantile-value comparison at 5 probes)"
    ),
)
@click.option(
    "--kll-int-cols",
    default=None,
    help="Comma-separated BYTES columns to treat as KLL_QUANTILES.INIT_INT64 sketches",
)
@click.option(
    "--kll-abs-tol",
    default=0.0,
    type=float,
    help=(
        "Absolute tolerance on extracted quantile values for KLL comparisons "
        "(default 0.0; raise for columns with a known scale)"
    ),
)
@click.option(
    "--kll-rel-tol",
    default=0.05,
    type=float,
    help=(
        "Relative tolerance on extracted quantile values for KLL comparisons "
        "(default 0.05; tighten for stricter equivalence)"
    ),
)
def diff(
    table_a: str,
    table_b: str,
    keys: str,
    credentials: str,
    partition_filter_a: str | None,
    partition_filter_b: str | None,
    tolerance: str | None,
    rel_tolerance: str | None,
    dry_run: bool,
    limit: int,
    output_table: str | None,
    write_mode: str,
    expiration_hours: int | None,
    only_diffs: bool,
    max_display_rows: int,
    kll_cols: str | None,
    kll_int_cols: str | None,
    kll_abs_tol: float,
    kll_rel_tol: float,
):
    """Compare two tables and show differences."""
    from .backend.pipeline import differing_columns, run_pipeline

    key_columns = [k.strip() for k in keys.split(",")]

    # Set credentials if provided
    if credentials:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials

    client = bigquery.Client()

    kll_float_names = _parse_csv_names(kll_cols)
    kll_int_names = _parse_csv_names(kll_int_cols)

    columns = _intersect_table_schemas(
        client,
        table_a,
        table_b,
        key_columns,
        kll_float64_cols=kll_float_names,
        kll_int64_cols=kll_int_names,
    )

    # Get partition filters (auto-detect or use provided)
    filter_a, filter_b = get_partition_filters(
        client, table_a, table_b, partition_filter_a, partition_filter_b
    )

    # Parse tolerance config
    tolerance_config = _parse_tolerance(tolerance, rel_tolerance)

    # Build the query builder
    builder = QueryBuilder(
        table_a=table_a,
        table_b=table_b,
        key_columns=key_columns,
        columns=columns,
        partition_filter_a=filter_a,
        partition_filter_b=filter_b,
        tolerance_config=tolerance_config,
        kll_abs_tol=kll_abs_tol,
        kll_rel_tol=kll_rel_tol,
    )
    _warn_excluded_columns(builder)

    # Determine column filter if --only-diffs is set
    columns_filter: list[str] | None = None
    if only_diffs:
        click.echo("Running pipeline to identify differing columns...")
        pipeline_result = run_pipeline(client, builder, PipelineConfig())
        columns_filter = differing_columns(pipeline_result.column_diff_counts)
        if not columns_filter:
            click.echo("Tables are identical (no differences found).")
            return
        click.echo(f"Columns with differences: {', '.join(columns_filter)}")

    # Branch: persist to table vs. print rows
    if output_table:
        ddl = builder.build_diff_table_statement(
            destination=output_table,
            write_mode=write_mode,
            expiration_hours=expiration_hours,
            columns_filter=columns_filter,
        )

        if dry_run:
            click.echo("\n--- Generated DDL ---")
            click.echo(ddl)
            return

        click.echo(f"Writing diff to {output_table}...")
        client.query(ddl).result()

        # Report row count
        count_result = client.query(f"SELECT COUNT(*) AS cnt FROM `{output_table}`").result()
        row_count = list(count_result)[0].cnt
        click.echo(f"Done. {row_count} row(s) written to {output_table}")
    else:
        query = builder.build_diff_query(columns_filter=columns_filter)

        if dry_run:
            click.echo("\n--- Generated Query ---")
            click.echo(query)
            return

        # Execute with limit
        query_with_limit = f"{query}\nLIMIT {limit}"
        click.echo("Executing diff query...")

        result = client.query(query_with_limit).result()
        rows = list(result)

        if not rows:
            click.echo("Tables are identical (no differences found).")
            return

        click.echo(f"\nFound {len(rows)} differing row(s):\n")
        _display_diff_results(rows, max_display_rows)


@main.command()
@click.option("--table-a", required=True, help="First table (project.dataset.table)")
@click.option("--table-b", required=True, help="Second table (project.dataset.table)")
@click.option("--keys", required=True, help="Comma-separated key columns for joining")
@click.option("--credentials", envvar="GOOGLE_APPLICATION_CREDENTIALS", help="Path to SA JSON")
@click.option("--partition-filter-a", default=None, help="Partition filter for table A")
@click.option("--partition-filter-b", default=None, help="Partition filter for table B")
@click.option(
    "--tolerance",
    default=None,
    help="Absolute tolerance for floats (default: 1e-15). Pass '0' to disable. (e.g., '1e-9' or 'col1:1e-9')",
)
@click.option(
    "--rel-tolerance",
    default=None,
    help="Relative tolerance for floats (default: 1e-12). Pass '0' to disable. (e.g., '1e-9' or 'col1:1e-9')",
)
def count(
    table_a: str,
    table_b: str,
    keys: str,
    credentials: str,
    partition_filter_a: str | None,
    partition_filter_b: str | None,
    tolerance: str | None,
    rel_tolerance: str | None,
):
    """Count the number of differing rows between two tables."""
    key_columns = [k.strip() for k in keys.split(",")]

    if credentials:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials

    client = bigquery.Client()

    columns = _intersect_table_schemas(client, table_a, table_b, key_columns)

    # Get partition filters
    filter_a, filter_b = get_partition_filters(
        client, table_a, table_b, partition_filter_a, partition_filter_b
    )

    # Parse tolerance config
    tolerance_config = _parse_tolerance(tolerance, rel_tolerance)

    builder = QueryBuilder(
        table_a=table_a,
        table_b=table_b,
        key_columns=key_columns,
        columns=columns,
        partition_filter_a=filter_a,
        partition_filter_b=filter_b,
        tolerance_config=tolerance_config,
    )
    _warn_excluded_columns(builder)

    query = builder.build_count_query()
    result = client.query(query).result()
    count_val = list(result)[0].diff_count

    if count_val == 0:
        click.echo("Tables are identical.")
    else:
        click.echo(f"Tables differ: {count_val} row(s) with differences.")


@main.command()
@click.option("--table-a", required=True, help="First table (project.dataset.table)")
@click.option("--table-b", required=True, help="Second table (project.dataset.table)")
@click.option("--keys", required=True, help="Comma-separated key columns for joining")
@click.option("--credentials", envvar="GOOGLE_APPLICATION_CREDENTIALS", help="Path to SA JSON")
@click.option("--partition-filter-a", default=None, help="Partition filter for table A")
@click.option("--partition-filter-b", default=None, help="Partition filter for table B")
@click.option(
    "--tolerance",
    default=None,
    help="Absolute tolerance for floats (default: 1e-15). Pass '0' to disable. (e.g., '1e-9' or 'col1:1e-9')",
)
@click.option(
    "--rel-tolerance",
    default=None,
    help="Relative tolerance for floats (default: 1e-12). Pass '0' to disable. (e.g., '1e-9' or 'col1:1e-9')",
)
@click.option(
    "--sort-columns",
    default="alphabetical",
    type=click.Choice(["alphabetical", "significance"], case_sensitive=False),
    help="Sort columns alphabetically or by significance (sum of abs relative deltas)",
)
@click.option(
    "--format",
    "output_format",
    default="verbose",
    type=click.Choice(["verbose", "table"], case_sensitive=False),
    help="Output format: verbose (default) or compact table",
)
@click.option(
    "--max-diff-pct",
    default=100.0,
    type=float,
    help=(
        "Circuit breaker: abort detailed stats if more than X%% of rows differ. "
        "Default 100 effectively disables the breaker; lower it to re-enable "
        "(e.g. --max-diff-pct=10)."
    ),
)
@click.option(
    "--legacy",
    is_flag=True,
    default=False,
    help="Use legacy multi-query path instead of pipeline",
)
@click.option(
    "--output-json",
    default=None,
    help=(
        "Write the ComparisonSummary to a JSON file (consumable by `format` and "
        "`verify-query`). Defaults to a deterministic path under "
        "$XDG_CACHE_HOME/table-check/ keyed on table_a + table_b + keys."
    ),
)
@click.option(
    "--kll-cols",
    default=None,
    help=(
        "Comma-separated BYTES columns to treat as KLL_QUANTILES.INIT_FLOAT64 "
        "sketches (compared via quantile-value comparison at 5 probes)"
    ),
)
@click.option(
    "--kll-int-cols",
    default=None,
    help="Comma-separated BYTES columns to treat as KLL_QUANTILES.INIT_INT64 sketches",
)
@click.option(
    "--kll-abs-tol",
    default=0.0,
    type=float,
    help=(
        "Absolute tolerance on extracted quantile values for KLL comparisons "
        "(default 0.0; raise for columns with a known scale)"
    ),
)
@click.option(
    "--kll-rel-tol",
    default=0.05,
    type=float,
    help=(
        "Relative tolerance on extracted quantile values for KLL comparisons "
        "(default 0.05; tighten for stricter equivalence)"
    ),
)
def summary(
    table_a: str,
    table_b: str,
    keys: str,
    credentials: str,
    partition_filter_a: str | None,
    partition_filter_b: str | None,
    tolerance: str | None,
    rel_tolerance: str | None,
    sort_columns: str,
    output_format: str,
    max_diff_pct: float,
    legacy: bool,
    output_json: str | None,
    kll_cols: str | None,
    kll_int_cols: str | None,
    kll_abs_tol: float,
    kll_rel_tol: float,
):
    """Generate a comprehensive comparison summary."""
    key_columns = [k.strip() for k in keys.split(",")]

    if credentials:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials

    client = bigquery.Client()
    _log_client_info(client)

    kll_float_names = _parse_csv_names(kll_cols)
    kll_int_names = _parse_csv_names(kll_int_cols)

    columns = _intersect_table_schemas(
        client,
        table_a,
        table_b,
        key_columns,
        kll_float64_cols=kll_float_names,
        kll_int64_cols=kll_int_names,
    )

    # Get partition filters
    filter_a, filter_b = get_partition_filters(
        client, table_a, table_b, partition_filter_a, partition_filter_b
    )

    # Parse tolerance config
    tolerance_config = _parse_tolerance(tolerance, rel_tolerance)

    builder = QueryBuilder(
        table_a=table_a,
        table_b=table_b,
        key_columns=key_columns,
        columns=columns,
        partition_filter_a=filter_a,
        partition_filter_b=filter_b,
        tolerance_config=tolerance_config,
        kll_abs_tol=kll_abs_tol,
        kll_rel_tol=kll_rel_tol,
    )
    _warn_excluded_columns(builder)

    pipeline_config = None if legacy else PipelineConfig(max_diff_pct=max_diff_pct / 100.0)

    mode = "legacy" if legacy else "pipeline"
    click.echo(f"Generating comparison summary ({mode} mode)...")
    result = generate_summary(
        client,
        builder,
        column_sort_order=sort_columns,
        output_format=output_format,
        pipeline_config=pipeline_config,
    )
    click.echo("")
    click.echo(str(result))

    json_path = output_json or _default_summary_json_path(table_a, table_b, key_columns)
    with open(json_path, "w") as f:
        json.dump(to_json_dict(result), f, indent=2, default=str)
    click.echo(f"\nSummary written to {json_path}")


@main.command("format")
@click.option("--input-json", required=True, help="Path to a summary JSON file")
@click.option(
    "--format",
    "output_format",
    default=None,
    type=click.Choice(["verbose", "table"], case_sensitive=False),
    help="Override output format (default: use value saved in JSON)",
)
@click.option(
    "--sort-columns",
    default=None,
    type=click.Choice(["alphabetical", "significance"], case_sensitive=False),
    help="Override column sort order",
)
def format_cmd(input_json: str, output_format: str | None, sort_columns: str | None):
    """Re-render a saved ComparisonSummary from JSON without rerunning BQ queries."""
    with open(input_json) as f:
        data = json.load(f)
    summary_obj = from_json_dict(data)
    if output_format:
        summary_obj.output_format = output_format
    if sort_columns:
        summary_obj.column_sort_order = sort_columns
    click.echo(str(summary_obj))


@main.command("verify-query")
@click.option("--input-json", required=True, help="Path to a summary JSON file")
def verify_query_cmd(input_json: str):
    """Emit an EXCEPT DISTINCT / UNION ALL verification query from a saved summary.

    The query includes columns that the comparison found equal (pre-tolerance)
    and excludes columns with differences, unsupported-type columns, and
    GEOGRAPHY columns (which are not groupable in BigQuery).
    """
    with open(input_json) as f:
        data = json.load(f)
    summary_obj = from_json_dict(data)
    click.echo(build_verify_query(summary_obj))


@main.command("breakdown")
@click.option("--table-a", required=True, help="First table (project.dataset.table)")
@click.option("--table-b", required=True, help="Second table (project.dataset.table)")
@click.option("--keys", required=True, help="Comma-separated key columns for joining")
@click.option("--dimension", required=True, help="Column to break down results by (e.g., date)")
@click.option("--delta-col", default=None, help="Numeric column to track max deltas for")
@click.option("--limit", default=None, type=int, help="Limit number of dimension buckets")
@click.option("--credentials", envvar="GOOGLE_APPLICATION_CREDENTIALS", help="Path to SA JSON")
@click.option("--partition-filter-a", default=None, help="Partition filter for table A")
@click.option("--partition-filter-b", default=None, help="Partition filter for table B")
@click.option(
    "--tolerance",
    default=None,
    help="Absolute tolerance for floats (default: 1e-15). Pass '0' to disable. (e.g., '1e-9' or 'col1:1e-9')",
)
@click.option(
    "--rel-tolerance",
    default=None,
    help="Relative tolerance for floats (default: 1e-12). Pass '0' to disable. (e.g., '1e-9' or 'col1:1e-9')",
)
def breakdown(
    table_a: str,
    table_b: str,
    keys: str,
    dimension: str,
    delta_col: str | None,
    limit: int | None,
    credentials: str,
    partition_filter_a: str | None,
    partition_filter_b: str | None,
    tolerance: str | None,
    rel_tolerance: str | None,
):
    """Generate comparison summary broken down by a dimension."""
    key_columns = [k.strip() for k in keys.split(",")]

    if credentials:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials

    client = bigquery.Client()

    columns = _intersect_table_schemas(client, table_a, table_b, key_columns)

    # Get partition filters
    filter_a, filter_b = get_partition_filters(
        client, table_a, table_b, partition_filter_a, partition_filter_b
    )

    # Parse tolerance config
    tolerance_config = _parse_tolerance(tolerance, rel_tolerance)

    builder = QueryBuilder(
        table_a=table_a,
        table_b=table_b,
        key_columns=key_columns,
        columns=columns,
        partition_filter_a=filter_a,
        partition_filter_b=filter_b,
        tolerance_config=tolerance_config,
    )
    _warn_excluded_columns(builder)

    click.echo(f"Generating breakdown by {dimension}...")
    result = generate_dimension_summary(
        client,
        builder,
        dimension_column=dimension,
        delta_column=delta_col,
        limit=limit,
    )
    click.echo("")
    click.echo(str(result))


if __name__ == "__main__":
    main()
