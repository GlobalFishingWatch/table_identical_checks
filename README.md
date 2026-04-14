# Table Identical Checks

> :warning: **Disclaimer:** This project is entirely maintained by Claude Code with high-level human supervision. Manual guidance is provided up to the following level:
> 1. Choice of infrastructure and architecture,
> 2. Flow of the data and checkpoints,
> 3. Definition of comparability and tolerances,
> 4. Input and output interfaces.

---

Compare BigQuery tables and identify differences.

## Installation

```bash
pip install -e ".[dev]"
```

## Quick Start

```bash
# Summary with compact table output (recommended starting point)
table-check summary --table-a=project.dataset.table1 --table-b=project.dataset.table2 \
  --keys=id --format=table

# Show all differing rows
table-check diff --table-a=... --table-b=... --keys=id

# Count differing rows
table-check count --table-a=... --table-b=... --keys=id

# Breakdown by dimension (e.g., time series analysis)
table-check breakdown --table-a=... --table-b=... --keys=id --dimension=date_col
```

### Common Options

```bash
# Apply tolerance for float comparisons
table-check summary ... --tolerance=1e-9

# Per-column tolerance
table-check summary ... --tolerance=col1:1e-9,col2:1e-6

# Persist diff results to a BQ table
table-check diff ... --output-table=project.dataset.diff_results

# Show only columns with actual differences
table-check diff ... --only-diffs

# Dry run (print generated SQL only)
table-check diff ... --dry-run
```

See [docs/cli-reference.md](docs/cli-reference.md) for full option details.

## Example Output

Comparing two versions of a vessel info table (623 rows, 29 value columns):

```
$ table-check summary \
    --table-a=pipe_ais_test_202408250000_published.vessel_info \
    --table-b=pipe_ais_test_202408290000_published.vessel_info \
    --keys=vessel_id --format=table

================================================================================
=============================== table comparison ===============================
pipe_ais_test_202408250000_published.vessel_info vs pipe_ais_test_202408290000_published.vessel_info
keys: vessel_id

rows: 623 vs 623 | diffs: 2

Identical columns: callsign.count, callsign.freq, callsign.value, first_timestamp,
  imo.count, imo.freq, imo.value, last_timestamp, length.count, length.freq,
  length.value, msg_count, n_callsign.count, n_callsign.freq, n_callsign.value,
  n_imo.count, n_imo.freq, n_imo.value, n_shipname.count, n_shipname.value,
  pos_count, shipname.count, shipname.value, ssvid, width.count, width.freq,
  width.value

--------------------------------------------------------------------------------
Column                   Type           Diffs     Diff%      MaxAbs      MaxRel      AvgAbs  Status
---------------------------------------------------------------------------------------------------
n_shipname.freq           FLT               2     0.32%     1.0e-04     2.1e-04     7.1e-05     NOK
shipname.freq             FLT               2     0.32%     1.0e-04     2.1e-04     7.1e-05     NOK
================================================================================
============================== DIFFERENCES FOUND ===============================
```

Out of 29 value columns (including flattened STRUCT sub-fields), only 2 rows differ in 2 columns.

Adding `--tolerance=0.001` filters out those small float differences -- both columns
flip to `OK` and the overall result becomes `IDENTICAL`:

```
$ table-check summary \
    --table-a=pipe_ais_test_202408250000_published.vessel_info \
    --table-b=pipe_ais_test_202408290000_published.vessel_info \
    --keys=vessel_id --format=table --tolerance=0.001

================================================================================
=============================== table comparison ===============================
...vessel_info vs ...vessel_info
keys: vessel_id | tol: 0.001

rows: 623 vs 623 | diffs: 2 (filtered 2)

Identical columns: callsign.count, callsign.freq, callsign.value, ...

--------------------------------------------------------------------------------
Column                   Type           Diffs     Diff%      MaxAbs      MaxRel      AvgAbs      Exc.tol   Within tol  Status
-----------------------------------------------------------------------------------------------------------------------------
n_shipname.freq           FLT               2     0.32%     1.0e-04     2.1e-04     7.1e-05            0            2      OK
shipname.freq             FLT               2     0.32%     1.0e-04     2.1e-04     7.1e-05            0            2      OK
================================================================================
===================================IDENTICAL====================================
```

The `Exc.tol` (exceeding tolerance) and `Within tol` columns appear when tolerance is active.
All 2 diffs fall within `0.001`, so `Exc.tol = 0` and `Status = OK` for both.

## Features

- **Multi-layer pipeline**: The `summary` command runs as a single BQ multi-statement script with automatic circuit breaker
- **Duplicate key detection**: Automatically checks for non-unique keys before comparison and warns prominently
- **Per-column diff counts**: Summary output shows how many rows differ per column and the percentage
- **Tolerance filtering** with default noise suppression (see [Tolerance](#tolerance) below)
- **Automatic partition filter detection** via dry-run queries (works for views over partitioned tables)
- **STRUCT flattening**: Non-repeated STRUCT/RECORD fields are recursively flattened to dot-notation sub-fields
- **Unsupported column auto-exclusion**: ARRAY, JSON, BYTES, etc. are excluded with a warning
- **Diff persistence** (`--output-table`): Write results to a BQ table with optional TTL
- **Focused diffs** (`--only-diffs`): Restrict output to columns with actual differences
- NULL-safe comparison (NULLs treated as equal)

### Supported Column Types

| Type | Comparison | Delta Calculation | Tolerance |
|------|------------|-------------------|-----------|
| INT64 | Exact (NULL-safe) | `a - b`, `ABS(a - b)`, `SAFE_DIVIDE(a - b, b)` | No |
| FLOAT64 | Delta metrics | Same as INT64 | Yes (absolute and/or relative) |
| STRING | Exact (NULL-safe) | Match flag (boolean) | No |
| TIMESTAMP | Exact (NULL-safe) | `TIMESTAMP_DIFF(a, b, MICROSECOND)` | No |
| BOOLEAN | Cast to INT64 | Treated as integer (0/1) | No |
| GEOGRAPHY | `ST_EQUALS` (NULL-safe) | `ST_DISTANCE(a, b, TRUE)` in meters | Yes (`ST_DISTANCE(a, b) <= tol`) |
| STRUCT/RECORD | Flattened to sub-fields | Per sub-field (by type) | Per sub-field |
| ARRAY, JSON, etc. | Auto-excluded | N/A | N/A |

## Tolerance

Float comparisons use two tolerance thresholds to filter IEEE 754 floating-point
noise. Both apply by default and are combined with OR -- a value is within
tolerance if **either** condition holds:

| Threshold | Default | Formula | Purpose |
|-----------|---------|---------|---------|
| `--tolerance` (absolute) | `1e-15` | `ABS(a - b) <= tol` | Catches noise near zero |
| `--rel-tolerance` (relative) | `1e-12` | `ABS(a - b) / GREATEST(ABS(a), ABS(b)) <= rel_tol` | Catches noise at any scale |

```bash
# Use defaults (recommended for most cases)
table-check summary --table-a=... --table-b=... --keys=id

# Custom thresholds
table-check summary ... --tolerance=1e-9 --rel-tolerance=1e-6

# Disable tolerance entirely (exact comparison)
table-check summary ... --tolerance=0 --rel-tolerance=0
```

Rows are only filtered when **all** toleranced columns are within tolerance **and**
all non-toleranced columns (boolean, string, integer, timestamp) are exactly equal.

**Caveats:**
- Relative tolerance uses `SAFE_DIVIDE`, which returns NULL (not within tolerance) when
  dividing by zero. Values near zero are handled by the absolute tolerance instead.
- There is a narrow gap for small-but-not-tiny values: if one value is 0 and the other is
  between 1e-15 and ~1e-12, neither tolerance catches it. In practice this is rarely
  meaningful for BigQuery analytics data.
- GEOGRAPHY columns only support absolute tolerance (`ST_DISTANCE <= tol` in meters).
  Relative tolerance does not apply to geography comparisons.
- Per-column overrides combine with global defaults via OR:
  `--tolerance=col1:0.01 --rel-tolerance=1e-6` means `col1` is within tolerance
  if `abs_delta <= 0.01` OR `rel_delta <= 1e-6`.

## Testing

```bash
# Unit tests only (fast, no BQ connection needed, ~1s)
pytest

# BigQuery integration tests only
pytest -m bq

# All tests
pytest -m ""
```

211 tests across 9 test files: 123 unit tests and 88 BigQuery integration tests. BQ tests run against `world-fishing-827.tech_great_expectations`.

## Documentation

- [CLI Reference](docs/cli-reference.md) -- full option tables for all commands
- [Architecture](docs/architecture.md) -- pipeline design, STRUCT handling, module layout

## Limitations

- No JSON/CSV export (stdout only, unless using `--output-table` on `diff`)
- No schema validation (assumes identical schemas)
- Key columns must be specified manually
- Pipeline mode only available for `summary`; other commands use the SQLAlchemy query path
- REPEATED STRUCT/RECORD fields are not supported (auto-excluded)
