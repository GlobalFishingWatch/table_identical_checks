"""Rows of duplicated keys are paired one-to-one, identical rows first.

A plain key join pairs every row of a duplicated key with every row of it on
the other side: {X, Y} against {X, Y} gave four pairs, and the two crossed
ones were reported as value differences although both tables held the same
rows.
"""

from __future__ import annotations

from google.cloud import bigquery

from table_identical_checks.backend import (
    ColumnInfo,
    ColumnType,
    PipelineConfig,
    QueryBuilder,
    generate_summary,
    get_table_schema,
)
from table_identical_checks.backend.pipeline import run_pipeline


def _builder(**kwargs) -> QueryBuilder:
    return QueryBuilder(
        table_a="p.d.a",
        table_b="p.d.b",
        key_columns=["id"],
        columns=[
            ColumnInfo("id", "INT64", ColumnType.INTEGER),
            ColumnInfo("a", "STRING", ColumnType.STRING),
        ],
        **kwargs,
    )


class TestPairingSql:
    def test_join_is_on_key_and_pair(self):
        script = _builder().build_pipeline_script()

        assert "ON a.__key = b.__key AND a.__pair = b.__pair" in script
        assert "FROM a_src AS a\nFULL OUTER JOIN b_src AS b" in script
        assert "dupkeys AS (" in script
        assert "INTERSECT DISTINCT" in script

    def test_pairing_can_be_switched_off(self):
        script = _builder(pair_duplicates=False).build_pipeline_script()

        assert "dupkeys" not in script
        assert "a_src AS (SELECT *, '' AS __pair FROM sa)" in script

    def test_diff_query_uses_the_paired_sources(self):
        sql = _builder().build_diff_query()

        assert sql.startswith("WITH sa AS (")
        assert "`a`.`__key` = `b`.`__key` AND `a`.`__pair` = `b`.`__pair`" in sql


SCHEMA = [
    bigquery.SchemaField("id", "INT64"),
    bigquery.SchemaField("a", "STRING"),
    bigquery.SchemaField("b", "INT64"),
]


def _rows(*triples):
    return [{"id": i, "a": a, "b": b} for i, a, b in triples]


# One key per case from the handover (2026-11-25), plus two unique keys.
ROWS_A = _rows(
    (1, "x", 1), (1, "y", 2),  # {X, Y} vs {Y, X}: identical
    (2, "m", 1), (2, "y", 2),  # {X, Y} vs {X, Z}: one differing pair (Y, Z)
    (3, "x", 1), (3, "x", 1),  # {X, X} vs {X}: one row only in A
    (4, "x", 1), (4, "y", 2),  # {X, Y} vs {X}: Y only in A
    (5, "u", 1),               # unique key, identical
    (6, "u", 1),               # unique key, differs
)
ROWS_B = _rows(
    (1, "y", 2), (1, "x", 1),
    # Z sorts before X, so pairing by row order alone would cross the rows
    (2, "m", 1), (2, "a", 3),
    (3, "x", 1),
    (4, "x", 1),
    (5, "u", 1),
    (6, "u", 9),
)


class TestPairingEndToEnd:
    def _builder(self, bq_client, table_factory) -> QueryBuilder:
        table_a = table_factory(SCHEMA, ROWS_A)
        table_b = table_factory(SCHEMA, ROWS_B)
        return QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=get_table_schema(bq_client, table_a),
        )

    def test_pipeline_counts(self, bq_client, table_factory):
        builder = self._builder(bq_client, table_factory)
        result = run_pipeline(bq_client, builder, PipelineConfig(max_diff_pct=1.0))

        assert (result.rows_only_in_a, result.rows_only_in_b) == (2, 0)
        assert result.rows_in_both_with_differences == 2  # key 2 (Y, Z) and key 6
        assert result.column_diff_counts == {"a": 1, "b": 2}

    def test_summary_matched_and_identical(self, bq_client, table_factory):
        builder = self._builder(bq_client, table_factory)
        summary = generate_summary(
            bq_client, builder, pipeline_config=PipelineConfig(max_diff_pct=1.0)
        )

        assert builder.pair_duplicates  # the duplicate check found some
        assert summary.rows_identical == 6
        assert summary.rows_identical + summary.rows_in_both_with_differences == 8
        assert summary.fail_reasons[:1] == ["2 rows only in A"]

    def test_diff_query_count(self, bq_client, table_factory):
        builder = self._builder(bq_client, table_factory)
        rows = list(bq_client.query(builder.build_count_query()).result())

        assert rows[0]["diff_count"] == 4  # 2 only in A + 2 differing pairs

    def test_identical_duplicate_groups_pass(self, bq_client, table_factory):
        """The handover's real case: same two rows per side under one key."""
        rows = _rows((1, "x", 1), (1, "y", 2), (2, "u", 1))
        table_a = table_factory(SCHEMA, rows)
        table_b = table_factory(SCHEMA, list(reversed(rows)))
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=get_table_schema(bq_client, table_a),
        )
        summary = generate_summary(
            bq_client, builder, pipeline_config=PipelineConfig(max_diff_pct=1.0)
        )

        assert summary.verdict == "PASS"
        assert summary.rows_identical == 3
