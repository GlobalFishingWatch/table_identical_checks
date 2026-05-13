"""SQL-shape tests for --write-diffs pipeline output.

These don't run BigQuery; they only inspect the generated multi-statement
script to verify the new CREATE TABLE statements are present in both
IF branches and that the L1 row_within_tolerance flag is emitted iff
tolerance is configured.
"""

from __future__ import annotations

from table_identical_checks.backend import (
    ColumnInfo,
    ColumnType,
    OutputDiffConfig,
    QueryBuilder,
    ToleranceConfig,
)


def _builder(tolerance: ToleranceConfig | None = None) -> QueryBuilder:
    return QueryBuilder(
        table_a="proj.ds.tab_a",
        table_b="proj.ds.tab_b",
        key_columns=["id"],
        columns=[
            ColumnInfo("id", "INT64", ColumnType.INTEGER, is_nullable=False),
            ColumnInfo("amount", "FLOAT64", ColumnType.FLOAT),
            ColumnInfo("label", "STRING", ColumnType.STRING),
        ],
        tolerance_config=tolerance,
    )


def test_no_diff_output_changes_nothing():
    script = _builder().build_pipeline_script()
    assert "CREATE TABLE `" not in script
    assert "CREATE OR REPLACE TABLE" not in script
    assert "row_within_tolerance" not in script


def test_diff_output_emits_two_create_statements():
    cfg = OutputDiffConfig(
        output_a="scratch.ds.DIFF_a",
        output_b="scratch.ds.DIFF_b",
        write_mode="error",
    )
    script = _builder().build_pipeline_script(output_diff=cfg)
    assert "CREATE TABLE `scratch.ds.DIFF_a`" in script
    assert "CREATE TABLE `scratch.ds.DIFF_b`" in script
    # One CREATE per branch (so two of each across the IF/ELSE).
    assert script.count("CREATE TABLE `scratch.ds.DIFF_a`") == 2
    assert script.count("CREATE TABLE `scratch.ds.DIFF_b`") == 2


def test_replace_write_mode_uses_create_or_replace():
    cfg = OutputDiffConfig(
        output_a="scratch.ds.DIFF_a",
        output_b="scratch.ds.DIFF_b",
        write_mode="replace",
    )
    script = _builder().build_pipeline_script(output_diff=cfg)
    assert "CREATE OR REPLACE TABLE `scratch.ds.DIFF_a`" in script
    assert "CREATE OR REPLACE TABLE `scratch.ds.DIFF_b`" in script
    assert "CREATE TABLE `scratch.ds.DIFF_" not in script


def test_expiration_options_emitted_when_nonzero():
    cfg = OutputDiffConfig(
        output_a="scratch.ds.DIFF_a",
        output_b="scratch.ds.DIFF_b",
        write_mode="error",
        expiration_hours=72,
    )
    script = _builder().build_pipeline_script(output_diff=cfg)
    assert "INTERVAL 72 HOUR" in script
    assert "expiration_timestamp" in script


def test_expiration_zero_omits_options_clause():
    cfg = OutputDiffConfig(
        output_a="scratch.ds.DIFF_a",
        output_b="scratch.ds.DIFF_b",
        write_mode="error",
        expiration_hours=0,
    )
    script = _builder().build_pipeline_script(output_diff=cfg)
    assert "expiration_timestamp" not in script


def test_row_within_tolerance_emitted_only_with_tolerance():
    cfg = OutputDiffConfig(
        output_a="scratch.ds.DIFF_a",
        output_b="scratch.ds.DIFF_b",
        write_mode="error",
    )
    # No tolerance config -> L1 doesn't need the flag.
    script_no_tol = _builder().build_pipeline_script(output_diff=cfg)
    assert "row_within_tolerance" not in script_no_tol

    # With tolerance -> the flag is emitted in L1 and referenced in the filter.
    tol = ToleranceConfig.parse("amount:1e-9")
    script_tol = _builder(tol).build_pipeline_script(output_diff=cfg)
    assert "row_within_tolerance" in script_tol
    assert "NOT _l1.row_within_tolerance" in script_tol


def test_diff_filter_uses_exists_subquery_not_join():
    """EXISTS prevents duplicate-key fanout that a JOIN would produce."""
    cfg = OutputDiffConfig(
        output_a="scratch.ds.DIFF_a",
        output_b="scratch.ds.DIFF_b",
        write_mode="error",
    )
    script = _builder().build_pipeline_script(output_diff=cfg)
    assert "WHERE EXISTS (" in script
    assert "JOIN _l1" not in script
    # Each side's predicate references the side's existence flag.
    assert "_l1.in_a" in script
    assert "_l1.in_b" in script


def test_diff_statements_appear_in_both_if_branches():
    """Aborted-path coverage: CREATE TABLE statements must also be emitted
    inside the IF (abort) branch, not just the ELSE branch."""
    cfg = OutputDiffConfig(
        output_a="scratch.ds.DIFF_a",
        output_b="scratch.ds.DIFF_b",
        write_mode="error",
    )
    script = _builder().build_pipeline_script(output_diff=cfg)
    # The abort branch is everything between "THEN" and "ELSE";
    # the happy branch is between "ELSE" and "END IF".
    abort_branch = script.split("THEN", 1)[1].split("ELSE", 1)[0]
    else_branch = script.split("ELSE", 1)[1].split("END IF", 1)[0]
    assert "CREATE TABLE `scratch.ds.DIFF_a`" in abort_branch
    assert "CREATE TABLE `scratch.ds.DIFF_b`" in abort_branch
    assert "CREATE TABLE `scratch.ds.DIFF_a`" in else_branch
    assert "CREATE TABLE `scratch.ds.DIFF_b`" in else_branch
