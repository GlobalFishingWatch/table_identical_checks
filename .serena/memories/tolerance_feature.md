# Tolerance Feature Implementation

## Overview
Implemented tolerance-based filtering for FLOAT64 column comparisons to handle floating-point precision artifacts in BigQuery.

## Default Configuration
- Default tolerance: 1e-9 (recommended for BigQuery FLOAT64 precision)
- Applied to absolute delta: `abs(a - b) <= tolerance`

## Usage

### Global Tolerance
```bash
table-check summary --table-a=... --table-b=... --keys=id --tolerance=1e-9
```

### Per-Column Tolerance
```bash
table-check diff --table-a=... --table-b=... --keys=id --tolerance=col1:1e-9,col2:1e-6
```

## Behavior

### Filtering Logic
Rows are excluded from diff results when:
- ALL non-float columns are equal (NULL-safe)
- ALL float columns WITHOUT tolerance are equal (NULL-safe)
- ALL float columns WITH tolerance have `abs_delta <= tolerance`

### Summary Statistics - ENHANCED

The summary output now clearly distinguishes between pre and post-tolerance statistics:

#### Tolerance Configuration Section
Shows which columns have tolerance configured and their values.

#### Row Counts
**When tolerance is configured**, shows TWO sets of counts:

1. **ALL DIFFERENCES (including within tolerance)**:
   - Pre-tolerance filtering counts
   - "Rows identical (exact)" = exactly identical rows

2. **SIGNIFICANT DIFFERENCES (excluding within tolerance)**:
   - Post-tolerance filtering counts
   - "Rows identical (within tolerance)" = identical after applying tolerance

3. **ROWS FILTERED BY TOLERANCE**:
   - Shows how many rows were excluded due to tolerance

**When no tolerance**, shows standard single set of counts.

#### Numeric Column Deltas
- Includes clear note explaining stats are from post-tolerance filtered rows
- This clarifies why `max_abs_delta` might show values within tolerance (row had other columns that exceeded tolerance)
- `within_tolerance_count` labeled as "(from all comparisons)" to clarify it's from pre-tolerance data

## Real-World Results

On the 8M row test tables:
- **Pre-tolerance**: 61,953 total differences
- **Post-tolerance**: 57,799 total differences
- **Filtered by tolerance**: 4,154 rows
- Most geographic columns (lat/lon/speed/course): 15,137 rows within tolerance
- Satellite distance calculations: 8,737 rows with sub-nanosecond differences

## Key Learnings

### Understanding Delta Statistics with Tolerance

**Important**: `max_abs_delta` can show values within tolerance even when tolerance is configured!

**Why?** A row is only excluded if ALL float columns are within tolerance. Example:
- Row has `distance_from_sat_km` delta of 3.05e-10 (within 1e-9)
- BUT same row has `nnet_score` delta of 1.0 (exceeds tolerance)
- Row is still included in diff because not ALL floats are within tolerance
- So `distance_from_sat_km`'s max delta appears in stats even though it's < 1e-9

This is why the summary now shows both pre and post-tolerance counts AND notes that delta stats are from post-tolerance filtered rows.

### Floating Point Precision in BigQuery
During testing discovered that literal values like `1.000000001` are actually represented as `1.000000082740371e-09` in BigQuery FLOAT64, which exceeds 1e-9 tolerance. Always test with actual tolerance thresholds when defining test cases.

### NULL Handling
Tolerance comparison only applies when both values are NOT NULL:
```sql
(col_a IS NULL AND col_b IS NULL) OR 
(col_a IS NOT NULL AND col_b IS NOT NULL AND ABS(col_a - col_b) <= tolerance)
```

This prevents SQL errors from `ABS(NULL - value)`.

## Outside Tolerance Count (Enhancement)
Added complementary statistic showing how many rows have deltas exceeding tolerance:
- `within_tolerance_count`: rows where `abs_delta <= tolerance`
- `outside_tolerance_count`: rows where `abs_delta > tolerance`

Both are shown per float column when tolerance is configured.

## Significance Sorting (Enhancement)
Column statistics can now be sorted by "significance" of differences:
- **Alphabetical** (default): `--sort-columns=alphabetical`
- **Significance**: `--sort-columns=significance`

Significance metric: `SUM(ABS(rel_delta))` - higher values indicate more significant differences.

Example output with significance sorting shows most different columns first:
```
nnet_score: max_abs_delta=1.0, sum_abs_rel_delta=583.0
  - outside_tolerance: 731
  - within_tolerance: 100
```

## Test Coverage
- 19 tolerance-specific tests in `test_tolerance.py`
- 141 tests total across all test files
- Tolerance is also exercised by pipeline tests (`test_pipeline.py`)

