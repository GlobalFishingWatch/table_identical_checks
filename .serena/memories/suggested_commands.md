# Suggested Commands

## Virtual Environment
```bash
source venv/bin/activate
```

## Install Dependencies
```bash
pip install -e ".[dev]"
```

## Run Tests
```bash
pytest tests/
```

## Run CLI
```bash
table-check --help
```

## Linting/Formatting (to be configured)
```bash
ruff check .
ruff format .
```

## BigQuery Authentication
The project uses a service account key at `sa.json`.
Credentials are automatically set via direnv (`.envrc`).

**Test dataset**: `world-fishing-827.tech_great_expectations`
- SA has read access to most datasets
- SA has write access to `tech_great_expectations` for test tables
