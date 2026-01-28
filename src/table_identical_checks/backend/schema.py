"""Schema introspection for BigQuery tables."""

from dataclasses import dataclass
from enum import Enum

from google.cloud import bigquery


class ColumnType(Enum):
    """Supported column types for comparison."""

    INTEGER = "integer"
    FLOAT = "float"
    STRING = "string"
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


def get_column_names_by_type(
    columns: list[ColumnInfo],
    column_type: ColumnType,
) -> list[str]:
    """Get column names filtered by type."""
    return [c.name for c in columns if c.column_type == column_type]
