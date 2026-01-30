# Table Identical Checks

## Version
v0.1.0 - Initial MVP release (with tolerance feature)

## Purpose
Python library for conducting table identity/comparison checks between BigQuery tables.

## Architecture
- **Backend**: SQL query generation for BigQuery (generates diff queries)
- **API**: Future - REST API layer
- **Frontend**: Future - UI layer
- **CLI**: Basic command-line interface

## Core Functionality
The backend generates a "diff table" comparing two BQ tables by:
1. Joining tables on composite key columns
2. For each non-key column, computing:
   - Simple delta (a - b)
   - Absolute delta (|a - b|)
   - Relative delta ((a - b) / b)
3. Flagging rows that exist only in one table
4. **Tolerance-based filtering** for float columns (optional)

## CLI Commands
All commands support `--tolerance` for float comparison filtering:
- `table-check diff` - Show differing rows (with --dry-run option)
- `table-check count` - Count differences
- `table-check summary` - Comprehensive comparison summary with delta stats per column
- `table-check breakdown` - Summary broken down by a dimension (e.g., date) with optional delta column tracking

## Summary Features
- `ComparisonSummary`: Overall statistics (rows only in A/B, rows with differences, identical rows, per-column min/max/avg deltas)
- `DimensionSummary`: Breakdown by dimension value with DimensionBucket objects tracking differences and deltas per bucket
- **Tolerance statistics**: `within_tolerance_count` and `outside_tolerance_count` for float columns when tolerance is configured
- **Dual row counts**: Shows both pre-tolerance (all differences) and post-tolerance (significant differences) when tolerance active
- **Sortable columns**: `--sort-columns=significance` sorts by `SUM(ABS(rel_delta))`

## Output Formats (`--format` option on `summary` command)
- **`verbose`** (default): Multi-line detailed output via `VerboseFormatter` (original behavior)
- **`table`**: Compact tabular format via `TableFormatter` -- one row per column, one column per statistic
  - Shows header with table names, keys, tolerance, row counts
  - Lists identical columns separately (alphabetically)
  - Delta table columns: Column name, Type (FLT/INT/BOOL/TS/STR/GEO), MaxAbs, MaxRel, AvgAbs, [Exc.tol, Within tol], Status (OK/NOK)
  - String columns show mismatch count inline
  - OK = all within tolerance or zero differences; NOK = some exceed tolerance or any differences
  - Formatter protocol: `SummaryFormatter` with `format(summary) -> str` method
  - Formatter registry: `get_formatter(name)` dispatches to named formatters
  - `ComparisonSummary.__str__()` delegates to the formatter selected by `output_format` field

## Column Type Handling
| Type | Comparison | Delta Calculation | Tolerance Support |
|------|------------|-------------------|-------------------|
| INT64 | Exact match | (a - b), abs, rel_delta | No |
| FLOAT64 | Delta metrics | (a - b), abs, rel_delta | **Yes** |
| STRING | Exact match | Match flag (boolean) | No |
| TIMESTAMP | TIMESTAMP_DIFF | Seconds difference (INT64) | No |
| BOOLEAN | Cast to INT64 | Treated as integer (0/1) | No |
| GEOGRAPHY | ST_EQUALS | ST_DISTANCE (meters, WGS84) | **Yes** (meters) |
| UNSUPPORTED | Auto-excluded | N/A | N/A |

## Unsupported Column Auto-Exclusion
Tables with unsupported types (ARRAY, STRUCT, RECORD, JSON, BYTES, RANGE) are handled gracefully:
- Unsupported columns are automatically excluded from query generation
- Excluded columns are tracked via `QueryBuilder.excluded_columns`
- CLI prints a prominent yellow warning when unsupported columns are detected
- Summary output shows an "EXCLUDED COLUMNS" section near the top

## GEOGRAPHY Support
- **Equality**: `ST_EQUALS(a.col, b.col)` with NULL-safe wrapper
- **Distance**: `ST_DISTANCE(a.col, b.col, TRUE)` for WGS84 spheroid distance in meters
- **Tolerance**: Uses `ST_DISTANCE(a.col, b.col)` (spherical, not spheroid) for tolerance comparisons
  - BigQuery rewrites `ST_DISTANCE(..., TRUE) <= tolerance` to `ST_DWITHIN(..., TRUE)` which is unsupported
  - Spherical approximation is adequate for tolerance checks
- **Summary stats**: `max_distance_meters`, `avg_distance_meters`, plus tolerance counts
- **NULL handling**: Only computes distance when both values are NOT NULL

## Tolerance Feature
- **Default**: 1e-9 (recommended for BigQuery FLOAT64 precision)
- **Global**: `--tolerance=1e-9` applies to all float columns
- **Per-column**: `--tolerance=col1:1e-9,col2:1e-6` for different tolerances per column
- **Filtering**: Excludes rows where ALL float deltas are within tolerance AND all other columns are equal
- **Statistics**: Shows `within_tolerance_count` and `outside_tolerance_count` per float column
- **Pre/Post Tolerance Counts**: Summary shows both "all differences" and "significant differences"
- **Significance Sorting**: `--sort-columns=significance` orders columns by SUM(ABS(rel_delta))

## NULL Handling
NULLs are treated as equal (NULL-safe comparison).

## Tech Stack
- Python 3.10+
- google-cloud-bigquery>=3.0.0
- **sqlalchemy>=2.0.0** - SQL query construction
- **sqlalchemy-bigquery>=1.5.0** - BigQuery dialect
- click>=8.0.0 (CLI)
- pytest>=7.0.0 (testing)
- pytest-cov>=4.0.0 (coverage)
- ruff>=0.1.0 (linting/formatting)

## Multi-Layer Pipeline Architecture
The summary command uses a 3-layer pipeline that executes as a single BQ multi-statement script:
- **Layer 1**: FULL OUTER JOIN to identify non-identical rows with per-column equality flags (`CREATE TEMP TABLE _l1`)
- **Circuit Breaker**: If diff % exceeds threshold (`--max-diff-pct`), aborts and returns Layer 1 counts only
- **Layer 2**: INNER JOIN only non-identical rows back to source tables to compute deltas (`CREATE TEMP TABLE _l2`)
- **Layer 3**: Aggregates all statistics into a single output row via CROSS JOIN

Key files:
- `backend/pipeline.py`: `PipelineConfig`, `PipelineResult`, `run_pipeline()` orchestrator
- `backend/query_builder.py`: `build_pipeline_script()` generates the multi-statement SQL
- `backend/summary.py`: `generate_summary()` dispatches to `_generate_summary_pipeline()` or `_generate_summary_legacy()`
- Pipeline is default; `--legacy` flag falls back to multi-query path

## Testing & Coverage
- 141 tests total (18 numeric + 11 string + 19 tolerance + 54 table-formatter + 13 geography + 19 unsupported + 18 pipeline)
- Tests run against real BigQuery (no mocking)
- Current coverage: 74% overall
  - backend/pipeline.py: 94%
  - backend/query_builder.py: 92%
  - backend/tolerance.py: 88%
  - backend/schema.py: 72%
  - backend/summary.py: 79%
- CLI not covered by automated tests

## Environment Setup
- Authentication: Application Default Credentials (ADC) via `gcloud auth application-default login`
- A service account key `sa.json` exists but is NOT used by default (`.envrc` export is commented out)
- The SA fallback was removed from `conftest.py` -- tests use ADC only
- Test dataset: `world-fishing-827.tech_great_expectations`
- Default execution project: `world-fishing-827`
