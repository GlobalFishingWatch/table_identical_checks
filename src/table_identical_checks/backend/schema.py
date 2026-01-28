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
    Get the partition field for a BigQuery table from INFORMATION_SCHEMA.

    Args:
        client: BigQuery client
        table_ref: Fully qualified table reference (project.dataset.table)

    Returns:
        Name of the partition column, or None if table is not partitioned
    """
    # Parse table reference
    parts = table_ref.split(".")
    if len(parts) != 3:
        raise ValueError(f"Invalid table reference: {table_ref}")
    
    project_id, dataset_id, table_id = parts

    query = f"""
    SELECT
        ddl
    FROM
        `{project_id}.{dataset_id}.INFORMATION_SCHEMA.TABLES`
    WHERE
        table_name = '{table_id}'
    """

    result = client.query(query).result()
    
    for row in result:
        ddl = row.ddl
        if ddl and "PARTITION BY" in ddl:
            # Extract partition column from DDL
            # Format examples:
            # PARTITION BY DATE(timestamp)
            # PARTITION BY timestamp
            # PARTITION BY DATE_TRUNC(timestamp, DAY)
            # PARTITION BY TIMESTAMP_TRUNC(timestamp, MONTH)
            import re
            # Match function wrapping column: DATE(col), TIMESTAMP_TRUNC(col, ...)
            match = re.search(r"PARTITION BY \w+\((\w+)", ddl)
            if match:
                return match.group(1)
            
            # Simple case: PARTITION BY column_name
            match = re.search(r"PARTITION BY (\w+)", ddl)
            if match:
                return match.group(1)
    
    return None


def get_column_names_by_type(
    columns: list[ColumnInfo],
    column_type: ColumnType,
) -> list[str]:
    """Get column names filtered by type."""
    return [c.name for c in columns if c.column_type == column_type]
