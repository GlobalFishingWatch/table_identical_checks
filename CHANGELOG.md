# Changelog

All notable changes to `table-identical-checks` are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.2.1](https://github.com/GlobalFishingWatch/table_identical_checks/compare/v0.2.0...v0.2.1) (2026-06-12)


### Documentation

* add worked recipes for common comparison workflows ([14e1356](https://github.com/GlobalFishingWatch/table_identical_checks/commit/14e13561bff8f0b898e134ee7208e0b141f832c9))
* refresh user and architecture docs for v0.2.0 ([ff8abe8](https://github.com/GlobalFishingWatch/table_identical_checks/commit/ff8abe83f30fd9a3e2a75aac64ccdf63965bd637))

## [Unreleased]

## [0.2.0] -- 2026-06-12

### Added

- **Per-side BigQuery snapshot reads**: all four commands (`summary`, `diff`,
  `count`, `breakdown`) now accept `--snapshot-a` / `--snapshot-b` ISO 8601
  timestamps. When the source table is still live, the generated SQL wraps
  reads in `FOR SYSTEM_TIME AS OF TIMESTAMP('…')`. When the source has been
  deleted, the resolver restores the snapshot via BigQuery's `<table>@<millis>`
  time-travel decorator into a scratch dataset (set via `--scratch-dataset`
  or the `BQ_SCRATCH_DATASET` env var) and reads from the restored copy.
  Restored tables use a deterministic name (`_RESTORED_<basename>_<ts>`) so
  re-runs reuse the same materialised copy, and default to a 7-day expiration.

## [0.1.0] -- 2026-05-15

Initial public release.

### Added

- **`summary` command**: 3-layer BigQuery pipeline that compares two tables in
  a single multi-statement job. Reports row-set differences, per-column diff
  counts, and tolerance-aware delta statistics.
- **`diff` command**: emits an `EXCEPT DISTINCT`-style row-level diff to
  stdout or to a BigQuery table (with `--output-table`, `--write-mode`,
  `--expiration-hours`).
- **`count` command**: cheap diff-row count without materialising results.
- **`breakdown` command**: dimension-grouped diff statistics.
- **`format` command**: re-render a saved summary JSON without re-running BQ.
- **`verify-query` command**: emit an SQL probe that confirms the comparison's
  equality verdict for all columns flagged identical (pre-tolerance).
- **`summary --write-diffs`**: materialise two filtered BigQuery tables, one
  per input, containing only the rows that contribute to the diff after
  tolerance. Honours `TABLE_CHECK_OUTPUT_DATASET` env var or explicit
  `--output-a` / `--output-b` flags; `--write-mode=replace` enforces a
  `DIFF_` prefix safety rail.
- **Tolerance configuration**: absolute and relative tolerances, per-column
  overrides via `--tolerance` and `--rel-tolerance`. Default float
  tolerance (`abs=1e-15`, `rel=1e-12`) filters IEEE 754 noise.
- **Column-type support**: INTEGER, FLOAT, STRING, BOOLEAN, TIMESTAMP, DATE,
  GEOGRAPHY (with optional distance tolerance), STRUCT (flattened to
  dot-notation), ARRAY (multiset equality on scalar / scalar-struct
  elements), KLL sketches (quantile-value comparison via `--kll-cols` /
  `--kll-int-cols`).
- **Partition handling**: auto-detect required partition filter columns and
  emit NULL-safe dummy filter so rows in the `__NULL__` partition aren't
  silently dropped.
- **Schema intersection**: when the two inputs don't share an identical
  schema, compare only the common columns and emit a clear warning listing
  the excluded ones. Hard-errors if any key column is missing from either
  side.
- **Duplicate-key detection**: report duplicate-key counts on both sides
  before the comparison runs.
- **Circuit breaker**: `--max-diff-pct` aborts the pipeline after Layer 1
  when too many rows differ (default disabled at 100%).
- **Verbose and table output formats**, plus a deterministic JSON cache
  path under `$XDG_CACHE_HOME/table-check/` so `format` / `verify-query`
  follow-ups work without remembering paths.
- **Claude Code integration**: a user-scope `/compare` skill wraps
  `table-check summary` with sensible defaults.
- 309 tests (219 unit, 90 BQ integration).

### Notes

- The library is currently optimised for BigQuery; the SQL generation uses
  BQ-specific functions (`APPROX_COUNT_DISTINCT`, `ST_DISTANCE`,
  `KLL_QUANTILES.EXTRACT_POINT_*`, etc.). Cross-engine support is not in
  scope for 0.1.0.

[Unreleased]: https://github.com/GlobalFishingWatch/table_identical_checks/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/GlobalFishingWatch/table_identical_checks/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/GlobalFishingWatch/table_identical_checks/releases/tag/v0.1.0
