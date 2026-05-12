"""Tests for the schema-intersection helper used by all CLI commands."""

from __future__ import annotations

from typing import cast
from unittest.mock import patch

import click
import pytest
from google.cloud import bigquery

from table_identical_checks.backend import ColumnInfo, ColumnType
from table_identical_checks.cli import _intersect_table_schemas

# get_table_schema is mocked away in every test below, so the client is unused.
FAKE_CLIENT: bigquery.Client = cast(bigquery.Client, None)


def _col(name: str, ct: ColumnType, bq_type: str) -> ColumnInfo:
    return ColumnInfo(name=name, bq_type=bq_type, column_type=ct)


def _patch_schemas(side_a: list[ColumnInfo], side_b: list[ColumnInfo]):
    """Patch get_table_schema to return ``side_a`` then ``side_b``."""
    return patch(
        "table_identical_checks.cli.get_table_schema",
        side_effect=[side_a, side_b],
    )


def test_intersection_returns_common_columns_in_a_order():
    a = [
        _col("id", ColumnType.INTEGER, "INT64"),
        _col("name", ColumnType.STRING, "STRING"),
        _col("score", ColumnType.FLOAT, "FLOAT64"),
    ]
    b = [
        _col("score", ColumnType.FLOAT, "FLOAT64"),
        _col("name", ColumnType.STRING, "STRING"),
        _col("id", ColumnType.INTEGER, "INT64"),
    ]
    with _patch_schemas(a, b):
        result = _intersect_table_schemas(FAKE_CLIENT, "p.d.a", "p.d.b", ["id"])
    assert [c.name for c in result] == ["id", "name", "score"]


def test_columns_only_in_a_are_excluded():
    a = [
        _col("id", ColumnType.INTEGER, "INT64"),
        _col("extra_a", ColumnType.STRING, "STRING"),
    ]
    b = [_col("id", ColumnType.INTEGER, "INT64")]
    with _patch_schemas(a, b):
        result = _intersect_table_schemas(FAKE_CLIENT, "p.d.a", "p.d.b", ["id"])
    assert [c.name for c in result] == ["id"]


def test_columns_only_in_b_are_excluded():
    a = [_col("id", ColumnType.INTEGER, "INT64")]
    b = [
        _col("id", ColumnType.INTEGER, "INT64"),
        _col("extra_b", ColumnType.STRING, "STRING"),
    ]
    with _patch_schemas(a, b):
        result = _intersect_table_schemas(FAKE_CLIENT, "p.d.a", "p.d.b", ["id"])
    assert [c.name for c in result] == ["id"]


def test_type_mismatch_drops_column():
    a = [
        _col("id", ColumnType.INTEGER, "INT64"),
        _col("score", ColumnType.INTEGER, "INT64"),
    ]
    b = [
        _col("id", ColumnType.INTEGER, "INT64"),
        _col("score", ColumnType.FLOAT, "FLOAT64"),
    ]
    with _patch_schemas(a, b):
        result = _intersect_table_schemas(FAKE_CLIENT, "p.d.a", "p.d.b", ["id"])
    assert [c.name for c in result] == ["id"]


def test_bq_type_mismatch_with_same_column_type_drops_column():
    # Same ColumnType.STRING, but different bq_type — still treat as mismatch
    # because canonicalisation logic depends on bq_type strings.
    a = [
        _col("id", ColumnType.INTEGER, "INT64"),
        _col("payload", ColumnType.ARRAY, "ARRAY<STRING>"),
    ]
    b = [
        _col("id", ColumnType.INTEGER, "INT64"),
        _col("payload", ColumnType.ARRAY, "ARRAY<INT64>"),
    ]
    with _patch_schemas(a, b):
        result = _intersect_table_schemas(FAKE_CLIENT, "p.d.a", "p.d.b", ["id"])
    assert [c.name for c in result] == ["id"]


def test_missing_key_in_a_raises_clickexception():
    a = [_col("name", ColumnType.STRING, "STRING")]
    b = [
        _col("id", ColumnType.INTEGER, "INT64"),
        _col("name", ColumnType.STRING, "STRING"),
    ]
    with _patch_schemas(a, b):
        with pytest.raises(click.ClickException) as exc_info:
            _intersect_table_schemas(FAKE_CLIENT, "p.d.a", "p.d.b", ["id"])
    assert "missing in p.d.a" in str(exc_info.value.message)


def test_missing_key_in_b_raises_clickexception():
    a = [
        _col("id", ColumnType.INTEGER, "INT64"),
        _col("name", ColumnType.STRING, "STRING"),
    ]
    b = [_col("name", ColumnType.STRING, "STRING")]
    with _patch_schemas(a, b):
        with pytest.raises(click.ClickException) as exc_info:
            _intersect_table_schemas(FAKE_CLIENT, "p.d.a", "p.d.b", ["id"])
    assert "missing in p.d.b" in str(exc_info.value.message)


def test_identical_schemas_no_warning(capsys):
    a = [
        _col("id", ColumnType.INTEGER, "INT64"),
        _col("name", ColumnType.STRING, "STRING"),
    ]
    b = [
        _col("id", ColumnType.INTEGER, "INT64"),
        _col("name", ColumnType.STRING, "STRING"),
    ]
    with _patch_schemas(a, b):
        _intersect_table_schemas(FAKE_CLIENT, "p.d.a", "p.d.b", ["id"])
    out = capsys.readouterr().out
    assert "Schema mismatch" not in out


def test_warning_lists_each_excluded_column(capsys):
    a = [
        _col("id", ColumnType.INTEGER, "INT64"),
        _col("only_a", ColumnType.STRING, "STRING"),
        _col("typediff", ColumnType.INTEGER, "INT64"),
    ]
    b = [
        _col("id", ColumnType.INTEGER, "INT64"),
        _col("only_b", ColumnType.STRING, "STRING"),
        _col("typediff", ColumnType.FLOAT, "FLOAT64"),
    ]
    with _patch_schemas(a, b):
        _intersect_table_schemas(FAKE_CLIENT, "p.d.a", "p.d.b", ["id"])
    out = capsys.readouterr().out
    assert "Schema mismatch" in out
    assert "only_a" in out
    assert "only_b" in out
    assert "typediff" in out
