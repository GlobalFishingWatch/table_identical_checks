"""NULL values in key columns must match NULL on the other side.

`a.k = b.k` never matches NULLs, and presence used to be read from the first
key column, so rows with a NULL first key vanished from every count and were
reported as identical by subtraction. Keys are now joined on a NULL-safe JSON
serialisation (`__key`), which also serves as the presence flag.
"""

from __future__ import annotations

import uuid

from google.cloud import bigquery

from table_identical_checks.backend import (
    ColumnInfo,
    ColumnType,
    OutputDiffConfig,
    PipelineConfig,
    QueryBuilder,
    generate_summary,
    get_table_schema,
)
from table_identical_checks.backend.pipeline import run_pipeline


def _builder(**kwargs) -> QueryBuilder:
    return QueryBuilder(
        table_a="proj.ds.tab_a",
        table_b="proj.ds.tab_b",
        key_columns=["k1", "k2"],
        columns=[
            ColumnInfo("k1", "STRING", ColumnType.STRING),
            ColumnInfo("k2", "INT64", ColumnType.INTEGER),
            ColumnInfo("val", "FLOAT64", ColumnType.FLOAT),
        ],
        **kwargs,
    )


class TestNullSafeKeySql:
    def test_key_sql_names_fields_by_position(self):
        builder = QueryBuilder(
            table_a="p.d.a",
            table_b="p.d.b",
            key_columns=["x.code", "y.code"],
            columns=[],
        )
        assert builder._key_sql() == (
            "TO_JSON_STRING(STRUCT(`x`.`code` AS k0, `y`.`code` AS k1))"
        )
        assert builder._key_sql("a") == (
            "TO_JSON_STRING(STRUCT(a.`x`.`code` AS k0, a.`y`.`code` AS k1))"
        )

    def test_pipeline_joins_and_flags_presence_on_key(self):
        script = _builder().build_pipeline_script()

        assert (
            "TO_JSON_STRING(STRUCT(`k1` AS k0, `k2` AS k1)) AS __key FROM `proj.ds.tab_a`"
        ) in script
        assert "ON a.__key = b.__key" in script
        assert "a.__key IS NOT NULL AS in_a" in script
        assert "b.__key IS NOT NULL AS in_b" in script
        assert "a.k1 = b.k1" not in script
        assert "a.k1 IS NOT NULL" not in script

    def test_diff_split_matches_on_key(self):
        cfg = OutputDiffConfig(output_a="s.d.DIFF_a", output_b="s.d.DIFF_b")
        script = _builder().build_pipeline_script(output_diff=cfg)

        assert "COALESCE(a.__key, b.__key) AS __key" in script
        assert "_l1.__key = TO_JSON_STRING(STRUCT(a.`k1` AS k0, a.`k2` AS k1))" in script
        # The written copy selects from the plain source, without __key
        assert "SELECT a.*\nFROM `proj.ds.tab_a` AS a\n" in script

    def test_diff_query_joins_and_flags_presence_on_key(self):
        sql = _builder().build_diff_query()

        assert "ON `a`.`__key` = `b`.`__key`" in sql
        assert "`a`.`__key` IS NOT NULL AS `in_a`" in sql
        assert "`a`.`k1` = `b`.`k1`" not in sql


SCHEMA = [
    bigquery.SchemaField("k1", "STRING"),
    bigquery.SchemaField("k2", "INT64"),
    bigquery.SchemaField("val", "FLOAT64"),
]

# The first key column is NULL in most rows (the case that used to vanish).
ROWS_A = [
    {"k1": None, "k2": 1, "val": 1.0},  # identical
    {"k1": None, "k2": 2, "val": 2.0},  # value differs
    {"k1": None, "k2": 3, "val": 3.0},  # only in A
    {"k1": "x", "k2": 4, "val": 4.0},  # identical
]
ROWS_B = [
    {"k1": None, "k2": 1, "val": 1.0},
    {"k1": None, "k2": 2, "val": 2.5},
    {"k1": "x", "k2": 4, "val": 4.0},
    {"k1": None, "k2": 5, "val": 5.0},  # only in B
]


class TestNullKeysEndToEnd:
    def _builder(self, bq_client, table_factory) -> QueryBuilder:
        table_a = table_factory(SCHEMA, ROWS_A)
        table_b = table_factory(SCHEMA, ROWS_B)
        return QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["k1", "k2"],
            columns=get_table_schema(bq_client, table_a),
        )

    def test_pipeline_summary(self, bq_client, table_factory):
        builder = self._builder(bq_client, table_factory)
        summary = generate_summary(
            bq_client, builder, pipeline_config=PipelineConfig(max_diff_pct=1.0)
        )

        assert summary.rows_only_in_a == 1
        assert summary.rows_only_in_b == 1
        assert summary.rows_in_both_with_differences == 1
        assert summary.rows_identical == 2
        assert summary.column_diff_counts["val"] == 1

    def test_legacy_summary(self, bq_client, table_factory):
        """No pipeline config: the SQLAlchemy diff-query path."""
        summary = generate_summary(bq_client, self._builder(bq_client, table_factory))

        assert summary.rows_only_in_a == 1
        assert summary.rows_only_in_b == 1
        assert summary.rows_in_both_with_differences == 1
        assert summary.rows_identical == 2

    def test_count_query(self, bq_client, table_factory):
        builder = self._builder(bq_client, table_factory)
        rows = list(bq_client.query(builder.build_count_query()).result())

        assert rows[0]["diff_count"] == 3

    def test_write_diffs(self, bq_client, table_factory, test_dataset):
        builder = self._builder(bq_client, table_factory)
        suffix = uuid.uuid4().hex[:8]
        cfg = OutputDiffConfig(
            output_a=f"{test_dataset}._test_nullkey_diff_a_{suffix}",
            output_b=f"{test_dataset}._test_nullkey_diff_b_{suffix}",
            expiration_hours=1,
        )
        try:
            run_pipeline(bq_client, builder, PipelineConfig(max_diff_pct=1.0, output_diff=cfg))

            def written(table: str) -> set[tuple]:
                rows = bq_client.query(f"SELECT * FROM `{table}`").result()
                return {(r["k1"], r["k2"], r["val"]) for r in rows}

            assert written(cfg.output_a) == {(None, 2, 2.0), (None, 3, 3.0)}
            assert written(cfg.output_b) == {(None, 2, 2.5), (None, 5, 5.0)}
            assert "__key" not in {f.name for f in bq_client.get_table(cfg.output_a).schema}
        finally:
            for table in (cfg.output_a, cfg.output_b):
                bq_client.delete_table(table, not_found_ok=True)
