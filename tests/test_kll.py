"""Tests for KLL_QUANTILES sketch column comparison support."""

from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner
from google.cloud import bigquery

from table_identical_checks.backend import (
    ComparisonSummary,
    PipelineResult,
    QueryBuilder,
    from_json_dict,
    to_json_dict,
)
from table_identical_checks.backend.pipeline import _parse_pipeline_result
from table_identical_checks.backend.schema import (
    ColumnInfo,
    ColumnType,
    _apply_kll_classification,
    _flatten_fields,
)
from table_identical_checks.backend.summary import (
    TableFormatter,
    _reject_kll_tolerance,
)
from table_identical_checks.backend.tolerance import ToleranceConfig
from table_identical_checks.cli import _parse_csv_names, main


# ---------------------------------------------------------------------------
# Schema classification + validation (no BigQuery)
# ---------------------------------------------------------------------------


def _bytes_schema_columns() -> list[ColumnInfo]:
    fields = [
        bigquery.SchemaField("id", "INT64"),
        bigquery.SchemaField("speed_sketch", "BYTES"),
        bigquery.SchemaField("hour_sketch", "BYTES"),
        bigquery.SchemaField("ssvid", "STRING"),
    ]
    return _flatten_fields(fields)


class TestKllSchemaClassification:
    """Unit tests for KLL classification in get_table_schema via opt-in kwargs."""

    def test_flags_bytes_as_kll_float64(self):
        cols = _bytes_schema_columns()
        out = _apply_kll_classification(cols, ["speed_sketch"], None)
        speed = next(c for c in out if c.name == "speed_sketch")
        assert speed.column_type == ColumnType.KLL_FLOAT64
        assert speed.bq_type == "BYTES (KLL_FLOAT64 sketch)"

    def test_flags_bytes_as_kll_int64(self):
        cols = _bytes_schema_columns()
        out = _apply_kll_classification(cols, None, ["hour_sketch"])
        hour = next(c for c in out if c.name == "hour_sketch")
        assert hour.column_type == ColumnType.KLL_INT64
        assert hour.bq_type == "BYTES (KLL_INT64 sketch)"

    def test_unknown_column_raises(self):
        cols = _bytes_schema_columns()
        with pytest.raises(ValueError, match="unknown column 'nope'"):
            _apply_kll_classification(cols, ["nope"], None)

    def test_non_bytes_column_raises(self):
        cols = _bytes_schema_columns()
        with pytest.raises(ValueError, match="not BYTES"):
            _apply_kll_classification(cols, ["ssvid"], None)

    def test_column_in_both_sets_raises(self):
        cols = _bytes_schema_columns()
        with pytest.raises(ValueError, match="cannot be flagged as both"):
            _apply_kll_classification(cols, ["speed_sketch"], ["speed_sketch"])

    def test_no_flags_leaves_bytes_as_unsupported(self):
        cols = _bytes_schema_columns()
        out = _apply_kll_classification(cols, None, None)
        speed = next(c for c in out if c.name == "speed_sketch")
        assert speed.column_type == ColumnType.UNSUPPORTED
        assert speed.bq_type == "BYTES"


# ---------------------------------------------------------------------------
# Query builder SQL generation (no BigQuery)
# ---------------------------------------------------------------------------


def _kll_builder(
    col_name: str = "speed_sketch",
    column_type: ColumnType = ColumnType.KLL_FLOAT64,
    kll_abs_tol: float = 0.0,
    kll_rel_tol: float = 0.05,
) -> QueryBuilder:
    """QueryBuilder with one key + one KLL sketch column."""
    return QueryBuilder(
        table_a="proj.ds.ta",
        table_b="proj.ds.tb",
        key_columns=["id"],
        columns=[
            ColumnInfo(name="id", bq_type="INT64", column_type=ColumnType.INTEGER),
            ColumnInfo(
                name=col_name,
                bq_type="BYTES (KLL_FLOAT64 sketch)",
                column_type=column_type,
            ),
        ],
        kll_abs_tol=kll_abs_tol,
        kll_rel_tol=kll_rel_tol,
    )


class TestKllSqlSnapshots:
    """Snapshot tests for KLL SQL across pipeline script and diff query."""

    def test_l1_eq_float64_shape(self):
        b = _kll_builder()
        expr = b._l1_kll_eq("speed_sketch", ColumnType.KLL_FLOAT64)
        # NULL-safe wrapper on the sketch values themselves
        assert "a.speed_sketch IS NULL AND b.speed_sketch IS NULL" in expr
        # BQ does not offer RANK_* for KLL -- must not appear.
        assert "RANK_FLOAT64" not in expr
        assert "RANK_INT64" not in expr
        # BQ requires phi to be a literal constant, so probes are unrolled
        # inline rather than iterated via UNNEST.
        assert "UNNEST(" not in expr
        # Every probe phi appears as a literal in the generated expression.
        for phi in (0.1, 0.25, 0.5, 0.75, 0.9):
            assert f"KLL_QUANTILES.EXTRACT_POINT_FLOAT64(a.speed_sketch, {phi})" in expr
            assert f"KLL_QUANTILES.EXTRACT_POINT_FLOAT64(b.speed_sketch, {phi})" in expr
        # Both tolerances inlined (defaults 0.0 abs, 0.05 rel)
        assert "<= 0.0 " in expr or "<= 0.0\n" in expr or "<= 0.0)" in expr
        assert "<= 0.05 " in expr or "<= 0.05\n" in expr or "<= 0.05)" in expr
        # Relative-tolerance shape: SAFE_DIVIDE over GREATEST of absolute values
        assert "SAFE_DIVIDE" in expr and "GREATEST" in expr
        # NULL-safety at the per-probe level: per-probe CASE on each
        # EXTRACT_POINT result (no longer using shared a_q/b_q aliases).
        assert "IS NULL AND" in expr
        assert "IS NULL OR" in expr

    def test_l1_eq_int64_uses_int_suffix(self):
        b = _kll_builder(column_type=ColumnType.KLL_INT64)
        expr = b._l1_kll_eq("speed_sketch", ColumnType.KLL_INT64)
        assert "EXTRACT_POINT_INT64" in expr
        assert "EXTRACT_POINT_FLOAT64" not in expr
        # Still no rank calls
        assert "RANK_INT64" not in expr
        assert "RANK_FLOAT64" not in expr

    def test_tolerance_flows_through(self):
        b = _kll_builder(kll_abs_tol=0.01, kll_rel_tol=0.02)
        script = b.build_pipeline_script(max_diff_pct=1.0)
        # Custom tolerances appear; defaults (0.05) do not.
        assert "<= 0.01" in script
        assert "<= 0.02" in script
        # The default rel-tol literal (0.05) must not appear when overridden.
        assert "<= 0.05" not in script

    def test_pipeline_script_l1_contains_kll_eq(self):
        b = _kll_builder()
        script = b.build_pipeline_script(max_diff_pct=1.0)
        assert "AS speed_sketch__eq" in script
        assert "KLL_QUANTILES.EXTRACT_POINT_FLOAT64" in script
        assert "RANK_FLOAT64" not in script
        for phi in (0.1, 0.25, 0.5, 0.75, 0.9):
            assert f"EXTRACT_POINT_FLOAT64(a.speed_sketch, {phi})" in script

    def test_pipeline_script_l2_has_mismatch_and_max_value_diff(self):
        b = _kll_builder()
        script = b.build_pipeline_script(max_diff_pct=1.0)
        assert "AS speed_sketch__max_abs_value_diff" in script
        assert "AS speed_sketch__mismatch" in script
        # Old aliases must be gone
        assert "max_abs_rank_diff" not in script
        assert "avg_abs_rank_diff" not in script

    def test_pipeline_script_l3_aggregates(self):
        b = _kll_builder()
        script = b.build_pipeline_script(max_diff_pct=1.0)
        assert "COUNTIF(speed_sketch__mismatch) AS speed_sketch__mismatch_count" in script
        assert (
            "MAX(speed_sketch__max_abs_value_diff) AS speed_sketch__max_abs_value_diff"
            in script
        )
        assert (
            "AVG(speed_sketch__max_abs_value_diff) AS speed_sketch__avg_abs_value_diff"
            in script
        )

    def test_diff_query_contains_kll_expressions(self):
        b = _kll_builder()
        sql = b.build_diff_query()
        assert "KLL_QUANTILES.EXTRACT_POINT_FLOAT64" in sql
        # BQ does not offer RANK_* for KLL
        assert "RANK_FLOAT64" not in sql
        # p50 preview slots
        assert "EXTRACT_POINT_FLOAT64(a.speed_sketch, 0.5)" in sql
        assert "EXTRACT_POINT_FLOAT64(b.speed_sketch, 0.5)" in sql

    def test_safe_alias_for_dotted_kll_column(self):
        b = QueryBuilder(
            table_a="proj.ds.ta",
            table_b="proj.ds.tb",
            key_columns=["id"],
            columns=[
                ColumnInfo(name="id", bq_type="INT64", column_type=ColumnType.INTEGER),
                ColumnInfo(
                    name="nested.speed_sketch",
                    bq_type="BYTES (KLL_FLOAT64 sketch)",
                    column_type=ColumnType.KLL_FLOAT64,
                ),
            ],
        )
        script = b.build_pipeline_script(max_diff_pct=1.0)
        assert "AS nested__speed_sketch__eq" in script
        assert "AS nested__speed_sketch__mismatch" in script
        assert "AS nested__speed_sketch__max_abs_value_diff" in script
        assert "AS nested__speed_sketch__mismatch_count" in script

    def test_both_tolerances_zero_still_valid(self):
        """Setting both tolerances to 0 is allowed -- strict per-quantile equality."""
        b = _kll_builder(kll_abs_tol=0.0, kll_rel_tol=0.0)
        script = b.build_pipeline_script(max_diff_pct=1.0)
        # The expression should be generated -- no validation error.
        assert "EXTRACT_POINT_FLOAT64" in script
        # Both tolerance shapes are present in the per-probe CASE.
        assert "SAFE_DIVIDE" in script and "GREATEST" in script


# ---------------------------------------------------------------------------
# Pipeline result parsing (no BigQuery)
# ---------------------------------------------------------------------------


class _SyntheticRow:
    def __init__(self, data: dict):
        self._data = data

    def keys(self):
        return self._data.keys()

    def __getitem__(self, key):
        return self._data[key]


class TestKllParsePipelineResult:
    def test_stats_populated_when_mismatches(self):
        builder = _kll_builder()
        row = _SyntheticRow(
            {
                "pipeline_status": "COMPLETED",
                "total_rows_a": 100,
                "total_rows_b": 100,
                "rows_only_in_a": 0,
                "rows_only_in_b": 0,
                "rows_in_both_with_differences": 4,
                "speed_sketch__diff_count": 4,
                "speed_sketch__mismatch_count": 4,
                "speed_sketch__max_abs_value_diff": 0.42,
                "speed_sketch__avg_abs_value_diff": 0.21,
                "total_differing_rows": 4,
            }
        )
        result = _parse_pipeline_result(row, builder)
        assert isinstance(result, PipelineResult)
        assert result.kll_column_stats == {
            "speed_sketch": {
                "mismatch_count": 4,
                "max_abs_value_diff": 0.42,
                "avg_abs_value_diff": 0.21,
            }
        }

    def test_stats_skipped_when_zero_mismatches(self):
        builder = _kll_builder()
        row = _SyntheticRow(
            {
                "pipeline_status": "COMPLETED",
                "total_rows_a": 100,
                "total_rows_b": 100,
                "rows_only_in_a": 0,
                "rows_only_in_b": 0,
                "rows_in_both_with_differences": 0,
                "speed_sketch__diff_count": 0,
                "speed_sketch__mismatch_count": 0,
                "speed_sketch__max_abs_value_diff": None,
                "speed_sketch__avg_abs_value_diff": None,
                "total_differing_rows": 0,
            }
        )
        result = _parse_pipeline_result(row, builder)
        assert result.kll_column_stats == {}


# ---------------------------------------------------------------------------
# Tolerance rejection
# ---------------------------------------------------------------------------


class TestKllToleranceRejection:
    def test_rejects_abs_tolerance_on_kll(self):
        builder = _kll_builder()
        builder.tolerance_config = ToleranceConfig.parse("speed_sketch:0.01")
        with pytest.raises(ValueError, match="KLL column 'speed_sketch'"):
            _reject_kll_tolerance(builder)

    def test_rejects_rel_tolerance_on_kll(self):
        builder = _kll_builder()
        builder.tolerance_config = ToleranceConfig.parse_rel("speed_sketch:0.01")
        with pytest.raises(ValueError, match="kll-abs-tol"):
            _reject_kll_tolerance(builder)

    def test_allows_global_tolerance_with_kll_present(self):
        """Global --tolerance must not raise just because a KLL column exists.

        Global tolerance applies to FLOAT only; KLL columns already ignore it.
        """
        b = QueryBuilder(
            table_a="proj.ds.ta",
            table_b="proj.ds.tb",
            key_columns=["id"],
            columns=[
                ColumnInfo(name="id", bq_type="INT64", column_type=ColumnType.INTEGER),
                ColumnInfo(name="v", bq_type="FLOAT64", column_type=ColumnType.FLOAT),
                ColumnInfo(
                    name="speed_sketch",
                    bq_type="BYTES (KLL_FLOAT64 sketch)",
                    column_type=ColumnType.KLL_FLOAT64,
                ),
            ],
            tolerance_config=ToleranceConfig.parse("1e-9"),
        )
        # Should not raise
        _reject_kll_tolerance(b)


# ---------------------------------------------------------------------------
# TableFormatter rendering
# ---------------------------------------------------------------------------


def _make_summary_with_kll(**overrides) -> ComparisonSummary:
    defaults = dict(
        table_a="proj.ds.a",
        table_b="proj.ds.b",
        key_columns=["id"],
        rows_only_in_a=0,
        rows_only_in_b=0,
        rows_in_both_with_differences=4,
        rows_identical=96,
        total_rows_a=100,
        total_rows_b=100,
        numeric_column_stats={},
        string_column_mismatches={},
        geography_column_stats={},
        array_column_stats={},
        kll_column_stats={
            "speed_sketch": {
                "mismatch_count": 4,
                "max_abs_value_diff": 0.42,
                "avg_abs_value_diff": 0.21,
            }
        },
        column_types={"speed_sketch": ColumnType.KLL_FLOAT64},
        column_diff_counts={"speed_sketch": 4},
        output_format="table",
    )
    defaults.update(overrides)
    return ComparisonSummary(**defaults)


class TestTableFormatterKll:
    def test_kll_row_appears_with_kllf_type(self):
        summary = _make_summary_with_kll()
        out = TableFormatter().format(summary)
        assert "speed_sketch" in out
        assert "KLLf" in out
        # mismatch count 4 should appear somewhere
        assert "4" in out

    def test_kll_int_row_renders_with_klli(self):
        summary = _make_summary_with_kll(
            column_types={"speed_sketch": ColumnType.KLL_INT64}
        )
        out = TableFormatter().format(summary)
        assert "KLLi" in out

    def test_kll_row_shows_dash_for_max_rel(self):
        summary = _make_summary_with_kll()
        out = TableFormatter().format(summary)
        line = next(line for line in out.splitlines() if "speed_sketch" in line)
        # The MaxRel slot should be a dash (max_rel stays None for KLL)
        assert " - " in line or "-" in line

    def test_kll_column_not_in_identical_list(self):
        summary = _make_summary_with_kll(
            column_types={
                "speed_sketch": ColumnType.KLL_FLOAT64,
                "other_col": ColumnType.STRING,
            }
        )
        identical = TableFormatter._identical_columns(summary)
        assert "speed_sketch" not in identical
        assert "other_col" in identical

    def test_identical_kll_classifies_as_identical(self):
        """A KLL column with zero mismatches (absent from kll_column_stats)
        should be listed under 'Identical columns'."""
        summary = _make_summary_with_kll(
            kll_column_stats={},
            rows_in_both_with_differences=0,
            rows_identical=100,
            column_diff_counts={"speed_sketch": 0},
        )
        identical = TableFormatter._identical_columns(summary)
        assert "speed_sketch" in identical

    def test_roundtrip_json_preserves_kll_stats(self):
        summary = _make_summary_with_kll()
        payload = to_json_dict(summary)
        restored = from_json_dict(payload)
        assert restored.kll_column_stats == summary.kll_column_stats
        assert restored.column_types["speed_sketch"] == ColumnType.KLL_FLOAT64


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------


class TestCliParsing:
    def test_parse_csv_names_splits_and_strips(self):
        assert _parse_csv_names("a,b, c ") == ["a", "b", "c"]

    def test_parse_csv_names_empty_returns_none(self):
        assert _parse_csv_names(None) is None
        assert _parse_csv_names("") is None
        assert _parse_csv_names(",, ") is None

    def test_summary_accepts_kll_flags(self, monkeypatch):
        """Invoke `table-check summary --help` to confirm flags parse."""
        runner = CliRunner()
        result = runner.invoke(main, ["summary", "--help"])
        assert result.exit_code == 0
        assert "--kll-cols" in result.output
        assert "--kll-int-cols" in result.output
        assert "--kll-abs-tol" in result.output
        assert "--kll-rel-tol" in result.output
        assert "--kll-rank-tol" not in result.output

    def test_diff_accepts_kll_flags(self):
        runner = CliRunner()
        result = runner.invoke(main, ["diff", "--help"])
        assert result.exit_code == 0
        assert "--kll-cols" in result.output
        assert "--kll-int-cols" in result.output
        assert "--kll-abs-tol" in result.output
        assert "--kll-rel-tol" in result.output
        assert "--kll-rank-tol" not in result.output

    def test_unknown_kll_column_errors_before_bq(self, monkeypatch):
        """A nonexistent --kll-cols target raises before any BQ job runs."""
        fake_field = bigquery.SchemaField("id", "INT64")
        fake_table = MagicMock()
        fake_table.schema = [fake_field]
        fake_client = MagicMock()
        fake_client.get_table.return_value = fake_table

        from table_identical_checks.backend.schema import get_table_schema

        with pytest.raises(ValueError, match="unknown column 'nope'"):
            get_table_schema(
                fake_client, "proj.ds.t", kll_float64_cols=["nope"]
            )

    def test_per_column_tolerance_on_kll_rejected_via_summary(self):
        """`--tolerance=speed_sketch:0.01` alongside `--kll-cols=speed_sketch`
        raises a clear error via _reject_kll_tolerance."""
        builder = _kll_builder()
        builder.tolerance_config = ToleranceConfig.parse("speed_sketch:0.01")
        with pytest.raises(ValueError, match="kll-abs-tol"):
            _reject_kll_tolerance(builder)
