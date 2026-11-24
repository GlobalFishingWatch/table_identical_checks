"""--expect-diff / --ignore-columns and the PASS/FAIL verdict."""

from __future__ import annotations

import json

import click
import pytest
from click.testing import CliRunner
from google.cloud import bigquery

from table_identical_checks.backend import ColumnType, build_verify_query
from table_identical_checks.backend.summary import (
    ComparisonSummary,
    from_json_dict,
    to_json_dict,
)
from table_identical_checks.cli import _resolve_column_patterns, main

COLUMNS = ["id", "name", "score", "regions.eez", "regions.fao", "created_ts"]


class TestResolveColumnPatterns:
    def test_globs_csv_and_repeats(self):
        resolved = _resolve_column_patterns(
            "--ignore-columns", ("regions.*", "name,*_ts"), COLUMNS, ["id"]
        )
        assert resolved == ["created_ts", "name", "regions.eez", "regions.fao"]

    def test_no_patterns(self):
        assert _resolve_column_patterns("--expect-diff", (), COLUMNS, ["id"]) == []

    def test_unmatched_pattern_is_an_error(self):
        with pytest.raises(click.ClickException, match="no column matches regionz"):
            _resolve_column_patterns("--expect-diff", ("regionz.*",), COLUMNS, ["id"])

    def test_key_column_is_an_error(self):
        with pytest.raises(click.ClickException, match="key column.*id"):
            _resolve_column_patterns("--ignore-columns", ("i*",), COLUMNS, ["id"])


def _summary(**kwargs) -> ComparisonSummary:
    defaults = dict(
        table_a="p.d.a",
        table_b="p.d.b",
        key_columns=["id"],
        rows_only_in_a=0,
        rows_only_in_b=0,
        rows_in_both_with_differences=2,
        rows_identical=8,
        total_rows_a=10,
        total_rows_b=10,
        numeric_column_stats={
            "score": {
                "max_abs_delta": 1.0,
                "max_rel_delta": 0.5,
                "avg_abs_delta": 0.75,
                "sum_abs_rel_delta": 1.0,
            }
        },
        string_column_mismatches={"name": 1},
        column_types={
            "name": ColumnType.STRING,
            "score": ColumnType.FLOAT,
            "value": ColumnType.INTEGER,
        },
        column_diff_counts={"name": 1, "score": 2, "value": 0},
    )
    return ComparisonSummary(**{**defaults, **kwargs})


class TestVerdict:
    def test_unexpected_differences_fail(self):
        summary = _summary(expected_diff_columns=["score"])
        assert summary.differing_columns == ["name", "score"]
        assert summary.failing_columns == ["name"]
        assert summary.verdict == "FAIL"

    def test_only_expected_differences_pass(self):
        summary = _summary(expected_diff_columns=["name", "score"])
        assert summary.verdict == "PASS"
        assert not summary.tables_identical

    def test_rows_on_one_side_fail_even_when_all_columns_expected(self):
        summary = _summary(expected_diff_columns=["name", "score"], rows_only_in_b=1)
        assert summary.verdict == "FAIL"

    def test_identical_tables_pass(self):
        summary = _summary(
            rows_in_both_with_differences=0,
            numeric_column_stats={},
            string_column_mismatches={},
            column_diff_counts={"name": 0, "score": 0, "value": 0},
        )
        assert summary.verdict == "PASS"

    def test_differences_within_tolerance_do_not_count(self):
        summary = _summary(
            rows_in_both_with_differences=0,
            numeric_column_stats={
                "score": {"outside_tolerance_count": 0, "within_tolerance_count": 2}
            },
            string_column_mismatches={},
            column_diff_counts={"name": 0, "score": 2, "value": 0},
        )
        assert summary.differing_columns == []
        assert summary.verdict == "PASS"

    def test_aborted_pipeline_uses_layer1_counts(self):
        summary = _summary(
            pipeline_aborted=True,
            numeric_column_stats={},
            string_column_mismatches={},
            expected_diff_columns=["score"],
        )
        assert summary.failing_columns == ["name"]

    def test_legacy_summary_without_counts_uses_stat_dicts(self):
        summary = _summary(column_diff_counts={}, expected_diff_columns=["name"])
        assert summary.failing_columns == ["score"]


class TestOutput:
    def test_json_round_trip_and_derived_fields(self):
        summary = _summary(expected_diff_columns=["score"], ignored_columns=["created_ts"])
        data = json.loads(json.dumps(to_json_dict(summary), default=str))

        assert data["verdict"] == "FAIL"
        assert data["failing_columns"] == ["name"]
        restored = from_json_dict(data)
        assert restored.expected_diff_columns == ["score"]
        assert restored.ignored_columns == ["created_ts"]
        assert restored.verdict == "FAIL"

    def test_old_json_without_new_fields_loads(self):
        data = to_json_dict(_summary())
        for name in ("verdict", "failing_columns", "expected_diff_columns", "ignored_columns"):
            data.pop(name)
        assert from_json_dict(data).expected_diff_columns == []

    def test_table_format_marks_expected_columns(self):
        summary = _summary(output_format="table", expected_diff_columns=["score"])
        text = str(summary)

        score_line = next(ln for ln in text.splitlines() if ln.startswith("score"))
        assert score_line.rstrip().endswith("EXPECT")
        assert "VERDICT: FAIL (unexpected differences in: name)" in text
        assert "Expected to differ: score" in text

    def test_verbose_format_notes_unchanged_expected_column(self):
        summary = _summary(expected_diff_columns=["name", "score", "value"])
        text = str(summary)

        assert "VERDICT: PASS" in text
        assert "Note: expected to differ but identical: value" in text

    def test_verify_query_lists_ignored_columns(self):
        sql = build_verify_query(_summary(ignored_columns=["created_ts"]))
        assert "--   created_ts (ignored)" in sql


SCHEMA = [
    bigquery.SchemaField("id", "INT64"),
    bigquery.SchemaField("name", "STRING"),
    bigquery.SchemaField("score", "FLOAT64"),
    bigquery.SchemaField("loaded_at", "TIMESTAMP"),
]


class TestSummaryCli:
    def test_expect_and_ignore_end_to_end(self, table_factory, tmp_path, monkeypatch):
        """score differs (expected), loaded_at differs (ignored): PASS."""
        table_a = table_factory(
            SCHEMA,
            [{"id": 1, "name": "x", "score": 1.0, "loaded_at": "2026-01-01T00:00:00Z"}],
        )
        table_b = table_factory(
            SCHEMA,
            [{"id": 1, "name": "x", "score": 2.0, "loaded_at": "2026-02-01T00:00:00Z"}],
        )
        out = tmp_path / "summary.json"
        args = [
            "summary",
            f"--table-a={table_a}",
            f"--table-b={table_b}",
            "--keys=id",
            "--format=table",
            f"--output-json={out}",
            "--expect-diff=score",
            "--ignore-columns=loaded_*",
        ]
        result = CliRunner().invoke(main, args)

        assert result.exit_code == 0, result.output
        data = json.loads(out.read_text())
        assert data["verdict"] == "PASS"
        assert data["ignored_columns"] == ["loaded_at"]
        assert "loaded_at" not in data["column_types"]
        assert "VERDICT: PASS" in result.output
