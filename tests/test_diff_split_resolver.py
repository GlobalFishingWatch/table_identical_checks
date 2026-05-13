"""Tests for the --write-diffs output-table resolution logic.

This is the CLI helper that takes the user-supplied flags + the env var
``TABLE_CHECK_OUTPUT_DATASET`` and returns the two fully-qualified output
table names (or raises a ClickException).
"""

from __future__ import annotations

import click
import pytest

from table_identical_checks.cli import (
    ENV_OUTPUT_DATASET,
    _resolve_output_diff_tables,
)


@pytest.fixture
def env(monkeypatch):
    """Provide a setter for TABLE_CHECK_OUTPUT_DATASET; clean by default."""
    monkeypatch.delenv(ENV_OUTPUT_DATASET, raising=False)
    return monkeypatch


def test_both_overrides_used_verbatim(env):
    out_a, out_b = _resolve_output_diff_tables(
        "p.d.left",
        "p.d.right",
        "scratch.ds.DIFF_left",
        "scratch.ds.DIFF_right",
        write_mode="error",
    )
    assert out_a == "scratch.ds.DIFF_left"
    assert out_b == "scratch.ds.DIFF_right"


def test_only_one_override_is_an_error(env):
    with pytest.raises(click.ClickException, match="both be set or both omitted"):
        _resolve_output_diff_tables(
            "p.d.left", "p.d.right", "scratch.ds.x", None, "error"
        )
    with pytest.raises(click.ClickException, match="both be set or both omitted"):
        _resolve_output_diff_tables(
            "p.d.left", "p.d.right", None, "scratch.ds.x", "error"
        )


def test_env_var_path_derives_diff_basenames(env):
    env.setenv(ENV_OUTPUT_DATASET, "scratch.diffs")
    out_a, out_b = _resolve_output_diff_tables(
        "p.d.left_table", "p.d.right_table", None, None, "error"
    )
    assert out_a == "scratch.diffs.DIFF_left_table"
    assert out_b == "scratch.diffs.DIFF_right_table"


def test_neither_overrides_nor_env_is_an_error(env):
    with pytest.raises(click.ClickException, match="TABLE_CHECK_OUTPUT_DATASET"):
        _resolve_output_diff_tables(
            "p.d.left", "p.d.right", None, None, "error"
        )


def test_invalid_env_var_format(env):
    env.setenv(ENV_OUTPUT_DATASET, "not_a_dataset")  # missing the dot
    with pytest.raises(click.ClickException, match="is not a valid 'project.dataset'"):
        _resolve_output_diff_tables(
            "p.d.left", "p.d.right", None, None, "error"
        )


def test_invalid_fqn_override(env):
    with pytest.raises(click.ClickException, match="is not a valid 'project.dataset.table'"):
        _resolve_output_diff_tables(
            "p.d.left",
            "p.d.right",
            "two.parts",  # missing the third segment
            "p.d.DIFF_right",
            "error",
        )


def test_fqn_collision_via_env_var_is_an_error(env):
    # Both sources have the same basename — env-var path derives the same
    # DIFF_<basename> on both sides.
    env.setenv(ENV_OUTPUT_DATASET, "scratch.diffs")
    with pytest.raises(click.ClickException, match="collide on 'scratch.diffs.DIFF_messages'"):
        _resolve_output_diff_tables(
            "proj_a.ds.messages",
            "proj_b.ds.messages",
            None,
            None,
            "error",
        )


def test_same_basename_with_explicit_distinct_overrides_is_fine(env):
    """Two source tables sharing a basename are fine when overrides differ."""
    out_a, out_b = _resolve_output_diff_tables(
        "proj_a.ds.messages",
        "proj_b.ds.messages",
        "scratch.ds.DIFF_messages_a",
        "scratch.ds.DIFF_messages_b",
        "error",
    )
    assert out_a == "scratch.ds.DIFF_messages_a"
    assert out_b == "scratch.ds.DIFF_messages_b"


def test_replace_requires_diff_prefix_on_overrides(env):
    """Safety rail: --write-mode=replace refuses to overwrite non-DIFF names."""
    with pytest.raises(click.ClickException, match="requires output table basenames"):
        _resolve_output_diff_tables(
            "p.d.left",
            "p.d.right",
            "scratch.ds.my_important_table",  # no DIFF_ prefix
            "scratch.ds.DIFF_right",
            "replace",
        )


def test_replace_with_diff_prefix_passes(env):
    out_a, out_b = _resolve_output_diff_tables(
        "p.d.left",
        "p.d.right",
        "scratch.ds.DIFF_left",
        "scratch.ds.DIFF_right",
        "replace",
    )
    assert out_a == "scratch.ds.DIFF_left"
    assert out_b == "scratch.ds.DIFF_right"


def test_replace_via_env_var_path_passes(env):
    """Auto-derived names always start with DIFF_ -> safety rail no-ops."""
    env.setenv(ENV_OUTPUT_DATASET, "scratch.diffs")
    out_a, out_b = _resolve_output_diff_tables(
        "p.d.left", "p.d.right", None, None, "replace"
    )
    assert out_a == "scratch.diffs.DIFF_left"
    assert out_b == "scratch.diffs.DIFF_right"


def test_error_mode_does_not_enforce_diff_prefix(env):
    """Without --write-mode=replace, the user can name outputs anything."""
    out_a, out_b = _resolve_output_diff_tables(
        "p.d.left",
        "p.d.right",
        "scratch.ds.my_custom_name",
        "scratch.ds.another_name",
        "error",
    )
    assert out_a == "scratch.ds.my_custom_name"
    assert out_b == "scratch.ds.another_name"
