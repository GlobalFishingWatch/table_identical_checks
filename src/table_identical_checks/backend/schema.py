"""Schema introspection for BigQuery tables."""

from dataclasses import dataclass
from enum import Enum

from google.cloud import bigquery


class ColumnType(Enum):
    """Supported column types for comparison."""

    INTEGER = "integer"
    FLOAT = "float"
    STRING = "string"
    TIMESTAMP = "timestamp"
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
    "DATE": ColumnType.TIMESTAMP,
    "BOOLEAN": ColumnType.BOOLEAN,
    "BOOL": ColumnType.BOOLEAN,
    "GEOGRAPHY": ColumnType.GEOGRAPHY,
    # Unsupported complex types
    "ARRAY": ColumnType.UNSUPPORTED,
    "STRUCT": ColumnType.UNSUPPORTED,
    "RECORD": ColumnType.UNSUPPORTED,
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


def get_table_schema(client: bigquery.Client, table_ref: str) -> list[ColumnInfo]:
    """
    Get schema information for a BigQuery table.

    Args:
        client: BigQuery client
        table_ref: Fully qualified table reference (project.dataset.table)

    Returns:
        List of ColumnInfo objects describing each column
    """
    table = client.get_table(table_ref)
    columns = []

    for field in table.schema:
        column_type = BQ_TYPE_MAP.get(field.field_type, ColumnType.UNSUPPORTED)
        columns.append(
            ColumnInfo(
                name=field.name,
                bq_type=field.field_type,
                column_type=column_type,
                is_nullable=(field.mode != "REQUIRED"),
            )
        )

    return columns


def get_partition_field(client: bigquery.Client, table_ref: str) -> str | None:
    """
    Get the partition field for a BigQuery table using the Table API.

    Uses client.get_table() which only requires read access to the table,
    unlike INFORMATION_SCHEMA which requires project-level access.

    Args:
        client: BigQuery client
        table_ref: Fully qualified table reference (project.dataset.table)

    Returns:
        Name of the partition column, or None if table is not partitioned
    """
    table = client.get_table(table_ref)

    if table.time_partitioning is not None:
        return table.time_partitioning.field

    if table.range_partitioning is not None:
        return table.range_partitioning.field

    return None


def get_column_names_by_type(
    columns: list[ColumnInfo],
    column_type: ColumnType,
) -> list[str]:
    """Get column names filtered by type."""
    return [c.name for c in columns if c.column_type == column_type]
