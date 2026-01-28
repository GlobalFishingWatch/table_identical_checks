# Style and Conventions

## Python Style
- Use type hints for function signatures
- Use `ruff` for linting and formatting
- Follow PEP 8 naming conventions

## Project Structure
```
src/table_identical_checks/
  __init__.py
  backend/
    __init__.py
    query_builder.py   # Generates diff SQL
    schema.py          # Column type detection
  cli.py
tests/
  conftest.py
  test_*.py
pyproject.toml
```

## Testing
- Use pytest
- Tests run against real BigQuery (not mocked)
- Service account key: `sa.json`

## SQL Style (BigQuery)
- Use `IS NOT DISTINCT FROM` for NULL-safe equality
- Use `SAFE_DIVIDE` for division to avoid divide-by-zero errors
- Column naming in diff output: `a__col`, `b__col`, `col__delta`, `col__abs_delta`, `col__rel_delta`
