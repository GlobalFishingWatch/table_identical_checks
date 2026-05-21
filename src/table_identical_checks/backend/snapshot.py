"""BigQuery time-travel snapshot resolution.

Two cases:

1. **Live table + snapshot timestamp** -- inject ``FOR SYSTEM_TIME AS OF
   TIMESTAMP('<ts>')`` into the source query. Cheap, no side effects.

2. **Deleted table + snapshot timestamp** -- ``FOR SYSTEM_TIME AS OF`` only
   works against tables that currently exist. To read a snapshot of a table
   that no longer exists, restore it via BigQuery's ``@<millis>`` time-travel
   decorator into a scratch dataset, then point the comparison at the
   restored copy. Restored tables are written with an expiration so they
   self-clean.

The resolver picks the right strategy automatically: it tries to fetch the
table's current schema, and if BQ returns 404 it falls through to the
restore path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from google.api_core.exceptions import NotFound
from google.cloud import bigquery

_FQN_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_PROJECT_DATASET_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class ResolvedSnapshotSource:
    """Effective source description after resolving a snapshot request.

    Attributes:
        table_ref: The fully-qualified table reference to query. Either the
            original (when the table is still live) or the restored copy
            (when the original was deleted).
        snapshot_timestamp: When non-None, callers must wrap reads in
            ``FOR SYSTEM_TIME AS OF TIMESTAMP('<value>')``. When None, the
            ``table_ref`` already points at a static snapshot (the restored
            copy) and no time-travel SQL is needed.
        restored: True iff ``table_ref`` is a freshly-materialised restore
            (informational only -- e.g. for printing in CLI output).
    """

    table_ref: str
    snapshot_timestamp: str | None
    restored: bool = False


def parse_snapshot_timestamp(value: str) -> tuple[datetime, int]:
    """Parse an ISO 8601 timestamp string into ``(datetime_utc, millis_since_epoch)``.

    The millis form is needed for BigQuery's ``<table>@<millis>`` decorator
    used when restoring deleted tables.
    """
    s_norm = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s_norm)
    except ValueError as exc:
        raise ValueError(
            f"Invalid snapshot timestamp {value!r}; expected ISO 8601 "
            "(e.g. '2026-05-08T12:00:00Z')."
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    millis = int(dt.timestamp() * 1000)
    return dt, millis


def _restored_table_ref(scratch_dataset: str, source_ref: str, dt: datetime) -> str:
    """Deterministic name for a restored copy.

    Re-runs against the same source + snapshot land on the same restored
    table, so the second run is essentially free (we no-op when the restored
    table already exists).
    """
    if not _PROJECT_DATASET_RE.match(scratch_dataset):
        raise ValueError(
            f"scratch_dataset {scratch_dataset!r} is not a valid 'project.dataset' reference."
        )
    basename = source_ref.rsplit(".", 1)[-1]
    ts_compact = dt.strftime("%Y%m%d%H%M%S")
    return f"{scratch_dataset}._RESTORED_{basename}_{ts_compact}"


def _table_exists(client: bigquery.Client, table_ref: str) -> bool:
    try:
        client.get_table(table_ref)
        return True
    except NotFound:
        return False


def _table_exists_at_snapshot(client: bigquery.Client, table_ref: str, dt: datetime) -> bool:
    """Try a dry-run FOR SYSTEM TIME query to see whether the snapshot is readable."""
    ts_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    query = (
        f"SELECT 1 FROM `{table_ref}` "
        f"FOR SYSTEM_TIME AS OF TIMESTAMP('{ts_str}') LIMIT 0"
    )
    job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
    try:
        client.query(query, job_config=job_config)
        return True
    except Exception:
        return False


def resolve_snapshot_source(
    client: bigquery.Client,
    table_ref: str,
    snapshot_time: str,
    scratch_dataset: str | None,
    expiration_hours: int = 168,
) -> ResolvedSnapshotSource:
    """Resolve a (table, snapshot_time) pair to an effective source.

    Strategy:
      1. Live table + readable snapshot -> use ``FOR SYSTEM_TIME AS OF`` SQL
         against the original.
      2. Deleted table (or snapshot not readable on live table) -> restore
         the snapshot to ``scratch_dataset`` via the BQ ``@<millis>``
         decorator. Subsequent reads target the restored copy as a normal
         table; no time-travel SQL needed.

    The restored table is created with an expiration_timestamp option so
    it self-cleans after ``expiration_hours`` (default 168 = 7 days).

    Args:
        client: BigQuery client.
        table_ref: Fully-qualified source table reference.
        snapshot_time: ISO 8601 timestamp string.
        scratch_dataset: ``project.dataset`` to write restored tables into.
            Required when the source table is deleted; ignored otherwise.
        expiration_hours: TTL for restored tables. Pass 0 to disable.

    Returns:
        A ResolvedSnapshotSource describing the effective table reference
        and whether FOR SYSTEM_TIME wrapping is still required.

    Raises:
        ValueError: invalid timestamp or invalid scratch_dataset.
        RuntimeError: source table is deleted but no scratch_dataset is
            available to restore into.
    """
    if not _FQN_RE.match(table_ref):
        raise ValueError(
            f"table_ref {table_ref!r} must be 'project.dataset.table'."
        )

    dt, millis = parse_snapshot_timestamp(snapshot_time)
    ts_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC")

    if _table_exists(client, table_ref) and _table_exists_at_snapshot(client, table_ref, dt):
        return ResolvedSnapshotSource(
            table_ref=table_ref, snapshot_timestamp=ts_str, restored=False
        )

    # Live table missing OR snapshot not readable on the live table. Restore
    # the snapshot into the scratch dataset via the @<millis> decorator.
    if not scratch_dataset:
        raise RuntimeError(
            f"Cannot read snapshot of {table_ref!r} at {snapshot_time!r}: the table is "
            "not readable at that point in time (either deleted or pre-creation). "
            "Pass --scratch-dataset=<project.dataset> or set BQ_SCRATCH_DATASET to "
            "restore the snapshot into a temporary copy."
        )

    restored_ref = _restored_table_ref(scratch_dataset, table_ref, dt)
    if not _table_exists(client, restored_ref):
        _restore_via_copy(client, table_ref, millis, restored_ref, expiration_hours)
    return ResolvedSnapshotSource(
        table_ref=restored_ref, snapshot_timestamp=None, restored=True
    )


def _restore_via_copy(
    client: bigquery.Client,
    source_ref: str,
    millis: int,
    target_ref: str,
    expiration_hours: int,
) -> None:
    """Copy ``<source>@<millis>`` to ``<target>``. Caller ensures target doesn't exist."""
    source_with_decorator = f"{source_ref}@{millis}"
    job_config = bigquery.CopyJobConfig(write_disposition="WRITE_EMPTY")
    job = client.copy_table(source_with_decorator, target_ref, job_config=job_config)
    job.result()
    if expiration_hours > 0:
        table = client.get_table(target_ref)
        from datetime import timedelta

        table.expires = datetime.now(timezone.utc) + timedelta(hours=expiration_hours)
        client.update_table(table, ["expires"])
