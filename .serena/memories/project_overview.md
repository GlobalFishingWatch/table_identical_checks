# Table Identical Checks

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
- `table-check diff` - Show differing rows
- `table-check count` - Count differences
- `table-check summary` - Comprehensive comparison summary with delta stats
- `table-check breakdown` - Summary broken down by a dimension (e.g., date)

## Column Type Handling
| Type | Comparison |
|------|------------|
| INT64 | Exact match |
| FLOAT64 | Delta metrics (tolerance applied at higher layer) |
| STRING | Exact match |

## NULL Handling
NULLs are treated as equal (using `IS NOT DISTINCT FROM` in BQ).

## Tech Stack
- Python 3.x
- google-cloud-bigquery
- click (CLI)
- pytest (testing)
