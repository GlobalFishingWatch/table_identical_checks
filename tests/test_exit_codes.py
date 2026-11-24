"""Exit codes of the console entry point: 0 PASS, 1 FAIL, 2 error."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import click
import pytest
from google.api_core.exceptions import BadRequest

from table_identical_checks.backend import ColumnInfo, ColumnType
from table_identical_checks.backend.summary import (
    ComparisonSummary,
    from_json_dict,
    to_json_dict,
)
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


def test_version_exits_0():
    assert _exit_code(["--version"]) == 0


def test_abort_exits_2(tmp_path, capsys):
    path = _summary_json(tmp_path)
    with patch("table_identical_checks.cli.from_json_dict", side_effect=click.Abort()):
        assert _exit_code(["format", f"--input-json={path}"]) == 2
    assert "Aborted!" in capsys.readouterr().err


def test_exit_1_from_inside_click_is_an_error_not_fail():
    """click exits 1 itself on a broken pipe; that must not read as FAIL."""
    with patch("table_identical_checks.cli.main.main", side_effect=SystemExit(1)):
        assert _exit_code(["format"]) == 2


def test_unexpected_error_prints_traceback(tmp_path, capsys):
    path = _summary_json(tmp_path)
    with patch("table_identical_checks.cli.from_json_dict", side_effect=KeyError("k")):
        assert _exit_code(["format", f"--input-json={path}"]) == 2
    assert "Traceback" in capsys.readouterr().err


def test_non_verdict_return_values_exit_0():
    with patch("table_identical_checks.cli.main.main", return_value=True):
        assert _exit_code(["format"]) == 0


def test_entry_point_is_run():
    # Plain text match: tomllib needs Python 3.11 and CI also runs 3.10.
    pyproject = (Path(__file__).parents[1] / "pyproject.toml").read_text()
    assert 'table-check = "table_identical_checks.cli:run"' in pyproject.splitlines()


@pytest.mark.parametrize("rows_only_in_a, expected", [(0, 0), (2, 1)])
def test_summary_exit_code_follows_verdict(tmp_path, rows_only_in_a, expected):
    """summary end to end with BigQuery patched out."""
    columns = [
        ColumnInfo("id", "INT64", ColumnType.INTEGER),
        ColumnInfo("value", "INT64", ColumnType.INTEGER),
    ]
    result = from_json_dict(json.loads(Path(_summary_json(tmp_path)).read_text()))
    result.rows_only_in_a = rows_only_in_a
    cli = "table_identical_checks.cli"
    with (
        patch(f"{cli}.bigquery.Client"),
        patch(f"{cli}._log_client_info"),
        patch(f"{cli}._resolve_snapshot_sides", return_value=("p.d.a", "p.d.b", None, None)),
        patch(f"{cli}._intersect_table_schemas_with_drift", return_value=(columns, [])),
        patch(f"{cli}.get_partition_filters", return_value=(None, None)),
        patch(f"{cli}.generate_summary", return_value=result),
    ):
        argv = [
            "summary",
            "--table-a=p.d.a",
            "--table-b=p.d.b",
            "--keys=id",
            f"--output-json={tmp_path}/out.json",
        ]
        assert _exit_code(argv) == expected
