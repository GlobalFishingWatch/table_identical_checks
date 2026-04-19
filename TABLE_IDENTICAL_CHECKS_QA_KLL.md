# QA handoff: comparing KLL_QUANTILES sketch columns across two BigQuery tables

> **Update:** KLL support is now built in via **value-based quantile
> comparison** -- run with `--kll-cols=speed_sketch` (or
> `--kll-int-cols=<col>` for `KLL_INT64`). Tolerances come from
> `--kll-abs-tol` (default 0.0) and `--kll-rel-tol` (default 0.05), applied
> to the extracted quantile value at 5 probes
> `[0.1, 0.25, 0.5, 0.75, 0.9]`.
>
> Strategy 2 (rank-based) is **not implementable in BigQuery** -- BigQuery
> does not expose a rank-from-value function for KLL sketches
> (`KLL_QUANTILES.RANK_*` does not exist). Strategy 1 (value-space
> comparison) is what the tool now implements, and the hand-rolled
> Strategy 1 query below remains a valid manual fallback.

## Context

We are running a table-equivalence comparison between two BigQuery tables using
the `table-check summary` CLI (from `/mnt/encrypted_data/git/table_identical_checks`).
The tool excludes `BYTES`-typed columns from its comparison. In our tables, one
such column is a KLL quantile sketch:

```sql
KLL_QUANTILES.INIT_FLOAT64(IF(
  distance_from_port_m < 3000,
  NULL,
  speed_knots
)) AS speed_sketch
```

The `speed_sketch` column holds a per-`(seg_id, date)` KLL sketch of `speed_knots`
for non-port positions. Direct `BYTES` equality is not meaningful: two KLL
sketches built from the same multiset of input values are not required to be
byte-identical. We need a semantic comparison.

## Why direct BYTES comparison is wrong

- KLL's internal representation depends on ordering of the stream and internal
  compaction decisions. BigQuery may parallelise aggregation differently between
  a per-day query and a single range query, producing different byte strings
  even though both represent the same underlying multiset.
- KLL guarantees statistical equivalence within a rank-error bound, not
  byte equivalence. The correct equivalence test is on *what the sketch is for*:
  quantile extraction.

## KLL error model (what "close enough" means)

Source: BigQuery docs + Apache DataSketches docs.

- BigQuery default precision: **K = 200**
- Normalized rank error at K=200: **≈ 1.33% single-sided / 1.65% double-sided**.
- Holds in **99.999%** of queries per BigQuery's documented guarantee.
- Error is **uniform across the distribution** (median and tails equally
  approximated). This is an absolute +/- epsilon on the rank, not the value.
- Sketch is **exact when n <= K**: below ~200 input values, the sketch stores
  every value. Exact quantiles come back for sparse segment-days.
- Value error can be arbitrarily large if the distribution has cliffs
  (not expected here; vessel speed is smooth within a segment-day).

## The two comparison strategies

Two independent checks. If either passes, we consider the sketches equivalent.
Recommend running both.

### Strategy 1: Compare extracted quantiles

Extract a fixed set of quantiles from both sketches and compare the values
with a tolerance.

```sql
WITH
a AS (
  SELECT
    seg_id,
    date,
    KLL_QUANTILES.EXTRACT_POINT_FLOAT64(speed_sketch, 0.10) AS p10,
    KLL_QUANTILES.EXTRACT_POINT_FLOAT64(speed_sketch, 0.25) AS p25,
    KLL_QUANTILES.EXTRACT_POINT_FLOAT64(speed_sketch, 0.50) AS p50,
    KLL_QUANTILES.EXTRACT_POINT_FLOAT64(speed_sketch, 0.75) AS p75,
    KLL_QUANTILES.EXTRACT_POINT_FLOAT64(speed_sketch, 0.90) AS p90,
    KLL_QUANTILES.EXTRACT_POINT_FLOAT64(speed_sketch, 0.99) AS p99
  FROM `<table_a>`
  WHERE speed_sketch IS NOT NULL
),
b AS (
  SELECT
    seg_id,
    date,
    KLL_QUANTILES.EXTRACT_POINT_FLOAT64(speed_sketch, 0.10) AS p10,
    KLL_QUANTILES.EXTRACT_POINT_FLOAT64(speed_sketch, 0.25) AS p25,
    KLL_QUANTILES.EXTRACT_POINT_FLOAT64(speed_sketch, 0.50) AS p50,
    KLL_QUANTILES.EXTRACT_POINT_FLOAT64(speed_sketch, 0.75) AS p75,
    KLL_QUANTILES.EXTRACT_POINT_FLOAT64(speed_sketch, 0.90) AS p90,
    KLL_QUANTILES.EXTRACT_POINT_FLOAT64(speed_sketch, 0.99) AS p99
  FROM `<table_b>`
  WHERE speed_sketch IS NOT NULL
)
SELECT
  COUNT(*) AS rows_compared,
  COUNTIF(ABS(a.p10 - b.p10) > 2.0) AS p10_bad,
  COUNTIF(ABS(a.p25 - b.p25) > 2.0) AS p25_bad,
  COUNTIF(ABS(a.p50 - b.p50) > 2.0) AS p50_bad,
  COUNTIF(ABS(a.p75 - b.p75) > 2.0) AS p75_bad,
  COUNTIF(ABS(a.p90 - b.p90) > 2.0) AS p90_bad,
  COUNTIF(ABS(a.p99 - b.p99) > 2.0) AS p99_bad,
  MAX(ABS(a.p50 - b.p50)) AS p50_max_abs_diff,
  MAX(ABS(a.p90 - b.p90)) AS p90_max_abs_diff
FROM a INNER JOIN b USING (seg_id, date)
```

`INNER JOIN` is safe here **only** because a prior `table-check summary` run
on the same table pair confirmed `only in A = 0` and `only in B = 0` for the
`(seg_id, date)` key. If that's not established, keys unique to one side
would be silently dropped. With `FULL OUTER JOIN`, the `ABS(NULL - x) > 2.0`
expressions evaluate to NULL, and `COUNTIF(NULL)` is FALSE, so rows missing
on one side would silently pass the bad-count checks — hence the need to
establish key-set equality up front.

**Tolerance choice (the `2.0` knot threshold above)**: chosen empirically to
absorb the combined rank error of two independent K=200 sketches over the
typical 0-30 knot range of non-port vessel speed. The mapping from rank error
to value error depends on local CDF slope near the quantile, so this is a
rule-of-thumb, not a formal bound. Tighten or loosen based on observed
`max_abs_diff`.

**Pass condition**: all of `p10_bad..p99_bad` should be zero (or at most a
tiny fraction of total rows -- BigQuery's 99.999% guarantee allows a handful
of outliers per run).

### Strategy 2: Compare rank of fixed anchor values

Feed the same set of anchor values through `KLL_QUANTILES.RANK_FLOAT64` on
both sketches. The rank difference is directly bounded by the 1.33% error.

```sql
-- For each sketch, find the rank of values 1, 5, 10, 15, 20 knots
WITH
a AS (
  SELECT seg_id, date,
    KLL_QUANTILES.RANK_FLOAT64(speed_sketch, 1.0) AS r_1,
    KLL_QUANTILES.RANK_FLOAT64(speed_sketch, 5.0) AS r_5,
    KLL_QUANTILES.RANK_FLOAT64(speed_sketch, 10.0) AS r_10,
    KLL_QUANTILES.RANK_FLOAT64(speed_sketch, 15.0) AS r_15,
    KLL_QUANTILES.RANK_FLOAT64(speed_sketch, 20.0) AS r_20
  FROM `<table_a>` WHERE speed_sketch IS NOT NULL
),
b AS (
  SELECT seg_id, date,
    KLL_QUANTILES.RANK_FLOAT64(speed_sketch, 1.0) AS r_1,
    KLL_QUANTILES.RANK_FLOAT64(speed_sketch, 5.0) AS r_5,
    KLL_QUANTILES.RANK_FLOAT64(speed_sketch, 10.0) AS r_10,
    KLL_QUANTILES.RANK_FLOAT64(speed_sketch, 15.0) AS r_15,
    KLL_QUANTILES.RANK_FLOAT64(speed_sketch, 20.0) AS r_20
  FROM `<table_b>` WHERE speed_sketch IS NOT NULL
)
SELECT
  COUNT(*) AS rows_compared,
  -- Rank error at K=200 is ~1.33%, but we're comparing two independent
  -- sketches so the combined bound is ~2.66%. Use 0.03 as a safe threshold.
  COUNTIF(ABS(a.r_1 - b.r_1) > 0.03) AS r_1_bad,
  COUNTIF(ABS(a.r_5 - b.r_5) > 0.03) AS r_5_bad,
  COUNTIF(ABS(a.r_10 - b.r_10) > 0.03) AS r_10_bad,
  COUNTIF(ABS(a.r_15 - b.r_15) > 0.03) AS r_15_bad,
  COUNTIF(ABS(a.r_20 - b.r_20) > 0.03) AS r_20_bad,
  MAX(ABS(a.r_10 - b.r_10)) AS r_10_max_abs_diff
FROM a INNER JOIN b USING (seg_id, date)
```

Same `INNER JOIN` caveat as Strategy 1: it assumes key-set equality has
already been established by `table-check summary`.

**Pass condition**: all `*_bad` columns zero (or near-zero; BigQuery's 99.999%
guarantee allows rare outliers).

## Concrete tables to run on

The two tables to compare for this QA round:

- `world-fishing-827.scratch_christian_homberg_ttl120d._refactoring_research_segments_daily_daily_20251225_20260105`
- `world-fishing-827.scratch_christian_homberg_ttl120d._refactoring_research_segments_daily_range_20251225_20260105`

Key columns: `(seg_id, date)`.

### Prerequisite: run `table-check summary` first

Both strategies above `INNER JOIN` on `(seg_id, date)` and assume the key sets
are identical. Run:

```
GOOGLE_CLOUD_PROJECT=world-fishing-827 table-check summary \
  --table-a=<table_a> --table-b=<table_b> --keys=seg_id,date --format=table
```

and confirm the header shows `only in A: 0 | only in B: 0` (or they are
omitted entirely, which means the same). After the 2026-04-18 update that
added first-class ARRAY support, `speed_sketch` (BYTES) should be the only
column in the "EXCLUDED" warning — everything else (scalars, timestamps,
geography, `type`, `receivers_outofrange`) is compared automatically. If any
non-sketch column surfaces real differences, triage those first before
running the KLL checks below.

## Edge cases worth watching for

- **Empty sketches** (all port positions -> all NULL inputs -> sketch might be
  NULL or empty). Filter with `WHERE speed_sketch IS NOT NULL`.
- **Segment-days with n <= K (~200)**: sketch is exact in both modes, so
  quantiles should match to machine precision. If they don't match there, it's
  a real bug (not float noise).
- **Null handling**: `KLL_QUANTILES.INIT_FLOAT64` skips NULL inputs. Both modes
  apply the same `IF(distance_from_port_m < 3000, NULL, speed_knots)` mask, so
  they see identical input streams.

## Sources consulted

- https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/kll_functions
- https://cloud.google.com/bigquery/docs/reference/standard-sql/approximate_aggregate_functions
- https://cloud.google.com/blog/products/data-analytics/bigquery-supports-apache-datasketches-for-approximate-analytics
- https://datasketches.apache.org/docs/KLL/KLLSketch.html
- https://datasketches.apache.org/docs/KLL/KLLAccuracyAndSize.html
