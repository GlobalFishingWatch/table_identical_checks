"""Schema introspection for BigQuery tables."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from google.api_core.exceptions import BadRequest
from google.cloud import bigquery


class ColumnType(Enum):
    """Supported column types for comparison."""

    INTEGER = "integer"
    FLOAT = "float"
    STRING = "string"
    TIMESTAMP = "timestamp"
    DATE = "date"
    BOOLEAN = "boolean"
    GEOGRAPHY = "geography"
    UNSUPPORTED = "unsupported"


# Mapping from BigQuery types to our column types
BQ_TYPE_MAP: dict[str, ColumnType] = {
    "INT64": ColumnType.INTEGER,
    "INTEGER": ColumnType.INTEGER,
    "FLOAT64": ColumnType.FLOAT,
    "FLOAT": ColumnType.FLOAT,
    "NUMERIC": ColumnType.FLOAT,
    "BIGNUMERIC": ColumnType.FLOAT,
    "STRING": ColumnType.STRING,
    "TIMESTAMP": ColumnType.TIMESTAMP,
    "DATETIME": ColumnType.TIMESTAMP,
    "DATE": ColumnType.DATE,
    "BOOLEAN": ColumnType.BOOLEAN,
    "BOOL": ColumnType.BOOLEAN,
    "GEOGRAPHY": ColumnType.GEOGRAPHY,
    # Unsupported complex types (STRUCT/RECORD handled by flattening, not mapped here)
    "ARRAY": ColumnType.UNSUPPORTED,
    "JSON": ColumnType.UNSUPPORTED,
    "BYTES": ColumnType.UNSUPPORTED,
    "RANGE": ColumnType.UNSUPPORTED,
}


@dataclass
class ColumnInfo:
    """Information about a table column."""

    name: str
    bq_type: str
    column_type: ColumnType
    is_nullable: bool = True



def _flatten_fields(
    fields: Sequence[bigquery.SchemaField],
    prefix: str = "",
) -> list[ColumnInfo]:
    """Recursively flatten non-repeated STRUCT/RECORD fields into dot-notation columns."""
    result: list[ColumnInfo] = []
    for field in fields:
        name = f"{prefix}.{field.name}" if prefix else field.name

        if field.field_type in ("STRUCT", "RECORD") and field.mode != "REPEATED":
            result.extend(_flatten_fields(field.fields, prefix=name))
        else:
            column_type = BQ_TYPE_MAP.get(field.field_type, ColumnType.UNSUPPORTED)
            if field.mode == "REPEATED":
                column_type = ColumnType.UNSUPPORTED
            result.append(
                ColumnInfo(
                    name=name,
                    bq_type=field.field_type,
                    column_type=column_type,
                    is_nullable=(field.mode != "REQUIRED"),
                )
            )
    return result


def get_table_schema(client: bigquery.Client, table_ref: str) -> list[ColumnInfo]:
    """
    Get schema information for a BigQuery table.

    Non-repeated STRUCT/RECORD fields are recursively flattened into sub-fields
    with dot-notation names (e.g. ``address.street``).

    Args:
        client: BigQuery client
        table_ref: Fully qualified table reference (project.dataset.table)

    Returns:
        List of ColumnInfo objects describing each column
    """
    table = client.get_table(table_ref)
    return _flatten_fields(table.schema)


def get_partition_field(client: bigquery.Client, table_ref: str) -> str | None:
    """Detect whether a table (or view) requires a partition filter.

    Uses a dry-run query to trigger BigQuery's partition filter validation.
    This works for both base tables and views over partitioned tables, because
    BQ resolves the full query plan during dry-run validation.

    The dry-run is free (no bytes processed) and takes ~0.5s.

    Falls back to the Table API metadata if the dry-run does not raise a
    partition filter error (e.g. base tables without require_partition_filter).

    Args:
        client: BigQuery client.
        table_ref: Fully qualified table reference (project.dataset.table).

    Returns:
        Name of the partition column, or None if no partition filter is required.
    """
    # Pattern matching BQ's partition filter error message.
    partition_error_re = re.compile(
        r"without a filter over column\(s\) '([^']+)'"
    )

    query = f"SELECT * FROM `{table_ref}`"
    job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
    try:
        client.query(query, job_config=job_config)
    except BadRequest as exc:
        match = partition_error_re.search(str(exc))
        if match:
            # May contain multiple columns; take the first.
            return match.group(1).split(",")[0].strip()
        # A different BadRequest (syntax error, permissions, etc.) -- not
        # a partition filter issue, so fall through to the metadata check.
    except Exception:
        # Network errors, auth errors, etc. -- fall through.
        pass

    # Fallback: direct metadata check (works for base partitioned tables
    # that do not have require_partition_filter set).
    try:
        table = client.get_table(table_ref)
        if table.time_partitioning is not None:
            return table.time_partitioning.field
        if table.range_partitioning is not None:
            return table.range_partitioning.field
    except Exception:
        pass

    return None


def get_column_names_by_type(
    columns: list[ColumnInfo],
    column_type: ColumnType,
) -> list[str]:
    """Get column names filtered by type."""
    return [c.name for c in columns if c.column_type == column_type]
