# Security policy

## Scope

`table-identical-checks` is a CLI that generates and runs SQL against
BigQuery using the caller's own credentials. It does not host a service,
accept untrusted input from a network, or store user data.

The realistic security concerns are:

- **SQL injection** via table / column names passed on the CLI. Identifiers
  are interpolated into generated SQL; we rely on BigQuery rejecting
  malformed identifiers but do not sanitise hostile input.
- **Credential leakage** via verbose mode or logs. The CLI prints the BQ
  client's project and credential type but never the credential material.
- **BQ cost surprises** — runaway queries on large tables. The
  `--max-diff-pct` circuit breaker, `summary`'s pre-flight row count, and
  the `--write-mode=error` default mitigate this.

## Reporting a vulnerability

Email `christian.homberg@globalfishingwatch.org` with a description and a
minimal repro. We aim to acknowledge within 5 working days. Please **do
not** open a public GitHub issue for security reports.

For low-severity / non-exploitable issues (e.g. cosmetic logging of
identifiers), feel free to file a regular GitHub issue.

## Supported versions

Only `master` is supported. There is no LTS branch.
