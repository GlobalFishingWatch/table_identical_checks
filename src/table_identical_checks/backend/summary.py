"""Summary statistics for table comparisons."""

from dataclasses import dataclass, field
from typing import Any

from google.cloud import bigquery
from sqlalchemy import func, select
from sqlalchemy_bigquery import BigQueryDialect

from .query_builder import QueryBuilder
from .schema import ColumnType


@dataclass
class ComparisonSummary:
    """Summary of a table comparison."""

    table_a: str
    table_b: str
    key_columns: list[str]

    # Row counts
    rows_only_in_a: int
    rows_only_in_b: int
    rows_in_both_with_differences: int
    rows_identical: int

    # Totals
    total_rows_a: int
    total_rows_b: int

    # Column-level summaries for numeric columns with differences
    numeric_column_stats: dict[str, dict]  # col -> {max_abs_delta, max_rel_delta, avg_abs_delta}

    # String columns with mismatches
    string_column_mismatches: dict[str, int]  # col -> count of mismatches

    @property
    def total_differences(self) -> int:
        return self.rows_only_in_a + self.rows_only_in_b + self.rows_in_both_with_differences

    @property
    def tables_identical(self) -> bool:
        return self.total_differences == 0

    def __str__(self) -> str:
        lines = [
            "=" * 60,
            "TABLE COMPARISON SUMMARY",
            "=" * 60,
            f"Table A: {self.table_a}",
            f"Table B: {self.table_b}",
            f"Key columns: {', '.join(self.key_columns)}",
            "",
            "ROW COUNTS",
            "-" * 40,
            f"Total rows in A: {self.total_rows_a:,}",
            f"Total rows in B: {self.total_rows_b:,}",
            "",
            f"Rows only in A: {self.rows_only_in_a:,}",
            f"Rows only in B: {self.rows_only_in_b:,}",
            f"Rows in both with differences: {self.rows_in_both_with_differences:,}",
            f"Rows identical: {self.rows_identical:,}",
            "",
            f"TOTAL DIFFERENCES: {self.total_differences:,}",
        ]

        if self.numeric_column_stats:
            lines.extend(
                [
                    "",
                    "NUMERIC COLUMN DELTAS",
                    "-" * 40,
                ]
            )
            for col, stats in self.numeric_column_stats.items():
                lines.append(f"  {col}:")
                lines.append(f"    max_abs_delta: {stats['max_abs_delta']}")
                lines.append(f"    max_rel_delta: {stats['max_rel_delta']}")
                lines.append(f"    avg_abs_delta: {stats['avg_abs_delta']}")

        if self.string_column_mismatches:
            lines.extend(
                [
                    "",
                    "STRING COLUMN MISMATCHES",
                    "-" * 40,
                ]
            )
            for col, count in self.string_column_mismatches.items():
                lines.append(f"  {col}: {count:,} mismatches")

        lines.extend(
            [
                "",
                "=" * 60,
                f"RESULT: {'IDENTICAL' if self.tables_identical else 'DIFFERENCES FOUND'}",
                "=" * 60,
            ]
        )

        return "\n".join(lines)


def generate_summary(
    client: bigquery.Client,
    builder: QueryBuilder,
) -> ComparisonSummary:
    """
    Generate a comprehensive summary of differences between two tables.

    Args:
        client: BigQuery client
        builder: QueryBuilder configured for the comparison

    Returns:
        ComparisonSummary with all statistics
    """
    # Get total row counts for each table using SQLAlchemy
    # Use the same filtered table objects as in the diff query
    table_a_obj, table_b_obj = builder.get_table_objects()
    
    count_a_stmt = select(func.count().label("cnt")).select_from(table_a_obj)
    count_b_stmt = select(func.count().label("cnt")).select_from(table_b_obj)
    
    count_a_query = str(count_a_stmt.compile(dialect=BigQueryDialect(), compile_kwargs={"literal_binds": True}))
    count_b_query = str(count_b_stmt.compile(dialect=BigQueryDialect(), compile_kwargs={"literal_binds": True}))

    total_rows_a = list(client.query(count_a_query).result())[0].cnt
    total_rows_b = list(client.query(count_b_query).result())[0].cnt

    # Build the diff query and wrap it for summary statistics
    diff_query = builder.build_diff_query()

    summary_query = f"""
    WITH diff AS (
        {diff_query}
    )
    SELECT
        COUNTIF(in_a AND NOT in_b) AS rows_only_in_a,
        COUNTIF(NOT in_a AND in_b) AS rows_only_in_b,
        COUNTIF(in_a AND in_b) AS rows_in_both_with_differences
    FROM diff
    """

    result = list(client.query(summary_query).result())[0]
    rows_only_in_a = result.rows_only_in_a
    rows_only_in_b = result.rows_only_in_b
    rows_in_both_with_differences = result.rows_in_both_with_differences

    # Calculate identical rows
    rows_identical = min(total_rows_a, total_rows_b) - rows_in_both_with_differences

    # Get numeric column statistics
    numeric_column_stats = {}
    string_column_mismatches = {}

    value_columns = [c for c in builder.columns if c.name not in builder.key_columns]

    for col in value_columns:
        if col.column_type in (ColumnType.INTEGER, ColumnType.FLOAT, ColumnType.BOOLEAN, ColumnType.TIMESTAMP):
            # Numeric-like columns: INTEGER, FLOAT, BOOLEAN (as INT64), TIMESTAMP (as seconds)
            stats_query = f"""
            WITH diff AS (
                {diff_query}
            )
            SELECT
                MAX({col.name}__abs_delta) AS max_abs_delta,
                MAX(ABS({col.name}__rel_delta)) AS max_rel_delta,
                AVG({col.name}__abs_delta) AS avg_abs_delta
            FROM diff
            WHERE in_a AND in_b
            """
            stats_result = list(client.query(stats_query).result())[0]
            if stats_result.max_abs_delta is not None:
                numeric_column_stats[col.name] = {
                    "max_abs_delta": stats_result.max_abs_delta,
                    "max_rel_delta": stats_result.max_rel_delta,
                    "avg_abs_delta": stats_result.avg_abs_delta,
                }

        elif col.column_type == ColumnType.STRING:
            mismatch_query = f"""
            WITH diff AS (
                {diff_query}
            )
            SELECT COUNT(*) AS mismatches
            FROM diff
            WHERE in_a AND in_b AND NOT {col.name}__match
            """
            mismatch_result = list(client.query(mismatch_query).result())[0]
            if mismatch_result.mismatches > 0:
                string_column_mismatches[col.name] = mismatch_result.mismatches

    return ComparisonSummary(
        table_a=builder.table_a,
        table_b=builder.table_b,
        key_columns=builder.key_columns,
        rows_only_in_a=rows_only_in_a,
        rows_only_in_b=rows_only_in_b,
        rows_in_both_with_differences=rows_in_both_with_differences,
        rows_identical=rows_identical,
        total_rows_a=total_rows_a,
        total_rows_b=total_rows_b,
        numeric_column_stats=numeric_column_stats,
        string_column_mismatches=string_column_mismatches,
    )


@dataclass
class DimensionBucket:
    """Statistics for a single dimension value."""

    dimension_value: Any
    rows_only_in_a: int
    rows_only_in_b: int
    rows_with_differences: int
    total_differences: int

    # Optional: max absolute delta for a specific column of interest
    max_abs_delta: float | None = None
    max_rel_delta: float | None = None


@dataclass
class DimensionSummary:
    """Summary broken down by a dimension column."""

    table_a: str
    table_b: str
    key_columns: list[str]
    dimension_column: str
    delta_column: str | None  # Column to track deltas for

    buckets: list[DimensionBucket] = field(default_factory=list)

    # Overall totals
    total_rows_only_in_a: int = 0
    total_rows_only_in_b: int = 0
    total_rows_with_differences: int = 0

    @property
    def total_differences(self) -> int:
        return (
            self.total_rows_only_in_a + self.total_rows_only_in_b + self.total_rows_with_differences
        )

    @property
    def tables_identical(self) -> bool:
        return self.total_differences == 0

    def buckets_with_differences(self) -> list[DimensionBucket]:
        """Return only buckets that have differences."""
        return [b for b in self.buckets if b.total_differences > 0]

    def __str__(self) -> str:
        lines = [
            "=" * 80,
            "DIMENSION BREAKDOWN SUMMARY",
            "=" * 80,
            f"Table A: {self.table_a}",
            f"Table B: {self.table_b}",
            f"Key columns: {', '.join(self.key_columns)}",
            f"Dimension: {self.dimension_column}",
        ]
        if self.delta_column:
            lines.append(f"Delta column: {self.delta_column}")

        lines.extend(
            [
                "",
                "OVERALL TOTALS",
                "-" * 40,
                f"Total rows only in A: {self.total_rows_only_in_a:,}",
                f"Total rows only in B: {self.total_rows_only_in_b:,}",
                f"Total rows with value differences: {self.total_rows_with_differences:,}",
                f"TOTAL DIFFERENCES: {self.total_differences:,}",
            ]
        )

        diff_buckets = self.buckets_with_differences()
        if diff_buckets:
            lines.extend(
                [
                    "",
                    f"BREAKDOWN BY {self.dimension_column.upper()} (only showing differences)",
                    "-" * 80,
                ]
            )

            # Header
            if self.delta_column:
                lines.append(
                    f"{'Dimension':<20} {'Only A':>10} {'Only B':>10} {'Diff':>10} "
                    f"{'Total':>10} {'Max Abs':>15} {'Max Rel':>15}"
                )
                lines.append("-" * 80)
                for bucket in diff_buckets:
                    max_abs = f"{bucket.max_abs_delta:.6g}" if bucket.max_abs_delta else "N/A"
                    max_rel = f"{bucket.max_rel_delta:.6g}" if bucket.max_rel_delta else "N/A"
                    lines.append(
                        f"{str(bucket.dimension_value):<20} "
                        f"{bucket.rows_only_in_a:>10,} "
                        f"{bucket.rows_only_in_b:>10,} "
                        f"{bucket.rows_with_differences:>10,} "
                        f"{bucket.total_differences:>10,} "
                        f"{max_abs:>15} "
                        f"{max_rel:>15}"
                    )
            else:
                lines.append(
                    f"{'Dimension':<20} {'Only A':>10} {'Only B':>10} {'Diff':>10} {'Total':>10}"
                )
                lines.append("-" * 80)
                for bucket in diff_buckets:
                    lines.append(
                        f"{str(bucket.dimension_value):<20} "
                        f"{bucket.rows_only_in_a:>10,} "
                        f"{bucket.rows_only_in_b:>10,} "
                        f"{bucket.rows_with_differences:>10,} "
                        f"{bucket.total_differences:>10,}"
                    )
        else:
            lines.extend(
                [
                    "",
                    "No differences found in any dimension bucket.",
                ]
            )

        lines.extend(
            [
                "",
                "=" * 80,
                f"RESULT: {'IDENTICAL' if self.tables_identical else 'DIFFERENCES FOUND'}",
                "=" * 80,
            ]
        )

        return "\n".join(lines)


def generate_dimension_summary(
    client: bigquery.Client,
    builder: QueryBuilder,
    dimension_column: str,
    delta_column: str | None = None,
    limit: int | None = None,
) -> DimensionSummary:
    """
    Generate a comparison summary broken down by a dimension column.

    Args:
        client: BigQuery client
        builder: QueryBuilder configured for the comparison
        dimension_column: Column to group results by (e.g., 'date', 'timestamp')
        delta_column: Optional numeric column to track max deltas for
        limit: Optional limit on number of dimension buckets to return

    Returns:
        DimensionSummary with per-dimension statistics
    """
    diff_query = builder.build_diff_query()

    # Build aggregation query grouped by dimension
    delta_selects = ""
    if delta_column:
        # Verify the column exists and is numeric-like
        col_info = next((c for c in builder.columns if c.name == delta_column), None)
        if col_info and col_info.column_type in (ColumnType.INTEGER, ColumnType.FLOAT, ColumnType.BOOLEAN, ColumnType.TIMESTAMP):
            delta_selects = f"""
                MAX({delta_column}__abs_delta) AS max_abs_delta,
                MAX(ABS({delta_column}__rel_delta)) AS max_rel_delta,
            """

    order_clause = f"ORDER BY {dimension_column}"
    limit_clause = f"LIMIT {limit}" if limit else ""

    summary_query = f"""
    WITH diff AS (
        {diff_query}
    )
    SELECT
        {dimension_column} AS dimension_value,
        COUNTIF(in_a AND NOT in_b) AS rows_only_in_a,
        COUNTIF(NOT in_a AND in_b) AS rows_only_in_b,
        COUNTIF(in_a AND in_b) AS rows_with_differences,
        {delta_selects}
        COUNT(*) AS total_differences
    FROM diff
    GROUP BY {dimension_column}
    {order_clause}
    {limit_clause}
    """

    result = client.query(summary_query).result()

    buckets = []
    total_only_a = 0
    total_only_b = 0
    total_with_diff = 0

    for row in result:
        bucket = DimensionBucket(
            dimension_value=row.dimension_value,
            rows_only_in_a=row.rows_only_in_a,
            rows_only_in_b=row.rows_only_in_b,
            rows_with_differences=row.rows_with_differences,
            total_differences=row.total_differences,
            max_abs_delta=getattr(row, "max_abs_delta", None),
            max_rel_delta=getattr(row, "max_rel_delta", None),
        )
        buckets.append(bucket)
        total_only_a += row.rows_only_in_a
        total_only_b += row.rows_only_in_b
        total_with_diff += row.rows_with_differences

    return DimensionSummary(
        table_a=builder.table_a,
        table_b=builder.table_b,
        key_columns=builder.key_columns,
        dimension_column=dimension_column,
        delta_column=delta_column,
        buckets=buckets,
        total_rows_only_in_a=total_only_a,
        total_rows_only_in_b=total_only_b,
        total_rows_with_differences=total_with_diff,
    )
