"""Tests for the compact TableFormatter and supporting utilities."""

import pytest

from table_identical_checks.backend.schema import ColumnType
from table_identical_checks.backend.summary import (
    ComparisonSummary,
    DuplicateInfo,
    TableFormatter,
    VerboseFormatter,
    _ColumnRow,
    _fmt_count,
    _fmt_number,
    _truncate,
    get_formatter,
)

# ---------------------------------------------------------------------------
# Helpers to build ComparisonSummary objects without BigQuery
# ---------------------------------------------------------------------------


def _make_summary(
    *,
    numeric_column_stats: dict[str, dict] | None = None,
    string_column_mismatches: dict[str, int] | None = None,
    column_types: dict[str, ColumnType] | None = None,
    tolerance_config: dict[str, float] | None = None,
    rows_only_in_a: int = 0,
    rows_only_in_b: int = 0,
    rows_in_both_with_differences: int = 100,
    rows_identical: int = 900,
    total_rows_a: int = 1_000,
    total_rows_b: int = 1_000,
    rows_in_both_with_differences_pretolerance: int | None = None,
    rows_identical_pretolerance: int | None = None,
    rows_only_in_a_pretolerance: int | None = None,
    rows_only_in_b_pretolerance: int | None = None,
    column_sort_order: str = "alphabetical",
    output_format: str = "table",
    duplicate_info_a: DuplicateInfo | None = None,
    duplicate_info_b: DuplicateInfo | None = None,
) -> ComparisonSummary:
    return ComparisonSummary(
        table_a="proj.ds.table_a",
        table_b="proj.ds.table_b",
        key_columns=["id"],
        rows_only_in_a=rows_only_in_a,
        rows_only_in_b=rows_only_in_b,
        rows_in_both_with_differences=rows_in_both_with_differences,
        rows_identical=rows_identical,
        total_rows_a=total_rows_a,
        total_rows_b=total_rows_b,
        numeric_column_stats=numeric_column_stats or {},
        string_column_mismatches=string_column_mismatches or {},
        column_types=column_types or {},
        tolerance_config=tolerance_config,
        rows_only_in_a_pretolerance=rows_only_in_a_pretolerance,
        rows_only_in_b_pretolerance=rows_only_in_b_pretolerance,
        rows_in_both_with_differences_pretolerance=rows_in_both_with_differences_pretolerance,
        rows_identical_pretolerance=rows_identical_pretolerance,
        column_sort_order=column_sort_order,
        output_format=output_format,
        duplicate_info_a=duplicate_info_a,
        duplicate_info_b=duplicate_info_b,
    )


# ---------------------------------------------------------------------------
# Number formatting
# ---------------------------------------------------------------------------


class TestFmtNumber:
    """Tests for _fmt_number."""

    def test_none_returns_dash(self):
        assert _fmt_number(None) == "-"

    def test_integer(self):
        assert _fmt_number(12345) == "12,345"

    def test_zero_float(self):
        assert _fmt_number(0.0) == "0.0e+00"

    def test_small_value_scientific(self):
        result = _fmt_number(3.05e-10)
        assert "e" in result.lower()

    def test_large_value_scientific(self):
        result = _fmt_number(15000.0)
        assert "e" in result.lower()

    def test_mid_range_plain(self):
        result = _fmt_number(42.5)
        assert result == "42.50"

    def test_negative_small(self):
        result = _fmt_number(-1.2e-8)
        assert "e" in result.lower()

    def test_boundary_0_001(self):
        # Exactly 0.001 should NOT be in scientific notation
        result = _fmt_number(0.001)
        # 0.001 is not < 0.001, so it should remain plain
        assert result == "0.00"

    def test_just_below_0_001(self):
        result = _fmt_number(0.0009)
        assert "e" in result.lower()


class TestFmtCount:
    """Tests for _fmt_count."""

    def test_none_returns_dash(self):
        assert _fmt_count(None) == "-"

    def test_zero(self):
        assert _fmt_count(0) == "0"

    def test_thousands_separator(self):
        assert _fmt_count(15137) == "15,137"


class TestTruncate:
    """Tests for _truncate."""

    def test_short_name_unchanged(self):
        assert _truncate("lat", 22) == "lat"

    def test_exact_length(self):
        name = "a" * 22
        assert _truncate(name, 22) == name

    def test_long_name_truncated(self):
        name = "distance_from_satellite_km"
        result = _truncate(name, 22)
        assert len(result) == 22
        assert result.endswith("\u2026")

    def test_very_long_name(self):
        name = "x" * 100
        result = _truncate(name, 10)
        assert len(result) == 10
        assert result == "x" * 9 + "\u2026"


# ---------------------------------------------------------------------------
# _ColumnRow status logic
# ---------------------------------------------------------------------------


class TestColumnRowStatus:
    """Tests for _ColumnRow.status property."""

    def test_float_with_tolerance_ok(self):
        row = _ColumnRow(
            name="lat",
            col_type=ColumnType.FLOAT,
            outside_tolerance=0,
            within_tolerance=100,
            has_tolerance=True,
        )
        assert row.status == "OK"

    def test_float_with_tolerance_nok(self):
        row = _ColumnRow(
            name="lat",
            col_type=ColumnType.FLOAT,
            outside_tolerance=5,
            within_tolerance=95,
            has_tolerance=True,
        )
        assert row.status == "NOK"

    def test_float_without_tolerance_nok(self):
        """A float column appearing in the table (i.e. not identical) is NOK."""
        row = _ColumnRow(
            name="speed",
            col_type=ColumnType.FLOAT,
            max_abs=0.01,
            has_tolerance=False,
        )
        assert row.status == "NOK"

    def test_string_with_mismatches_nok(self):
        row = _ColumnRow(
            name="flag",
            col_type=ColumnType.STRING,
            mismatch_count=731,
        )
        assert row.status == "NOK"

    def test_string_zero_mismatches_ok(self):
        row = _ColumnRow(
            name="flag",
            col_type=ColumnType.STRING,
            mismatch_count=0,
        )
        assert row.status == "OK"

    def test_integer_without_tolerance_nok(self):
        row = _ColumnRow(
            name="course",
            col_type=ColumnType.INTEGER,
            max_abs=180,
            has_tolerance=False,
        )
        assert row.status == "NOK"

    def test_boolean_without_tolerance_nok(self):
        row = _ColumnRow(
            name="active",
            col_type=ColumnType.BOOLEAN,
            max_abs=1,
            has_tolerance=False,
        )
        assert row.status == "NOK"


# ---------------------------------------------------------------------------
# Identical column detection
# ---------------------------------------------------------------------------


class TestIdenticalColumns:
    """Tests for identical column detection in TableFormatter."""

    def test_all_columns_have_diffs(self):
        s = _make_summary(
            numeric_column_stats={
                "lat": {"max_abs_delta": 0.01},
                "lon": {"max_abs_delta": 0.02},
            },
            column_types={
                "lat": ColumnType.FLOAT,
                "lon": ColumnType.FLOAT,
            },
        )
        result = TableFormatter._identical_columns(s)
        assert result == []

    def test_some_columns_identical(self):
        s = _make_summary(
            numeric_column_stats={
                "lat": {"max_abs_delta": 0.01},
            },
            string_column_mismatches={
                "flag": 10,
            },
            column_types={
                "lat": ColumnType.FLOAT,
                "lon": ColumnType.FLOAT,
                "speed": ColumnType.FLOAT,
                "flag": ColumnType.STRING,
                "name": ColumnType.STRING,
            },
        )
        result = TableFormatter._identical_columns(s)
        # lon, speed, and name have no diffs
        assert result == ["lon", "name", "speed"]

    def test_all_columns_identical(self):
        s = _make_summary(
            column_types={
                "lat": ColumnType.FLOAT,
                "lon": ColumnType.FLOAT,
            },
            rows_in_both_with_differences=0,
            rows_identical=1000,
        )
        result = TableFormatter._identical_columns(s)
        assert result == ["lat", "lon"]

    def test_no_value_columns(self):
        s = _make_summary(column_types={})
        result = TableFormatter._identical_columns(s)
        assert result == []


# ---------------------------------------------------------------------------
# Table format output: with tolerance
# ---------------------------------------------------------------------------


class TestTableFormatWithTolerance:
    """Tests for table format output when tolerance is configured."""

    def test_basic_output_structure(self):
        s = _make_summary(
            numeric_column_stats={
                "nnet_score": {
                    "max_abs_delta": 1.0,
                    "max_rel_delta": 0.5,
                    "avg_abs_delta": 0.025,
                    "sum_abs_rel_delta": 583.0,
                    "within_tolerance_count": 100,
                    "outside_tolerance_count": 731,
                },
                "lat": {
                    "max_abs_delta": 1.2e-4,
                    "max_rel_delta": 4.6e-6,
                    "avg_abs_delta": 1.2e-8,
                    "sum_abs_rel_delta": 1.0,
                    "within_tolerance_count": 15137,
                    "outside_tolerance_count": 731,
                },
            },
            column_types={
                "nnet_score": ColumnType.FLOAT,
                "lat": ColumnType.FLOAT,
                "col_a": ColumnType.FLOAT,
            },
            tolerance_config={"nnet_score": 1e-9, "lat": 1e-9, "col_a": 1e-9},
            rows_in_both_with_differences=57799,
            rows_identical=7942201,
            total_rows_a=8_000_000,
            total_rows_b=8_000_000,
            rows_in_both_with_differences_pretolerance=61953,
            rows_identical_pretolerance=7938047,
            rows_only_in_a_pretolerance=0,
            rows_only_in_b_pretolerance=0,
        )
        output = str(s)

        # Verify structural elements
        assert "table comparison" in output
        assert "proj.ds.table_a vs proj.ds.table_b" in output
        assert "keys: id" in output
        assert "tol:" in output
        assert "Identical columns: col_a" in output
        assert "DIFFERENCES FOUND" in output

    def test_tolerance_columns_present(self):
        s = _make_summary(
            numeric_column_stats={
                "lat": {
                    "max_abs_delta": 1.2e-4,
                    "max_rel_delta": 4.6e-6,
                    "avg_abs_delta": 1.2e-8,
                    "sum_abs_rel_delta": 1.0,
                    "within_tolerance_count": 15137,
                    "outside_tolerance_count": 731,
                },
            },
            column_types={"lat": ColumnType.FLOAT},
            tolerance_config={"lat": 1e-9},
            rows_only_in_a_pretolerance=0,
            rows_only_in_b_pretolerance=0,
            rows_in_both_with_differences_pretolerance=200,
            rows_identical_pretolerance=800,
        )
        output = str(s)

        assert "Exc.tol" in output
        assert "Within tol" in output
        assert "731" in output
        assert "15,137" in output

    def test_filtered_count_shown(self):
        s = _make_summary(
            numeric_column_stats={
                "val": {
                    "max_abs_delta": 0.01,
                    "max_rel_delta": 0.001,
                    "avg_abs_delta": 0.005,
                    "within_tolerance_count": 50,
                    "outside_tolerance_count": 50,
                },
            },
            column_types={"val": ColumnType.FLOAT},
            tolerance_config={"val": 1e-9},
            rows_in_both_with_differences=50,
            rows_identical=950,
            rows_in_both_with_differences_pretolerance=100,
            rows_identical_pretolerance=900,
            rows_only_in_a_pretolerance=0,
            rows_only_in_b_pretolerance=0,
        )
        output = str(s)
        # Should show "diffs: 100 (filtered 50)"
        assert "diffs: 100" in output
        assert "filtered 50" in output


# ---------------------------------------------------------------------------
# Table format output: without tolerance
# ---------------------------------------------------------------------------


class TestTableFormatWithoutTolerance:
    """Tests for table format output when no tolerance is configured."""

    def test_no_tolerance_columns_in_header(self):
        s = _make_summary(
            numeric_column_stats={
                "speed": {
                    "max_abs_delta": 2.5,
                    "max_rel_delta": 0.1,
                    "avg_abs_delta": 1.0,
                },
            },
            column_types={"speed": ColumnType.FLOAT},
        )
        output = str(s)
        assert "Exc.tol" not in output
        assert "Within tol" not in output

    def test_status_nok_for_numeric_without_tolerance(self):
        s = _make_summary(
            numeric_column_stats={
                "speed": {
                    "max_abs_delta": 2.5,
                    "max_rel_delta": 0.1,
                    "avg_abs_delta": 1.0,
                },
            },
            column_types={"speed": ColumnType.FLOAT},
        )
        output = str(s)
        assert "NOK" in output

    def test_string_column_mismatches_shown(self):
        s = _make_summary(
            string_column_mismatches={"flag": 731},
            column_types={"flag": ColumnType.STRING},
        )
        output = str(s)
        assert "flag" in output
        assert "STR" in output
        assert "731 mismatches" in output
        assert "NOK" in output

    def test_diffs_count_without_tolerance(self):
        s = _make_summary(
            numeric_column_stats={
                "speed": {
                    "max_abs_delta": 2.5,
                    "max_rel_delta": 0.1,
                    "avg_abs_delta": 1.0,
                },
            },
            column_types={"speed": ColumnType.FLOAT},
            rows_in_both_with_differences=42,
        )
        output = str(s)
        # No "(filtered ...)" when no tolerance
        assert "diffs: 42" in output
        assert "filtered" not in output


# ---------------------------------------------------------------------------
# Table format: mixed column types
# ---------------------------------------------------------------------------


class TestTableFormatMixedTypes:
    """Tests with numeric and string columns together."""

    def test_mixed_numeric_and_string(self):
        s = _make_summary(
            numeric_column_stats={
                "lat": {
                    "max_abs_delta": 1.2e-4,
                    "max_rel_delta": 4.6e-6,
                    "avg_abs_delta": 1.2e-8,
                },
                "course": {
                    "max_abs_delta": 180,
                    "max_rel_delta": 0.50,
                    "avg_abs_delta": 12.5,
                },
            },
            string_column_mismatches={"flag": 731},
            column_types={
                "lat": ColumnType.FLOAT,
                "course": ColumnType.INTEGER,
                "flag": ColumnType.STRING,
                "identical_col": ColumnType.FLOAT,
            },
        )
        output = str(s)

        # All non-identical columns should appear
        assert "lat" in output
        assert "course" in output
        assert "flag" in output
        # identical_col should be in the identical list
        assert "Identical columns: identical_col" in output

    def test_integer_type_abbreviation(self):
        s = _make_summary(
            numeric_column_stats={
                "count_val": {
                    "max_abs_delta": 5,
                    "max_rel_delta": 0.1,
                    "avg_abs_delta": 2.0,
                },
            },
            column_types={"count_val": ColumnType.INTEGER},
        )
        output = str(s)
        assert "INT" in output

    def test_boolean_type_abbreviation(self):
        s = _make_summary(
            numeric_column_stats={
                "active": {
                    "max_abs_delta": 1,
                    "max_rel_delta": 1.0,
                    "avg_abs_delta": 0.5,
                },
            },
            column_types={"active": ColumnType.BOOLEAN},
        )
        output = str(s)
        assert "BOOL" in output

    def test_timestamp_type_abbreviation(self):
        s = _make_summary(
            numeric_column_stats={
                "created_at": {
                    "max_abs_delta": 3600,
                    "max_rel_delta": 0.001,
                    "avg_abs_delta": 120.0,
                },
            },
            column_types={"created_at": ColumnType.TIMESTAMP},
        )
        output = str(s)
        assert "TS" in output


# ---------------------------------------------------------------------------
# Table format: identical tables
# ---------------------------------------------------------------------------


class TestTableFormatIdentical:
    """Tests for when tables are identical."""

    def test_identical_tables(self):
        s = _make_summary(
            column_types={
                "lat": ColumnType.FLOAT,
                "lon": ColumnType.FLOAT,
            },
            rows_in_both_with_differences=0,
            rows_identical=1000,
        )
        output = str(s)
        assert "IDENTICAL" in output
        assert "Identical columns: lat, lon" in output

    def test_identical_no_column_delta_table(self):
        """When all columns are identical, no delta table should appear."""
        s = _make_summary(
            column_types={
                "lat": ColumnType.FLOAT,
                "lon": ColumnType.FLOAT,
            },
            rows_in_both_with_differences=0,
            rows_identical=1000,
        )
        output = str(s)
        # The column delta header should not be rendered
        assert "MaxAbs" not in output


# ---------------------------------------------------------------------------
# OK/NOK status in the rendered output
# ---------------------------------------------------------------------------


class TestOkNokInOutput:
    """Verify OK and NOK labels appear correctly in the rendered table."""

    def test_ok_status_when_all_within_tolerance(self):
        s = _make_summary(
            numeric_column_stats={
                "dist": {
                    "max_abs_delta": 3.1e-10,
                    "max_rel_delta": 2.4e-10,
                    "avg_abs_delta": 1.2e-10,
                    "within_tolerance_count": 8737,
                    "outside_tolerance_count": 0,
                },
            },
            column_types={"dist": ColumnType.FLOAT},
            tolerance_config={"dist": 1e-9},
            rows_only_in_a_pretolerance=0,
            rows_only_in_b_pretolerance=0,
            rows_in_both_with_differences_pretolerance=100,
            rows_identical_pretolerance=900,
        )
        output = str(s)
        # The only data row should have "OK"
        data_lines = [line for line in output.split("\n") if "dist" in line and "FLT" in line]
        assert len(data_lines) == 1
        assert "OK" in data_lines[0]
        # Make sure it's not "NOK"
        assert "NOK" not in data_lines[0]

    def test_nok_status_when_outside_tolerance(self):
        s = _make_summary(
            numeric_column_stats={
                "nnet_score": {
                    "max_abs_delta": 1.0,
                    "max_rel_delta": 0.5,
                    "avg_abs_delta": 0.025,
                    "within_tolerance_count": 100,
                    "outside_tolerance_count": 731,
                },
            },
            column_types={"nnet_score": ColumnType.FLOAT},
            tolerance_config={"nnet_score": 1e-9},
            rows_only_in_a_pretolerance=0,
            rows_only_in_b_pretolerance=0,
            rows_in_both_with_differences_pretolerance=831,
            rows_identical_pretolerance=169,
        )
        output = str(s)
        data_lines = [line for line in output.split("\n") if "nnet_score" in line and "FLT" in line]
        assert len(data_lines) == 1
        assert "NOK" in data_lines[0]


# ---------------------------------------------------------------------------
# Column name truncation in output
# ---------------------------------------------------------------------------


class TestColumnNameTruncation:
    """Tests for long column name handling in table output."""

    def test_long_column_name_truncated(self):
        long_name = "very_long_column_name_that_exceeds_max"
        s = _make_summary(
            numeric_column_stats={
                long_name: {
                    "max_abs_delta": 0.01,
                    "max_rel_delta": 0.001,
                    "avg_abs_delta": 0.005,
                },
            },
            column_types={long_name: ColumnType.FLOAT},
        )
        output = str(s)
        # The full name should not appear; instead a truncated version should
        assert long_name not in output
        # The unicode ellipsis should be present
        assert "\u2026" in output

    def test_short_column_name_not_truncated(self):
        s = _make_summary(
            numeric_column_stats={
                "lat": {
                    "max_abs_delta": 0.01,
                    "max_rel_delta": 0.001,
                    "avg_abs_delta": 0.005,
                },
            },
            column_types={"lat": ColumnType.FLOAT},
        )
        output = str(s)
        assert "lat" in output
        assert "\u2026" not in output


# ---------------------------------------------------------------------------
# get_formatter dispatch
# ---------------------------------------------------------------------------


class TestGetFormatter:
    """Tests for the formatter registry."""

    def test_verbose_formatter(self):
        fmt = get_formatter("verbose")
        assert isinstance(fmt, VerboseFormatter)

    def test_table_formatter(self):
        fmt = get_formatter("table")
        assert isinstance(fmt, TableFormatter)

    def test_unknown_format_raises(self):
        with pytest.raises(ValueError, match="Unknown output format"):
            get_formatter("json")


# ---------------------------------------------------------------------------
# VerboseFormatter backward compatibility
# ---------------------------------------------------------------------------


class TestVerboseFormatterBackwardCompat:
    """Verify the VerboseFormatter still produces the same output structure."""

    def test_verbose_str_output(self):
        s = _make_summary(
            numeric_column_stats={
                "lat": {
                    "max_abs_delta": 0.01,
                    "max_rel_delta": 0.001,
                    "avg_abs_delta": 0.005,
                },
            },
            column_types={"lat": ColumnType.FLOAT},
            output_format="verbose",
        )
        output = str(s)
        assert "TABLE COMPARISON SUMMARY" in output
        assert "NUMERIC COLUMN DELTAS" in output
        assert "RESULT:" in output

    def test_verbose_tolerance_output(self):
        s = _make_summary(
            numeric_column_stats={
                "lat": {
                    "max_abs_delta": 0.01,
                    "max_rel_delta": 0.001,
                    "avg_abs_delta": 0.005,
                    "within_tolerance_count": 100,
                    "outside_tolerance_count": 5,
                },
            },
            column_types={"lat": ColumnType.FLOAT},
            tolerance_config={"lat": 1e-9},
            rows_only_in_a_pretolerance=0,
            rows_only_in_b_pretolerance=0,
            rows_in_both_with_differences_pretolerance=200,
            rows_identical_pretolerance=800,
            output_format="verbose",
        )
        output = str(s)
        assert "TOLERANCE CONFIGURATION" in output
        assert "ALL DIFFERENCES" in output
        assert "SIGNIFICANT DIFFERENCES" in output
        assert "within_tolerance:" in output


# ---------------------------------------------------------------------------
# Significance sorting in table format
# ---------------------------------------------------------------------------


class TestTableFormatSorting:
    """Tests for column sorting in table format."""

    def test_alphabetical_sorting(self):
        s = _make_summary(
            numeric_column_stats={
                "zzz": {
                    "max_abs_delta": 0.01,
                    "max_rel_delta": 0.001,
                    "avg_abs_delta": 0.005,
                    "sum_abs_rel_delta": 100.0,
                },
                "aaa": {
                    "max_abs_delta": 100.0,
                    "max_rel_delta": 10.0,
                    "avg_abs_delta": 50.0,
                    "sum_abs_rel_delta": 1000.0,
                },
            },
            column_types={
                "zzz": ColumnType.FLOAT,
                "aaa": ColumnType.FLOAT,
            },
            column_sort_order="alphabetical",
        )
        output = str(s)
        # aaa should come before zzz
        assert output.index("aaa") < output.index("zzz")

    def test_significance_sorting(self):
        s = _make_summary(
            numeric_column_stats={
                "zzz": {
                    "max_abs_delta": 0.01,
                    "max_rel_delta": 0.001,
                    "avg_abs_delta": 0.005,
                    "sum_abs_rel_delta": 1000.0,
                },
                "aaa": {
                    "max_abs_delta": 100.0,
                    "max_rel_delta": 10.0,
                    "avg_abs_delta": 50.0,
                    "sum_abs_rel_delta": 1.0,
                },
            },
            column_types={
                "zzz": ColumnType.FLOAT,
                "aaa": ColumnType.FLOAT,
            },
            column_sort_order="significance",
        )
        output = str(s)
        # zzz has higher sum_abs_rel_delta, so should come first
        assert output.index("zzz") < output.index("aaa")


# ---------------------------------------------------------------------------
# Per-column tolerance display
# ---------------------------------------------------------------------------


class TestPerColumnToleranceDisplay:
    """Tests for per-column tolerance display in table format."""

    def test_per_column_tolerance_label(self):
        s = _make_summary(
            numeric_column_stats={
                "lat": {
                    "max_abs_delta": 1e-4,
                    "max_rel_delta": 1e-6,
                    "avg_abs_delta": 1e-8,
                    "within_tolerance_count": 100,
                    "outside_tolerance_count": 5,
                },
                "lon": {
                    "max_abs_delta": 2e-4,
                    "max_rel_delta": 2e-6,
                    "avg_abs_delta": 2e-8,
                    "within_tolerance_count": 200,
                    "outside_tolerance_count": 10,
                },
            },
            column_types={
                "lat": ColumnType.FLOAT,
                "lon": ColumnType.FLOAT,
            },
            tolerance_config={"lat": 1e-9, "lon": 1e-6},
            rows_only_in_a_pretolerance=0,
            rows_only_in_b_pretolerance=0,
            rows_in_both_with_differences_pretolerance=200,
            rows_identical_pretolerance=800,
        )
        output = str(s)
        # Different tolerance values -> should say "per-column"
        assert "tol: per-column" in output

    def test_uniform_tolerance_label(self):
        s = _make_summary(
            numeric_column_stats={
                "lat": {
                    "max_abs_delta": 1e-4,
                    "max_rel_delta": 1e-6,
                    "avg_abs_delta": 1e-8,
                    "within_tolerance_count": 100,
                    "outside_tolerance_count": 5,
                },
            },
            column_types={"lat": ColumnType.FLOAT},
            tolerance_config={"lat": 1e-9},
            rows_only_in_a_pretolerance=0,
            rows_only_in_b_pretolerance=0,
            rows_in_both_with_differences_pretolerance=200,
            rows_identical_pretolerance=800,
        )
        output = str(s)
        assert "tol: 1e-09" in output


# ---------------------------------------------------------------------------
# Non-float column without tolerance: dash for tolerance columns
# ---------------------------------------------------------------------------


class TestNonFloatToleranceColumns:
    """When tolerance is configured, non-float columns show '-' for tolerance cols."""

    def test_integer_column_shows_dash_for_tolerance(self):
        s = _make_summary(
            numeric_column_stats={
                "lat": {
                    "max_abs_delta": 1e-4,
                    "max_rel_delta": 1e-6,
                    "avg_abs_delta": 1e-8,
                    "within_tolerance_count": 100,
                    "outside_tolerance_count": 5,
                },
                "course": {
                    "max_abs_delta": 180,
                    "max_rel_delta": 0.5,
                    "avg_abs_delta": 12.5,
                    # No tolerance keys for integer column
                },
            },
            column_types={
                "lat": ColumnType.FLOAT,
                "course": ColumnType.INTEGER,
            },
            tolerance_config={"lat": 1e-9},
            rows_only_in_a_pretolerance=0,
            rows_only_in_b_pretolerance=0,
            rows_in_both_with_differences_pretolerance=200,
            rows_identical_pretolerance=800,
        )
        output = str(s)
        # Find the line for course
        course_lines = [line for line in output.split("\n") if "course" in line and "INT" in line]
        assert len(course_lines) == 1
        # Should contain dashes for the tolerance columns
        # The line should have "-" where tolerance counts would be
        course_line = course_lines[0]
        # Split by multiple spaces to get fields
        # The tolerance columns should show "-"
        assert "NOK" in course_line


# ---------------------------------------------------------------------------
# Duplicate key warning tests
# ---------------------------------------------------------------------------


class TestDuplicateWarningTable:
    """Tests for duplicate key warnings in TableFormatter output."""

    def test_no_duplicates_no_warning(self):
        """No warning when both tables have no duplicates."""
        s = _make_summary(
            duplicate_info_a=DuplicateInfo(0, 0, 0),
            duplicate_info_b=DuplicateInfo(0, 0, 0),
        )
        output = str(s)
        assert "DUPLICATE" not in output

    def test_no_duplicate_info_no_warning(self):
        """No warning when duplicate info was not collected."""
        s = _make_summary()
        output = str(s)
        assert "DUPLICATE" not in output

    def test_duplicates_in_table_a_only(self):
        """Warning shown when only table A has duplicates."""
        s = _make_summary(
            duplicate_info_a=DuplicateInfo(5, 12, 3),
            duplicate_info_b=DuplicateInfo(0, 0, 0),
        )
        output = str(s)
        assert "DUPLICATE KEYS DETECTED" in output
        assert "Table A: 5 duplicate key(s)" in output
        assert "12 rows affected" in output
        assert "max 3x per key" in output
        # Table B should not appear in the duplicate warning section
        assert "Table B:" not in output

    def test_duplicates_in_both_tables(self):
        """Warning shown for both tables when both have duplicates."""
        s = _make_summary(
            duplicate_info_a=DuplicateInfo(5, 12, 3),
            duplicate_info_b=DuplicateInfo(10, 25, 4),
        )
        output = str(s)
        assert "DUPLICATE KEYS DETECTED" in output
        assert "Table A: 5 duplicate key(s)" in output
        assert "Table B: 10 duplicate key(s)" in output

    def test_duplicates_warning_appears_before_row_counts(self):
        """Duplicate warning should appear before the row counts section."""
        s = _make_summary(
            duplicate_info_a=DuplicateInfo(5, 12, 3),
            duplicate_info_b=DuplicateInfo(0, 0, 0),
        )
        output = str(s)
        dup_pos = output.index("DUPLICATE KEYS DETECTED")
        rows_pos = output.index("rows:")
        assert dup_pos < rows_pos

    def test_large_duplicate_counts_formatted(self):
        """Large duplicate counts are formatted with commas."""
        s = _make_summary(
            duplicate_info_a=DuplicateInfo(1_234, 5_678, 99),
            duplicate_info_b=DuplicateInfo(0, 0, 0),
        )
        output = str(s)
        assert "1,234 duplicate key(s)" in output
        assert "5,678 rows affected" in output


class TestDuplicateWarningVerbose:
    """Tests for duplicate key warnings in VerboseFormatter output."""

    def test_no_duplicates_no_warning(self):
        """No warning in verbose output when both tables have no duplicates."""
        s = _make_summary(
            output_format="verbose",
            duplicate_info_a=DuplicateInfo(0, 0, 0),
            duplicate_info_b=DuplicateInfo(0, 0, 0),
        )
        output = str(s)
        assert "DUPLICATE" not in output

    def test_duplicates_shown_in_verbose(self):
        """Verbose formatter shows duplicate warnings."""
        s = _make_summary(
            output_format="verbose",
            duplicate_info_a=DuplicateInfo(3, 8, 4),
            duplicate_info_b=DuplicateInfo(0, 0, 0),
        )
        output = str(s)
        assert "DUPLICATE KEYS DETECTED" in output
        assert "Table A: 3 duplicate key(s)" in output

    def test_duplicates_warning_before_row_counts_verbose(self):
        """Duplicate warning should appear before ROW COUNTS in verbose."""
        s = _make_summary(
            output_format="verbose",
            duplicate_info_a=DuplicateInfo(1, 2, 2),
            duplicate_info_b=DuplicateInfo(0, 0, 0),
        )
        output = str(s)
        dup_pos = output.index("DUPLICATE KEYS DETECTED")
        rows_pos = output.index("ROW COUNTS")
        assert dup_pos < rows_pos


class TestDuplicateInfo:
    """Tests for DuplicateInfo dataclass."""

    def test_has_duplicates_true(self):
        info = DuplicateInfo(1, 2, 2)
        assert info.has_duplicates is True

    def test_has_duplicates_false(self):
        info = DuplicateInfo(0, 0, 0)
        assert info.has_duplicates is False
