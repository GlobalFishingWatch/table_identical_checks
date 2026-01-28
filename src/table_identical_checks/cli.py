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
@click.option("--dry-run", is_flag=True, help="Print query without executing")
@click.option("--limit", default=100, help="Max rows to return")
def diff(table_a: str, table_b: str, keys: str, credentials: str, dry_run: bool, limit: int):
    """Compare two tables and show differences."""
    key_columns = [k.strip() for k in keys.split(",")]

    # Set credentials if provided
    if credentials:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials

    client = bigquery.Client()

    # Get schema from table_a (assuming both have same schema)
    click.echo(f"Fetching schema from {table_a}...")
    columns = get_table_schema(client, table_a)

    # Build the diff query
    builder = QueryBuilder(
        table_a=table_a,
        table_b=table_b,
        key_columns=key_columns,
        columns=columns,
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
def count(table_a: str, table_b: str, keys: str, credentials: str):
    """Count the number of differing rows between two tables."""
    key_columns = [k.strip() for k in keys.split(",")]

    if credentials:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials

    client = bigquery.Client()

    columns = get_table_schema(client, table_a)

    builder = QueryBuilder(
        table_a=table_a,
        table_b=table_b,
        key_columns=key_columns,
        columns=columns,
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
def summary(table_a: str, table_b: str, keys: str, credentials: str):
    """Generate a comprehensive comparison summary."""
    key_columns = [k.strip() for k in keys.split(",")]

    if credentials:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials

    client = bigquery.Client()

    click.echo(f"Fetching schema from {table_a}...")
    columns = get_table_schema(client, table_a)

    builder = QueryBuilder(
        table_a=table_a,
        table_b=table_b,
        key_columns=key_columns,
        columns=columns,
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
def breakdown(
    table_a: str,
    table_b: str,
    keys: str,
    dimension: str,
    delta_col: str | None,
    limit: int | None,
    credentials: str,
):
    """Generate comparison summary broken down by a dimension."""
    key_columns = [k.strip() for k in keys.split(",")]

    if credentials:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials

    client = bigquery.Client()

    click.echo(f"Fetching schema from {table_a}...")
    columns = get_table_schema(client, table_a)

    builder = QueryBuilder(
        table_a=table_a,
        table_b=table_b,
        key_columns=key_columns,
        columns=columns,
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
