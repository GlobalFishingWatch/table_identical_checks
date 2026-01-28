# Table Identical Checks

## Version
v0.1.0 - Initial MVP release

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

## CLI Commands
- `table-check diff` - Show differing rows (with --dry-run option)
- `table-check count` - Count differences
- `table-check summary` - Comprehensive comparison summary with delta stats per column
- `table-check breakdown` - Summary broken down by a dimension (e.g., date) with optional delta column tracking

## Summary Features
- `ComparisonSummary`: Overall statistics (rows only in A/B, rows with differences, identical rows, per-column min/max/avg deltas)
- `DimensionSummary`: Breakdown by dimension value with DimensionBucket objects tracking differences and deltas per bucket

## Column Type Handling
| Type | Comparison |
|------|------------|
| INT64 | Exact match |
| FLOAT64 | Delta metrics (tolerance applied at higher layer) |
| STRING | Exact match |

## NULL Handling
NULLs are treated as equal (using `IS NOT DISTINCT FROM` in BQ).

## Tech Stack
- Python 3.10+
- google-cloud-bigquery>=3.0.0
- **sqlalchemy>=2.0.0** - SQL query construction
- **sqlalchemy-bigquery>=1.5.0** - BigQuery dialect
- click>=8.0.0 (CLI)
- pytest>=7.0.0 (testing)
- pytest-cov>=4.0.0 (coverage)
- ruff>=0.1.0 (linting/formatting)

## Testing & Coverage
- 18 tests covering INT64, FLOAT64, STRING columns
- Tests run against real BigQuery (no mocking)
- Current coverage: 40% overall (backend/query_builder.py: 99%, backend/schema.py: 96%)
- CLI and summary functions not covered by automated tests

## Environment Setup
- Uses direnv for GOOGLE_APPLICATION_CREDENTIALS
- Service account: automated-testing@world-fishing-827.iam.gserviceaccount.com
- Test dataset: world-fishing-827.tech_great_expectations
