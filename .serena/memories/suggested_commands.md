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
table-check summary --table-a=... --table-b=... --keys=id --format=table
table-check summary --table-a=... --table-b=... --keys=id --legacy
```

## Linting/Formatting
```bash
ruff check .
ruff format .
```

## BigQuery Authentication
- Primary method: Application Default Credentials (ADC)
  - Set up via: `gcloud auth application-default login`
- A service account key `sa.json` exists in the repo root but its use is commented out in `.envrc`
- The `--credentials` CLI option can override with a specific SA JSON path
- Test dataset: `world-fishing-827.tech_great_expectations`
- Default execution project: `world-fishing-827`
