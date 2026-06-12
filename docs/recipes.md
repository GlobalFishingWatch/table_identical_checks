# Recipes

Concrete, end-to-end examples for common comparison workflows. Each recipe
shows the command, the relevant output, and how to read it.

The placeholders `<proj>` / `<ds>` / `<scratch_ds>` stand in for your own
BigQuery project, dataset, and scratch dataset references.

## 1. Quick comparison of two table versions

The starting point: compare two versions of a table that are *supposed* to
be identical (e.g. a backfill vs the live table, or two snapshots of the
same logical view).

```bash
table-check summary \
  --table-a=<proj>.<ds>.vessel_info_v1 \
  --table-b=<proj>.<ds>.vessel_info_v2 \
  --keys=vessel_id \
  --format=table
```

Output (excerpt):

```
rows: 623 vs 623 | diffs: 2 (filtered 0)

Identical columns: callsign.count, callsign.freq, callsign.value, ...

Column                Type    Diffs    Diff%    MaxAbs    MaxRel    Exc.tol   Status
n_shipname.freq       FLT         2    0.32%   1.0e-04   2.1e-04         2      NOK
shipname.freq         FLT         2    0.32%   1.0e-04   2.1e-04         2      NOK
```

How to read it:

- **`rows: 623 vs 623`** — both tables have the same row count.
- **`diffs: 2 (filtered 0)`** — 2 rows differ; 0 of those diffs fell within
  the default tolerance (`abs=1e-15`, `rel=1e-12`).
- **`Identical columns: ...`** — columns that compared bit-equal on every
  matched row; they're folded into a single line so the per-column section
  only shows columns that actually differ.
- **Per-column rows** — for each non-identical column, you get the diff
  count, the max absolute and relative deltas, and a status. `NOK` means
  the tolerance wasn't enough to absorb the diff; `OK` would mean it was.

Floats with `MaxRel = 2.1e-04` far exceed the default `1e-12` relative
tolerance, so these are real differences -- not IEEE 754 noise.

## 2. Materialise filtered copies of each side with `--write-diffs`

Useful when you want to drop into SQL to investigate specific differences.
`summary --write-diffs` writes two tables -- one per side -- containing only
the rows that participate in the diff after tolerance:

```bash
TABLE_CHECK_OUTPUT_DATASET=<proj>.<scratch_ds> \
table-check summary \
  --table-a=<proj>.<ds>.product_events_a \
  --table-b=<proj>.<ds>.product_events_b \
  --keys=event_id \
  --write-diffs --write-mode=replace \
  --format=table
```

After the run, two tables exist in your scratch dataset:

```
<proj>.<scratch_ds>.DIFF_product_events_a
<proj>.<scratch_ds>.DIFF_product_events_b
```

Each table is a true row-subset of its source:

- Rows present only on its side (only-in-A / only-in-B).
- Rows present on both sides where at least one column differs outside
  tolerance.

Rows that were identical or absorbed by tolerance are excluded. Both tables
expire after 7 days by default (override with `--expiration-hours`).

To inspect a specific difference:

```sql
SELECT a.event_id, a.event_info AS a_event_info, b.event_info AS b_event_info
FROM `<proj>.<scratch_ds>.DIFF_product_events_a` a
JOIN `<proj>.<scratch_ds>.DIFF_product_events_b` b USING (event_id)
WHERE a.event_info != b.event_info
LIMIT 10
```

Notes on the safety rail: `--write-mode=replace` only overwrites tables
whose basename starts with `DIFF_`. This protects you from accidentally
clobbering a hand-named table by passing a typo'd `--output-a`. To opt out
of this convention you'd need to drop the auto-derived names and pass
explicit `--output-a` / `--output-b` flags pointing at non-`DIFF_*` names
*and* leave `--write-mode=error` (the default).

## 3. Compare a table against its past self via time travel

BigQuery's logical billing tier keeps 7 days of free time-travel history.
The `--snapshot-a` / `--snapshot-b` flags wrap reads in
`FOR SYSTEM_TIME AS OF TIMESTAMP('...')` so you can compare any two points
in that window:

```bash
# 24 hours ago vs now
YESTERDAY=$(date -u -d '24 hours ago' +"%Y-%m-%dT%H:%M:%SZ")
table-check summary \
  --table-a=<proj>.<ds>.live_table \
  --table-b=<proj>.<ds>.live_table \
  --keys=id \
  --snapshot-a=$YESTERDAY \
  --format=table
```

Note that `--table-a` and `--table-b` point at the *same* table -- only the
snapshot differs.

### When the source has been deleted

`FOR SYSTEM_TIME AS OF` only works against tables that currently exist.
If the source has been deleted, the resolver falls through to BigQuery's
`<table>@<millis>` time-travel decorator: it copies the snapshot into your
scratch dataset and reads from the restored copy.

```bash
BQ_SCRATCH_DATASET=<proj>.<scratch_ds> \
table-check summary \
  --table-a=<proj>.<ds>.deleted_table \
  --table-b=<proj>.<ds>.replacement_table \
  --keys=id \
  --snapshot-a=2026-05-08T12:00:00Z \
  --format=table
```

The restored copy lands at
`<proj>.<scratch_ds>._RESTORED_deleted_table_20260508120000` and expires
after 7 days. Re-runs against the same `(source, snapshot)` pair reuse the
existing restored copy instead of copying again.

If the source is deleted but no scratch dataset is configured, the
resolver raises with a clear message pointing at `--scratch-dataset` /
`$BQ_SCRATCH_DATASET`.

## 4. Re-render and verify a saved summary without re-running BigQuery

Every `summary` run writes its result to a deterministic JSON cache path
(`$XDG_CACHE_HOME/table-check/<hash>.json`, keyed on `table_a + table_b +
keys`). The companion commands consume that file:

```bash
# Run once -- the path is printed at the end of the summary output
table-check summary --table-a=... --table-b=... --keys=...
# ...
# Summary written to /home/.../table-check/abc123def456.json

# Switch to compact table view without re-running BQ
table-check format --input-json=/home/.../table-check/abc123def456.json --format=table

# Emit an EXCEPT DISTINCT / UNION ALL probe that verifies the comparison's
# equality verdict on the columns it found equal (pre-tolerance). Paste
# the emitted SQL into BigQuery; a zero-row result confirms.
table-check verify-query --input-json=/home/.../table-check/abc123def456.json
```

`verify-query` is most useful when you want an independent sanity check
of the comparison's identical-columns claim, separate from the pipeline
that produced it.

## 5. Comparison with a non-unique key

`table-check` will run a comparison whose key columns aren't unique on
either side, but it warns prominently when it detects duplicates:

```
!! DUPLICATE KEYS DETECTED !!
  Table A: 214,668 duplicate key(s), 429,336 rows affected, max 2x per key
  Table B: 169,932 duplicate key(s), 339,864 rows affected, max 2x per key
```

Most metrics in the per-column breakdown become meaningless once duplicates
exist (the `FULL OUTER JOIN` fans out, so `Diff%` can easily exceed 100%).
The fix is always to broaden the key until it's unique. Common patterns:

| Symptom | Likely fix |
|---|---|
| Same row repeats in both A and B with same values | Add a time-window column (e.g. `(id, first_timestamp, last_timestamp)`) |
| Same row repeats in A only | Add a versioning / source column (e.g. `(id, source_code)`) |
| Massive only-in-A and only-in-B numbers | One of the *current* key columns is shifting between snapshots; drop the unstable column |

For composite-key tables, start with the most specific identifying tuple
and remove columns one at a time until the duplicate warning disappears or
the comparison starts producing sensible numbers. The CLI's match-rate
plus the per-column `Diff%` are the signals to watch.
