"""Tests for diff output features: --output-table and --only-diffs."""

import os
import time
import uuid
from unittest.mock import patch

import pytest
from google.cloud import bigquery

from table_identical_checks.backend import QueryBuilder, get_table_schema
from table_identical_checks.backend.pipeline import (
    PipelineConfig,
    differing_columns,
    run_pipeline,
)
from table_identical_checks.backend.schema import ColumnInfo, ColumnType
from table_identical_checks.backend.tolerance import ToleranceConfig

# ---------------------------------------------------------------------------
# Schema for test tables
# ---------------------------------------------------------------------------
SCHEMA = [
    bigquery.SchemaField("id", "INT64"),
    bigquery.SchemaField("int_val", "INT64"),
    bigquery.SchemaField("float_val", "FLOAT64"),
    bigquery.SchemaField("name", "STRING"),
]


# ---------------------------------------------------------------------------
# Unit tests (no BQ)
# ---------------------------------------------------------------------------
class TestDifferingColumns:
    """Pure-function tests for differing_columns()."""

    def test_all_zero(self):
        counts = {"col_a": 0, "col_b": 0, "col_c": 0}
        assert differing_columns(counts) == []

    def test_all_nonzero(self):
        counts = {"col_a": 5, "col_b": 3}
        assert differing_columns(counts) == ["col_a", "col_b"]

    def test_mixed(self):
        counts = {"col_a": 0, "col_b": 7, "col_c": 0, "col_d": 1}
        result = differing_columns(counts)
        assert result == ["col_b", "col_d"]

    def test_empty_dict(self):
        assert differing_columns({}) == []


class TestBuildDiffTableStatementDryRun:
    """Dry-run tests for build_diff_table_statement (SQL structure, no BQ)."""

    @pytest.fixture()
    def builder(self):
        columns = [
            ColumnInfo(name="id", bq_type="INT64", column_type=ColumnType.INTEGER),
            ColumnInfo(name="val", bq_type="INT64", column_type=ColumnType.INTEGER),
            ColumnInfo(name="score", bq_type="FLOAT64", column_type=ColumnType.FLOAT),
        ]
        return QueryBuilder(
            table_a="proj.ds.table_a",
            table_b="proj.ds.table_b",
            key_columns=["id"],
            columns=columns,
        )

    def test_replace_mode(self, builder):
        ddl = builder.build_diff_table_statement(destination="proj.ds.output")
        assert ddl.startswith("CREATE OR REPLACE TABLE `proj.ds.output`")
        assert "AS (" in ddl

    def test_if_not_exists_mode(self, builder):
        ddl = builder.build_diff_table_statement(
            destination="proj.ds.output", write_mode="if_not_exists"
        )
        assert ddl.startswith("CREATE TABLE IF NOT EXISTS `proj.ds.output`")

    def test_expiration_hours(self, builder):
        ddl = builder.build_diff_table_statement(
            destination="proj.ds.output", expiration_hours=24
        )
        assert "INTERVAL 24 HOUR" in ddl
        assert "expiration_timestamp" in ddl

    def test_no_expiration(self, builder):
        ddl = builder.build_diff_table_statement(destination="proj.ds.output")
        assert "expiration_timestamp" not in ddl

    def test_columns_filter(self, builder):
        ddl = builder.build_diff_table_statement(
            destination="proj.ds.output", columns_filter=["val"]
        )
        # val should appear, score should not (except in aliases)
        assert "a__val" in ddl
        # score columns should NOT appear
        assert "a__score" not in ddl


class TestBuildFilteredDiffQuery:
    """Dry-run tests for build_diff_query with columns_filter."""

    @pytest.fixture()
    def builder(self):
        columns = [
            ColumnInfo(name="id", bq_type="INT64", column_type=ColumnType.INTEGER),
            ColumnInfo(name="val", bq_type="INT64", column_type=ColumnType.INTEGER),
            ColumnInfo(name="score", bq_type="FLOAT64", column_type=ColumnType.FLOAT),
            ColumnInfo(name="label", bq_type="STRING", column_type=ColumnType.STRING),
        ]
        return QueryBuilder(
            table_a="proj.ds.table_a",
            table_b="proj.ds.table_b",
            key_columns=["id"],
            columns=columns,
        )

    def test_filter_to_single_column(self, builder):
        query = builder.build_diff_query(columns_filter=["val"])
        assert "a__val" in query
        assert "a__score" not in query
        assert "a__label" not in query

    def test_filter_to_multiple_columns(self, builder):
        query = builder.build_diff_query(columns_filter=["val", "label"])
        assert "a__val" in query
        assert "a__label" in query
        assert "a__score" not in query

    def test_no_filter_includes_all(self, builder):
        query = builder.build_diff_query(columns_filter=None)
        assert "a__val" in query
        assert "a__score" in query
        assert "a__label" in query

    def test_empty_filter_includes_keys_only(self, builder):
        """An empty filter list results in only keys + existence flags."""
        query = builder.build_diff_query(columns_filter=[])
        assert "a__val" not in query
        assert "a__score" not in query
        # Key column should always be present
        assert "id" in query


class TestDisplayDiffResults:
    """Unit tests for _display_diff_results() truncation and temp file logic.

    Covers cli.py lines 188-218: truncation at max_display_rows,
    "more row(s) not shown" message, and temp file creation.
    """

    @staticmethod
    def _make_fake_rows(n: int) -> list[dict[str, object]]:
        """Create n dict-like objects that behave like BQ Row (support dict())."""

        class FakeRow(dict):
            """Minimal dict subclass satisfying dict(row) in _rows_to_dicts."""

            pass

        return [FakeRow(id=i, val=float(i * 10)) for i in range(1, n + 1)]

    @patch("table_identical_checks.cli.click")
    def test_no_truncation_when_within_limit(self, mock_click: object) -> None:
        """When total rows <= max_display_rows, no truncation message should appear."""
        from table_identical_checks.cli import _display_diff_results

        rows = self._make_fake_rows(3)
        _display_diff_results(rows, max_display_rows=10)

        echoed = " ".join(str(c) for c in mock_click.echo.call_args_list)
        assert "more row(s) not shown" not in echoed
        assert "Full result written to" in echoed

        # Clean up temp file
        for call in mock_click.echo.call_args_list:
            arg = str(call)
            if "/tmp/table-check-diff-" in arg:
                for part in arg.split():
                    if part.startswith("/tmp/table-check-diff-"):
                        path = part.rstrip("')")
                        if os.path.exists(path):
                            os.unlink(path)

    @patch("table_identical_checks.cli.click")
    def test_truncation_shows_remaining_count(self, mock_click: object) -> None:
        """When total > max_display_rows, display how many rows were hidden."""
        from table_identical_checks.cli import _display_diff_results

        rows = self._make_fake_rows(5)
        _display_diff_results(rows, max_display_rows=2)

        echoed = " ".join(str(c) for c in mock_click.echo.call_args_list)
        assert "3 more row(s) not shown" in echoed
        assert "showing 2 of 5" in echoed

        # Clean up temp file
        for call in mock_click.echo.call_args_list:
            arg = str(call)
            if "/tmp/table-check-diff-" in arg:
                for part in arg.split():
                    if part.startswith("/tmp/table-check-diff-"):
                        path = part.rstrip("')")
                        if os.path.exists(path):
                            os.unlink(path)

    @patch("table_identical_checks.cli.click")
    def test_tempfile_contains_all_rows(self, mock_click: object) -> None:
        """Temp file should contain ALL rows, even when display is truncated."""
        from table_identical_checks.cli import _display_diff_results

        rows = self._make_fake_rows(5)
        _display_diff_results(rows, max_display_rows=2)

        # Extract temp path from the "Full result written to:" echo call.
        # cli.py line 217 emits: "\nFull result written to: /tmp/..."
        temp_path: str | None = None
        marker = "Full result written to: "
        for call in mock_click.echo.call_args_list:
            args = call[0] if call[0] else ()
            for arg in args:
                if isinstance(arg, str) and marker in arg:
                    temp_path = arg.split(marker, 1)[1].strip()
                    break

        assert temp_path is not None, "Temp file path not echoed"
        try:
            content = open(temp_path).read()
            assert "Full diff output (5 rows)" in content
            # All five row values should be present in the file
            assert "10.0" in content
            assert "50.0" in content
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)


class TestBuildDiffTableStatementWithTolerance:
    """Verify build_diff_table_statement embeds tolerance filtering.

    Covers query_builder.py line 530 which hardcodes apply_tolerance=True.
    If someone changes that to False, persisted tables would include
    within-tolerance rows.
    """

    @pytest.fixture()
    def builder_with_tolerance(self) -> QueryBuilder:
        columns = [
            ColumnInfo(name="id", bq_type="INT64", column_type=ColumnType.INTEGER),
            ColumnInfo(name="val", bq_type="INT64", column_type=ColumnType.INTEGER),
            ColumnInfo(name="score", bq_type="FLOAT64", column_type=ColumnType.FLOAT),
        ]
        return QueryBuilder(
            table_a="proj.ds.table_a",
            table_b="proj.ds.table_b",
            key_columns=["id"],
            columns=columns,
            tolerance_config=ToleranceConfig.parse("score:1e-9"),
        )

    def test_ddl_contains_tolerance_exclusion(
        self, builder_with_tolerance: QueryBuilder
    ) -> None:
        """DDL should contain tolerance filtering logic in the embedded query."""
        ddl = builder_with_tolerance.build_diff_table_statement(
            destination="proj.ds.output"
        )
        # The tolerance exclusion uses ABS(...) <= tolerance value
        ddl_lower = ddl.lower()
        assert "1e-09" in ddl_lower or "1e-9" in ddl_lower

    def test_ddl_with_tolerance_and_columns_filter(
        self, builder_with_tolerance: QueryBuilder
    ) -> None:
        """DDL with both tolerance and columns_filter should embed both."""
        ddl = builder_with_tolerance.build_diff_table_statement(
            destination="proj.ds.output",
            columns_filter=["score"],
        )
        assert "CREATE OR REPLACE TABLE" in ddl
        assert "a__score" in ddl
        # val should be excluded from SELECT by columns_filter
        assert "a__val" not in ddl
        # Tolerance exclusion should still be present
        ddl_lower = ddl.lower()
        assert "1e-09" in ddl_lower or "1e-9" in ddl_lower


class TestColumnsFilterGeography:
    """Dry-run tests for columns_filter with GEOGRAPHY columns.

    Geography columns use a completely different code path in both
    _build_select_columns (ST_ASTEXT, ST_DISTANCE -- lines 248-287)
    and _build_where_clause (ST_EQUALS -- lines 352-354).
    """

    @pytest.fixture()
    def builder(self) -> QueryBuilder:
        columns = [
            ColumnInfo(name="id", bq_type="INT64", column_type=ColumnType.INTEGER),
            ColumnInfo(
                name="loc", bq_type="GEOGRAPHY", column_type=ColumnType.GEOGRAPHY
            ),
            ColumnInfo(name="val", bq_type="INT64", column_type=ColumnType.INTEGER),
        ]
        return QueryBuilder(
            table_a="proj.ds.a",
            table_b="proj.ds.b",
            key_columns=["id"],
            columns=columns,
        )

    def test_filter_includes_geography_column(self, builder: QueryBuilder) -> None:
        """Filtering to geography col should produce ST_ASTEXT/ST_DISTANCE."""
        query = builder.build_diff_query(columns_filter=["loc"])
        assert "ST_ASTEXT" in query
        assert "ST_DISTANCE" in query
        # val columns should be absent
        assert "a__val" not in query

    def test_filter_excludes_geography_column(self, builder: QueryBuilder) -> None:
        """Filtering to non-geography col should omit all geography SQL constructs."""
        query = builder.build_diff_query(columns_filter=["val"])
        assert "a__val" in query
        assert "ST_ASTEXT" not in query
        assert "ST_DISTANCE" not in query
        # WHERE clause should not reference loc via ST_EQUALS
        assert "ST_EQUALS" not in query


class TestBuildDiffQueryNoToleranceWithFilter:
    """Test build_diff_query(apply_tolerance=False) with columns_filter.

    Covers _build_where_clause_no_tolerance (lines 550-579) which was
    newly added with columns_filter support but has zero test coverage.
    This path is invoked for statistics queries (apply_tolerance=False).
    """

    @pytest.fixture()
    def builder(self) -> QueryBuilder:
        columns = [
            ColumnInfo(name="id", bq_type="INT64", column_type=ColumnType.INTEGER),
            ColumnInfo(name="val", bq_type="INT64", column_type=ColumnType.INTEGER),
            ColumnInfo(name="score", bq_type="FLOAT64", column_type=ColumnType.FLOAT),
            ColumnInfo(name="label", bq_type="STRING", column_type=ColumnType.STRING),
        ]
        return QueryBuilder(
            table_a="proj.ds.table_a",
            table_b="proj.ds.table_b",
            key_columns=["id"],
            columns=columns,
            tolerance_config=ToleranceConfig.parse("score:1e-9"),
        )

    def test_no_tolerance_with_filter_restricts_columns(
        self, builder: QueryBuilder
    ) -> None:
        """apply_tolerance=False + columns_filter should skip tolerance AND filter columns."""
        query = builder.build_diff_query(
            apply_tolerance=False, columns_filter=["val"]
        )
        # SELECT should only have val columns
        assert "a__val" in query
        assert "a__score" not in query
        assert "a__label" not in query

    def test_no_tolerance_without_filter_includes_all(
        self, builder: QueryBuilder
    ) -> None:
        """apply_tolerance=False without columns_filter should include all columns."""
        query = builder.build_diff_query(apply_tolerance=False, columns_filter=None)
        assert "a__val" in query
        assert "a__score" in query
        assert "a__label" in query

    def test_no_tolerance_with_filter_geography(self) -> None:
        """apply_tolerance=False + columns_filter should work for geography too."""
        columns = [
            ColumnInfo(name="id", bq_type="INT64", column_type=ColumnType.INTEGER),
            ColumnInfo(
                name="loc", bq_type="GEOGRAPHY", column_type=ColumnType.GEOGRAPHY
            ),
            ColumnInfo(name="val", bq_type="INT64", column_type=ColumnType.INTEGER),
        ]
        builder = QueryBuilder(
            table_a="proj.ds.a",
            table_b="proj.ds.b",
            key_columns=["id"],
            columns=columns,
        )
        query = builder.build_diff_query(
            apply_tolerance=False, columns_filter=["val"]
        )
        assert "a__val" in query
        assert "ST_ASTEXT" not in query
        assert "ST_DISTANCE" not in query


class TestToleranceExclusionIndependentOfFilter:
    """Regression guard: tolerance exclusion always uses ALL value columns.

    Covers query_builder.py lines 367-371 and 394.  The docstring at
    lines 337-342 documents that this is intentional: columns_filter
    comes from --only-diffs (columns the pipeline identified as having
    differences), but tolerance exclusion needs the full column picture
    to correctly decide whether a row's differences are entirely within
    tolerance.

    A regression could easily make _build_tolerance_exclusion use the
    filtered list instead.
    """

    def test_tolerance_references_columns_outside_filter(self) -> None:
        """When columns_filter=["val"], tolerance should still reference 'score'."""
        columns = [
            ColumnInfo(name="id", bq_type="INT64", column_type=ColumnType.INTEGER),
            ColumnInfo(name="val", bq_type="INT64", column_type=ColumnType.INTEGER),
            ColumnInfo(name="score", bq_type="FLOAT64", column_type=ColumnType.FLOAT),
        ]
        builder = QueryBuilder(
            table_a="proj.ds.a",
            table_b="proj.ds.b",
            key_columns=["id"],
            columns=columns,
            tolerance_config=ToleranceConfig.parse("score:1e-9"),
        )
        query = builder.build_diff_query(columns_filter=["val"])

        # SELECT should NOT include score columns (filtered out)
        assert "a__score" not in query

        # But tolerance exclusion SHOULD still reference score in WHERE.
        # The tolerance generates ABS(a.score - b.score) <= tolerance
        # so "score" must appear somewhere after the SELECT.
        where_portion = query.split("WHERE")[1] if "WHERE" in query else ""
        assert "score" in where_portion
        assert "1e-09" in where_portion.lower() or "1e-9" in where_portion.lower()

    def test_without_tolerance_filter_limits_where_clause(self) -> None:
        """Without tolerance, columns_filter should limit the WHERE clause too."""
        columns = [
            ColumnInfo(name="id", bq_type="INT64", column_type=ColumnType.INTEGER),
            ColumnInfo(name="val", bq_type="INT64", column_type=ColumnType.INTEGER),
            ColumnInfo(name="score", bq_type="FLOAT64", column_type=ColumnType.FLOAT),
        ]
        builder = QueryBuilder(
            table_a="proj.ds.a",
            table_b="proj.ds.b",
            key_columns=["id"],
            columns=columns,
            # No tolerance configured
        )
        query = builder.build_diff_query(columns_filter=["val"])

        # Without tolerance, score should not appear anywhere
        assert "a__score" not in query
        # The WHERE base condition should only reference val (and key IS NULL checks)
        where_portion = query.split("WHERE")[1] if "WHERE" in query else ""
        assert "score" not in where_portion

    def test_tolerance_with_geography_outside_filter(self) -> None:
        """Geography tolerance should reference geo column even when filtered out."""
        columns = [
            ColumnInfo(name="id", bq_type="INT64", column_type=ColumnType.INTEGER),
            ColumnInfo(name="val", bq_type="INT64", column_type=ColumnType.INTEGER),
            ColumnInfo(
                name="loc", bq_type="GEOGRAPHY", column_type=ColumnType.GEOGRAPHY
            ),
        ]
        builder = QueryBuilder(
            table_a="proj.ds.a",
            table_b="proj.ds.b",
            key_columns=["id"],
            columns=columns,
            tolerance_config=ToleranceConfig(column_tolerances={"loc": 100.0}),
        )
        query = builder.build_diff_query(columns_filter=["val"])

        # SELECT should NOT include geography columns
        assert "ST_ASTEXT" not in query
        assert "distance_meters" not in query

        # But tolerance exclusion SHOULD still reference loc in WHERE
        where_portion = query.split("WHERE")[1] if "WHERE" in query else ""
        assert "loc" in where_portion
        assert "ST_DISTANCE" in where_portion


# ---------------------------------------------------------------------------
# Integration tests (require BQ)
# ---------------------------------------------------------------------------
class TestOutputTableIntegration:
    """Integration: --output-table writes diff to a BQ table."""

    def test_persist_diff_table(self, bq_client, table_factory, test_dataset):
        rows_a = [
            {"id": 1, "int_val": 100, "float_val": 1.0, "name": "alpha"},
            {"id": 2, "int_val": 200, "float_val": 2.0, "name": "beta"},
        ]
        rows_b = [
            {"id": 1, "int_val": 100, "float_val": 1.0, "name": "alpha"},
            {"id": 2, "int_val": 999, "float_val": 2.0, "name": "beta"},  # int_val differs
        ]

        table_a = table_factory(SCHEMA, rows_a)
        table_b = table_factory(SCHEMA, rows_b)

        # Let streaming buffer settle
        time.sleep(5)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
        )

        dest = f"{test_dataset}._test_diff_out_{uuid.uuid4().hex[:8]}"
        try:
            ddl = builder.build_diff_table_statement(destination=dest)
            bq_client.query(ddl).result()

            # Verify the table exists and has the expected row count
            count_result = bq_client.query(
                f"SELECT COUNT(*) AS cnt FROM `{dest}`"
            ).result()
            cnt = list(count_result)[0].cnt
            assert cnt == 1  # Only id=2 differs

            # Verify schema includes expected columns
            dest_table = bq_client.get_table(dest)
            col_names = {f.name for f in dest_table.schema}
            assert "id" in col_names
            assert "a__int_val" in col_names
            assert "b__int_val" in col_names
            assert "int_val__delta" in col_names
        finally:
            bq_client.delete_table(dest, not_found_ok=True)

    def test_persist_with_expiration(self, bq_client, table_factory, test_dataset):
        rows = [
            {"id": 1, "int_val": 10, "float_val": 1.0, "name": "x"},
        ]

        table_a = table_factory(SCHEMA, rows)
        table_b = table_factory(SCHEMA, rows)

        time.sleep(5)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
        )

        dest = f"{test_dataset}._test_diff_exp_{uuid.uuid4().hex[:8]}"
        try:
            ddl = builder.build_diff_table_statement(
                destination=dest, expiration_hours=1
            )
            bq_client.query(ddl).result()

            # Verify table was created (even if empty -- identical tables)
            dest_table = bq_client.get_table(dest)
            assert dest_table.expires is not None
        finally:
            bq_client.delete_table(dest, not_found_ok=True)


class TestOnlyDiffsIntegration:
    """Integration: filtered diff query returns only columns with differences."""

    def test_filtered_query_returns_subset(self, bq_client, table_factory):
        rows_a = [
            {"id": 1, "int_val": 100, "float_val": 1.0, "name": "alpha"},
            {"id": 2, "int_val": 200, "float_val": 2.0, "name": "beta"},
        ]
        rows_b = [
            {"id": 1, "int_val": 100, "float_val": 1.0, "name": "alpha"},
            {"id": 2, "int_val": 200, "float_val": 2.0, "name": "CHANGED"},  # only name differs
        ]

        table_a = table_factory(SCHEMA, rows_a)
        table_b = table_factory(SCHEMA, rows_b)

        time.sleep(5)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
        )

        # Only include the 'name' column (simulating --only-diffs after pipeline)
        query = builder.build_diff_query(columns_filter=["name"])
        result = bq_client.query(query).result()
        rows = list(result)

        assert len(rows) == 1
        row = dict(rows[0])
        # Should have key + existence flags + name columns only
        assert "id" in row
        assert "a__name" in row
        assert "b__name" in row
        # Should NOT have int_val or float_val columns
        assert "a__int_val" not in row
        assert "a__float_val" not in row

    def test_filtered_output_table(self, bq_client, table_factory, test_dataset):
        """Combined: --only-diffs + --output-table."""
        rows_a = [
            {"id": 1, "int_val": 100, "float_val": 1.0, "name": "alpha"},
            {"id": 2, "int_val": 200, "float_val": 2.0, "name": "beta"},
        ]
        rows_b = [
            {"id": 1, "int_val": 100, "float_val": 1.0, "name": "alpha"},
            {"id": 2, "int_val": 999, "float_val": 2.0, "name": "beta"},  # only int_val differs
        ]

        table_a = table_factory(SCHEMA, rows_a)
        table_b = table_factory(SCHEMA, rows_b)

        time.sleep(5)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
        )

        dest = f"{test_dataset}._test_diff_filt_{uuid.uuid4().hex[:8]}"
        try:
            ddl = builder.build_diff_table_statement(
                destination=dest, columns_filter=["int_val"]
            )
            bq_client.query(ddl).result()

            dest_table = bq_client.get_table(dest)
            col_names = {f.name for f in dest_table.schema}

            # Should have key + existence + int_val columns
            assert "id" in col_names
            assert "a__int_val" in col_names
            assert "b__int_val" in col_names

            # Should NOT have float_val or name columns
            assert "a__float_val" not in col_names
            assert "a__name" not in col_names
        finally:
            bq_client.delete_table(dest, not_found_ok=True)


class TestEndToEndOnlyDiffsFlow:
    """Integration: full pipeline -> differing_columns -> filtered query.

    This tests the actual production flow executed by ``table-check diff
    --only-diffs`` (cli.py lines 314-321):

        1. run_pipeline() to get per-column diff counts
        2. differing_columns() to determine which columns differ
        3. build_diff_query(columns_filter=...) with that list
        4. Execute and verify only differing columns are returned
    """

    def test_pipeline_identifies_and_filters_to_differing_columns(
        self, bq_client: bigquery.Client, table_factory
    ) -> None:
        """Full flow: run pipeline, identify diffs, run filtered query."""
        rows_a = [
            {"id": 1, "int_val": 100, "float_val": 1.0, "name": "alpha"},
            {"id": 2, "int_val": 200, "float_val": 2.0, "name": "beta"},
        ]
        rows_b = [
            {"id": 1, "int_val": 100, "float_val": 1.0, "name": "alpha"},
            # Only name differs for id=2
            {"id": 2, "int_val": 200, "float_val": 2.0, "name": "CHANGED"},
        ]

        table_a = table_factory(SCHEMA, rows_a)
        table_b = table_factory(SCHEMA, rows_b)

        time.sleep(5)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
        )

        # Step 1: run pipeline (same as CLI)
        pipeline_result = run_pipeline(
            bq_client, builder, PipelineConfig(max_diff_pct=1.0)
        )

        # Step 2: identify differing columns
        cols_with_diffs = differing_columns(pipeline_result.column_diff_counts)
        assert "name" in cols_with_diffs
        assert "int_val" not in cols_with_diffs
        assert "float_val" not in cols_with_diffs

        # Step 3: run filtered query
        query = builder.build_diff_query(columns_filter=cols_with_diffs)
        result = bq_client.query(query).result()
        rows = list(result)

        # Step 4: verify only differing columns in result
        assert len(rows) == 1
        row = dict(rows[0])
        assert row["id"] == 2
        assert row["a__name"] == "beta"
        assert row["b__name"] == "CHANGED"
        # Non-differing columns should be absent
        assert "a__int_val" not in row
        assert "a__float_val" not in row

    def test_pipeline_all_identical_returns_empty_filter(
        self, bq_client: bigquery.Client, table_factory
    ) -> None:
        """When tables are identical, differing_columns returns empty list."""
        rows = [
            {"id": 1, "int_val": 100, "float_val": 1.0, "name": "alpha"},
        ]

        table_a = table_factory(SCHEMA, rows)
        table_b = table_factory(SCHEMA, rows)

        time.sleep(5)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
        )

        pipeline_result = run_pipeline(
            bq_client, builder, PipelineConfig(max_diff_pct=1.0)
        )

        cols_with_diffs = differing_columns(pipeline_result.column_diff_counts)
        assert cols_with_diffs == []

    def test_pipeline_multiple_columns_differ(
        self, bq_client: bigquery.Client, table_factory
    ) -> None:
        """When multiple columns differ, all appear in filter and result."""
        rows_a = [
            {"id": 1, "int_val": 100, "float_val": 1.0, "name": "alpha"},
        ]
        rows_b = [
            # int_val AND name both differ
            {"id": 1, "int_val": 999, "float_val": 1.0, "name": "CHANGED"},
        ]

        table_a = table_factory(SCHEMA, rows_a)
        table_b = table_factory(SCHEMA, rows_b)

        time.sleep(5)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
        )

        pipeline_result = run_pipeline(
            bq_client, builder, PipelineConfig(max_diff_pct=1.0)
        )
        cols_with_diffs = differing_columns(pipeline_result.column_diff_counts)

        assert "int_val" in cols_with_diffs
        assert "name" in cols_with_diffs
        assert "float_val" not in cols_with_diffs

        # Filtered query should include both differing columns
        query = builder.build_diff_query(columns_filter=cols_with_diffs)
        result = bq_client.query(query).result()
        rows = list(result)

        assert len(rows) == 1
        row = dict(rows[0])
        assert "a__int_val" in row
        assert "a__name" in row
        assert "a__float_val" not in row


class TestColumnsFilterWithToleranceIntegration:
    """Integration: columns_filter + tolerance together against BQ.

    This is the exact flow when a user runs:
        table-check diff --only-diffs --tolerance=score:1e-9

    Tests that:
    - Tolerance exclusion considers ALL value columns (not just filtered)
    - Rows with non-tolerance column diffs are still included
    - The SELECT only contains filtered columns
    """

    def test_filter_plus_tolerance_excludes_correctly(
        self, bq_client: bigquery.Client, table_factory
    ) -> None:
        """Filtered diff with tolerance should correctly include/exclude rows."""
        rows_a = [
            {"id": 1, "int_val": 100, "float_val": 1.0, "name": "alpha"},
            {"id": 2, "int_val": 200, "float_val": 2.0, "name": "beta"},
            {"id": 3, "int_val": 300, "float_val": 3.0, "name": "gamma"},
        ]
        rows_b = [
            {"id": 1, "int_val": 100, "float_val": 1.0, "name": "alpha"},
            # id=2: int_val differs (200->999); float_val tiny diff within tol
            {"id": 2, "int_val": 999, "float_val": 2.0 + 1e-12, "name": "beta"},
            # id=3: float_val large diff (3.0->3.5), outside tolerance
            {"id": 3, "int_val": 300, "float_val": 3.5, "name": "gamma"},
        ]

        table_a = table_factory(SCHEMA, rows_a)
        table_b = table_factory(SCHEMA, rows_b)

        time.sleep(5)

        columns = get_table_schema(bq_client, table_a)
        tolerance_config = ToleranceConfig.parse("float_val:1e-9")
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
            tolerance_config=tolerance_config,
        )

        # Filter to only show int_val and float_val (simulating --only-diffs)
        query = builder.build_diff_query(
            columns_filter=["int_val", "float_val"]
        )
        result = bq_client.query(query).result()
        rows = list(result)

        # id=2: int_val differs (200 vs 999), so row IS included even though
        #       float_val is within tolerance.  Tolerance exclusion checks ALL
        #       value columns; since int_val differs, the row is not excluded.
        # id=3: float_val exceeds tolerance (3.0 vs 3.5), so row is included.
        assert len(rows) == 2
        row_ids = {dict(r)["id"] for r in rows}
        assert row_ids == {2, 3}

        # Verify columns: only filtered columns present
        row_dict = dict(rows[0])
        assert "a__int_val" in row_dict
        assert "a__float_val" in row_dict
        assert "a__name" not in row_dict
