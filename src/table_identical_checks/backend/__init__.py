"""Backend module for BigQuery operations."""

from .pipeline import PipelineConfig, PipelineResult, differing_columns, run_pipeline
from .query_builder import QueryBuilder
from .schema import ColumnInfo, ColumnType, get_partition_field, get_table_schema
from .summary import (
    ComparisonSummary,
    DimensionBucket,
    DimensionSummary,
    DuplicateInfo,
    SummaryFormatter,
    TableFormatter,
    VerboseFormatter,
    build_verify_query,
    check_duplicates,
    from_json_dict,
    generate_dimension_summary,
    generate_summary,
    get_formatter,
    to_json_dict,
)
from .tolerance import ToleranceConfig

__all__ = [
    "PipelineConfig",
    "PipelineResult",
    "QueryBuilder",
    "get_table_schema",
    "get_partition_field",
    "ColumnInfo",
    "ColumnType",
    "ComparisonSummary",
    "DuplicateInfo",
    "check_duplicates",
    "generate_summary",
    "differing_columns",
    "run_pipeline",
    "DimensionBucket",
    "DimensionSummary",
    "generate_dimension_summary",
    "ToleranceConfig",
    "SummaryFormatter",
    "VerboseFormatter",
    "TableFormatter",
    "get_formatter",
    "to_json_dict",
    "from_json_dict",
    "build_verify_query",
]
