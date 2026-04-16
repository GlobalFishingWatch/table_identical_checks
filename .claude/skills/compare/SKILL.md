---
name: compare
description: Compare two BigQuery tables and show a summary of differences. Use when someone wants to compare tables, check for diffs, or validate data between two BQ tables.
argument-hint: <table_a> <table_b> --keys=<key_columns> [options]
allowed-tools: Bash(*/table-check *) Bash(*/python *)
---

# Compare BigQuery Tables

Run `table-check summary` to compare two BigQuery tables.

## Instructions

1. Parse the arguments from `$ARGUMENTS`. Expected format:
   - Two table references (project.dataset.table)
   - `--keys=<comma-separated key columns>`
   - Optional: `--tolerance=<value>`, `--rel-tolerance=<value>`, `--max-diff-pct=<value>`, `--format=<verbose|table>`

2. If the user provides bare arguments without flags, interpret them as:
   - First positional arg: table A
   - Second positional arg: table B
   - Third positional arg (or `--keys`): key column(s)

3. Default to `--format=table` for compact output unless the user requests verbose.

4. Set `GOOGLE_CLOUD_PROJECT=world-fishing-827` as the execution project.

5. Run the command:
```
GOOGLE_CLOUD_PROJECT=world-fishing-827 venv/bin/table-check summary \
  --table-a=<table_a> --table-b=<table_b> --keys=<keys> \
  --format=table [other options]
```

6. After showing the output, provide a brief interpretation highlighting:
   - Whether the tables are identical (post-tolerance)
   - Key match statistics (matched, only-in-A, only-in-B)
   - Which columns have real differences vs float noise
   - Any warnings (duplicate keys, excluded columns)

## Examples

```
/compare project.ds.table1 project.ds.table2 --keys=id
/compare project.ds.table1 project.ds.table2 --keys=id,date --max-diff-pct=100
/compare project.ds.table1 project.ds.table2 --keys=id --tolerance=0 --rel-tolerance=0
```
