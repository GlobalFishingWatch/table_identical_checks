"""Exit codes of the console entry point: 0 PASS, 1 FAIL, 2 error."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from google.api_core.exceptions import BadRequest

from table_identical_checks.backend import ColumnType
from table_identical_checks.backend.summary import ComparisonSummary, to_json_dict
from table_identical_checks.cli import run


def _exit_code(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as exc:
        run(argv)
    return exc.value.code


def _summary_json(tmp_path, **kwargs) -> str:
    defaults = dict(
        table_a="p.d.a",
        table_b="p.d.b",
        key_columns=["id"],
        rows_only_in_a=0,
        rows_only_in_b=0,
        rows_in_both_with_differences=0,
        rows_identical=1,
        total_rows_a=1,
        total_rows_b=1,
        numeric_column_stats={},
        string_column_mismatches={},
        column_types={"value": ColumnType.INTEGER},
        column_diff_counts={"value": 0},
    )
    summary = ComparisonSummary(**{**defaults, **kwargs})
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(to_json_dict(summary), default=str))
    return str(path)


def test_format_pass_exits_0(tmp_path):
    assert _exit_code(["format", f"--input-json={_summary_json(tmp_path)}"]) == 0


def test_format_fail_exits_1(tmp_path):
    path = _summary_json(tmp_path, rows_only_in_a=3)
    assert _exit_code(["format", f"--input-json={path}"]) == 1


def test_usage_error_exits_2():
    assert _exit_code(["summary", "--no-such-option"]) == 2


def test_click_exception_exits_2(tmp_path):
    assert _exit_code(["format", f"--input-json={tmp_path}/missing.json"]) == 2


def test_uncaught_bigquery_error_exits_2(tmp_path):
    path = _summary_json(tmp_path)
    with patch(
        "table_identical_checks.cli.from_json_dict", side_effect=BadRequest("boom")
    ):
        assert _exit_code(["format", f"--input-json={path}"]) == 2


def test_help_exits_0():
    assert _exit_code(["--help"]) == 0


def test_verify_query_exits_0(tmp_path):
    path = _summary_json(tmp_path, rows_only_in_a=3)
    assert _exit_code(["verify-query", f"--input-json={path}"]) == 0
