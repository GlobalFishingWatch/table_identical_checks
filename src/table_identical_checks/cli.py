"""Command-line interface for table identical checks."""

import os

import click
from google.cloud import bigquery

from .backend import (
    QueryBuilder,
    generate_dimension_summary,
    generate_summary,
    get_table_schema,
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
    and create dummy filters that satisfy partition elimination requirements.
    
    Args:
        client: BigQuery client
        table_a: First table reference
        table_b: Second table reference
        partition_filter_a: User-provided filter for table A (or None)
        partition_filter_b: User-provided filter for table B (or None)
        
    Returns:
        Tuple of (filter_a, filter_b) where each can be None if no partition
    """
    from table_identical_checks.backend import get_partition_field
    
    # Handle table A
    final_filter_a = partition_filter_a
    if final_filter_a is None:
        partition_col_a = get_partition_field(client, table_a)
        if partition_col_a:
            final_filter_a = f"DATE({partition_col_a}) != '1979-01-01'"
            click.echo(f"Auto-detected partition column '{partition_col_a}' for table A")
    
    # Handle table B
    final_filter_b = partition_filter_b
    if final_filter_b is None:
        partition_col_b = get_partition_field(client, table_b)
        if partition_col_b:
            final_filter_b = f"DATE({partition_col_b}) != '1979-01-01'"
            click.echo(f"Auto-detected partition column '{partition_col_b}' for table B")
    
    return final_filter_a, final_filter_b


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
@click.option("--dry-run", is_flag=True, help="Print query without executing")
@click.option("--limit", default=100, help="Max rows to return")
def diff(
    table_a: str,
    table_b: str,
    keys: str,
    credentials: str,
    partition_filter_a: str | None,
    partition_filter_b: str | None,
    dry_run: bool,
    limit: int,
):
    """Compare two tables and show differences."""
    key_columns = [k.strip() for k in keys.split(",")]

    # Set credentials if provided
    if credentials:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials

    client = bigquery.Client()

    # Get schema from table_a (assuming both have same schema)
    click.echo(f"Fetching schema from {table_a}...")
    columns = get_table_schema(client, table_a)

    # Get partition filters (auto-detect or use provided)
    filter_a, filter_b = get_partition_filters(
        client, table_a, table_b, partition_filter_a, partition_filter_b
    )

    # Build the diff query
    builder = QueryBuilder(
        table_a=table_a,
        table_b=table_b,
        key_columns=key_columns,
        columns=columns,
        partition_filter_a=filter_a,
        partition_filter_b=filter_b,
    )

    query = builder.build_diff_query()

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

    # Print results
    for row in rows:
        click.echo(dict(row))


@main.command()
@click.option("--table-a", required=True, help="First table (project.dataset.table)")
@click.option("--table-b", required=True, help="Second table (project.dataset.table)")
@click.option("--keys", required=True, help="Comma-separated key columns for joining")
@click.option("--credentials", envvar="GOOGLE_APPLICATION_CREDENTIALS", help="Path to SA JSON")
@click.option("--partition-filter-a", default=None, help="Partition filter for table A")
@click.option("--partition-filter-b", default=None, help="Partition filter for table B")
def count(
    table_a: str,
    table_b: str,
    keys: str,
    credentials: str,
    partition_filter_a: str | None,
    partition_filter_b: str | None,
):
    """Count the number of differing rows between two tables."""
    key_columns = [k.strip() for k in keys.split(",")]

    if credentials:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials

    client = bigquery.Client()

    columns = get_table_schema(client, table_a)

    # Get partition filters
    filter_a, filter_b = get_partition_filters(
        client, table_a, table_b, partition_filter_a, partition_filter_b
    )

    builder = QueryBuilder(
        table_a=table_a,
        table_b=table_b,
        key_columns=key_columns,
        columns=columns,
        partition_filter_a=filter_a,
        partition_filter_b=filter_b,
    )

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
def summary(
    table_a: str,
    table_b: str,
    keys: str,
    credentials: str,
    partition_filter_a: str | None,
    partition_filter_b: str | None,
):
    """Generate a comprehensive comparison summary."""
    key_columns = [k.strip() for k in keys.split(",")]

    if credentials:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials

    client = bigquery.Client()

    click.echo(f"Fetching schema from {table_a}...")
    columns = get_table_schema(client, table_a)

    # Get partition filters
    filter_a, filter_b = get_partition_filters(
        client, table_a, table_b, partition_filter_a, partition_filter_b
    )

    builder = QueryBuilder(
        table_a=table_a,
        table_b=table_b,
        key_columns=key_columns,
        columns=columns,
        partition_filter_a=filter_a,
        partition_filter_b=filter_b,
    )

    click.echo("Generating comparison summary...")
    result = generate_summary(client, builder)
    click.echo("")
    click.echo(str(result))


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
):
    """Generate comparison summary broken down by a dimension."""
    key_columns = [k.strip() for k in keys.split(",")]

    if credentials:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials

    client = bigquery.Client()

    click.echo(f"Fetching schema from {table_a}...")
    columns = get_table_schema(client, table_a)

    # Get partition filters
    filter_a, filter_b = get_partition_filters(
        client, table_a, table_b, partition_filter_a, partition_filter_b
    )

    builder = QueryBuilder(
        table_a=table_a,
        table_b=table_b,
        key_columns=key_columns,
        columns=columns,
        partition_filter_a=filter_a,
        partition_filter_b=filter_b,
    )

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
