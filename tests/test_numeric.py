"""Tests for numeric column comparisons (INT64, FLOAT64)."""

import pytest
from google.cloud import bigquery

from table_identical_checks.backend import (
    PipelineConfig,
    QueryBuilder,
    ToleranceConfig,
    get_table_schema,
)
from table_identical_checks.backend.pipeline import run_pipeline

# Schema for numeric test tables
NUMERIC_SCHEMA = [
    bigquery.SchemaField("id", "INT64"),
    bigquery.SchemaField("int_val", "INT64"),
    bigquery.SchemaField("float_val", "FLOAT64"),
]


class TestIdenticalNumericTables:
    """Test cases where numeric tables are identical."""

    def test_identical_integer_values(self, bq_client, table_factory):
        """Two tables with identical integer values should have no differences."""
        rows = [
            {"id": 1, "int_val": 100, "float_val": 1.0},
            {"id": 2, "int_val": 200, "float_val": 2.0},
        ]

        table_a = table_factory(NUMERIC_SCHEMA, rows)
        table_b = table_factory(NUMERIC_SCHEMA, rows)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
        )

        query = builder.build_count_query()
        result = bq_client.query(query).result()
        count = list(result)[0].diff_count

        assert count == 0

    def test_identical_float_values(self, bq_client, table_factory):
        """Two tables with identical float values should have no differences."""
        rows = [
            {"id": 1, "int_val": 1, "float_val": 3.14159},
            {"id": 2, "int_val": 2, "float_val": 2.71828},
        ]

        table_a = table_factory(NUMERIC_SCHEMA, rows)
        table_b = table_factory(NUMERIC_SCHEMA, rows)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
        )

        query = builder.build_count_query()
        result = bq_client.query(query).result()
        count = list(result)[0].diff_count

        assert count == 0


class TestDifferentNumericTables:
    """Test cases where numeric tables have differences."""

    def test_different_integer_values(self, bq_client, table_factory):
        """Tables with different integer values should report differences."""
        rows_a = [
            {"id": 1, "int_val": 100, "float_val": 1.0},
            {"id": 2, "int_val": 200, "float_val": 2.0},
        ]
        rows_b = [
            {"id": 1, "int_val": 100, "float_val": 1.0},
            {"id": 2, "int_val": 999, "float_val": 2.0},  # Different int_val
        ]

        table_a = table_factory(NUMERIC_SCHEMA, rows_a)
        table_b = table_factory(NUMERIC_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
        )

        query = builder.build_diff_query()
        result = bq_client.query(query).result()
        rows = list(result)

        assert len(rows) == 1
        row = rows[0]
        assert row.id == 2
        assert row.a__int_val == 200
        assert row.b__int_val == 999
        assert row.int_val__delta == -799  # 200 - 999
        assert row.int_val__abs_delta == 799

    def test_different_float_values(self, bq_client, table_factory):
        """Tables with different float values should report differences with deltas."""
        rows_a = [
            {"id": 1, "int_val": 1, "float_val": 1.0},
        ]
        rows_b = [
            {"id": 1, "int_val": 1, "float_val": 1.5},
        ]

        table_a = table_factory(NUMERIC_SCHEMA, rows_a)
        table_b = table_factory(NUMERIC_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
        )

        query = builder.build_diff_query()
        result = bq_client.query(query).result()
        rows = list(result)

        assert len(rows) == 1
        row = rows[0]
        assert row.a__float_val == 1.0
        assert row.b__float_val == 1.5
        assert row.float_val__delta == pytest.approx(-0.5)
        assert row.float_val__abs_delta == pytest.approx(0.5)
        assert row.float_val__rel_delta == pytest.approx(-0.5 / 1.5)

    def test_float_precision_difference(self, bq_client, table_factory):
        """Very small float differences should still be detected."""
        rows_a = [
            {"id": 1, "int_val": 1, "float_val": 1.0000000001},
        ]
        rows_b = [
            {"id": 1, "int_val": 1, "float_val": 1.0000000002},
        ]

        table_a = table_factory(NUMERIC_SCHEMA, rows_a)
        table_b = table_factory(NUMERIC_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
        )

        query = builder.build_diff_query()
        result = bq_client.query(query).result()
        rows = list(result)

        # Small difference should be detected
        assert len(rows) == 1
        row = rows[0]
        assert row.float_val__abs_delta > 0


class TestMissingRows:
    """Test cases where rows exist in only one table."""

    def test_row_only_in_table_a(self, bq_client, table_factory):
        """Row existing only in table A should be detected."""
        rows_a = [
            {"id": 1, "int_val": 100, "float_val": 1.0},
            {"id": 2, "int_val": 200, "float_val": 2.0},
        ]
        rows_b = [
            {"id": 1, "int_val": 100, "float_val": 1.0},
            # id=2 missing
        ]

        table_a = table_factory(NUMERIC_SCHEMA, rows_a)
        table_b = table_factory(NUMERIC_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
        )

        query = builder.build_diff_query()
        result = bq_client.query(query).result()
        rows = list(result)

        assert len(rows) == 1
        row = rows[0]
        assert row.id == 2
        assert row.in_a is True
        assert row.in_b is False

    def test_row_only_in_table_b(self, bq_client, table_factory):
        """Row existing only in table B should be detected."""
        rows_a = [
            {"id": 1, "int_val": 100, "float_val": 1.0},
        ]
        rows_b = [
            {"id": 1, "int_val": 100, "float_val": 1.0},
            {"id": 2, "int_val": 200, "float_val": 2.0},
        ]

        table_a = table_factory(NUMERIC_SCHEMA, rows_a)
        table_b = table_factory(NUMERIC_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
        )

        query = builder.build_diff_query()
        result = bq_client.query(query).result()
        rows = list(result)

        assert len(rows) == 1
        row = rows[0]
        assert row.id == 2
        assert row.in_a is False
        assert row.in_b is True


class TestNullValues:
    """Test cases for NULL handling."""

    def test_null_equals_null(self, bq_client, table_factory):
        """NULL values in both tables should be considered equal."""
        rows = [
            {"id": 1, "int_val": None, "float_val": None},
        ]

        table_a = table_factory(NUMERIC_SCHEMA, rows)
        table_b = table_factory(NUMERIC_SCHEMA, rows)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
        )

        query = builder.build_count_query()
        result = bq_client.query(query).result()
        count = list(result)[0].diff_count

        assert count == 0

    def test_null_vs_value_is_different(self, bq_client, table_factory):
        """NULL vs a value should be detected as different."""
        rows_a = [
            {"id": 1, "int_val": None, "float_val": 1.0},
        ]
        rows_b = [
            {"id": 1, "int_val": 100, "float_val": 1.0},
        ]

        table_a = table_factory(NUMERIC_SCHEMA, rows_a)
        table_b = table_factory(NUMERIC_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
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


# Schema with two FLOAT columns so we can verify per-column tolerance counts
# don't bleed across columns when only one of them differs in a given row.
TWO_FLOAT_SCHEMA = [
    bigquery.SchemaField("id", "INT64"),
    bigquery.SchemaField("a", "FLOAT64"),
    bigquery.SchemaField("b", "FLOAT64"),
]


class TestPerColumnToleranceCounts:
    """Regression tests for the per-column within/outside tolerance counts.

    The L3 aggregation runs over all rows that differ in *any* column. Without
    a per-column "actually differs" guard, the within-tol count for column X
    incorrectly includes rows where X was exactly equal (and the row is in L2
    only because some *other* column differs). The fix asserts the invariant
    ``within_tol + outside_tol == column_diff_count`` for every toleranced
    column.
    """

    def test_within_tol_does_not_include_equal_rows(self, bq_client, table_factory):
        rows_a = [
            {"id": 1, "a": 1.0, "b": 10.0},
            {"id": 2, "a": 2.0, "b": 20.0},
            {"id": 3, "a": 3.0, "b": 30.0},
        ]
        # Row 1: only a differs (within tol). Row 2: only b differs (within tol).
        # Row 3: identical -> not in L2 at all.
        rows_b = [
            {"id": 1, "a": 1.0 + 1e-12, "b": 10.0},
            {"id": 2, "a": 2.0, "b": 20.0 + 1e-12},
            {"id": 3, "a": 3.0, "b": 30.0},
        ]
        table_a = table_factory(TWO_FLOAT_SCHEMA, rows_a)
        table_b = table_factory(TWO_FLOAT_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        tol = ToleranceConfig.parse("a:1e-9,b:1e-9")
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
            tolerance_config=tol,
        )
        result = run_pipeline(bq_client, builder, PipelineConfig(max_diff_pct=1.0))

        assert result.pipeline_status == "COMPLETED"
        assert result.column_diff_counts["a"] == 1
        assert result.column_diff_counts["b"] == 1

        assert result.numeric_column_stats is not None
        stats_a = result.numeric_column_stats["a"]
        stats_b = result.numeric_column_stats["b"]

        # The fix: counts cover only rows where THIS column differs.
        assert stats_a["within_tolerance_count"] == 1
        assert stats_a["outside_tolerance_count"] == 0
        assert stats_b["within_tolerance_count"] == 1
        assert stats_b["outside_tolerance_count"] == 0

        # Invariant: within + outside == column-level diff count.
        assert (
            stats_a["within_tolerance_count"] + stats_a["outside_tolerance_count"]
            == result.column_diff_counts["a"]
        )
        assert (
            stats_b["within_tolerance_count"] + stats_b["outside_tolerance_count"]
            == result.column_diff_counts["b"]
        )

    def test_outside_tol_count_separates_from_within(self, bq_client, table_factory):
        rows_a = [
            {"id": 1, "a": 1.0, "b": 10.0},
            {"id": 2, "a": 2.0, "b": 20.0},
        ]
        # Row 1: a differs above tolerance. Row 2: a differs within tolerance.
        # b is equal in both rows.
        rows_b = [
            {"id": 1, "a": 1.5, "b": 10.0},
            {"id": 2, "a": 2.0 + 1e-12, "b": 20.0},
        ]
        table_a = table_factory(TWO_FLOAT_SCHEMA, rows_a)
        table_b = table_factory(TWO_FLOAT_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        tol = ToleranceConfig.parse("a:1e-9,b:1e-9")
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
            tolerance_config=tol,
        )
        result = run_pipeline(bq_client, builder, PipelineConfig(max_diff_pct=1.0))

        assert result.pipeline_status == "COMPLETED"
        assert result.column_diff_counts["a"] == 2
        assert result.column_diff_counts["b"] == 0

        assert result.numeric_column_stats is not None
        stats_a = result.numeric_column_stats["a"]
        assert stats_a["within_tolerance_count"] == 1
        assert stats_a["outside_tolerance_count"] == 1

        # b never differs, so its within/outside counts must be zero — the bug
        # would have charged each L2 row's within_tol flag against b.
        stats_b = result.numeric_column_stats.get("b")
        if stats_b is not None:
            assert stats_b.get("within_tolerance_count", 0) == 0
            assert stats_b.get("outside_tolerance_count", 0) == 0
