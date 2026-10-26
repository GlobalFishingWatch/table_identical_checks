"""Tests for build_verify_query (the `verify-query` command's SQL).

EXCEPT DISTINCT rejects ARRAY, STRUCT, GEOGRAPHY and JSON, so the query must
project an explicit, set-comparable column list on both sides.
"""

from __future__ import annotations

from google.cloud import bigquery

from table_identical_checks.backend import (
    ColumnType,
    PipelineConfig,
    QueryBuilder,
    build_verify_query,
    generate_summary,
    get_table_schema,
)
from table_identical_checks.backend.summary import ComparisonSummary


def _summary(column_types, diff_counts=None, excluded=None) -> ComparisonSummary:
    return ComparisonSummary(
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
        column_types=column_types,
        column_diff_counts=diff_counts or {c: 0 for c in column_types},
        excluded_columns=excluded or [],
    )


class TestVerifyQuerySql:
    def test_array_uses_pipeline_canonical_form(self):
        sql = build_verify_query(_summary({"tags": ColumnType.ARRAY}))

        canonical = QueryBuilder._array_canonical_sql("tags")
        assert f"{canonical} AS tags" in sql
        assert "SELECT *\n" not in sql.split("missing_in_a")[0]

    def test_struct_selected_by_leaf_path(self):
        sql = build_verify_query(
            _summary({"info.score": ColumnType.FLOAT, "info.label": ColumnType.STRING})
        )

        assert "info.score AS info__score" in sql
        assert "info.label AS info__label" in sql
        assert "    info,\n" not in sql

    def test_skipped_columns_listed_in_header(self):
        sql = build_verify_query(
            _summary(
                {
                    "geom": ColumnType.GEOGRAPHY,
                    "sketch": ColumnType.KLL_FLOAT64,
                    "name": ColumnType.STRING,
                    "value": ColumnType.INTEGER,
                },
                diff_counts={"geom": 0, "sketch": 0, "name": 3, "value": 0},
                excluded=[("payload", "JSON")],
            )
        )
        header, body = sql.split("WITH a AS", 1)

        assert "--   payload (JSON: unsupported type)" in header
        assert "--   name (has differences)" in header
        assert "--   geom (geography: no set-comparable form)" in header
        assert "--   sketch (kll_float64: no set-comparable form)" in header
        assert "    id,\n    value\n" in body
        for col in ("geom", "sketch", "name", "payload"):
            assert f"    {col}" not in body

    def test_no_header_when_everything_compared(self):
        sql = build_verify_query(_summary({"value": ColumnType.INTEGER}))
        assert sql.startswith("WITH a AS")

    def test_order_by_uses_key_aliases(self):
        summary = _summary({"value": ColumnType.INTEGER})
        summary.key_columns = ["id", "ref.code"]
        sql = build_verify_query(summary)

        assert "ref.code AS ref__code" in sql
        assert sql.endswith("ORDER BY id, ref__code, which")


VERIFY_SCHEMA = [
    bigquery.SchemaField("id", "INT64"),
    bigquery.SchemaField("tags", "STRING", mode="REPEATED"),
    bigquery.SchemaField(
        "info",
        "STRUCT",
        fields=[
            bigquery.SchemaField("score", "FLOAT64"),
            bigquery.SchemaField("label", "STRING"),
        ],
    ),
    bigquery.SchemaField("location", "GEOGRAPHY"),
]


class TestVerifyQueryRuns:
    def test_verify_query_runs_on_array_struct_geography(self, bq_client, table_factory):
        """Same rows (arrays in a different order) plus one row only in A."""
        shared = {"info": {"score": 1.5, "label": "x"}, "location": "POINT(1 2)"}
        rows_a = [
            {"id": 1, "tags": ["a", "b"], **shared},
            {"id": 2, "tags": ["c"], **shared},
        ]
        rows_b = [{"id": 1, "tags": ["b", "a"], **shared}]
        table_a = table_factory(VERIFY_SCHEMA, rows_a)
        table_b = table_factory(VERIFY_SCHEMA, rows_b)

        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=get_table_schema(bq_client, table_a),
        )
        summary = generate_summary(
            bq_client, builder, pipeline_config=PipelineConfig(max_diff_pct=1.0)
        )
        rows = list(bq_client.query(build_verify_query(summary)).result())

        assert [(r["which"], r["id"]) for r in rows] == [("missing_in_b", 2)]
