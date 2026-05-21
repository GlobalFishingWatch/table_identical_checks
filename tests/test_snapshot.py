"""Tests for the BigQuery snapshot resolver and QueryBuilder injection."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from google.api_core.exceptions import NotFound

from table_identical_checks.backend import ColumnInfo, ColumnType, QueryBuilder
from table_identical_checks.backend.snapshot import (
    ResolvedSnapshotSource,
    _restored_table_ref,
    parse_snapshot_timestamp,
    resolve_snapshot_source,
)

# ----- parse_snapshot_timestamp ----------------------------------------------


def test_parse_iso_8601_with_z_suffix():
    dt, millis = parse_snapshot_timestamp("2026-05-08T12:00:00Z")
    assert dt == datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)
    assert millis == int(dt.timestamp() * 1000)


def test_parse_iso_8601_with_explicit_offset():
    dt, _ = parse_snapshot_timestamp("2026-05-08T13:00:00+01:00")
    # Normalised to UTC
    assert dt.utcoffset().total_seconds() == 0
    assert dt.hour == 12


def test_parse_naive_timestamp_assumed_utc():
    dt, _ = parse_snapshot_timestamp("2026-05-08T12:00:00")
    assert dt.tzinfo == timezone.utc


def test_parse_invalid_string_raises():
    with pytest.raises(ValueError, match="Invalid snapshot timestamp"):
        parse_snapshot_timestamp("not-a-timestamp")


def test_restored_table_ref_format():
    dt = datetime(2026, 5, 8, 12, 34, 56, tzinfo=timezone.utc)
    ref = _restored_table_ref("scratch.diffs", "proj.ds.events", dt)
    assert ref == "scratch.diffs._RESTORED_events_20260508123456"


def test_restored_table_ref_rejects_bad_scratch_dataset():
    dt = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="not a valid 'project.dataset'"):
        _restored_table_ref("bad-format", "proj.ds.events", dt)


# ----- resolve_snapshot_source -----------------------------------------------


def _mock_client():
    """A bigquery.Client mock with controllable get_table / query / copy_table."""
    return MagicMock()


def test_resolve_live_table_returns_for_system_time_path():
    client = _mock_client()
    client.get_table.return_value = MagicMock()  # exists
    # Dry-run for snapshot readability succeeds
    client.query.return_value = MagicMock()

    resolved = resolve_snapshot_source(
        client,
        "proj.ds.tab",
        "2026-05-08T12:00:00Z",
        scratch_dataset=None,
    )
    assert resolved.restored is False
    assert resolved.table_ref == "proj.ds.tab"
    # ts_str is normalized to "YYYY-MM-DD HH:MM:SS UTC" for BQ
    assert resolved.snapshot_timestamp == "2026-05-08 12:00:00 UTC"


def test_resolve_deleted_table_requires_scratch_dataset():
    client = _mock_client()
    client.get_table.side_effect = NotFound("table deleted")
    with pytest.raises(RuntimeError, match="Cannot read snapshot"):
        resolve_snapshot_source(
            client,
            "proj.ds.deleted_tab",
            "2026-05-08T12:00:00Z",
            scratch_dataset=None,
        )


def test_resolve_deleted_table_restores_via_copy():
    client = _mock_client()
    # First get_table call (existence check on source): NotFound.
    # Second get_table call (after restore): returns the new table.
    # Third get_table call (set expiration): returns the new table.
    client.get_table.side_effect = [
        NotFound("table deleted"),  # source check
        NotFound("restored table doesn't exist yet"),  # restored check
        MagicMock(),  # after copy: fetch to set expiration
    ]
    client.copy_table.return_value = MagicMock(result=lambda: None)
    client.update_table.return_value = MagicMock()

    resolved = resolve_snapshot_source(
        client,
        "proj.ds.deleted_tab",
        "2026-05-08T12:00:00Z",
        scratch_dataset="scratch.diffs",
    )

    assert resolved.restored is True
    assert resolved.table_ref == "scratch.diffs._RESTORED_deleted_tab_20260508120000"
    assert resolved.snapshot_timestamp is None  # no FOR SYSTEM_TIME needed
    # Verify the @<millis> decorator was used in the copy source
    copy_call = client.copy_table.call_args
    src = copy_call[0][0]
    assert src.startswith("proj.ds.deleted_tab@")
    # Decorator value should be parseable as int (millis since epoch)
    millis = int(src.split("@")[1])
    assert millis > 0


def test_resolve_reuses_existing_restored_table():
    """If the restored table already exists in scratch, don't copy again."""
    client = _mock_client()
    client.get_table.side_effect = [
        NotFound("source deleted"),  # source missing
        MagicMock(),  # restored table already exists
    ]
    resolved = resolve_snapshot_source(
        client,
        "proj.ds.deleted_tab",
        "2026-05-08T12:00:00Z",
        scratch_dataset="scratch.diffs",
    )
    assert resolved.restored is True
    # copy_table must NOT have been called
    client.copy_table.assert_not_called()


def test_resolve_rejects_bad_table_ref():
    client = _mock_client()
    with pytest.raises(ValueError, match="must be 'project.dataset.table'"):
        resolve_snapshot_source(
            client,
            "not.fully.qualified.too.many.parts",
            "2026-05-08T12:00:00Z",
            scratch_dataset="scratch.diffs",
        )


# ----- QueryBuilder SQL injection --------------------------------------------


def _builder(snapshot_a=None, snapshot_b=None):
    cols = [
        ColumnInfo("id", "INT64", ColumnType.INTEGER, is_nullable=False),
        ColumnInfo("val", "FLOAT64", ColumnType.FLOAT),
    ]
    return QueryBuilder(
        table_a="proj.ds.tab_a",
        table_b="proj.ds.tab_b",
        key_columns=["id"],
        columns=cols,
        snapshot_time_a=snapshot_a,
        snapshot_time_b=snapshot_b,
    )


def test_pipeline_script_omits_for_system_time_when_no_snapshots():
    script = _builder().build_pipeline_script()
    assert "FOR SYSTEM_TIME AS OF" not in script


def test_pipeline_script_injects_per_side_snapshots():
    b = _builder(
        snapshot_a="2026-05-08 12:00:00 UTC",
        snapshot_b="2026-05-01 00:00:00 UTC",
    )
    script = b.build_pipeline_script()
    # Both timestamps appear somewhere
    assert "FOR SYSTEM_TIME AS OF TIMESTAMP('2026-05-08 12:00:00 UTC')" in script
    assert "FOR SYSTEM_TIME AS OF TIMESTAMP('2026-05-01 00:00:00 UTC')" in script
    # Crucially: A's row-count subquery uses A's snapshot and B's uses B's
    assert (
        "SET total_rows_a = (SELECT COUNT(*) FROM (SELECT id, val "
        "FROM `proj.ds.tab_a` FOR SYSTEM_TIME AS OF TIMESTAMP("
        "'2026-05-08 12:00:00 UTC')) AS t)"
    ) in script
    assert (
        "SET total_rows_b = (SELECT COUNT(*) FROM (SELECT id, val "
        "FROM `proj.ds.tab_b` FOR SYSTEM_TIME AS OF TIMESTAMP("
        "'2026-05-01 00:00:00 UTC')) AS t)"
    ) in script


def test_pipeline_script_supports_snapshot_on_only_one_side():
    b = _builder(snapshot_a="2026-05-08 12:00:00 UTC")
    script = b.build_pipeline_script()
    assert "FOR SYSTEM_TIME AS OF TIMESTAMP('2026-05-08 12:00:00 UTC')" in script
    # B should still appear (FROM ... AS b, the join alias) without FOR SYSTEM_TIME
    # attached. The only FOR SYSTEM_TIME usage should reference tab_a.
    assert "`proj.ds.tab_b` AS b" in script
    for_system_time_lines = [ln for ln in script.split("\n") if "FOR SYSTEM_TIME" in ln]
    assert all("tab_a" in ln for ln in for_system_time_lines)
    assert not any("tab_b" in ln for ln in for_system_time_lines)


def test_diff_query_injects_for_system_time():
    """Verify the SQLAlchemy path (build_diff_query) also honours snapshots."""
    b = _builder(snapshot_a="2026-05-08 12:00:00 UTC")
    query = b.build_diff_query()
    assert "FOR SYSTEM_TIME AS OF TIMESTAMP('2026-05-08 12:00:00 UTC')" in query
    assert "proj.ds.tab_a" in query


# ----- CLI resolver ---------------------------------------------------------


def test_cli_resolver_skips_sides_without_snapshot():
    """When neither side has --snapshot, the helper passes through."""
    from table_identical_checks.cli import _resolve_snapshot_sides

    client = _mock_client()
    a, b, ta, tb = _resolve_snapshot_sides(
        client, "proj.ds.a", "proj.ds.b", None, None, None
    )
    assert (a, b, ta, tb) == ("proj.ds.a", "proj.ds.b", None, None)
    # No BQ calls should have happened
    client.get_table.assert_not_called()


def test_cli_resolver_invokes_for_live_side_only():
    from table_identical_checks.cli import _resolve_snapshot_sides

    client = _mock_client()
    client.get_table.return_value = MagicMock()
    client.query.return_value = MagicMock()

    with patch("table_identical_checks.cli.resolve_snapshot_source") as mock_resolve:
        mock_resolve.return_value = ResolvedSnapshotSource(
            table_ref="proj.ds.a",
            snapshot_timestamp="2026-05-08 12:00:00 UTC",
            restored=False,
        )
        a, b, ta, tb = _resolve_snapshot_sides(
            client, "proj.ds.a", "proj.ds.b", "2026-05-08T12:00:00Z", None, None
        )
    assert a == "proj.ds.a"
    assert ta == "2026-05-08 12:00:00 UTC"
    assert b == "proj.ds.b"
    assert tb is None
    # Only one call -- side B was passed through
    assert mock_resolve.call_count == 1
