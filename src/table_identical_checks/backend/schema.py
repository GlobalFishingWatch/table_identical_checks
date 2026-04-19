"""Schema introspection for BigQuery tables."""

import re
from collections.abc import Iterable, Sequence
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
    ARRAY = "array"
    # KLL_QUANTILES sketch columns stored as BYTES. Never auto-detected from
    # schema alone -- opt-in only, via the --kll-cols / --kll-int-cols flags.
    KLL_FLOAT64 = "kll_float64"
    KLL_INT64 = "kll_int64"
    UNSUPPORTED = "unsupported"


# Mapping from BigQuery types to our column types.
# ARRAY is not listed here: repetition is expressed through field.mode == "REPEATED",
# not through a distinct field_type keyword. Array detection happens in _flatten_fields.
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
    "JSON": ColumnType.UNSUPPORTED,
    "BYTES": ColumnType.UNSUPPORTED,
    "RANGE": ColumnType.UNSUPPORTED,
}


# Scalar BQ types that are supported as ARRAY elements.
_ARRAY_SCALAR_SUPPORTED: frozenset[str] = frozenset(
    {
        "INT64",
        "INTEGER",
        "FLOAT64",
        "FLOAT",
        "NUMERIC",
        "BIGNUMERIC",
        "STRING",
        "TIMESTAMP",
        "DATETIME",
        "DATE",
        "BOOLEAN",
        "BOOL",
        "GEOGRAPHY",
    }
)


@dataclass
class ColumnInfo:
    """Information about a table column."""

    name: str
    bq_type: str
    column_type: ColumnType
    is_nullable: bool = True



def _render_array_bq_type(field: bigquery.SchemaField) -> str:
    """Render a human-readable BQ type string for a REPEATED field.

    Produces e.g. ``ARRAY<STRING>`` or ``ARRAY<STRUCT<value STRING, count INT64>>``.
    """
    if field.field_type in ("STRUCT", "RECORD"):
        children = ", ".join(f"{f.name} {f.field_type}" for f in field.fields)
        return f"ARRAY<STRUCT<{children}>>"
    return f"ARRAY<{field.field_type}>"


def _classify_array_field(field: bigquery.SchemaField) -> ColumnType:
    """Classify a REPEATED field as ARRAY or UNSUPPORTED.

    Supported:
      - ARRAY<scalar> where scalar is a groupable, non-complex type.
      - ARRAY<STRUCT<scalars...>> where every child is scalar (no nested RECORD,
        no REPEATED grandchild, no unsupported scalar type like BYTES/JSON).
    Everything else (nested arrays, structs with structs/arrays inside, arrays of
    BYTES/JSON/RANGE) is marked UNSUPPORTED.
    """
    if field.field_type in ("STRUCT", "RECORD"):
        for child in field.fields:
            if child.mode == "REPEATED":
                return ColumnType.UNSUPPORTED
            if child.field_type in ("STRUCT", "RECORD"):
                return ColumnType.UNSUPPORTED
            if child.field_type not in _ARRAY_SCALAR_SUPPORTED:
                return ColumnType.UNSUPPORTED
        return ColumnType.ARRAY

    if field.field_type in _ARRAY_SCALAR_SUPPORTED:
        return ColumnType.ARRAY
    return ColumnType.UNSUPPORTED


def _flatten_fields(
    fields: Sequence[bigquery.SchemaField],
    prefix: str = "",
) -> list[ColumnInfo]:
    """Recursively flatten non-repeated STRUCT/RECORD fields into dot-notation columns.

    REPEATED fields (ARRAYs) are NOT flattened: each array column stays as a single
    ColumnInfo with ColumnType.ARRAY (or UNSUPPORTED for nested arrays / non-scalar
    element structs).
    """
    result: list[ColumnInfo] = []
    for field in fields:
        name = f"{prefix}.{field.name}" if prefix else field.name

        if field.mode == "REPEATED":
            result.append(
                ColumnInfo(
                    name=name,
                    bq_type=_render_array_bq_type(field),
                    column_type=_classify_array_field(field),
                    is_nullable=True,
                )
            )
            continue

        if field.field_type in ("STRUCT", "RECORD"):
            result.extend(_flatten_fields(field.fields, prefix=name))
        else:
            column_type = BQ_TYPE_MAP.get(field.field_type, ColumnType.UNSUPPORTED)
            result.append(
                ColumnInfo(
                    name=name,
                    bq_type=field.field_type,
                    column_type=column_type,
                    is_nullable=(field.mode != "REQUIRED"),
                )
            )
    return result


def _apply_kll_classification(
    columns: list[ColumnInfo],
    kll_float64_cols: Iterable[str] | None,
    kll_int64_cols: Iterable[str] | None,
) -> list[ColumnInfo]:
    """Reclassify user-flagged BYTES columns as KLL sketches.

    Validation (raises ValueError, before any BQ job runs):
      - A name may not appear in both sets.
      - The name must exist in the schema.
      - The underlying column must be BYTES (UNSUPPORTED via BQ_TYPE_MAP).

    Mutates and returns ``columns``.
    """
    float_set = set(kll_float64_cols or ())
    int_set = set(kll_int64_cols or ())
    overlap = float_set & int_set
    if overlap:
        name = sorted(overlap)[0]
        raise ValueError(
            f"column '{name}' cannot be flagged as both --kll-cols "
            f"and --kll-int-cols"
        )

    by_name = {c.name: c for c in columns}
    for name, kll_type, suffix in (
        (n, ColumnType.KLL_FLOAT64, "FLOAT64") for n in float_set
    ):
        _classify_one_kll(by_name, name, kll_type, suffix)
    for name, kll_type, suffix in (
        (n, ColumnType.KLL_INT64, "INT64") for n in int_set
    ):
        _classify_one_kll(by_name, name, kll_type, suffix)
    return columns


def _classify_one_kll(
    by_name: dict[str, ColumnInfo],
    name: str,
    kll_type: ColumnType,
    suffix: str,
) -> None:
    col = by_name.get(name)
    if col is None:
        raise ValueError(
            f"--kll-cols / --kll-int-cols references unknown column '{name}'"
        )
    if col.bq_type != "BYTES":
        raise ValueError(
            f"column '{name}' flagged as KLL but has type {col.bq_type}, "
            f"not BYTES"
        )
    col.column_type = kll_type
    col.bq_type = f"BYTES (KLL_{suffix} sketch)"


def get_table_schema(
    client: bigquery.Client,
    table_ref: str,
    kll_float64_cols: Iterable[str] | None = None,
    kll_int64_cols: Iterable[str] | None = None,
) -> list[ColumnInfo]:
    """
    Get schema information for a BigQuery table.

    Non-repeated STRUCT/RECORD fields are recursively flattened into sub-fields
    with dot-notation names (e.g. ``address.street``).

    Args:
        client: BigQuery client
        table_ref: Fully qualified table reference (project.dataset.table)
        kll_float64_cols: Optional BYTES column names to treat as KLL_FLOAT64
            sketches (compared via quantile-value comparison at runtime).
        kll_int64_cols: Same as above for KLL_INT64 sketches.

    Returns:
        List of ColumnInfo objects describing each column.

    Raises:
        ValueError: If any flagged KLL column is unknown, is not BYTES, or
            appears in both kll_float64_cols and kll_int64_cols.
    """
    table = client.get_table(table_ref)
    columns = _flatten_fields(table.schema)
    return _apply_kll_classification(columns, kll_float64_cols, kll_int64_cols)


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
