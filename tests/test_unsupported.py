"""Tests for auto-exclusion of unsupported column types."""

import pytest
from google.cloud import bigquery

from table_identical_checks.backend import QueryBuilder, get_table_schema
from table_identical_checks.backend.schema import BQ_TYPE_MAP, ColumnInfo, ColumnType
from table_identical_checks.backend.summary import ComparisonSummary


class TestUnsupportedTypeMapping:
    """Test that unsupported BQ types map to ColumnType.UNSUPPORTED."""

    def test_array_is_unsupported(self):
        assert BQ_TYPE_MAP["ARRAY"] == ColumnType.UNSUPPORTED

    def test_struct_not_in_type_map(self):
        """STRUCT is handled by schema flattening, not mapped directly."""
        assert BQ_TYPE_MAP.get("STRUCT", ColumnType.UNSUPPORTED) == ColumnType.UNSUPPORTED

    def test_record_not_in_type_map(self):
        """RECORD is handled by schema flattening, not mapped directly."""
        assert BQ_TYPE_MAP.get("RECORD", ColumnType.UNSUPPORTED) == ColumnType.UNSUPPORTED

    def test_json_is_unsupported(self):
        assert BQ_TYPE_MAP["JSON"] == ColumnType.UNSUPPORTED

    def test_bytes_is_unsupported(self):
        assert BQ_TYPE_MAP["BYTES"] == ColumnType.UNSUPPORTED

    def test_range_is_unsupported(self):
        assert BQ_TYPE_MAP["RANGE"] == ColumnType.UNSUPPORTED

    def test_unknown_type_is_unsupported(self):
        """Unknown/unmapped types should default to UNSUPPORTED."""
        assert BQ_TYPE_MAP.get("TOTALLY_NEW_TYPE", ColumnType.UNSUPPORTED) == ColumnType.UNSUPPORTED

    def test_geography_is_supported(self):
        """GEOGRAPHY should be its own supported type, not UNSUPPORTED."""
        assert BQ_TYPE_MAP["GEOGRAPHY"] == ColumnType.GEOGRAPHY


class TestAutoExcludeUnsupported:
    """Test that QueryBuilder automatically excludes unsupported columns."""

    def test_unsupported_columns_excluded(self):
        """Unsupported columns should be excluded from query generation."""
        columns = [
            ColumnInfo(name="id", bq_type="INT64", column_type=ColumnType.INTEGER),
            ColumnInfo(name="value", bq_type="FLOAT64", column_type=ColumnType.FLOAT),
            ColumnInfo(name="tags", bq_type="ARRAY", column_type=ColumnType.UNSUPPORTED),
            ColumnInfo(name="metadata", bq_type="JSON", column_type=ColumnType.UNSUPPORTED),
        ]

        builder = QueryBuilder(
            table_a="project.dataset.table_a",
            table_b="project.dataset.table_b",
            key_columns=["id"],
            columns=columns,
        )

        assert len(builder.excluded_columns) == 2
        excluded_names = {c.name for c in builder.excluded_columns}
        assert excluded_names == {"tags", "metadata"}

    def test_excluded_columns_not_in_value_columns(self):
        """Unsupported columns should not appear in value columns."""
        columns = [
            ColumnInfo(name="id", bq_type="INT64", column_type=ColumnType.INTEGER),
            ColumnInfo(name="value", bq_type="FLOAT64", column_type=ColumnType.FLOAT),
            ColumnInfo(name="nested", bq_type="STRUCT", column_type=ColumnType.UNSUPPORTED),
        ]

        builder = QueryBuilder(
            table_a="project.dataset.table_a",
            table_b="project.dataset.table_b",
            key_columns=["id"],
            columns=columns,
        )

        value_cols = builder._value_columns()
        value_names = {c.name for c in value_cols}
        assert "nested" not in value_names
        assert "value" in value_names

    def test_excluded_columns_info(self):
        """Excluded columns should retain their type information."""
        columns = [
            ColumnInfo(name="id", bq_type="INT64", column_type=ColumnType.INTEGER),
            ColumnInfo(name="tags", bq_type="ARRAY", column_type=ColumnType.UNSUPPORTED),
            ColumnInfo(name="nested", bq_type="STRUCT", column_type=ColumnType.UNSUPPORTED),
        ]

        builder = QueryBuilder(
            table_a="project.dataset.table_a",
            table_b="project.dataset.table_b",
            key_columns=["id"],
            columns=columns,
        )

        excluded = builder.excluded_columns
        excluded_dict = {c.name: c.bq_type for c in excluded}
        assert excluded_dict == {"tags": "ARRAY", "nested": "STRUCT"}

    def test_no_unsupported_columns(self):
        """When all columns are supported, excluded list should be empty."""
        columns = [
            ColumnInfo(name="id", bq_type="INT64", column_type=ColumnType.INTEGER),
            ColumnInfo(name="value", bq_type="FLOAT64", column_type=ColumnType.FLOAT),
            ColumnInfo(name="name", bq_type="STRING", column_type=ColumnType.STRING),
        ]

        builder = QueryBuilder(
            table_a="project.dataset.table_a",
            table_b="project.dataset.table_b",
            key_columns=["id"],
            columns=columns,
        )

        assert len(builder.excluded_columns) == 0


class TestQueriesWithExcludedColumns:
    """Test that queries still work correctly when unsupported columns are excluded."""

    def test_diff_query_with_excluded_columns(self, bq_client, table_factory):
        """Diff query should work when some columns are unsupported (excluded)."""
        # Create tables with only supported types (we simulate unsupported via ColumnInfo)
        schema = [
            bigquery.SchemaField("id", "INT64"),
            bigquery.SchemaField("value", "FLOAT64"),
            bigquery.SchemaField("name", "STRING"),
        ]
        rows_a = [
            {"id": 1, "value": 1.0, "name": "alice"},
            {"id": 2, "value": 2.0, "name": "bob"},
        ]
        rows_b = [
            {"id": 1, "value": 1.0, "name": "alice"},
            {"id": 2, "value": 2.5, "name": "bob"},
        ]

        table_a = table_factory(schema, rows_a)
        table_b = table_factory(schema, rows_b)

        # Get schema and add a fake UNSUPPORTED column to simulate mixed table
        columns = get_table_schema(bq_client, table_a)
        # Add a synthetic unsupported column (won't exist in actual table,
        # but since it's excluded from queries, it won't cause errors)
        columns.append(
            ColumnInfo(
                name="fake_struct",
                bq_type="STRUCT",
                column_type=ColumnType.UNSUPPORTED,
            )
        )

        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
        )

        # Verify exclusion
        assert len(builder.excluded_columns) == 1
        assert builder.excluded_columns[0].name == "fake_struct"

        # Query should still work
        query = builder.build_diff_query()
        result = bq_client.query(query).result()
        rows = list(result)

        assert len(rows) == 1
        assert rows[0].id == 2
        assert rows[0].value__abs_delta == pytest.approx(0.5)

    def test_count_query_with_excluded_columns(self, bq_client, table_factory):
        """Count query should work with excluded columns."""
        schema = [
            bigquery.SchemaField("id", "INT64"),
            bigquery.SchemaField("value", "INT64"),
        ]
        rows_a = [{"id": 1, "value": 10}]
        rows_b = [{"id": 1, "value": 20}]

        table_a = table_factory(schema, rows_a)
        table_b = table_factory(schema, rows_b)

        columns = get_table_schema(bq_client, table_a)
        columns.append(
            ColumnInfo(
                name="json_blob",
                bq_type="JSON",
                column_type=ColumnType.UNSUPPORTED,
            )
        )

        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
        )

        query = builder.build_count_query()
        result = bq_client.query(query).result()
        count = list(result)[0].diff_count

        assert count == 1


class TestExcludedColumnsInOutput:
    """Test that excluded columns appear in summary output."""

    def test_excluded_columns_in_summary_data(self):
        """ComparisonSummary should have excluded_columns populated."""
        summary = ComparisonSummary(
            table_a="a",
            table_b="b",
            key_columns=["id"],
            rows_only_in_a=0,
            rows_only_in_b=0,
            rows_in_both_with_differences=0,
            rows_identical=0,
            total_rows_a=0,
            total_rows_b=0,
            numeric_column_stats={},
            string_column_mismatches={},
            excluded_columns=[
                ("nested_data", "STRUCT"),
                ("tags", "ARRAY"),
                ("metadata", "JSON"),
            ],
        )

        assert len(summary.excluded_columns) == 3

    def test_excluded_columns_in_verbose_output(self):
        """Verbose output should show excluded columns prominently."""
        summary = ComparisonSummary(
            table_a="a",
            table_b="b",
            key_columns=["id"],
            rows_only_in_a=0,
            rows_only_in_b=0,
            rows_in_both_with_differences=0,
            rows_identical=0,
            total_rows_a=0,
            total_rows_b=0,
            numeric_column_stats={},
            string_column_mismatches={},
            excluded_columns=[
                ("nested_data", "STRUCT"),
                ("tags", "ARRAY"),
            ],
            output_format="verbose",
        )

        output = str(summary)
        assert "EXCLUDED COLUMNS" in output
        assert "nested_data" in output
        assert "STRUCT" in output
        assert "tags" in output
        assert "ARRAY" in output

    def test_no_excluded_columns_section_when_none(self):
        """Output should not show excluded columns section when there are none."""
        summary = ComparisonSummary(
            table_a="a",
            table_b="b",
            key_columns=["id"],
            rows_only_in_a=0,
            rows_only_in_b=0,
            rows_in_both_with_differences=0,
            rows_identical=0,
            total_rows_a=0,
            total_rows_b=0,
            numeric_column_stats={},
            string_column_mismatches={},
            output_format="verbose",
        )

        output = str(summary)
        assert "EXCLUDED COLUMNS" not in output


class TestEdgeCases:
    """Test edge cases for unsupported column handling."""

    def test_only_key_and_unsupported_columns(self):
        """Table with only key columns and unsupported columns should still build."""
        columns = [
            ColumnInfo(name="id", bq_type="INT64", column_type=ColumnType.INTEGER),
            ColumnInfo(name="data", bq_type="STRUCT", column_type=ColumnType.UNSUPPORTED),
            ColumnInfo(name="tags", bq_type="ARRAY", column_type=ColumnType.UNSUPPORTED),
        ]

        builder = QueryBuilder(
            table_a="project.dataset.table_a",
            table_b="project.dataset.table_b",
            key_columns=["id"],
            columns=columns,
        )

        # All non-key columns are unsupported
        assert len(builder._value_columns()) == 0
        assert len(builder.excluded_columns) == 2

    def test_mix_supported_and_unsupported(self):
        """Mix of supported and unsupported columns should work correctly."""
        columns = [
            ColumnInfo(name="id", bq_type="INT64", column_type=ColumnType.INTEGER),
            ColumnInfo(name="name", bq_type="STRING", column_type=ColumnType.STRING),
            ColumnInfo(name="data", bq_type="STRUCT", column_type=ColumnType.UNSUPPORTED),
            ColumnInfo(name="value", bq_type="FLOAT64", column_type=ColumnType.FLOAT),
            ColumnInfo(name="tags", bq_type="ARRAY", column_type=ColumnType.UNSUPPORTED),
            ColumnInfo(name="location", bq_type="GEOGRAPHY", column_type=ColumnType.GEOGRAPHY),
        ]

        builder = QueryBuilder(
            table_a="project.dataset.table_a",
            table_b="project.dataset.table_b",
            key_columns=["id"],
            columns=columns,
        )

        value_names = {c.name for c in builder._value_columns()}
        assert value_names == {"name", "value", "location"}

        excluded_names = {c.name for c in builder.excluded_columns}
        assert excluded_names == {"data", "tags"}
