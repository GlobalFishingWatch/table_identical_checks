"""Tests for the multi-layer pipeline execution."""

import pytest
from google.cloud import bigquery

from table_identical_checks.backend import (
    PipelineConfig,
    QueryBuilder,
    generate_summary,
    get_table_schema,
)
from table_identical_checks.backend.pipeline import run_pipeline
from table_identical_checks.backend.tolerance import ToleranceConfig

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

NUMERIC_SCHEMA = [
    bigquery.SchemaField("id", "INT64"),
    bigquery.SchemaField("int_val", "INT64"),
    bigquery.SchemaField("float_val", "FLOAT64"),
]

MIXED_SCHEMA = [
    bigquery.SchemaField("id", "INT64"),
    bigquery.SchemaField("int_val", "INT64"),
    bigquery.SchemaField("float_val", "FLOAT64"),
    bigquery.SchemaField("str_val", "STRING"),
    bigquery.SchemaField("ts_val", "TIMESTAMP"),
    bigquery.SchemaField("bool_val", "BOOLEAN"),
]

FLOAT_ONLY_SCHEMA = [
    bigquery.SchemaField("id", "INT64"),
    bigquery.SchemaField("val_a", "FLOAT64"),
    bigquery.SchemaField("val_b", "FLOAT64"),
]

GEO_SCHEMA = [
    bigquery.SchemaField("id", "INT64"),
    bigquery.SchemaField("location", "GEOGRAPHY"),
]


# ---------------------------------------------------------------------------
# Identical tables
# ---------------------------------------------------------------------------


class TestPipelineIdenticalTables:
    """Pipeline should report zero diffs when tables are identical."""

    def test_identical_numeric_tables(self, bq_client, table_factory):
        rows = [
            {"id": 1, "int_val": 100, "float_val": 1.0},
            {"id": 2, "int_val": 200, "float_val": 2.0},
        ]
        table_a = table_factory(NUMERIC_SCHEMA, rows)
        table_b = table_factory(NUMERIC_SCHEMA, rows)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a, table_b=table_b, key_columns=["id"], columns=columns
        )
        # max_diff_pct=1.0 ensures circuit breaker never triggers
        result = run_pipeline(bq_client, builder, PipelineConfig(max_diff_pct=1.0))

        assert result.pipeline_status == "COMPLETED"
        assert result.total_rows_a == 2
        assert result.total_rows_b == 2
        assert result.rows_only_in_a == 0
        assert result.rows_only_in_b == 0
        assert result.rows_in_both_with_differences == 0
        assert all(v == 0 for v in result.column_diff_counts.values())

    def test_identical_mixed_types(self, bq_client, table_factory):
        rows = [
            {
                "id": 1,
                "int_val": 10,
                "float_val": 3.14,
                "str_val": "hello",
                "ts_val": "2024-01-01T00:00:00Z",
                "bool_val": True,
            },
        ]
        table_a = table_factory(MIXED_SCHEMA, rows)
        table_b = table_factory(MIXED_SCHEMA, rows)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a, table_b=table_b, key_columns=["id"], columns=columns
        )
        result = run_pipeline(bq_client, builder, PipelineConfig(max_diff_pct=1.0))

        assert result.pipeline_status == "COMPLETED"
        assert result.rows_in_both_with_differences == 0


# ---------------------------------------------------------------------------
# Tables with differences
# ---------------------------------------------------------------------------


class TestPipelineDifferences:
    """Pipeline should report correct counts and stats when tables differ."""

    def test_numeric_differences(self, bq_client, table_factory):
        rows_a = [
            {"id": 1, "int_val": 100, "float_val": 1.0},
            {"id": 2, "int_val": 200, "float_val": 2.0},
            {"id": 3, "int_val": 300, "float_val": 3.0},
        ]
        rows_b = [
            {"id": 1, "int_val": 100, "float_val": 1.0},  # identical
            {"id": 2, "int_val": 999, "float_val": 2.0},  # int_val differs
            {"id": 3, "int_val": 300, "float_val": 9.0},  # float_val differs
        ]

        table_a = table_factory(NUMERIC_SCHEMA, rows_a)
        table_b = table_factory(NUMERIC_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a, table_b=table_b, key_columns=["id"], columns=columns
        )
        result = run_pipeline(bq_client, builder, PipelineConfig(max_diff_pct=1.0))

        assert result.pipeline_status == "COMPLETED"
        assert result.total_rows_a == 3
        assert result.total_rows_b == 3
        assert result.rows_only_in_a == 0
        assert result.rows_only_in_b == 0
        assert result.rows_in_both_with_differences == 2

        # Per-column diff counts from Layer 1
        assert result.column_diff_counts["int_val"] == 1
        assert result.column_diff_counts["float_val"] == 1

        # Numeric stats from Layer 3
        assert result.numeric_column_stats is not None
        assert "int_val" in result.numeric_column_stats
        assert result.numeric_column_stats["int_val"]["max_abs_delta"] == 799
        assert "float_val" in result.numeric_column_stats
        assert result.numeric_column_stats["float_val"]["max_abs_delta"] == pytest.approx(6.0)

    def test_missing_rows(self, bq_client, table_factory):
        rows_a = [
            {"id": 1, "int_val": 100, "float_val": 1.0},
            {"id": 2, "int_val": 200, "float_val": 2.0},
        ]
        rows_b = [
            {"id": 2, "int_val": 200, "float_val": 2.0},
            {"id": 3, "int_val": 300, "float_val": 3.0},
        ]

        table_a = table_factory(NUMERIC_SCHEMA, rows_a)
        table_b = table_factory(NUMERIC_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a, table_b=table_b, key_columns=["id"], columns=columns
        )
        result = run_pipeline(bq_client, builder, PipelineConfig(max_diff_pct=1.0))

        assert result.pipeline_status == "COMPLETED"
        assert result.rows_only_in_a == 1
        assert result.rows_only_in_b == 1
        assert result.rows_in_both_with_differences == 0

    def test_mixed_type_differences(self, bq_client, table_factory):
        rows_a = [
            {
                "id": 1,
                "int_val": 10,
                "float_val": 3.14,
                "str_val": "hello",
                "ts_val": "2024-01-01T00:00:00Z",
                "bool_val": True,
            },
            {
                "id": 2,
                "int_val": 20,
                "float_val": 2.72,
                "str_val": "world",
                "ts_val": "2024-06-15T12:00:00Z",
                "bool_val": False,
            },
        ]
        rows_b = [
            {
                "id": 1,
                "int_val": 10,
                "float_val": 3.14,
                "str_val": "HELLO",  # different string
                "ts_val": "2024-01-01T00:00:00Z",
                "bool_val": True,
            },
            {
                "id": 2,
                "int_val": 99,  # different int
                "float_val": 2.72,
                "str_val": "world",
                "ts_val": "2024-06-15T12:00:00Z",
                "bool_val": True,  # different bool
            },
        ]

        table_a = table_factory(MIXED_SCHEMA, rows_a)
        table_b = table_factory(MIXED_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a, table_b=table_b, key_columns=["id"], columns=columns
        )
        result = run_pipeline(bq_client, builder, PipelineConfig(max_diff_pct=1.0))

        assert result.pipeline_status == "COMPLETED"
        assert result.rows_in_both_with_differences == 2

        # String mismatch
        assert result.string_column_mismatches is not None
        assert result.string_column_mismatches.get("str_val", 0) == 1

        # Int diff
        assert result.numeric_column_stats is not None
        assert "int_val" in result.numeric_column_stats

        # Bool diff
        assert "bool_val" in result.numeric_column_stats

    def test_string_only_differences(self, bq_client, table_factory):
        """When only string columns differ, pipeline should detect them."""
        schema = [
            bigquery.SchemaField("id", "INT64"),
            bigquery.SchemaField("name", "STRING"),
        ]
        rows_a = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
        rows_b = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Robert"}]

        table_a = table_factory(schema, rows_a)
        table_b = table_factory(schema, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a, table_b=table_b, key_columns=["id"], columns=columns
        )
        result = run_pipeline(bq_client, builder, PipelineConfig(max_diff_pct=1.0))

        assert result.pipeline_status == "COMPLETED"
        assert result.rows_in_both_with_differences == 1
        assert result.string_column_mismatches["name"] == 1

    def test_identical_column_detection(self, bq_client, table_factory):
        """Columns with zero diffs should have diff_count = 0."""
        rows_a = [
            {"id": 1, "int_val": 100, "float_val": 1.0},
            {"id": 2, "int_val": 200, "float_val": 2.0},
        ]
        rows_b = [
            {"id": 1, "int_val": 100, "float_val": 9.0},  # only float differs
            {"id": 2, "int_val": 200, "float_val": 8.0},  # only float differs
        ]

        table_a = table_factory(NUMERIC_SCHEMA, rows_a)
        table_b = table_factory(NUMERIC_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a, table_b=table_b, key_columns=["id"], columns=columns
        )
        result = run_pipeline(bq_client, builder, PipelineConfig(max_diff_pct=1.0))

        assert result.column_diff_counts["int_val"] == 0  # identical
        assert result.column_diff_counts["float_val"] == 2  # differs


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


class TestPipelineCircuitBreaker:
    """Circuit breaker should abort when diff % exceeds threshold."""

    def test_circuit_breaker_triggers(self, bq_client, table_factory):
        """All rows differ -> any threshold < 1.0 should trigger abort."""
        rows_a = [
            {"id": 1, "int_val": 100, "float_val": 1.0},
            {"id": 2, "int_val": 200, "float_val": 2.0},
        ]
        rows_b = [
            {"id": 1, "int_val": 999, "float_val": 9.0},
            {"id": 2, "int_val": 888, "float_val": 8.0},
        ]

        table_a = table_factory(NUMERIC_SCHEMA, rows_a)
        table_b = table_factory(NUMERIC_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a, table_b=table_b, key_columns=["id"], columns=columns
        )

        # Set threshold very low so circuit breaker triggers
        config = PipelineConfig(max_diff_pct=0.01)  # 1%
        result = run_pipeline(bq_client, builder, config)

        assert result.pipeline_status == "ABORTED"
        assert result.rows_in_both_with_differences == 2
        # Layer 3 stats should be absent
        assert result.numeric_column_stats is None
        # But column diff counts from Layer 1 should still be present
        assert result.column_diff_counts["int_val"] == 2
        assert result.column_diff_counts["float_val"] == 2

    def test_circuit_breaker_does_not_trigger(self, bq_client, table_factory):
        """One row differs out of 3 -> 33%. Threshold at 50% should pass."""
        rows_a = [
            {"id": 1, "int_val": 100, "float_val": 1.0},
            {"id": 2, "int_val": 200, "float_val": 2.0},
            {"id": 3, "int_val": 300, "float_val": 3.0},
        ]
        rows_b = [
            {"id": 1, "int_val": 100, "float_val": 1.0},
            {"id": 2, "int_val": 999, "float_val": 2.0},  # only this differs
            {"id": 3, "int_val": 300, "float_val": 3.0},
        ]

        table_a = table_factory(NUMERIC_SCHEMA, rows_a)
        table_b = table_factory(NUMERIC_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a, table_b=table_b, key_columns=["id"], columns=columns
        )

        config = PipelineConfig(max_diff_pct=0.5)  # 50%
        result = run_pipeline(bq_client, builder, config)

        assert result.pipeline_status == "COMPLETED"
        assert result.numeric_column_stats is not None


# ---------------------------------------------------------------------------
# Tolerance
# ---------------------------------------------------------------------------


class TestPipelineTolerance:
    """Pipeline tolerance handling: within/outside counts, post-tol diff count."""

    def test_tolerance_within_outside_counts(self, bq_client, table_factory):
        """Float column with tolerance should report within/outside counts."""
        rows_a = [
            {"id": 1, "val_a": 1.0, "val_b": 10.0},
            {"id": 2, "val_a": 2.0, "val_b": 20.0},
            {"id": 3, "val_a": 3.0, "val_b": 30.0},
        ]
        rows_b = [
            {"id": 1, "val_a": 1.0 + 1e-12, "val_b": 10.0},  # within tolerance
            {"id": 2, "val_a": 2.5, "val_b": 20.0},  # outside tolerance
            {"id": 3, "val_a": 3.0 + 1e-12, "val_b": 30.0},  # within tolerance
        ]

        table_a = table_factory(FLOAT_ONLY_SCHEMA, rows_a)
        table_b = table_factory(FLOAT_ONLY_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
            tolerance_config=ToleranceConfig(global_tolerance=1e-9),
        )
        result = run_pipeline(bq_client, builder, PipelineConfig(max_diff_pct=1.0))

        assert result.pipeline_status == "COMPLETED"
        # All 3 rows should show up in Layer 1 (no tolerance filtering there)
        assert result.rows_in_both_with_differences == 3

        # val_a stats
        stats = result.numeric_column_stats["val_a"]
        assert stats["within_tolerance_count"] == 2
        assert stats["outside_tolerance_count"] == 1

    def test_post_tolerance_diff_count(self, bq_client, table_factory):
        """Post-tolerance diff count should exclude rows where ALL tol cols are within tol."""
        rows_a = [
            {"id": 1, "val_a": 1.0, "val_b": 10.0},
            {"id": 2, "val_a": 2.0, "val_b": 20.0},
        ]
        rows_b = [
            {"id": 1, "val_a": 1.0 + 1e-12, "val_b": 10.0 + 1e-12},  # ALL within tol
            {"id": 2, "val_a": 2.5, "val_b": 20.0},  # val_a outside tol
        ]

        table_a = table_factory(FLOAT_ONLY_SCHEMA, rows_a)
        table_b = table_factory(FLOAT_ONLY_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
            tolerance_config=ToleranceConfig(global_tolerance=1e-9),
        )
        result = run_pipeline(bq_client, builder, PipelineConfig(max_diff_pct=1.0))

        assert result.pipeline_status == "COMPLETED"
        # Both rows are in Layer 1 (pre-tolerance)
        assert result.rows_in_both_with_differences == 2
        # Post-tolerance: only id=2 is still differing (id=1 ALL within tol)
        assert result.post_tolerance_diff_count == 1


# ---------------------------------------------------------------------------
# generate_summary() pipeline integration
# ---------------------------------------------------------------------------


class TestGenerateSummaryPipeline:
    """generate_summary() with pipeline_config should use the pipeline path."""

    def test_summary_pipeline_basic(self, bq_client, table_factory):
        rows_a = [
            {"id": 1, "int_val": 100, "float_val": 1.0},
            {"id": 2, "int_val": 200, "float_val": 2.0},
        ]
        rows_b = [
            {"id": 1, "int_val": 100, "float_val": 1.0},
            {"id": 2, "int_val": 999, "float_val": 2.0},
        ]

        table_a = table_factory(NUMERIC_SCHEMA, rows_a)
        table_b = table_factory(NUMERIC_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a, table_b=table_b, key_columns=["id"], columns=columns
        )

        summary = generate_summary(
            bq_client,
            builder,
            pipeline_config=PipelineConfig(max_diff_pct=1.0),
        )

        assert summary.total_rows_a == 2
        assert summary.total_rows_b == 2
        assert summary.rows_in_both_with_differences == 1
        assert summary.rows_identical == 1
        assert not summary.tables_identical
        assert "int_val" in summary.numeric_column_stats

    def test_summary_pipeline_identical(self, bq_client, table_factory):
        rows = [
            {"id": 1, "int_val": 100, "float_val": 1.0},
        ]
        table_a = table_factory(NUMERIC_SCHEMA, rows)
        table_b = table_factory(NUMERIC_SCHEMA, rows)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a, table_b=table_b, key_columns=["id"], columns=columns
        )

        summary = generate_summary(
            bq_client,
            builder,
            pipeline_config=PipelineConfig(max_diff_pct=1.0),
        )

        assert summary.tables_identical
        assert summary.total_differences == 0

    def test_summary_pipeline_with_tolerance(self, bq_client, table_factory):
        rows_a = [
            {"id": 1, "val_a": 1.0, "val_b": 10.0},
            {"id": 2, "val_a": 2.5, "val_b": 20.0},
        ]
        rows_b = [
            {"id": 1, "val_a": 1.0 + 1e-12, "val_b": 10.0 + 1e-12},
            {"id": 2, "val_a": 5.0, "val_b": 20.0},
        ]

        table_a = table_factory(FLOAT_ONLY_SCHEMA, rows_a)
        table_b = table_factory(FLOAT_ONLY_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
            tolerance_config=ToleranceConfig(global_tolerance=1e-9),
        )

        summary = generate_summary(
            bq_client,
            builder,
            pipeline_config=PipelineConfig(max_diff_pct=1.0),
        )

        assert summary.has_tolerance
        # Pre-tolerance: both rows differ
        assert summary.rows_in_both_with_differences_pretolerance == 2
        # Post-tolerance: only id=2 differs
        assert summary.rows_in_both_with_differences == 1
        assert summary.tolerance_config is not None

    def test_summary_pipeline_str_output(self, bq_client, table_factory):
        """Summary from pipeline should be displayable via str()."""
        rows_a = [
            {"id": 1, "int_val": 100, "float_val": 1.0},
        ]
        rows_b = [
            {"id": 1, "int_val": 999, "float_val": 9.0},
        ]

        table_a = table_factory(NUMERIC_SCHEMA, rows_a)
        table_b = table_factory(NUMERIC_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a, table_b=table_b, key_columns=["id"], columns=columns
        )

        summary = generate_summary(
            bq_client,
            builder,
            pipeline_config=PipelineConfig(max_diff_pct=1.0),
        )

        output = str(summary)
        assert "TABLE COMPARISON SUMMARY" in output
        assert "DIFFERENCES FOUND" in output

    def test_summary_pipeline_aborted_str_output(self, bq_client, table_factory):
        """Aborted pipeline result should still produce displayable summary."""
        rows_a = [
            {"id": 1, "int_val": 100, "float_val": 1.0},
            {"id": 2, "int_val": 200, "float_val": 2.0},
        ]
        rows_b = [
            {"id": 1, "int_val": 999, "float_val": 9.0},
            {"id": 2, "int_val": 888, "float_val": 8.0},
        ]

        table_a = table_factory(NUMERIC_SCHEMA, rows_a)
        table_b = table_factory(NUMERIC_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a, table_b=table_b, key_columns=["id"], columns=columns
        )

        summary = generate_summary(
            bq_client,
            builder,
            pipeline_config=PipelineConfig(max_diff_pct=0.01),
        )

        output = str(summary)
        assert "TABLE COMPARISON SUMMARY" in output
        # Stats won't be present but summary should still render
        assert "DIFFERENCES FOUND" in output


# ---------------------------------------------------------------------------
# Geography
# ---------------------------------------------------------------------------


class TestPipelineGeography:
    """Pipeline geography column handling."""

    def test_geography_differences(self, bq_client, table_factory):
        rows_a = [
            {"id": 1, "location": "POINT(0 0)"},
            {"id": 2, "location": "POINT(10 10)"},
        ]
        rows_b = [
            {"id": 1, "location": "POINT(0 0)"},
            {"id": 2, "location": "POINT(10.001 10.001)"},  # slightly different
        ]

        table_a = table_factory(GEO_SCHEMA, rows_a)
        table_b = table_factory(GEO_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a, table_b=table_b, key_columns=["id"], columns=columns
        )
        result = run_pipeline(bq_client, builder, PipelineConfig(max_diff_pct=1.0))

        assert result.pipeline_status == "COMPLETED"
        assert result.rows_in_both_with_differences == 1
        assert result.geography_column_stats is not None
        assert "location" in result.geography_column_stats
        assert result.geography_column_stats["location"]["max_distance_meters"] > 0

    def test_geography_with_tolerance(self, bq_client, table_factory):
        rows_a = [
            {"id": 1, "location": "POINT(0 0)"},
            {"id": 2, "location": "POINT(10 10)"},
        ]
        rows_b = [
            {"id": 1, "location": "POINT(0 0)"},
            {"id": 2, "location": "POINT(10.001 10.001)"},
        ]

        table_a = table_factory(GEO_SCHEMA, rows_a)
        table_b = table_factory(GEO_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
            tolerance_config=ToleranceConfig(column_tolerances={"location": 1000.0}),
        )
        result = run_pipeline(bq_client, builder, PipelineConfig(max_diff_pct=1.0))

        assert result.pipeline_status == "COMPLETED"
        geo_stats = result.geography_column_stats["location"]
        # The small shift should be within 1000m tolerance
        assert "within_tolerance_count" in geo_stats
        assert "outside_tolerance_count" in geo_stats
