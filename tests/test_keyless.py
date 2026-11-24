"""Keyless mode: whole rows compared as multisets, identical-only."""

from __future__ import annotations

import json

import pytest
from google.cloud import bigquery

from table_identical_checks.backend import (
    ColumnInfo,
    ColumnType,
    QueryBuilder,
    generate_keyless_summary,
    get_table_schema,
)
from table_identical_checks.cli import run


def _builder(**kwargs) -> QueryBuilder:
    return QueryBuilder(
        table_a="p.d.a",
        table_b="p.d.b",
        key_columns=[],
        columns=[
            ColumnInfo("id", "INT64", ColumnType.INTEGER),
            ColumnInfo("outer.tags", "ARRAY<STRING>", ColumnType.ARRAY),
            ColumnInfo("outer.score", "FLOAT64", ColumnType.FLOAT),
            ColumnInfo("blob", "BYTES", ColumnType.UNSUPPORTED),
        ],
        **kwargs,
    )


class TestKeylessSql:
    def test_row_selects_leaves_and_canonicalises_arrays(self):
        row = _builder()._keyless_row_sql()

        assert row.startswith("TO_JSON_STRING(STRUCT(`id` AS `id`, ")
        assert (
            QueryBuilder._array_canonical_sql("`outer`.`tags`") + " AS `outer__tags`"
        ) in row
        assert "`outer`.`score` AS `outer__score`" in row
        assert "blob" not in row

    def test_script_counts_multisets(self):
        script = _builder(partition_filter_b="id > 0").build_keyless_script()

        assert "COUNT(*) AS n FROM `p.d.a` AS t GROUP BY row" in script
        assert "WHERE id > 0) AS t GROUP BY row" in script
        assert "ON ca.row = cb.row" in script
        assert "SUM(GREATEST(n_a - n_b, 0)), 0) AS rows_only_in_a" in script
        assert "SUM(LEAST(n_a, n_b)), 0) AS rows_identical" in script


def _exit_code(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as exc:
        run(argv)
    return exc.value.code


@pytest.mark.parametrize(
    "extra",
    [
        ["--keys=id"],
        ["--tolerance=1e-9"],
        ["--expect-diff=score"],
        ["--write-diffs"],
        ["--legacy"],
        ["--max-diff-pct=10"],
    ],
)
def test_conflicting_options_are_usage_errors(extra, capsys):
    argv = ["summary", "--keyless", "--table-a=p.d.a", "--table-b=p.d.b", *extra]
    assert _exit_code(argv) == 2
    assert "--keyless can't be combined with" in capsys.readouterr().err


def test_tables_are_required(capsys):
    assert _exit_code(["summary", "--keyless", "--table-a=p.d.a"]) == 2
    assert "--keyless needs --table-a and --table-b" in capsys.readouterr().err


SCHEMA = [
    bigquery.SchemaField("id", "INT64"),
    bigquery.SchemaField("tags", "STRING", mode="REPEATED"),
    bigquery.SchemaField(
        "outer",
        "STRUCT",
        fields=[
            bigquery.SchemaField("label", "STRING"),
            bigquery.SchemaField("codes", "INT64", mode="REPEATED"),
        ],
    ),
]


def _row(id_, tags=("a", "b"), label="x", codes=(1, 2)):
    return {"id": id_, "tags": list(tags), "outer": {"label": label, "codes": list(codes)}}


class TestKeylessEndToEnd:
    def _summary(self, bq_client, table_factory, rows_a, rows_b):
        table_a = table_factory(SCHEMA, rows_a)
        table_b = table_factory(SCHEMA, rows_b)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=[],
            columns=get_table_schema(bq_client, table_a),
        )
        return generate_keyless_summary(bq_client, builder)

    def test_compensating_duplicates_are_caught(self, bq_client, table_factory):
        """{X, X, Y} vs {X, Y, Y}: equal counts, but not the same rows."""
        x, y = _row(1), _row(2)
        summary = self._summary(bq_client, table_factory, [x, x, y], [x, y, y])

        assert summary.total_rows_a == summary.total_rows_b == 3
        assert summary.rows_identical == 2
        assert summary.rows_only_in_a == 1
        assert summary.rows_only_in_b == 1
        assert summary.verdict == "FAIL"
        assert summary.duplicate_info_a.duplicate_key_count == 1
        assert summary.duplicate_info_a.max_duplicate_count == 2
        sides = sorted((s["side"], s["n_a"], s["n_b"]) for s in summary.sample_rows)
        assert sides == [("only_in_a", 2, 1), ("only_in_b", 1, 2)]

    def test_reordered_arrays_and_nulls_are_identical(self, bq_client, table_factory):
        rows_a = [_row(1, tags=("a", "b"), codes=(1, 2)), _row(2, label=None, tags=())]
        rows_b = [_row(1, tags=("b", "a"), codes=(2, 1)), _row(2, label=None, tags=())]
        summary = self._summary(bq_client, table_factory, rows_a, rows_b)

        assert summary.rows_identical == 2
        assert summary.verdict == "PASS"
        assert summary.tables_identical
        assert "IDENTICAL" in str(summary)

    def test_cli_exit_code_and_json(self, table_factory, tmp_path, capsys):
        table_a = table_factory(SCHEMA, [_row(1), _row(2)])
        table_b = table_factory(SCHEMA, [_row(1)])
        out = tmp_path / "keyless.json"
        argv = [
            "summary",
            "--keyless",
            f"--table-a={table_a}",
            f"--table-b={table_b}",
            f"--output-json={out}",
        ]

        assert _exit_code(argv) == 1
        data = json.loads(out.read_text())
        assert data["keyless"] is True
        assert data["key_columns"] == []
        assert (data["rows_only_in_a"], data["rows_only_in_b"]) == (1, 0)
        assert "Sample rows only in A" in capsys.readouterr().out
