"""Tests for the dummy partition filter helper.

Regression: the auto-generated filter used to be ``DATE(col) != '1979-01-01'``,
which silently dropped rows in the ``__NULL__`` partition (because NULL !=
literal evaluates to NULL, not TRUE). The helper must now preserve NULLs.
"""

from table_identical_checks.cli import (
    DEFAULT_PARTITION_SENTINEL_DATE,
    _build_dummy_partition_filter,
)


def test_filter_keeps_null_partition():
    f = _build_dummy_partition_filter("ts")
    assert "ts IS NULL" in f


def test_filter_excludes_sentinel_date():
    f = _build_dummy_partition_filter("ts")
    assert f"DATE(ts) != '{DEFAULT_PARTITION_SENTINEL_DATE}'" in f


def test_filter_is_disjunction_of_null_and_non_sentinel():
    # The OR is what makes the predicate NULL-safe: each branch is TRUE for
    # exactly one of the two row populations (NULL partition, non-sentinel).
    f = _build_dummy_partition_filter("ts")
    assert " OR " in f


def test_custom_sentinel_date_is_used():
    f = _build_dummy_partition_filter("ts", sentinel_date="2000-01-01")
    assert "'2000-01-01'" in f
    assert "1979" not in f
