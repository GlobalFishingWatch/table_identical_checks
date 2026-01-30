# Style and Conventions

## Python Style
- Use type hints for function signatures
- Use `ruff` for linting and formatting (line-length 100, target py310)
- Follow PEP 8 naming conventions
- Functional programming preferred where appropriate

## Project Structure
```
src/table_identical_checks/
  __init__.py
  backend/
    __init__.py
    query_builder.py   # SQL generation (SQLAlchemy Core for legacy, raw SQL for pipeline)
    pipeline.py        # PipelineConfig, PipelineResult, run_pipeline()
    summary.py         # ComparisonSummary, formatters, generate_summary()
    schema.py          # Column type detection, partition field detection
    tolerance.py       # ToleranceConfig parsing (global/per-column)
  cli.py               # Click CLI (diff, count, summary, breakdown)
tests/
  conftest.py          # BQ client fixture, table_factory, cleanup
  test_numeric.py
  test_string.py
  test_tolerance.py
  test_table_formatter.py
  test_geography.py
  test_unsupported.py
  test_pipeline.py
pyproject.toml
```

## Testing
- Use pytest
- Tests run against real BigQuery (not mocked)
- Authentication: Application Default Credentials (ADC) via `gcloud auth application-default login`
- Test dataset: `world-fishing-827.tech_great_expectations`
- Service account `sa.json` exists but is NOT used by default (`.envrc` export is commented out)

## SQL Style (BigQuery)
- NULL-safe equality uses manual pattern (BQ does NOT support `IS NOT DISTINCT FROM`):
  `(a IS NOT NULL AND b IS NOT NULL AND a = b) OR (a IS NULL AND b IS NULL)`
- Use `SAFE_DIVIDE` for division to avoid divide-by-zero errors
- Column naming in diff output: `a__col`, `b__col`, `col__delta`, `col__abs_delta`, `col__rel_delta`
- Pipeline script uses raw SQL strings (not SQLAlchemy) because BQ scripting
  (DECLARE, IF/ELSE, CREATE TEMP TABLE) is not supported by SQLAlchemy
- In GROUP BY, always use column names, never positional indices
