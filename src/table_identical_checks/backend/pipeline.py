"""Pipeline orchestrator for multi-layer query execution."""

from dataclasses import dataclass

from google.cloud import bigquery

from .query_builder import QueryBuilder
from .schema import ColumnType


@dataclass
class PipelineConfig:
    """Configuration for the multi-layer pipeline.

    Attributes:
        max_diff_pct: Circuit breaker threshold as a fraction (0.1 = 10%).
                     If more than this fraction of rows differ, abort after Layer 1.
                     Default 1.0 (100%) effectively disables the breaker.
    """

    max_diff_pct: float = 1.0


@dataclass
class PipelineResult:
    """Result of the multi-layer pipeline execution.

    Attributes:
        pipeline_status: "COMPLETED" if all layers ran, "ABORTED" if circuit breaker triggered.
        total_rows_a: Total row count in table A.
        total_rows_b: Total row count in table B.
        rows_only_in_a: Rows present only in table A.
        rows_only_in_b: Rows present only in table B.
        rows_in_both_with_differences: Rows present in both but with at least one column diff.
        column_diff_counts: Per-column diff count from Layer 1 (always available).
        numeric_column_stats: Per-column aggregate stats for numeric cols (None if aborted).
        string_column_mismatches: Per-column mismatch count for string cols (None if aborted).
        geography_column_stats: Per-column distance stats for geography cols (None if aborted).
        array_column_stats: Per-column array stats (mismatch count, len delta). None if aborted.
        post_tolerance_diff_count: Rows where NOT all toleranced cols are within
            tol (None if aborted or no tolerance).
        total_differing_rows: Count of rows in _l2 (rows in both with diffs). None if aborted.
    """

    pipeline_status: str
    total_rows_a: int
    total_rows_b: int
    rows_only_in_a: int
    rows_only_in_b: int
    rows_in_both_with_differences: int
    column_diff_counts: dict[str, int]
    numeric_column_stats: dict[str, dict] | None = None
    string_column_mismatches: dict[str, int] | None = None
    geography_column_stats: dict[str, dict] | None = None
    array_column_stats: dict[str, dict] | None = None
    post_tolerance_diff_count: int | None = None
    total_differing_rows: int | None = None


def differing_columns(column_diff_counts: dict[str, int]) -> list[str]:
    """Return column names that have at least one difference.

    Args:
        column_diff_counts: Per-column diff counts from PipelineResult.column_diff_counts.

    Returns:
        List of column names where diff_count > 0.
    """
    return [col for col, count in column_diff_counts.items() if count > 0]


def _parse_pipeline_result(row: bigquery.Row, builder: QueryBuilder) -> PipelineResult:
    """Parse a BigQuery result row into a PipelineResult.

    The row schema depends on whether the pipeline completed or was aborted.
    """
    row_dict = dict(row)
    status = row_dict["pipeline_status"]

    value_cols = builder._value_columns()

    def _alias(col_name: str, suffix: str) -> str:
        """Build the mangled alias used in the pipeline SQL output columns."""
        return f"{col_name.replace('.', '__')}__{suffix}"

    # Column diff counts (always present from Layer 1)
    column_diff_counts = {col.name: row_dict[_alias(col.name, "diff_count")] for col in value_cols}

    result = PipelineResult(
        pipeline_status=status,
        total_rows_a=row_dict["total_rows_a"],
        total_rows_b=row_dict["total_rows_b"],
        rows_only_in_a=row_dict["rows_only_in_a"],
        rows_only_in_b=row_dict["rows_only_in_b"],
        rows_in_both_with_differences=row_dict["rows_in_both_with_differences"],
        column_diff_counts=column_diff_counts,
    )

    if status == "ABORTED":
        return result

    # Build tolerance config lookup
    tol_cols: set[str] = set()
    if builder.tolerance_config:
        for col in value_cols:
            if col.column_type == ColumnType.FLOAT:
                if builder.tolerance_config.has_any_tolerance(col.name):
                    tol_cols.add(col.name)
            elif col.column_type == ColumnType.GEOGRAPHY:
                if builder.tolerance_config.get_tolerance(col.name) is not None:
                    tol_cols.add(col.name)

    # Parse Layer 3 stats
    numeric_stats: dict[str, dict] = {}
    string_mismatches: dict[str, int] = {}
    geography_stats: dict[str, dict] = {}
    array_stats: dict[str, dict] = {}

    for col in value_cols:
        # Skip columns that Layer 1 identified as fully identical
        if column_diff_counts.get(col.name, 0) == 0:
            continue

        if col.column_type in (
            ColumnType.INTEGER,
            ColumnType.FLOAT,
            ColumnType.BOOLEAN,
            ColumnType.TIMESTAMP,
            ColumnType.DATE,
        ):
            max_abs = row_dict.get(_alias(col.name, "max_abs_delta"))
            if max_abs is not None:
                stats: dict = {
                    "max_abs_delta": max_abs,
                    "max_rel_delta": row_dict.get(_alias(col.name, "max_rel_delta")),
                    "avg_abs_delta": row_dict.get(_alias(col.name, "avg_abs_delta")),
                    "sum_abs_rel_delta": row_dict.get(_alias(col.name, "sum_abs_rel_delta")),
                }
                if col.column_type == ColumnType.FLOAT and col.name in tol_cols:
                    stats["within_tolerance_count"] = row_dict.get(
                        _alias(col.name, "within_tol_count")
                    )
                    stats["outside_tolerance_count"] = row_dict.get(
                        _alias(col.name, "outside_tol_count")
                    )
                numeric_stats[col.name] = stats

        elif col.column_type == ColumnType.STRING:
            mismatches = row_dict.get(_alias(col.name, "mismatches"), 0)
            if mismatches > 0:
                string_mismatches[col.name] = mismatches

        elif col.column_type == ColumnType.GEOGRAPHY:
            max_dist = row_dict.get(_alias(col.name, "max_distance_m"))
            if max_dist is not None:
                geo_stats: dict = {
                    "max_distance_meters": max_dist,
                    "avg_distance_meters": row_dict.get(_alias(col.name, "avg_distance_m")),
                }
                if col.name in tol_cols:
                    geo_stats["within_tolerance_count"] = row_dict.get(
                        _alias(col.name, "within_tol_count")
                    )
                    geo_stats["outside_tolerance_count"] = row_dict.get(
                        _alias(col.name, "outside_tol_count")
                    )
                geography_stats[col.name] = geo_stats

        elif col.column_type == ColumnType.ARRAY:
            mismatch_count = row_dict.get(_alias(col.name, "mismatch_count"))
            if mismatch_count is not None and mismatch_count > 0:
                array_stats[col.name] = {
                    "mismatch_count": mismatch_count,
                    "max_abs_len_delta": row_dict.get(_alias(col.name, "max_abs_len_delta")),
                    "avg_abs_len_delta": row_dict.get(_alias(col.name, "avg_abs_len_delta")),
                }

    result.numeric_column_stats = numeric_stats
    result.string_column_mismatches = string_mismatches
    result.geography_column_stats = geography_stats
    result.array_column_stats = array_stats
    result.total_differing_rows = row_dict.get("total_differing_rows")
    result.post_tolerance_diff_count = row_dict.get("post_tol_diff_count")

    return result


def run_pipeline(
    client: bigquery.Client,
    builder: QueryBuilder,
    config: PipelineConfig,
) -> PipelineResult:
    """Execute the 3-layer pipeline as a single BQ multi-statement script.

    Args:
        client: BigQuery client.
        builder: QueryBuilder configured for the comparison.
        config: Pipeline configuration (circuit breaker threshold).

    Returns:
        PipelineResult with all statistics from the pipeline execution.
    """
    script = builder.build_pipeline_script(max_diff_pct=config.max_diff_pct)
    # Execute the multi-statement script. BQ returns the result of the last SELECT.
    query_job = client.query(script)
    # For multi-statement scripts, we need to iterate through child jobs
    # to find the SELECT result (the last statement).
    rows = list(query_job.result())
    if not rows:
        # Edge case: no rows returned (shouldn't happen with our script design)
        raise RuntimeError("Pipeline script returned no rows")
    return _parse_pipeline_result(rows[0], builder)
