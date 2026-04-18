"""Tests for ARRAY column comparison support."""

import pytest
from google.cloud import bigquery

from table_identical_checks.backend import (
    ComparisonSummary,
    PipelineConfig,
    PipelineResult,
    QueryBuilder,
    from_json_dict,
    to_json_dict,
)
from table_identical_checks.backend.pipeline import _parse_pipeline_result
from table_identical_checks.backend.schema import (
    ColumnInfo,
    ColumnType,
    _flatten_fields,
)
from table_identical_checks.backend.summary import (
    TableFormatter,
    _reject_array_tolerance,
)
from table_identical_checks.backend.tolerance import ToleranceConfig


# ---------------------------------------------------------------------------
# Schema classification (no BigQuery)
# ---------------------------------------------------------------------------


class TestArraySchemaClassification:
    """Unit tests for ARRAY detection in _flatten_fields."""

    def test_array_of_strings(self):
        fields = [
            bigquery.SchemaField("id", "INT64"),
            bigquery.SchemaField("tags", "STRING", mode="REPEATED"),
        ]
        columns = _flatten_fields(fields)
        tags = next(c for c in columns if c.name == "tags")
        assert tags.column_type == ColumnType.ARRAY
        assert tags.bq_type == "ARRAY<STRING>"

    def test_array_of_int64(self):
        fields = [bigquery.SchemaField("counts", "INT64", mode="REPEATED")]
        columns = _flatten_fields(fields)
        assert columns[0].column_type == ColumnType.ARRAY
        assert columns[0].bq_type == "ARRAY<INT64>"

    def test_array_of_struct_scalars(self):
        fields = [
            bigquery.SchemaField(
                "receivers",
                "STRUCT",
                mode="REPEATED",
                fields=[
                    bigquery.SchemaField("receiver", "STRING"),
                    bigquery.SchemaField("pings", "INT64"),
                ],
            ),
        ]
        columns = _flatten_fields(fields)
        assert len(columns) == 1
        assert columns[0].name == "receivers"
        assert columns[0].column_type == ColumnType.ARRAY
        assert columns[0].bq_type == "ARRAY<STRUCT<receiver STRING, pings INT64>>"

    def test_array_of_struct_with_nested_struct_unsupported(self):
        fields = [
            bigquery.SchemaField(
                "data",
                "STRUCT",
                mode="REPEATED",
                fields=[
                    bigquery.SchemaField("name", "STRING"),
                    bigquery.SchemaField(
                        "inner",
                        "STRUCT",
                        fields=[bigquery.SchemaField("x", "INT64")],
                    ),
                ],
            ),
        ]
        columns = _flatten_fields(fields)
        assert columns[0].column_type == ColumnType.UNSUPPORTED

    def test_array_of_struct_with_repeated_child_unsupported(self):
        fields = [
            bigquery.SchemaField(
                "data",
                "STRUCT",
                mode="REPEATED",
                fields=[
                    bigquery.SchemaField("name", "STRING"),
                    bigquery.SchemaField("values", "INT64", mode="REPEATED"),
                ],
            ),
        ]
        columns = _flatten_fields(fields)
        assert columns[0].column_type == ColumnType.UNSUPPORTED

    def test_array_of_bytes_unsupported(self):
        fields = [bigquery.SchemaField("blob", "BYTES", mode="REPEATED")]
        columns = _flatten_fields(fields)
        assert columns[0].column_type == ColumnType.UNSUPPORTED
        assert columns[0].bq_type == "ARRAY<BYTES>"

    def test_array_of_json_unsupported(self):
        fields = [bigquery.SchemaField("payloads", "JSON", mode="REPEATED")]
        columns = _flatten_fields(fields)
        assert columns[0].column_type == ColumnType.UNSUPPORTED
        assert columns[0].bq_type == "ARRAY<JSON>"

    def test_array_of_struct_with_bytes_child_unsupported(self):
        fields = [
            bigquery.SchemaField(
                "items",
                "STRUCT",
                mode="REPEATED",
                fields=[
                    bigquery.SchemaField("name", "STRING"),
                    bigquery.SchemaField("blob", "BYTES"),
                ],
            ),
        ]
        columns = _flatten_fields(fields)
        assert columns[0].column_type == ColumnType.UNSUPPORTED


# ---------------------------------------------------------------------------
# Query builder SQL generation (no BigQuery)
# ---------------------------------------------------------------------------


def _array_builder() -> QueryBuilder:
    """Build a QueryBuilder with one key and one ARRAY column for snapshot tests."""
    return QueryBuilder(
        table_a="proj.ds.ta",
        table_b="proj.ds.tb",
        key_columns=["id"],
        columns=[
            ColumnInfo(name="id", bq_type="INT64", column_type=ColumnType.INTEGER),
            ColumnInfo(
                name="tags",
                bq_type="ARRAY<STRING>",
                column_type=ColumnType.ARRAY,
            ),
        ],
    )


class TestArraySqlSnapshots:
    """Snapshot tests for ARRAY SQL in pipeline script and diff query."""

    def test_l1_contains_canonical_equality(self):
        b = _array_builder()
        script = b.build_pipeline_script(max_diff_pct=1.0)
        assert (
            "TO_JSON_STRING(ARRAY(SELECT e FROM UNNEST(a.tags) AS e "
            "ORDER BY TO_JSON_STRING(e)))"
            in script
        )
        assert (
            "TO_JSON_STRING(ARRAY(SELECT e FROM UNNEST(b.tags) AS e "
            "ORDER BY TO_JSON_STRING(e)))"
            in script
        )
        # NULL-safety branch preserved
        assert "a.tags IS NULL AND b.tags IS NULL" in script
        # eq alias used
        assert "AS tags__eq" in script

    def test_l2_has_mismatch_and_len_delta(self):
        b = _array_builder()
        script = b.build_pipeline_script(max_diff_pct=1.0)
        assert "ARRAY_LENGTH(a.tags) - ARRAY_LENGTH(b.tags)" in script
        assert "AS tags__len_delta" in script
        assert "AS tags__mismatch" in script

    def test_l3_aggregates(self):
        b = _array_builder()
        script = b.build_pipeline_script(max_diff_pct=1.0)
        assert "COUNTIF(tags__mismatch) AS tags__mismatch_count" in script
        assert "MAX(ABS(tags__len_delta)) AS tags__max_abs_len_delta" in script
        assert "AVG(ABS(tags__len_delta)) AS tags__avg_abs_len_delta" in script

    def test_diff_query_contains_canonical_form(self):
        b = _array_builder()
        sql = b.build_diff_query()
        assert (
            "TO_JSON_STRING(ARRAY(SELECT e FROM UNNEST(a.tags) AS e "
            "ORDER BY TO_JSON_STRING(e)))"
            in sql
        )
        assert "ARRAY_LENGTH(a.tags) - ARRAY_LENGTH(b.tags)" in sql

    def test_safe_alias_for_dotted_array(self):
        """An ARRAY column from a flattened STRUCT keeps the dotted name unique."""
        b = QueryBuilder(
            table_a="proj.ds.ta",
            table_b="proj.ds.tb",
            key_columns=["id"],
            columns=[
                ColumnInfo(name="id", bq_type="INT64", column_type=ColumnType.INTEGER),
                ColumnInfo(
                    name="s.items",
                    bq_type="ARRAY<STRING>",
                    column_type=ColumnType.ARRAY,
                ),
            ],
        )
        script = b.build_pipeline_script(max_diff_pct=1.0)
        assert "AS s__items__eq" in script
        assert "AS s__items__mismatch" in script
        assert "AS s__items__len_delta" in script
        assert "AS s__items__mismatch_count" in script


# ---------------------------------------------------------------------------
# Pipeline result parsing (no BigQuery)
# ---------------------------------------------------------------------------


class _SyntheticRow:
    """Minimal stand-in for a bigquery.Row; supports dict(row) conversion."""

    def __init__(self, data: dict):
        self._data = data

    def keys(self):
        return self._data.keys()

    def __getitem__(self, key):
        return self._data[key]


class TestParsePipelineResult:
    """Unit tests for _parse_pipeline_result with ARRAY columns."""

    def test_array_stats_populated_when_mismatches(self):
        builder = _array_builder()
        row = _SyntheticRow(
            {
                "pipeline_status": "COMPLETED",
                "total_rows_a": 100,
                "total_rows_b": 100,
                "rows_only_in_a": 0,
                "rows_only_in_b": 0,
                "rows_in_both_with_differences": 5,
                "tags__diff_count": 5,
                "tags__mismatch_count": 5,
                "tags__max_abs_len_delta": 3,
                "tags__avg_abs_len_delta": 1.2,
                "total_differing_rows": 5,
            }
        )
        result = _parse_pipeline_result(row, builder)
        assert isinstance(result, PipelineResult)
        assert result.array_column_stats == {
            "tags": {
                "mismatch_count": 5,
                "max_abs_len_delta": 3,
                "avg_abs_len_delta": 1.2,
            }
        }

    def test_array_stats_skipped_when_zero_mismatches(self):
        builder = _array_builder()
        row = _SyntheticRow(
            {
                "pipeline_status": "COMPLETED",
                "total_rows_a": 100,
                "total_rows_b": 100,
                "rows_only_in_a": 0,
                "rows_only_in_b": 0,
                "rows_in_both_with_differences": 0,
                "tags__diff_count": 0,
                "tags__mismatch_count": 0,
                "tags__max_abs_len_delta": None,
                "tags__avg_abs_len_delta": None,
                "total_differing_rows": 0,
            }
        )
        result = _parse_pipeline_result(row, builder)
        assert result.array_column_stats == {}


# ---------------------------------------------------------------------------
# Tolerance rejection
# ---------------------------------------------------------------------------


class TestArrayToleranceRejection:
    """Tolerance on array columns must raise a clear error."""

    def test_rejects_abs_tolerance_on_array(self):
        builder = QueryBuilder(
            table_a="proj.ds.ta",
            table_b="proj.ds.tb",
            key_columns=["id"],
            columns=[
                ColumnInfo(name="id", bq_type="INT64", column_type=ColumnType.INTEGER),
                ColumnInfo(
                    name="tags",
                    bq_type="ARRAY<STRING>",
                    column_type=ColumnType.ARRAY,
                ),
            ],
            tolerance_config=ToleranceConfig.parse("tags:1.0"),
        )
        with pytest.raises(ValueError, match="array column 'tags'"):
            _reject_array_tolerance(builder)

    def test_rejects_rel_tolerance_on_array(self):
        builder = QueryBuilder(
            table_a="proj.ds.ta",
            table_b="proj.ds.tb",
            key_columns=["id"],
            columns=[
                ColumnInfo(name="id", bq_type="INT64", column_type=ColumnType.INTEGER),
                ColumnInfo(
                    name="tags",
                    bq_type="ARRAY<STRING>",
                    column_type=ColumnType.ARRAY,
                ),
            ],
            tolerance_config=ToleranceConfig.parse_rel("tags:0.01"),
        )
        with pytest.raises(ValueError, match="not supported"):
            _reject_array_tolerance(builder)

    def test_allows_tolerance_on_float_with_array_present(self):
        builder = QueryBuilder(
            table_a="proj.ds.ta",
            table_b="proj.ds.tb",
            key_columns=["id"],
            columns=[
                ColumnInfo(name="id", bq_type="INT64", column_type=ColumnType.INTEGER),
                ColumnInfo(name="v", bq_type="FLOAT64", column_type=ColumnType.FLOAT),
                ColumnInfo(
                    name="tags",
                    bq_type="ARRAY<STRING>",
                    column_type=ColumnType.ARRAY,
                ),
            ],
            tolerance_config=ToleranceConfig.parse("v:1e-6"),
        )
        # Should not raise
        _reject_array_tolerance(builder)


# ---------------------------------------------------------------------------
# TableFormatter rendering
# ---------------------------------------------------------------------------


def _make_summary_with_array(**overrides) -> ComparisonSummary:
    defaults = dict(
        table_a="proj.ds.a",
        table_b="proj.ds.b",
        key_columns=["id"],
        rows_only_in_a=0,
        rows_only_in_b=0,
        rows_in_both_with_differences=5,
        rows_identical=95,
        total_rows_a=100,
        total_rows_b=100,
        numeric_column_stats={},
        string_column_mismatches={},
        geography_column_stats={},
        array_column_stats={
            "tags": {
                "mismatch_count": 5,
                "max_abs_len_delta": 3,
                "avg_abs_len_delta": 1.2,
            }
        },
        column_types={"tags": ColumnType.ARRAY},
        column_diff_counts={"tags": 5},
        output_format="table",
    )
    defaults.update(overrides)
    return ComparisonSummary(**defaults)


class TestTableFormatterArray:
    """Tests for rendering ARRAY columns in TableFormatter."""

    def test_array_row_appears_with_arr_type(self):
        summary = _make_summary_with_array()
        out = TableFormatter().format(summary)
        assert "tags" in out
        assert "ARR" in out
        # mismatch count 5 should appear in the Diffs column
        assert "5" in out

    def test_array_row_shows_dashes_for_max_rel(self):
        summary = _make_summary_with_array()
        out = TableFormatter().format(summary)
        # Max rel should render as dash for arrays
        # Find the tags line and ensure it has a "-" in the MaxRel slot.
        tag_line = next(line for line in out.splitlines() if "tags" in line and "ARR" in line)
        assert " - " in tag_line or tag_line.rstrip().endswith("-") or "- " in tag_line

    def test_array_column_not_in_identical_list(self):
        summary = _make_summary_with_array(
            column_types={
                "tags": ColumnType.ARRAY,
                "other_col": ColumnType.STRING,
            }
        )
        identical = TableFormatter._identical_columns(summary)
        assert "tags" not in identical
        assert "other_col" in identical

    def test_roundtrip_json_preserves_array_stats(self):
        summary = _make_summary_with_array()
        payload = to_json_dict(summary)
        restored = from_json_dict(payload)
        assert restored.array_column_stats == summary.array_column_stats
