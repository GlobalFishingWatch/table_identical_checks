"""Tests for non-repeated STRUCT column flattening and comparison."""

import time

import pytest
from google.cloud import bigquery

from table_identical_checks.backend import (
    PipelineConfig,
    QueryBuilder,
    generate_summary,
    get_table_schema,
)
from table_identical_checks.backend.pipeline import run_pipeline
from table_identical_checks.backend.schema import ColumnType, _flatten_fields
from table_identical_checks.backend.tolerance import ToleranceConfig

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

SIMPLE_STRUCT_SCHEMA = [
    bigquery.SchemaField("id", "INT64"),
    bigquery.SchemaField(
        "address",
        "STRUCT",
        fields=[
            bigquery.SchemaField("street", "STRING"),
            bigquery.SchemaField("zip_code", "INT64"),
            bigquery.SchemaField("lat", "FLOAT64"),
        ],
    ),
]

MIXED_SCHEMA = [
    bigquery.SchemaField("id", "INT64"),
    bigquery.SchemaField("name", "STRING"),
    bigquery.SchemaField(
        "info",
        "STRUCT",
        fields=[
            bigquery.SchemaField("score", "FLOAT64"),
            bigquery.SchemaField("label", "STRING"),
        ],
    ),
    bigquery.SchemaField("value", "INT64"),
]

NESTED_STRUCT_SCHEMA = [
    bigquery.SchemaField("id", "INT64"),
    bigquery.SchemaField(
        "outer",
        "STRUCT",
        fields=[
            bigquery.SchemaField("tag", "STRING"),
            bigquery.SchemaField(
                "inner",
                "STRUCT",
                fields=[
                    bigquery.SchemaField("x", "FLOAT64"),
                    bigquery.SchemaField("y", "FLOAT64"),
                ],
            ),
        ],
    ),
]


# ---------------------------------------------------------------------------
# Helper: wait for streaming buffer
# ---------------------------------------------------------------------------

def _wait_for_streaming_buffer(client, table_id, max_wait=60):
    """Wait until BigQuery streaming buffer is flushed (needed for struct inserts)."""
    for _ in range(max_wait):
        tbl = client.get_table(table_id)
        if tbl.streaming_buffer is None:
            return
        time.sleep(1)


# ---------------------------------------------------------------------------
# Schema flattening unit tests (no BigQuery)
# ---------------------------------------------------------------------------


class TestFlattenFields:
    """Unit tests for _flatten_fields (no BQ calls)."""

    def test_simple_struct_flattened(self):
        """Non-repeated STRUCT should be flattened into dot-notation sub-fields."""
        fields = [
            bigquery.SchemaField("id", "INT64"),
            bigquery.SchemaField(
                "address",
                "STRUCT",
                fields=[
                    bigquery.SchemaField("street", "STRING"),
                    bigquery.SchemaField("zip_code", "INT64"),
                ],
            ),
        ]
        columns = _flatten_fields(fields)
        names = [c.name for c in columns]
        assert names == ["id", "address.street", "address.zip_code"]

    def test_flattened_types_correct(self):
        """Flattened sub-fields should have correct column types."""
        fields = [
            bigquery.SchemaField(
                "info",
                "RECORD",
                fields=[
                    bigquery.SchemaField("score", "FLOAT64"),
                    bigquery.SchemaField("label", "STRING"),
                ],
            ),
        ]
        columns = _flatten_fields(fields)
        assert columns[0].name == "info.score"
        assert columns[0].column_type == ColumnType.FLOAT
        assert columns[1].name == "info.label"
        assert columns[1].column_type == ColumnType.STRING

    def test_nested_struct_flattened_recursively(self):
        """Nested STRUCTs should produce multi-level dot-notation names."""
        fields = [
            bigquery.SchemaField(
                "outer",
                "STRUCT",
                fields=[
                    bigquery.SchemaField("tag", "STRING"),
                    bigquery.SchemaField(
                        "inner",
                        "STRUCT",
                        fields=[
                            bigquery.SchemaField("x", "FLOAT64"),
                            bigquery.SchemaField("y", "FLOAT64"),
                        ],
                    ),
                ],
            ),
        ]
        columns = _flatten_fields(fields)
        names = [c.name for c in columns]
        assert names == ["outer.tag", "outer.inner.x", "outer.inner.y"]

    def test_repeated_struct_marked_unsupported(self):
        """REPEATED STRUCT should NOT be flattened; marked UNSUPPORTED."""
        fields = [
            bigquery.SchemaField(
                "tags",
                "STRUCT",
                mode="REPEATED",
                fields=[
                    bigquery.SchemaField("key", "STRING"),
                    bigquery.SchemaField("value", "STRING"),
                ],
            ),
        ]
        columns = _flatten_fields(fields)
        assert len(columns) == 1
        assert columns[0].name == "tags"
        assert columns[0].column_type == ColumnType.UNSUPPORTED

    def test_repeated_field_inside_struct_marked_unsupported(self):
        """A REPEATED sub-field inside a non-repeated STRUCT should be UNSUPPORTED."""
        fields = [
            bigquery.SchemaField(
                "data",
                "STRUCT",
                fields=[
                    bigquery.SchemaField("name", "STRING"),
                    bigquery.SchemaField("values", "INT64", mode="REPEATED"),
                ],
            ),
        ]
        columns = _flatten_fields(fields)
        names = [c.name for c in columns]
        assert "data.name" in names
        assert "data.values" in names
        values_col = next(c for c in columns if c.name == "data.values")
        assert values_col.column_type == ColumnType.UNSUPPORTED

    def test_nullable_struct_sub_fields(self):
        """Sub-fields of a NULLABLE struct should be nullable."""
        fields = [
            bigquery.SchemaField(
                "info",
                "STRUCT",
                mode="NULLABLE",
                fields=[
                    bigquery.SchemaField("value", "INT64", mode="REQUIRED"),
                ],
            ),
        ]
        columns = _flatten_fields(fields)
        # Even though the sub-field is REQUIRED, the parent struct is NULLABLE,
        # so the sub-field's own mode is REQUIRED but the whole struct can be NULL.
        # We preserve the sub-field's own mode.
        assert columns[0].name == "info.value"
        assert columns[0].is_nullable is False


# ---------------------------------------------------------------------------
# Schema reading from BigQuery
# ---------------------------------------------------------------------------


class TestStructSchemaReading:
    """Test that get_table_schema correctly flattens STRUCT columns from BQ."""

    def test_simple_struct_schema(self, bq_client, table_factory):
        """Schema reading should flatten a simple struct into sub-fields."""
        rows = [{"id": 1, "address": {"street": "Main St", "zip_code": 12345, "lat": 40.7}}]
        table_id = table_factory(SIMPLE_STRUCT_SCHEMA, rows)

        columns = get_table_schema(bq_client, table_id)
        names = [c.name for c in columns]
        assert "address.street" in names
        assert "address.zip_code" in names
        assert "address.lat" in names
        # The parent "address" should NOT be present as a column
        assert "address" not in names

    def test_nested_struct_schema(self, bq_client, table_factory):
        """Schema reading should recursively flatten nested structs."""
        rows = [{"id": 1, "outer": {"tag": "test", "inner": {"x": 1.0, "y": 2.0}}}]
        table_id = table_factory(NESTED_STRUCT_SCHEMA, rows)

        columns = get_table_schema(bq_client, table_id)
        names = [c.name for c in columns]
        assert "outer.tag" in names
        assert "outer.inner.x" in names
        assert "outer.inner.y" in names


# ---------------------------------------------------------------------------
# Identical struct tables
# ---------------------------------------------------------------------------


class TestStructIdentical:
    """Test comparison of tables with identical struct values."""

    def test_identical_struct_values_pipeline(self, bq_client, table_factory):
        """Identical struct values should report zero differences (pipeline)."""
        rows = [
            {"id": 1, "address": {"street": "Main St", "zip_code": 12345, "lat": 40.7}},
            {"id": 2, "address": {"street": "Oak Ave", "zip_code": 67890, "lat": 34.1}},
        ]
        table_a = table_factory(SIMPLE_STRUCT_SCHEMA, rows)
        table_b = table_factory(SIMPLE_STRUCT_SCHEMA, rows)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a, table_b=table_b, key_columns=["id"], columns=columns
        )
        result = run_pipeline(bq_client, builder, PipelineConfig(max_diff_pct=1.0))

        assert result.pipeline_status == "COMPLETED"
        assert result.rows_in_both_with_differences == 0
        assert all(v == 0 for v in result.column_diff_counts.values())

    def test_identical_struct_values_legacy(self, bq_client, table_factory):
        """Identical struct values should report zero differences (legacy count query)."""
        rows = [
            {"id": 1, "address": {"street": "Main St", "zip_code": 12345, "lat": 40.7}},
        ]
        table_a = table_factory(SIMPLE_STRUCT_SCHEMA, rows)
        table_b = table_factory(SIMPLE_STRUCT_SCHEMA, rows)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a, table_b=table_b, key_columns=["id"], columns=columns
        )
        query = builder.build_count_query()
        result = bq_client.query(query).result()
        count = list(result)[0].diff_count
        assert count == 0


# ---------------------------------------------------------------------------
# Differing struct values
# ---------------------------------------------------------------------------


class TestStructDifferences:
    """Test comparison of tables with differing struct values."""

    def test_one_sub_field_differs_pipeline(self, bq_client, table_factory):
        """When one struct sub-field differs, pipeline should detect it."""
        rows_a = [
            {"id": 1, "address": {"street": "Main St", "zip_code": 12345, "lat": 40.7}},
        ]
        rows_b = [
            {"id": 1, "address": {"street": "Oak Ave", "zip_code": 12345, "lat": 40.7}},
        ]
        table_a = table_factory(SIMPLE_STRUCT_SCHEMA, rows_a)
        table_b = table_factory(SIMPLE_STRUCT_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a, table_b=table_b, key_columns=["id"], columns=columns
        )
        result = run_pipeline(bq_client, builder, PipelineConfig(max_diff_pct=1.0))

        assert result.pipeline_status == "COMPLETED"
        assert result.rows_in_both_with_differences == 1
        # Only address.street should differ
        assert result.column_diff_counts["address.street"] == 1
        assert result.column_diff_counts["address.zip_code"] == 0
        assert result.column_diff_counts["address.lat"] == 0

    def test_numeric_sub_field_differs_legacy(self, bq_client, table_factory):
        """Legacy diff query should detect numeric sub-field differences."""
        rows_a = [
            {"id": 1, "address": {"street": "Main St", "zip_code": 12345, "lat": 40.7}},
        ]
        rows_b = [
            {"id": 1, "address": {"street": "Main St", "zip_code": 12345, "lat": 41.0}},
        ]
        table_a = table_factory(SIMPLE_STRUCT_SCHEMA, rows_a)
        table_b = table_factory(SIMPLE_STRUCT_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a, table_b=table_b, key_columns=["id"], columns=columns
        )
        query = builder.build_count_query()
        result = bq_client.query(query).result()
        count = list(result)[0].diff_count
        assert count == 1


# ---------------------------------------------------------------------------
# NULL handling
# ---------------------------------------------------------------------------


class TestStructNulls:
    """Test NULL struct handling."""

    def test_whole_struct_null_identical(self, bq_client, table_factory):
        """Both tables with NULL struct should be considered identical."""
        rows = [{"id": 1, "address": None}]
        table_a = table_factory(SIMPLE_STRUCT_SCHEMA, rows)
        table_b = table_factory(SIMPLE_STRUCT_SCHEMA, rows)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a, table_b=table_b, key_columns=["id"], columns=columns
        )
        result = run_pipeline(bq_client, builder, PipelineConfig(max_diff_pct=1.0))

        assert result.rows_in_both_with_differences == 0

    def test_whole_struct_null_vs_value(self, bq_client, table_factory):
        """NULL struct in one table, non-NULL in other, should be a difference."""
        rows_a = [{"id": 1, "address": None}]
        rows_b = [
            {"id": 1, "address": {"street": "Main St", "zip_code": 12345, "lat": 40.7}},
        ]
        table_a = table_factory(SIMPLE_STRUCT_SCHEMA, rows_a)
        table_b = table_factory(SIMPLE_STRUCT_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a, table_b=table_b, key_columns=["id"], columns=columns
        )
        result = run_pipeline(bq_client, builder, PipelineConfig(max_diff_pct=1.0))

        assert result.rows_in_both_with_differences == 1


# ---------------------------------------------------------------------------
# Mixed: top-level + struct columns
# ---------------------------------------------------------------------------


class TestStructMixed:
    """Test tables with both top-level and struct columns."""

    def test_mixed_columns_identical(self, bq_client, table_factory):
        """Mixed top-level and struct columns, all identical."""
        rows = [
            {"id": 1, "name": "alice", "info": {"score": 9.5, "label": "A"}, "value": 100},
        ]
        table_a = table_factory(MIXED_SCHEMA, rows)
        table_b = table_factory(MIXED_SCHEMA, rows)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a, table_b=table_b, key_columns=["id"], columns=columns
        )
        result = run_pipeline(bq_client, builder, PipelineConfig(max_diff_pct=1.0))

        assert result.rows_in_both_with_differences == 0

    def test_mixed_columns_struct_differs(self, bq_client, table_factory):
        """Struct sub-field differs while top-level columns are identical."""
        rows_a = [
            {"id": 1, "name": "alice", "info": {"score": 9.5, "label": "A"}, "value": 100},
        ]
        rows_b = [
            {"id": 1, "name": "alice", "info": {"score": 8.0, "label": "A"}, "value": 100},
        ]
        table_a = table_factory(MIXED_SCHEMA, rows_a)
        table_b = table_factory(MIXED_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a, table_b=table_b, key_columns=["id"], columns=columns
        )
        result = run_pipeline(bq_client, builder, PipelineConfig(max_diff_pct=1.0))

        assert result.rows_in_both_with_differences == 1
        assert result.column_diff_counts["info.score"] == 1
        assert result.column_diff_counts["info.label"] == 0
        assert result.column_diff_counts["name"] == 0
        assert result.column_diff_counts["value"] == 0

    def test_mixed_columns_top_level_differs(self, bq_client, table_factory):
        """Top-level column differs while struct is identical."""
        rows_a = [
            {"id": 1, "name": "alice", "info": {"score": 9.5, "label": "A"}, "value": 100},
        ]
        rows_b = [
            {"id": 1, "name": "bob", "info": {"score": 9.5, "label": "A"}, "value": 100},
        ]
        table_a = table_factory(MIXED_SCHEMA, rows_a)
        table_b = table_factory(MIXED_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a, table_b=table_b, key_columns=["id"], columns=columns
        )
        result = run_pipeline(bq_client, builder, PipelineConfig(max_diff_pct=1.0))

        assert result.rows_in_both_with_differences == 1
        assert result.column_diff_counts["name"] == 1
        assert result.column_diff_counts["info.score"] == 0
        assert result.column_diff_counts["info.label"] == 0


# ---------------------------------------------------------------------------
# Nested struct
# ---------------------------------------------------------------------------


class TestNestedStruct:
    """Test deeply nested struct comparison."""

    def test_nested_struct_identical(self, bq_client, table_factory):
        """Nested struct with identical values should report zero differences."""
        rows = [{"id": 1, "outer": {"tag": "test", "inner": {"x": 1.0, "y": 2.0}}}]
        table_a = table_factory(NESTED_STRUCT_SCHEMA, rows)
        table_b = table_factory(NESTED_STRUCT_SCHEMA, rows)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a, table_b=table_b, key_columns=["id"], columns=columns
        )
        result = run_pipeline(bq_client, builder, PipelineConfig(max_diff_pct=1.0))

        assert result.rows_in_both_with_differences == 0

    def test_nested_struct_inner_differs(self, bq_client, table_factory):
        """Difference in nested inner struct sub-field should be detected."""
        rows_a = [{"id": 1, "outer": {"tag": "test", "inner": {"x": 1.0, "y": 2.0}}}]
        rows_b = [{"id": 1, "outer": {"tag": "test", "inner": {"x": 1.0, "y": 9.9}}}]
        table_a = table_factory(NESTED_STRUCT_SCHEMA, rows_a)
        table_b = table_factory(NESTED_STRUCT_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a, table_b=table_b, key_columns=["id"], columns=columns
        )
        result = run_pipeline(bq_client, builder, PipelineConfig(max_diff_pct=1.0))

        assert result.rows_in_both_with_differences == 1
        assert result.column_diff_counts["outer.inner.y"] == 1
        assert result.column_diff_counts["outer.inner.x"] == 0
        assert result.column_diff_counts["outer.tag"] == 0


# ---------------------------------------------------------------------------
# Tolerance on struct sub-fields
# ---------------------------------------------------------------------------


class TestStructTolerance:
    """Test tolerance applied to FLOAT64 sub-fields inside structs."""

    def test_float_sub_field_within_tolerance(self, bq_client, table_factory):
        """Small float difference in struct sub-field within tolerance should be excluded."""
        rows_a = [
            {"id": 1, "address": {"street": "Main St", "zip_code": 12345, "lat": 40.7}},
        ]
        rows_b = [
            {"id": 1, "address": {"street": "Main St", "zip_code": 12345, "lat": 40.7 + 1e-12}},
        ]
        table_a = table_factory(SIMPLE_STRUCT_SCHEMA, rows_a)
        table_b = table_factory(SIMPLE_STRUCT_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        tol = ToleranceConfig.parse("address.lat:1e-9")
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
            tolerance_config=tol,
        )
        result = run_pipeline(bq_client, builder, PipelineConfig(max_diff_pct=1.0))

        assert result.pipeline_status == "COMPLETED"
        # The float sub-field has a tiny difference
        assert result.column_diff_counts["address.lat"] in (0, 1)
        # If detected, it should be within tolerance
        if result.numeric_column_stats.get("address.lat"):
            stats = result.numeric_column_stats["address.lat"]
            assert stats.get("within_tolerance_count", 0) >= 0

    def test_float_sub_field_outside_tolerance(self, bq_client, table_factory):
        """Large float difference in struct sub-field should exceed tolerance."""
        rows_a = [
            {"id": 1, "address": {"street": "Main St", "zip_code": 12345, "lat": 40.7}},
        ]
        rows_b = [
            {"id": 1, "address": {"street": "Main St", "zip_code": 12345, "lat": 41.0}},
        ]
        table_a = table_factory(SIMPLE_STRUCT_SCHEMA, rows_a)
        table_b = table_factory(SIMPLE_STRUCT_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        tol = ToleranceConfig.parse("address.lat:1e-9")
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
            tolerance_config=tol,
        )
        result = run_pipeline(bq_client, builder, PipelineConfig(max_diff_pct=1.0))

        assert result.pipeline_status == "COMPLETED"
        assert result.column_diff_counts["address.lat"] == 1
        stats = result.numeric_column_stats["address.lat"]
        assert stats["outside_tolerance_count"] == 1


# ---------------------------------------------------------------------------
# Pipeline summary with struct columns
# ---------------------------------------------------------------------------


class TestStructPipelineSummary:
    """Test the summary command with struct columns."""

    def test_summary_with_struct_columns(self, bq_client, table_factory):
        """generate_summary should work with struct sub-fields in pipeline mode."""
        rows_a = [
            {"id": 1, "name": "alice", "info": {"score": 9.5, "label": "A"}, "value": 100},
            {"id": 2, "name": "bob", "info": {"score": 7.0, "label": "B"}, "value": 200},
        ]
        rows_b = [
            {"id": 1, "name": "alice", "info": {"score": 9.5, "label": "A"}, "value": 100},
            {"id": 2, "name": "bob", "info": {"score": 8.0, "label": "B"}, "value": 200},
        ]
        table_a = table_factory(MIXED_SCHEMA, rows_a)
        table_b = table_factory(MIXED_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a, table_b=table_b, key_columns=["id"], columns=columns
        )
        summary = generate_summary(
            client=bq_client,
            builder=builder,
            pipeline_config=PipelineConfig(max_diff_pct=1.0),
        )

        assert summary.rows_in_both_with_differences == 1
        # info.score should have numeric stats
        assert "info.score" in summary.numeric_column_stats
        stats = summary.numeric_column_stats["info.score"]
        assert stats["max_abs_delta"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Safe alias quoting
# ---------------------------------------------------------------------------


class TestSafeAlias:
    """Test _safe_alias mangling for dot-notation names."""

    def test_simple_name_unchanged(self):
        """Simple names should pass through unchanged."""
        assert QueryBuilder._safe_alias("value__eq") == "value__eq"

    def test_dot_name_mangled(self):
        """Dots in names should be replaced with double underscores."""
        assert QueryBuilder._safe_alias("address.street__eq") == "address__street__eq"

    def test_nested_dot_name_mangled(self):
        """Multi-level dot-notation names should have all dots replaced."""
        assert QueryBuilder._safe_alias("outer.inner.x__eq") == "outer__inner__x__eq"
