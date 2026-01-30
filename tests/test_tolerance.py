"""Tests for tolerance-based filtering of float comparisons."""

import pytest
from google.cloud import bigquery

from table_identical_checks.backend import QueryBuilder, get_table_schema
from table_identical_checks.backend.tolerance import ToleranceConfig

# Schema for tolerance test tables
TOLERANCE_SCHEMA = [
    bigquery.SchemaField("id", "INT64"),
    bigquery.SchemaField("float_a", "FLOAT64"),
    bigquery.SchemaField("float_b", "FLOAT64"),
    bigquery.SchemaField("int_val", "INT64"),
]


class TestToleranceConfig:
    """Test tolerance configuration parsing."""

    def test_no_tolerance(self):
        """No tolerance specified should return empty config."""
        config = ToleranceConfig.parse(None)
        assert config.get_tolerance("any_column") is None

    def test_global_tolerance(self):
        """Global tolerance should apply to all columns."""
        config = ToleranceConfig.parse("1e-9")
        assert config.get_tolerance("float_a") == 1e-9
        assert config.get_tolerance("float_b") == 1e-9
        assert config.get_tolerance("unknown_col") == 1e-9

    def test_per_column_tolerance(self):
        """Per-column tolerance should apply only to specified columns."""
        config = ToleranceConfig.parse("float_a:1e-9,float_b:1e-6")
        assert config.get_tolerance("float_a") == 1e-9
        assert config.get_tolerance("float_b") == 1e-6
        assert config.get_tolerance("unknown_col") is None

    def test_mixed_global_and_column_tolerance(self):
        """Column-specific tolerance should override global."""
        # This test assumes we support syntax like "1e-9,float_b:1e-6"
        # For now, we'll implement either global OR per-column, not both
        # So this test may be skipped or modified
        pass

    def test_invalid_tolerance_format(self):
        """Invalid tolerance format should raise ValueError."""
        with pytest.raises(ValueError):
            ToleranceConfig.parse("not_a_number")

    def test_invalid_column_format(self):
        """Invalid per-column format should raise ValueError."""
        with pytest.raises(ValueError):
            ToleranceConfig.parse("float_a:1e-9:extra")


class TestGlobalTolerance:
    """Test global tolerance filtering."""

    def test_float_within_tolerance_excluded(self, bq_client, table_factory):
        """Float differences within tolerance should be excluded from diff."""
        rows_a = [
            {"id": 1, "float_a": 1.0, "float_b": 2.0, "int_val": 10},
            {"id": 2, "float_a": 1.0, "float_b": 2.0, "int_val": 20},
        ]
        rows_b = [
            {"id": 1, "float_a": 1.0000000001, "float_b": 2.0, "int_val": 10},  # Within 1e-9
            {"id": 2, "float_a": 1.0, "float_b": 2.0000000001, "int_val": 20},  # Within 1e-9
        ]

        table_a = table_factory(TOLERANCE_SCHEMA, rows_a)
        table_b = table_factory(TOLERANCE_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        tolerance_config = ToleranceConfig.parse("1e-9")
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
            tolerance_config=tolerance_config,
        )

        query = builder.build_count_query()
        result = bq_client.query(query).result()
        count = list(result)[0].diff_count

        # Both rows should be excluded (all float diffs within tolerance)
        assert count == 0

    def test_float_exceeds_tolerance_included(self, bq_client, table_factory):
        """Float differences exceeding tolerance should be included in diff."""
        rows_a = [
            {"id": 1, "float_a": 1.0, "float_b": 2.0, "int_val": 10},
        ]
        rows_b = [
            {"id": 1, "float_a": 1.01, "float_b": 2.0, "int_val": 10},  # Exceeds 1e-9
        ]

        table_a = table_factory(TOLERANCE_SCHEMA, rows_a)
        table_b = table_factory(TOLERANCE_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        tolerance_config = ToleranceConfig.parse("1e-9")
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
            tolerance_config=tolerance_config,
        )

        query = builder.build_diff_query()
        result = bq_client.query(query).result()
        rows = list(result)

        assert len(rows) == 1
        row = rows[0]
        assert row.id == 1
        assert row.float_a__abs_delta == pytest.approx(0.01)

    def test_mixed_within_and_exceeds_tolerance(self, bq_client, table_factory):
        """Row with one float within tolerance and one exceeding should be included."""
        rows_a = [
            {"id": 1, "float_a": 1.0, "float_b": 2.0, "int_val": 10},
        ]
        rows_b = [
            {"id": 1, "float_a": 1.0000000001, "float_b": 2.5, "int_val": 10},
            # float_a within 1e-9, float_b exceeds 1e-9
        ]

        table_a = table_factory(TOLERANCE_SCHEMA, rows_a)
        table_b = table_factory(TOLERANCE_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        tolerance_config = ToleranceConfig.parse("1e-9")
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
            tolerance_config=tolerance_config,
        )

        query = builder.build_diff_query()
        result = bq_client.query(query).result()
        rows = list(result)

        # Should be included because float_b exceeds tolerance
        assert len(rows) == 1
        row = rows[0]
        assert row.float_b__abs_delta == pytest.approx(0.5)

    def test_tolerance_does_not_affect_integers(self, bq_client, table_factory):
        """Tolerance should not affect integer comparisons."""
        rows_a = [
            {"id": 1, "float_a": 1.0, "float_b": 2.0, "int_val": 10},
        ]
        rows_b = [
            {"id": 1, "float_a": 1.0, "float_b": 2.0, "int_val": 11},  # int differs
        ]

        table_a = table_factory(TOLERANCE_SCHEMA, rows_a)
        table_b = table_factory(TOLERANCE_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        tolerance_config = ToleranceConfig.parse("1e-9")
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
            tolerance_config=tolerance_config,
        )

        query = builder.build_diff_query()
        result = bq_client.query(query).result()
        rows = list(result)

        # Should be included due to int difference, even if floats are identical
        assert len(rows) == 1
        row = rows[0]
        assert row.int_val__delta == -1

    def test_all_floats_within_tolerance_but_int_differs(self, bq_client, table_factory):
        """Row with all floats within tolerance but int differing should be included."""
        rows_a = [
            {"id": 1, "float_a": 1.0, "float_b": 2.0, "int_val": 10},
        ]
        rows_b = [
            {"id": 1, "float_a": 1.0000000001, "float_b": 2.0000000001, "int_val": 11},
            # Both floats within tolerance, but int differs
        ]

        table_a = table_factory(TOLERANCE_SCHEMA, rows_a)
        table_b = table_factory(TOLERANCE_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        tolerance_config = ToleranceConfig.parse("1e-9")
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
            tolerance_config=tolerance_config,
        )

        query = builder.build_diff_query()
        result = bq_client.query(query).result()
        rows = list(result)

        # Should be included due to int difference
        assert len(rows) == 1
        row = rows[0]
        assert row.int_val__delta == -1


class TestPerColumnTolerance:
    """Test per-column tolerance filtering."""

    def test_different_tolerance_per_column(self, bq_client, table_factory):
        """Different tolerance values per column should filter correctly."""
        rows_a = [
            {"id": 1, "float_a": 1.0, "float_b": 2.0, "int_val": 10},
            {"id": 2, "float_a": 1.0, "float_b": 2.0, "int_val": 20},
        ]
        rows_b = [
            {"id": 1, "float_a": 1.0000001, "float_b": 2.0, "int_val": 10},  # float_a within 1e-6
            {"id": 2, "float_a": 1.0, "float_b": 2.00001, "int_val": 20},  # float_b exceeds 1e-6
        ]

        table_a = table_factory(TOLERANCE_SCHEMA, rows_a)
        table_b = table_factory(TOLERANCE_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        tolerance_config = ToleranceConfig.parse("float_a:1e-6,float_b:1e-6")
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
            tolerance_config=tolerance_config,
        )

        query = builder.build_diff_query()
        result = bq_client.query(query).result()
        rows = list(result)

        # id=1 should be excluded (float_a within tolerance)
        # id=2 should be included (float_b exceeds tolerance)
        assert len(rows) == 1
        assert rows[0].id == 2

    def test_column_without_tolerance_uses_exact_match(self, bq_client, table_factory):
        """Columns without specified tolerance should use exact matching."""
        rows_a = [
            {"id": 1, "float_a": 1.0, "float_b": 2.0, "int_val": 10},
        ]
        rows_b = [
            {"id": 1, "float_a": 1.0, "float_b": 2.0000000001, "int_val": 10},
            # Only float_a has tolerance, float_b should use exact match
        ]

        table_a = table_factory(TOLERANCE_SCHEMA, rows_a)
        table_b = table_factory(TOLERANCE_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        tolerance_config = ToleranceConfig.parse("float_a:1e-9")
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
            tolerance_config=tolerance_config,
        )

        query = builder.build_diff_query()
        result = bq_client.query(query).result()
        rows = list(result)

        # Should be included because float_b has tiny difference (no tolerance)
        assert len(rows) == 1


class TestToleranceWithNulls:
    """Test tolerance handling with NULL values."""

    def test_null_values_unaffected_by_tolerance(self, bq_client, table_factory):
        """NULL vs NULL should still be equal regardless of tolerance."""
        rows_a = [
            {"id": 1, "float_a": None, "float_b": None, "int_val": 10},
        ]
        rows_b = [
            {"id": 1, "float_a": None, "float_b": None, "int_val": 10},
        ]

        table_a = table_factory(TOLERANCE_SCHEMA, rows_a)
        table_b = table_factory(TOLERANCE_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        tolerance_config = ToleranceConfig.parse("1e-9")
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
            tolerance_config=tolerance_config,
        )

        query = builder.build_count_query()
        result = bq_client.query(query).result()
        count = list(result)[0].diff_count

        assert count == 0

    def test_null_vs_value_detected_with_tolerance(self, bq_client, table_factory):
        """NULL vs value should be detected as different even with tolerance."""
        rows_a = [
            {"id": 1, "float_a": None, "float_b": 2.0, "int_val": 10},
        ]
        rows_b = [
            {"id": 1, "float_a": 1.0, "float_b": 2.0, "int_val": 10},
        ]

        table_a = table_factory(TOLERANCE_SCHEMA, rows_a)
        table_b = table_factory(TOLERANCE_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        tolerance_config = ToleranceConfig.parse("1e-9")
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
            tolerance_config=tolerance_config,
        )

        query = builder.build_count_query()
        result = bq_client.query(query).result()
        count = list(result)[0].diff_count

        assert count == 1


class TestToleranceWithSummary:
    """Test tolerance statistics in summary output."""

    def test_summary_includes_within_tolerance_count(self, bq_client, table_factory):
        """Summary should include count of rows within tolerance per column."""
        rows_a = [
            {"id": 1, "float_a": 1.0, "float_b": 2.0, "int_val": 10},
            {"id": 2, "float_a": 1.0, "float_b": 2.0, "int_val": 20},
            {"id": 3, "float_a": 1.0, "float_b": 2.0, "int_val": 30},
        ]
        rows_b = [
            {"id": 1, "float_a": 1.0000000001, "float_b": 2.0, "int_val": 10},  # Within tolerance
            {"id": 2, "float_a": 1.01, "float_b": 2.0, "int_val": 20},  # Exceeds tolerance
            {"id": 3, "float_a": 1.0, "float_b": 2.0000000001, "int_val": 30},  # Within tolerance
        ]

        table_a = table_factory(TOLERANCE_SCHEMA, rows_a)
        table_b = table_factory(TOLERANCE_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        tolerance_config = ToleranceConfig.parse("1e-9")

        # This will require implementing summary support for tolerance
        # For now, we're just writing the test to define the expected behavior
        from table_identical_checks.backend.summary import generate_summary

        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
            tolerance_config=tolerance_config,
        )

        summary = generate_summary(
            client=bq_client,
            builder=builder,
        )

        # Check that float columns have within_tolerance_count
        float_a_stats = summary.numeric_column_stats.get("float_a")
        assert float_a_stats is not None
        assert "within_tolerance_count" in float_a_stats
        assert float_a_stats["within_tolerance_count"] == 2  # id=1 and id=3

        # float_b: all 3 are within tolerance
        float_b_stats = summary.numeric_column_stats.get("float_b")
        assert float_b_stats is not None
        assert "within_tolerance_count" in float_b_stats
        assert float_b_stats["within_tolerance_count"] == 3  # All identical or within tolerance


class TestEdgeCases:
    """Test edge cases for tolerance filtering."""

    def test_exact_tolerance_boundary_included(self, bq_client, table_factory):
        """Value within tolerance should be excluded (<=)."""
        rows_a = [
            {"id": 1, "float_a": 1.0, "float_b": 2.0, "int_val": 10},
        ]
        rows_b = [
            # Use a value that's definitely within 1e-9 after floating point representation
            {"id": 1, "float_a": 1.0 + 5e-10, "float_b": 2.0, "int_val": 10},
        ]

        table_a = table_factory(TOLERANCE_SCHEMA, rows_a)
        table_b = table_factory(TOLERANCE_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        tolerance_config = ToleranceConfig.parse("1e-9")
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
            tolerance_config=tolerance_config,
        )

        query = builder.build_count_query()
        result = bq_client.query(query).result()
        count = list(result)[0].diff_count

        # Should be excluded (abs_delta <= tolerance)
        assert count == 0

    def test_negative_differences_use_absolute_value(self, bq_client, table_factory):
        """Negative differences should use absolute value for tolerance check."""
        rows_a = [
            {"id": 1, "float_a": 2.0, "float_b": 2.0, "int_val": 10},
        ]
        rows_b = [
            {"id": 1, "float_a": 2.0000000001, "float_b": 2.0, "int_val": 10},
            # Negative delta: 2.0 - 2.0000000001 = -1e-10, abs = 1e-10 < 1e-9
        ]

        table_a = table_factory(TOLERANCE_SCHEMA, rows_a)
        table_b = table_factory(TOLERANCE_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        tolerance_config = ToleranceConfig.parse("1e-9")
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
            tolerance_config=tolerance_config,
        )

        query = builder.build_count_query()
        result = bq_client.query(query).result()
        count = list(result)[0].diff_count

        # Should be excluded
        assert count == 0

    def test_zero_tolerance(self, bq_client, table_factory):
        """Zero tolerance should behave like exact matching."""
        rows_a = [
            {"id": 1, "float_a": 1.0, "float_b": 2.0, "int_val": 10},
        ]
        rows_b = [
            {"id": 1, "float_a": 1.0000000001, "float_b": 2.0, "int_val": 10},
        ]

        table_a = table_factory(TOLERANCE_SCHEMA, rows_a)
        table_b = table_factory(TOLERANCE_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        tolerance_config = ToleranceConfig.parse("0")
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
            tolerance_config=tolerance_config,
        )

        query = builder.build_diff_query()
        result = bq_client.query(query).result()
        rows = list(result)

        # Should detect the tiny difference
        assert len(rows) == 1
