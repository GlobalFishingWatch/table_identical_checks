"""Summary statistics for table comparisons."""

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from google.cloud import bigquery
from sqlalchemy import func, select
from sqlalchemy_bigquery import BigQueryDialect

from .pipeline import PipelineConfig, run_pipeline
from .query_builder import QueryBuilder
from .schema import ColumnType

# ---------------------------------------------------------------------------
# Duplicate key info
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DuplicateInfo:
    """Result of a duplicate-key check for one table."""

    duplicate_key_count: int  # number of distinct keys that appear more than once
    duplicate_row_count: int  # total rows involved in those duplicates
    max_duplicate_count: int  # highest repeat count for a single key

    @property
    def has_duplicates(self) -> bool:
        return self.duplicate_key_count > 0


# ---------------------------------------------------------------------------
# Formatter protocol
# ---------------------------------------------------------------------------


class SummaryFormatter(Protocol):
    """Protocol for formatting a ComparisonSummary as a string."""

    def format(self, summary: "ComparisonSummary") -> str: ...


# ---------------------------------------------------------------------------
# Number formatting helpers
# ---------------------------------------------------------------------------

_TYPE_ABBREV: dict[ColumnType, str] = {
    ColumnType.FLOAT: "FLT",
    ColumnType.INTEGER: "INT",
    ColumnType.BOOLEAN: "BOOL",
    ColumnType.TIMESTAMP: "TS",
    ColumnType.DATE: "DATE",
    ColumnType.STRING: "STR",
    ColumnType.GEOGRAPHY: "GEO",
}


def _fmt_number(value: float | int | None) -> str:
    """Format a numeric value for the table display.

    - None -> "-"
    - Scientific notation for values < 0.001 or >= 10_000
    - Otherwise, plain decimal (up to 2dp for ints, variable for floats)
    """
    if value is None:
        return "-"
    if isinstance(value, int):
        return f"{value:,}"
    abs_val = abs(value)
    if abs_val == 0.0:
        return "0.0e+00"
    if abs_val < 0.001 or abs_val >= 10_000:
        return f"{value:.1e}"
    return f"{value:.2f}"


def _fmt_count(value: int | None) -> str:
    """Format an integer count, or '-' for None."""
    if value is None:
        return "-"
    return f"{value:,}"


def _truncate(name: str, max_len: int) -> str:
    """Truncate a column name if it exceeds *max_len*, adding an ellipsis."""
    if len(name) <= max_len:
        return name
    return name[: max_len - 1] + "\u2026"


# ---------------------------------------------------------------------------
# Shared duplicate-key warning block
# ---------------------------------------------------------------------------


def _format_duplicate_warning(summary: "ComparisonSummary") -> list[str]:
    """Return lines for a prominent duplicate-key warning, or empty list if clean."""
    lines: list[str] = []
    dup_a = summary.duplicate_info_a
    dup_b = summary.duplicate_info_b

    if dup_a is None and dup_b is None:
        return lines

    has_any = (dup_a and dup_a.has_duplicates) or (dup_b and dup_b.has_duplicates)
    if not has_any:
        return lines

    banner = "!" * 60
    lines.append("")
    lines.append(banner)
    lines.append("!! DUPLICATE KEYS DETECTED !!")
    lines.append(banner)
    lines.append("  Comparison results may be unreliable when keys are not unique.")
    lines.append("")

    for label, info in [("Table A", dup_a), ("Table B", dup_b)]:
        if info and info.has_duplicates:
            lines.append(
                f"  {label}: {info.duplicate_key_count:,} duplicate key(s), "
                f"{info.duplicate_row_count:,} rows affected, "
                f"max {info.max_duplicate_count}x per key"
            )

    lines.append(banner)
    lines.append("")
    return lines


def _format_aborted_warning(summary: "ComparisonSummary") -> list[str]:
    """Return lines for a prominent circuit-breaker abort warning, or empty list."""
    if not summary.pipeline_aborted:
        return []

    banner = "!" * 60
    return [
        "",
        banner,
        "!! PIPELINE ABORTED (circuit breaker) !!",
        banner,
        "  More than --max-diff-pct of rows differ; Layers 2 and 3 were skipped.",
        "  Per-column delta stats and tolerance filtering are NOT available.",
        "  Rerun with a higher --max-diff-pct (e.g. 100) for full stats.",
        banner,
        "",
    ]


# ---------------------------------------------------------------------------
# Verbose formatter (the original __str__ output)
# ---------------------------------------------------------------------------


class VerboseFormatter:
    """Produces the original multi-line verbose summary output."""

    def format(self, summary: "ComparisonSummary") -> str:
        lines = [
            "=" * 60,
            "TABLE COMPARISON SUMMARY",
            "=" * 60,
            f"Table A: {summary.table_a}",
            f"Table B: {summary.table_b}",
            f"Key columns: {', '.join(summary.key_columns)}",
        ]

        # Show duplicate key warning prominently
        lines.extend(_format_duplicate_warning(summary))

        # Show circuit-breaker abort warning prominently
        lines.extend(_format_aborted_warning(summary))

        # Show excluded columns prominently near the top
        if summary.excluded_columns:
            lines.extend(
                [
                    "",
                    "!" * 60,
                    "!! EXCLUDED COLUMNS (unsupported types) !!",
                    "!" * 60,
                    f"  {'Column':<30} {'Type':<20}",
                    "  " + "-" * 50,
                ]
            )
            for col_name, bq_type in sorted(summary.excluded_columns):
                lines.append(f"  {col_name:<30} {bq_type:<20}")
            lines.append("!" * 60)

        # Show tolerance configuration if present
        if summary.has_tolerance:
            lines.extend(
                [
                    "",
                    "TOLERANCE CONFIGURATION",
                    "-" * 40,
                ]
            )
            for col, tol in sorted(summary.tolerance_config.items()):
                lines.append(f"  {col}: {tol}")

        lines.extend(
            [
                "",
                "ROW COUNTS",
                "-" * 40,
                f"Total rows in A: {summary.total_rows_a:,}",
                f"Total rows in B: {summary.total_rows_b:,}",
            ]
        )

        # Show pre-tolerance counts if available
        if summary.has_tolerance and summary.total_differences_pretolerance is not None:
            lines.extend(
                [
                    "",
                    "ALL DIFFERENCES (including within tolerance):",
                    f"  Rows only in A: {summary.rows_only_in_a_pretolerance:,}",
                    f"  Rows only in B: {summary.rows_only_in_b_pretolerance:,}",
                    f"  Rows in both with differences: "
                    f"{summary.rows_in_both_with_differences_pretolerance:,}",
                    f"  Rows identical (exact): {summary.rows_identical_pretolerance:,}",
                    f"  TOTAL DIFFERENCES: {summary.total_differences_pretolerance:,}",
                    "",
                    "SIGNIFICANT DIFFERENCES (excluding within tolerance):",
                    f"  Rows only in A: {summary.rows_only_in_a:,}",
                    f"  Rows only in B: {summary.rows_only_in_b:,}",
                    f"  Rows in both with differences: {summary.rows_in_both_with_differences:,}",
                    f"  Rows identical (within tolerance): {summary.rows_identical:,}",
                    f"  TOTAL DIFFERENCES: {summary.total_differences:,}",
                    "",
                    f"ROWS FILTERED BY TOLERANCE: "
                    f"{summary.total_differences_pretolerance - summary.total_differences:,}",
                ]
            )
        else:
            # No tolerance - show standard counts
            lines.extend(
                [
                    "",
                    f"Rows only in A: {summary.rows_only_in_a:,}",
                    f"Rows only in B: {summary.rows_only_in_b:,}",
                    f"Rows in both with differences: {summary.rows_in_both_with_differences:,}",
                    f"Rows identical: {summary.rows_identical:,}",
                    "",
                    f"TOTAL DIFFERENCES: {summary.total_differences:,}",
                ]
            )

        if summary.numeric_column_stats:
            lines.extend(
                [
                    "",
                    "NUMERIC COLUMN DELTAS",
                    "-" * 40,
                ]
            )
            if summary.has_tolerance:
                lines.append(
                    "Note: Stats below are from post-tolerance filtered rows. Rows where ALL float"
                )
                lines.append(
                    "      columns are within tolerance have been excluded from these calculations."
                )
                lines.append("")

            # Sort columns based on sort order
            if summary.column_sort_order == "significance":
                sorted_cols = sorted(
                    summary.numeric_column_stats.items(),
                    key=lambda x: x[1].get("sum_abs_rel_delta", 0),
                    reverse=True,
                )
                lines.append(
                    "Sorted by significance (SUM(ABS(rel_delta)), most significant first):"
                )
                lines.append("")
            else:
                sorted_cols = sorted(summary.numeric_column_stats.items())

            for col, stats in sorted_cols:
                lines.append(f"  {col}:")
                lines.append(f"    max_abs_delta: {stats['max_abs_delta']}")
                lines.append(f"    max_rel_delta: {stats['max_rel_delta']}")
                lines.append(f"    avg_abs_delta: {stats['avg_abs_delta']}")
                if "sum_abs_rel_delta" in stats:
                    lines.append(f"    sum_abs_rel_delta: {stats['sum_abs_rel_delta']}")
                if "within_tolerance_count" in stats:
                    lines.append(
                        f"    within_tolerance: {stats['within_tolerance_count']} "
                        "(from all comparisons)"
                    )
                if "outside_tolerance_count" in stats:
                    lines.append(
                        f"    outside_tolerance: {stats['outside_tolerance_count']} "
                        "(from all comparisons)"
                    )

        if summary.geography_column_stats:
            lines.extend(
                [
                    "",
                    "GEOGRAPHY COLUMN DISTANCES",
                    "-" * 40,
                ]
            )
            for col, stats in sorted(summary.geography_column_stats.items()):
                lines.append(f"  {col}:")
                lines.append(f"    max_distance_meters: {stats['max_distance_meters']}")
                lines.append(f"    avg_distance_meters: {stats['avg_distance_meters']}")
                if "within_tolerance_count" in stats:
                    lines.append(
                        f"    within_tolerance: {stats['within_tolerance_count']} "
                        "(from all comparisons)"
                    )
                if "outside_tolerance_count" in stats:
                    lines.append(
                        f"    outside_tolerance: {stats['outside_tolerance_count']} "
                        "(from all comparisons)"
                    )

        if summary.string_column_mismatches:
            lines.extend(
                [
                    "",
                    "STRING COLUMN MISMATCHES",
                    "-" * 40,
                ]
            )
            for col, count in summary.string_column_mismatches.items():
                lines.append(f"  {col}: {count:,} mismatches")

        lines.extend(
            [
                "",
                "=" * 60,
                f"RESULT: {'IDENTICAL' if summary.tables_identical else 'DIFFERENCES FOUND'}",
                "=" * 60,
            ]
        )

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Table (compact tabular) formatter
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ColumnRow:
    """One row of the tabular column-deltas section."""

    name: str
    col_type: ColumnType
    diff_count: int | None = None  # per-column diff count (from pipeline Layer 1)
    total_rows: int | None = None  # total comparable rows (for computing diff%)
    max_abs: float | None = None
    max_rel: float | None = None
    avg_abs: float | None = None
    mismatch_count: int | None = None  # string columns only
    outside_tolerance: int | None = None
    within_tolerance: int | None = None
    has_tolerance: bool = False
    aborted: bool = False  # True when row was constructed from Layer 1 only (pipeline abort)

    @property
    def status(self) -> str:
        """Derive OK / NOK / ABORT.

        - Aborted rows (Layer 1 only): "ABORT" since we only know a diff exists.
        - For columns with tolerance: NOK if outside_tolerance > 0.
        - For string columns without tolerance: NOK if mismatch_count > 0.
        - For numeric columns without tolerance: NOK if any delta exists
          (the column would not appear in the table if it were identical,
          so reaching here means there are differences).
        """
        if self.aborted:
            return "ABORT"
        if self.has_tolerance:
            return "NOK" if (self.outside_tolerance or 0) > 0 else "OK"
        # Without tolerance: presence in the table means diffs exist.
        if self.col_type == ColumnType.STRING:
            return "NOK" if (self.mismatch_count or 0) > 0 else "OK"
        # Numeric column without tolerance -- if it has *any* nonzero delta,
        # it would not have been pruned as identical, so it must be NOK.
        return "NOK"


class TableFormatter:
    """Compact tabular formatter -- one row per column, one column per statistic."""

    WIDTH = 80  # total width of the rule lines
    COL_NAME_MAX = 22  # max chars for the column-name field

    def format(self, summary: "ComparisonSummary") -> str:
        lines: list[str] = []
        w = self.WIDTH

        # -- header -----------------------------------------------------------
        lines.append("=" * w)
        title = " table comparison "
        lines.append(f"{title:=^{w}}")

        lines.append(f"{summary.table_a} vs {summary.table_b}")

        # keys & tolerance one-liner
        keys_str = f"keys: {', '.join(summary.key_columns)}"
        if summary.has_tolerance:
            tol_parts = []
            if summary.tolerance_config:
                unique_abs = set(summary.tolerance_config.values())
                tol_parts.append(
                    f"tol: {next(iter(unique_abs))}"
                    if len(unique_abs) == 1
                    else "tol: per-column"
                )
            if summary.rel_tolerance_config:
                unique_rel = set(summary.rel_tolerance_config.values())
                tol_parts.append(
                    f"rel_tol: {next(iter(unique_rel))}"
                    if len(unique_rel) == 1
                    else "rel_tol: per-column"
                )
            lines.append(f"{keys_str} | {' | '.join(tol_parts)}")
        else:
            lines.append(keys_str)

        lines.append("")

        # -- duplicate key warning (very prominent) --------------------------
        lines.extend(_format_duplicate_warning(summary))

        # -- circuit-breaker abort warning (very prominent) ------------------
        lines.extend(_format_aborted_warning(summary))

        # -- excluded columns (prominent) ------------------------------------
        if summary.excluded_columns:
            lines.append(
                "!! EXCLUDED: "
                + ", ".join(
                    f"{name} ({bq_type})" for name, bq_type in sorted(summary.excluded_columns)
                )
            )
            lines.append("")

        # -- row counts -------------------------------------------------------
        lines.append(f"rows: {summary.total_rows_a:,} vs {summary.total_rows_b:,}")

        # Key match breakdown
        matched = summary.rows_identical + summary.rows_in_both_with_differences
        parts = [f"matched: {matched:,}"]
        if summary.rows_only_in_a > 0:
            parts.append(f"only in A: {summary.rows_only_in_a:,}")
        if summary.rows_only_in_b > 0:
            parts.append(f"only in B: {summary.rows_only_in_b:,}")

        if summary.has_tolerance and summary.rows_in_both_with_differences_pretolerance is not None:
            filtered = summary.rows_in_both_with_differences_pretolerance - summary.rows_in_both_with_differences
            parts.append(
                f"value diffs: {summary.rows_in_both_with_differences_pretolerance:,} "
                f"(filtered {filtered:,})"
            )
        elif summary.pipeline_aborted:
            parts.append(
                f"value diffs: {summary.rows_in_both_with_differences:,} "
                f"(pre-tolerance; aborted)"
            )
        else:
            parts.append(f"value diffs: {summary.rows_in_both_with_differences:,}")

        lines.append(" | ".join(parts))
        lines.append("")

        # -- identical columns ------------------------------------------------
        identical_cols = self._identical_columns(summary)
        if identical_cols:
            lines.append(f"Identical columns: {', '.join(sorted(identical_cols))}")
            lines.append("")

        # -- column deltas table ----------------------------------------------
        col_rows = self._build_column_rows(summary)

        if col_rows:
            lines.append("-" * w)
            show_tol = summary.has_tolerance
            header, separator, data_lines = self._render_table(col_rows, show_tol=show_tol)
            lines.append(header)
            lines.append(separator)
            lines.extend(data_lines)

        # -- footer -----------------------------------------------------------
        lines.append("=" * w)
        if summary.tables_identical:
            lines.append(f"{'IDENTICAL':=^{w}}")
        else:
            lines.append(f"{' DIFFERENCES FOUND ':=^{w}}")

        return "\n".join(lines)

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _identical_columns(summary: "ComparisonSummary") -> list[str]:
        """Return column names that are completely identical (zero differences)."""
        all_value_cols = set(summary.column_types.keys())
        if summary.pipeline_aborted and summary.column_diff_counts:
            # Layer 2/3 skipped, so the per-type stat dicts are empty.
            # Fall back to Layer 1's column_diff_counts for the "identical" set.
            cols_with_diffs = {
                c for c, n in summary.column_diff_counts.items() if n > 0
            }
        else:
            cols_with_diffs = set()
            cols_with_diffs.update(summary.numeric_column_stats.keys())
            cols_with_diffs.update(summary.string_column_mismatches.keys())
            cols_with_diffs.update(summary.geography_column_stats.keys())
        return sorted(all_value_cols - cols_with_diffs)

    def _build_column_rows(self, summary: "ComparisonSummary") -> list[_ColumnRow]:
        """Build the list of _ColumnRow objects for non-identical columns."""
        rows: list[_ColumnRow] = []
        has_tol = summary.has_tolerance
        cdc = summary.column_diff_counts
        # total_rows for diff%: rows present in both tables (matched on keys)
        total_rows = (summary.total_rows_a - summary.rows_only_in_a) if cdc else None

        # Aborted pipeline: Layer 2/3 skipped, so only column_diff_counts is
        # available. Build one row per differing column with stats as dashes.
        if summary.pipeline_aborted:
            rows.extend(
                _ColumnRow(
                    name=col_name,
                    col_type=summary.column_types.get(col_name, ColumnType.FLOAT),
                    diff_count=count,
                    total_rows=total_rows,
                    aborted=True,
                )
                for col_name, count in cdc.items()
                if count > 0
            )
            rows.sort(key=lambda r: r.name)
            return rows

        # Numeric columns
        for col_name, stats in summary.numeric_column_stats.items():
            col_type = summary.column_types.get(col_name, ColumnType.FLOAT)
            col_has_tolerance = has_tol and (
                "within_tolerance_count" in stats or "outside_tolerance_count" in stats
            )
            rows.append(
                _ColumnRow(
                    name=col_name,
                    col_type=col_type,
                    diff_count=cdc.get(col_name),
                    total_rows=total_rows,
                    max_abs=stats.get("max_abs_delta"),
                    max_rel=stats.get("max_rel_delta"),
                    avg_abs=stats.get("avg_abs_delta"),
                    outside_tolerance=stats.get("outside_tolerance_count"),
                    within_tolerance=stats.get("within_tolerance_count"),
                    has_tolerance=col_has_tolerance,
                )
            )

        # String columns
        for col_name, mismatch_count in summary.string_column_mismatches.items():
            rows.append(
                _ColumnRow(
                    name=col_name,
                    col_type=ColumnType.STRING,
                    diff_count=cdc.get(col_name),
                    total_rows=total_rows,
                    mismatch_count=mismatch_count,
                )
            )

        # Geography columns
        for col_name, stats in summary.geography_column_stats.items():
            col_has_tolerance = has_tol and (
                "within_tolerance_count" in stats or "outside_tolerance_count" in stats
            )
            rows.append(
                _ColumnRow(
                    name=col_name,
                    col_type=ColumnType.GEOGRAPHY,
                    diff_count=cdc.get(col_name),
                    total_rows=total_rows,
                    max_abs=stats.get("max_distance_meters"),
                    avg_abs=stats.get("avg_distance_meters"),
                    outside_tolerance=stats.get("outside_tolerance_count"),
                    within_tolerance=stats.get("within_tolerance_count"),
                    has_tolerance=col_has_tolerance,
                )
            )

        # Sort by the configured order
        if summary.column_sort_order == "significance":
            rows.sort(
                key=lambda r: (
                    summary.numeric_column_stats.get(r.name, {}).get("sum_abs_rel_delta") or 0
                ),
                reverse=True,
            )
        else:
            rows.sort(key=lambda r: r.name)

        return rows

    @staticmethod
    def _fmt_diff_pct(row: _ColumnRow) -> str:
        """Format the diff percentage for a column row."""
        if row.diff_count is None or row.total_rows is None or row.total_rows == 0:
            return "-"
        pct = row.diff_count / row.total_rows * 100
        if pct >= 10:
            return f"{pct:.1f}%"
        if pct >= 0.01:
            return f"{pct:.2f}%"
        return f"{pct:.1e}%"

    def _render_table(
        self, col_rows: list[_ColumnRow], *, show_tol: bool
    ) -> tuple[str, str, list[str]]:
        """Render the column-delta table.

        Returns (header_line, separator_line, [data_lines]).
        """
        name_w = self.COL_NAME_MAX
        type_w = 5
        num_w = 10
        count_w = 11
        diff_count_w = 14
        diff_pct_w = 8
        status_w = 6

        show_diffs = any(row.diff_count is not None for row in col_rows)

        # Build header parts
        parts_h = [
            f"{'Column':<{name_w}}",
            f"{'Type':>{type_w}}",
        ]
        if show_diffs:
            parts_h.append(f"{'Diffs':>{diff_count_w}}")
            parts_h.append(f"{'Diff%':>{diff_pct_w}}")
        parts_h.extend([
            f"{'MaxAbs':>{num_w}}",
            f"{'MaxRel':>{num_w}}",
            f"{'AvgAbs':>{num_w}}",
        ])
        if show_tol:
            parts_h.append(f"{'Exc.tol':>{count_w}}")
            parts_h.append(f"{'Within tol':>{count_w}}")
        parts_h.append(f"{'Status':>{status_w}}")

        header = "  ".join(parts_h)
        separator = "-" * len(header)

        # Build data lines
        data_lines: list[str] = []
        for row in col_rows:
            if row.col_type == ColumnType.STRING:
                # String columns get a special inline representation
                parts_d = [
                    f"{_truncate(row.name, name_w):<{name_w}}",
                    f"{_TYPE_ABBREV.get(row.col_type, '?'):>{type_w}}",
                ]
                if show_diffs:
                    parts_d.append(f"{_fmt_count(row.diff_count):>{diff_count_w}}")
                    parts_d.append(f"{self._fmt_diff_pct(row):>{diff_pct_w}}")
                # Merge the three numeric columns into a single mismatch info
                mismatch_text = f"{_fmt_count(row.mismatch_count)} mismatches"
                merged_w = num_w * 3 + 4  # three columns plus two "  " gaps
                parts_d.append(f"{mismatch_text:>{merged_w}}")
                if show_tol:
                    parts_d.append(f"{'-':>{count_w}}")
                    parts_d.append(f"{'-':>{count_w}}")
                parts_d.append(f"{row.status:>{status_w}}")
                data_lines.append("  ".join(parts_d))
            else:
                parts_d = [
                    f"{_truncate(row.name, name_w):<{name_w}}",
                    f"{_TYPE_ABBREV.get(row.col_type, '?'):>{type_w}}",
                ]
                if show_diffs:
                    parts_d.append(f"{_fmt_count(row.diff_count):>{diff_count_w}}")
                    parts_d.append(f"{self._fmt_diff_pct(row):>{diff_pct_w}}")
                parts_d.extend([
                    f"{_fmt_number(row.max_abs):>{num_w}}",
                    f"{_fmt_number(row.max_rel):>{num_w}}",
                    f"{_fmt_number(row.avg_abs):>{num_w}}",
                ])
                if show_tol:
                    parts_d.append(f"{_fmt_count(row.outside_tolerance):>{count_w}}")
                    parts_d.append(f"{_fmt_count(row.within_tolerance):>{count_w}}")
                parts_d.append(f"{row.status:>{status_w}}")
                data_lines.append("  ".join(parts_d))

        return header, separator, data_lines


# ---------------------------------------------------------------------------
# Formatter registry
# ---------------------------------------------------------------------------

_FORMATTERS: dict[str, SummaryFormatter] = {
    "verbose": VerboseFormatter(),
    "table": TableFormatter(),
}


def get_formatter(name: str) -> SummaryFormatter:
    """Return a formatter by name, raising ValueError for unknown names."""
    fmt = _FORMATTERS.get(name)
    if fmt is None:
        valid = ", ".join(sorted(_FORMATTERS))
        raise ValueError(f"Unknown output format '{name}'. Valid: {valid}")
    return fmt


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ComparisonSummary:
    """Summary of a table comparison."""

    table_a: str
    table_b: str
    key_columns: list[str]

    # Row counts (post-tolerance if tolerance is configured)
    rows_only_in_a: int
    rows_only_in_b: int
    rows_in_both_with_differences: int
    rows_identical: int

    # Totals
    total_rows_a: int
    total_rows_b: int

    # Column-level summaries for numeric columns with differences
    numeric_column_stats: dict[str, dict]  # col -> {max_abs_delta, ...}

    # String columns with mismatches
    string_column_mismatches: dict[str, int]  # col -> count of mismatches

    # Geography column stats
    geography_column_stats: dict[str, dict] = field(
        default_factory=dict
    )  # col -> {max_distance_meters, avg_distance_meters}

    # Columns excluded due to unsupported types
    excluded_columns: list[tuple[str, str]] = field(
        default_factory=list
    )  # [(col_name, bq_type), ...]

    # Per-column diff counts (from pipeline Layer 1; empty for legacy path)
    column_diff_counts: dict[str, int] = field(default_factory=dict)  # col -> count

    # Per-column type mapping for all *value* (non-key) columns
    column_types: dict[str, ColumnType] = field(default_factory=dict)  # col -> ColumnType

    # Tolerance configuration (optional)
    tolerance_config: dict[str, float] | None = None  # col -> abs tolerance value
    rel_tolerance_config: dict[str, float] | None = None  # col -> rel tolerance value

    # Pre-tolerance counts (optional, only present if tolerance is configured)
    rows_only_in_a_pretolerance: int | None = None
    rows_only_in_b_pretolerance: int | None = None
    rows_in_both_with_differences_pretolerance: int | None = None
    rows_identical_pretolerance: int | None = None

    # Duplicate key info (optional -- None means check was not run)
    duplicate_info_a: DuplicateInfo | None = None
    duplicate_info_b: DuplicateInfo | None = None

    # Partition filters used during comparison (captured for reproducibility)
    partition_filter_a: str | None = None
    partition_filter_b: str | None = None

    # True when the pipeline's circuit breaker aborted execution after Layer 1,
    # meaning per-column stats (numeric/string/geography) were NOT computed.
    pipeline_aborted: bool = False

    # Column sorting order
    column_sort_order: str = "alphabetical"  # "alphabetical" or "significance"

    # Output format
    output_format: str = "verbose"

    @property
    def total_differences(self) -> int:
        return self.rows_only_in_a + self.rows_only_in_b + self.rows_in_both_with_differences

    @property
    def total_differences_pretolerance(self) -> int | None:
        if self.rows_only_in_a_pretolerance is None:
            return None
        return (
            self.rows_only_in_a_pretolerance
            + self.rows_only_in_b_pretolerance
            + self.rows_in_both_with_differences_pretolerance
        )

    @property
    def tables_identical(self) -> bool:
        return self.total_differences == 0

    @property
    def has_tolerance(self) -> bool:
        has_abs = self.tolerance_config is not None and len(self.tolerance_config) > 0
        has_rel = self.rel_tolerance_config is not None and len(self.rel_tolerance_config) > 0
        return has_abs or has_rel

    def __str__(self) -> str:
        formatter = get_formatter(self.output_format)
        return formatter.format(self)


# ---------------------------------------------------------------------------
# Summary generation
# ---------------------------------------------------------------------------


def check_duplicates(
    client: bigquery.Client,
    builder: QueryBuilder,
) -> tuple[DuplicateInfo, DuplicateInfo]:
    """Run a duplicate-key check on both tables.

    Returns:
        (duplicate_info_a, duplicate_info_b)
    """
    query = builder.build_duplicate_check_query()
    row = list(client.query(query).result())[0]
    row_dict = dict(row)
    return (
        DuplicateInfo(
            duplicate_key_count=row_dict["dupes_a"],
            duplicate_row_count=row_dict["dupe_rows_a"],
            max_duplicate_count=row_dict["max_dupe_count_a"],
        ),
        DuplicateInfo(
            duplicate_key_count=row_dict["dupes_b"],
            duplicate_row_count=row_dict["dupe_rows_b"],
            max_duplicate_count=row_dict["max_dupe_count_b"],
        ),
    )


def generate_summary(
    client: bigquery.Client,
    builder: QueryBuilder,
    column_sort_order: str = "alphabetical",
    output_format: str = "verbose",
    pipeline_config: PipelineConfig | None = None,
) -> ComparisonSummary:
    """Generate a comprehensive summary of differences between two tables.

    Args:
        client: BigQuery client
        builder: QueryBuilder configured for the comparison
        column_sort_order: How to sort numeric columns in output
                          ("alphabetical" or "significance")
        output_format: Output format ("verbose" or "table")
        pipeline_config: If provided, use the multi-layer pipeline (single BQ job).
                        If None, use the legacy multi-query path.

    Returns:
        ComparisonSummary with all statistics
    """
    # Run duplicate check first (cheap, no join)
    dup_a, dup_b = check_duplicates(client, builder)

    if pipeline_config is not None:
        result = _generate_summary_pipeline(
            client, builder, pipeline_config, column_sort_order, output_format
        )
    else:
        result = _generate_summary_legacy(client, builder, column_sort_order, output_format)

    result.duplicate_info_a = dup_a
    result.duplicate_info_b = dup_b
    result.partition_filter_a = builder.partition_filter_a
    result.partition_filter_b = builder.partition_filter_b
    return result


def _generate_summary_pipeline(
    client: bigquery.Client,
    builder: QueryBuilder,
    pipeline_config: PipelineConfig,
    column_sort_order: str = "alphabetical",
    output_format: str = "verbose",
) -> ComparisonSummary:
    """Generate summary using the multi-layer pipeline (single BQ job).

    Executes one BigQuery multi-statement script that materialises intermediate
    results via temp tables. Produces the same ComparisonSummary as the legacy
    path but with dramatically fewer queries.
    """
    result = run_pipeline(client, builder, pipeline_config)

    # Build tolerance config for display
    tolerance_config_display = None
    rel_tolerance_config_display = None
    if builder.tolerance_config:
        tolerance_config_display = {}
        rel_tolerance_config_display = {}
        for col in builder.columns:
            if col.column_type in (ColumnType.FLOAT, ColumnType.GEOGRAPHY):
                tol = builder.tolerance_config.get_tolerance(col.name)
                if tol is not None:
                    tolerance_config_display[col.name] = tol
                rel_tol = builder.tolerance_config.get_rel_tolerance(col.name)
                if rel_tol is not None:
                    rel_tolerance_config_display[col.name] = rel_tol
        # Set to None if empty
        if not tolerance_config_display:
            tolerance_config_display = None
        if not rel_tolerance_config_display:
            rel_tolerance_config_display = None

    # Build column_types mapping and excluded columns list
    value_columns = [
        c
        for c in builder.columns
        if c.name not in builder.key_columns and c.column_type != ColumnType.UNSUPPORTED
    ]
    column_types: dict[str, ColumnType] = {c.name: c.column_type for c in value_columns}
    excluded_columns: list[tuple[str, str]] = [
        (c.name, c.bq_type) for c in builder.excluded_columns
    ]

    # rows_in_both = total_a - only_in_a (keys present in both tables)
    # rows_identical = rows_in_both - rows_in_both_with_differences
    rows_identical = (
        result.total_rows_a - result.rows_only_in_a - result.rows_in_both_with_differences
    )

    # Determine pre-tolerance counts using Layer 1 data
    # Layer 1 always finds ALL non-identical rows (no tolerance filtering).
    # The pipeline's rows_in_both_with_differences is the pre-tolerance count.
    # For post-tolerance: use Layer 3's post_tol_diff_count if available.
    has_tolerance = (
        (tolerance_config_display is not None and len(tolerance_config_display) > 0)
        or (rel_tolerance_config_display is not None and len(rel_tolerance_config_display) > 0)
    )

    if has_tolerance and result.pipeline_status == "COMPLETED":
        # Pre-tolerance counts come directly from Layer 1
        rows_only_in_a_pretolerance = result.rows_only_in_a
        rows_only_in_b_pretolerance = result.rows_only_in_b
        rows_in_both_pretolerance = result.rows_in_both_with_differences
        rows_in_both = result.total_rows_a - result.rows_only_in_a
        rows_identical_pretolerance = rows_in_both - rows_in_both_pretolerance

        # Post-tolerance: rows still differing after tolerance is applied
        post_tol_diff = result.post_tolerance_diff_count
        if post_tol_diff is not None:
            rows_in_both_post = post_tol_diff
        else:
            # No tolerance columns actually found in Layer 3
            rows_in_both_post = rows_in_both_pretolerance

        rows_identical_post = rows_in_both - rows_in_both_post

        return ComparisonSummary(
            table_a=builder.table_a,
            table_b=builder.table_b,
            key_columns=builder.key_columns,
            rows_only_in_a=result.rows_only_in_a,
            rows_only_in_b=result.rows_only_in_b,
            rows_in_both_with_differences=rows_in_both_post,
            rows_identical=rows_identical_post,
            total_rows_a=result.total_rows_a,
            total_rows_b=result.total_rows_b,
            numeric_column_stats=result.numeric_column_stats or {},
            string_column_mismatches=result.string_column_mismatches or {},
            geography_column_stats=result.geography_column_stats or {},
            excluded_columns=excluded_columns,
            column_diff_counts=result.column_diff_counts,
            column_types=column_types,
            tolerance_config=tolerance_config_display,
            rel_tolerance_config=rel_tolerance_config_display,
            rows_only_in_a_pretolerance=rows_only_in_a_pretolerance,
            rows_only_in_b_pretolerance=rows_only_in_b_pretolerance,
            rows_in_both_with_differences_pretolerance=rows_in_both_pretolerance,
            rows_identical_pretolerance=rows_identical_pretolerance,
            pipeline_aborted=(result.pipeline_status == "ABORTED"),
            column_sort_order=column_sort_order,
            output_format=output_format,
        )

    # No tolerance or aborted: simpler construction
    return ComparisonSummary(
        table_a=builder.table_a,
        table_b=builder.table_b,
        key_columns=builder.key_columns,
        rows_only_in_a=result.rows_only_in_a,
        rows_only_in_b=result.rows_only_in_b,
        rows_in_both_with_differences=result.rows_in_both_with_differences,
        rows_identical=rows_identical,
        total_rows_a=result.total_rows_a,
        total_rows_b=result.total_rows_b,
        numeric_column_stats=result.numeric_column_stats or {},
        string_column_mismatches=result.string_column_mismatches or {},
        geography_column_stats=result.geography_column_stats or {},
        excluded_columns=excluded_columns,
        column_diff_counts=result.column_diff_counts,
        column_types=column_types,
        tolerance_config=tolerance_config_display,
        rel_tolerance_config=rel_tolerance_config_display,
        pipeline_aborted=(result.pipeline_status == "ABORTED"),
        column_sort_order=column_sort_order,
        output_format=output_format,
    )


def _generate_summary_legacy(
    client: bigquery.Client,
    builder: QueryBuilder,
    column_sort_order: str = "alphabetical",
    output_format: str = "verbose",
) -> ComparisonSummary:
    """
    Generate a comprehensive summary of differences between two tables.

    Args:
        client: BigQuery client
        builder: QueryBuilder configured for the comparison
        column_sort_order: How to sort numeric columns in output
                          ("alphabetical" or "significance")
        output_format: Output format ("verbose" or "table")

    Returns:
        ComparisonSummary with all statistics
    """
    # Get total row counts for each table using SQLAlchemy
    table_a_obj, table_b_obj = builder.get_table_objects()

    count_a_stmt = select(func.count().label("cnt")).select_from(table_a_obj)
    count_b_stmt = select(func.count().label("cnt")).select_from(table_b_obj)

    count_a_query = str(
        count_a_stmt.compile(dialect=BigQueryDialect(), compile_kwargs={"literal_binds": True})
    )
    count_b_query = str(
        count_b_stmt.compile(dialect=BigQueryDialect(), compile_kwargs={"literal_binds": True})
    )

    total_rows_a = list(client.query(count_a_query).result())[0].cnt
    total_rows_b = list(client.query(count_b_query).result())[0].cnt

    # Build tolerance config for display (includes both FLOAT and GEOGRAPHY columns)
    tolerance_config_display = None
    rel_tolerance_config_display = None
    if builder.tolerance_config:
        tolerance_config_display = {}
        rel_tolerance_config_display = {}
        for col in builder.columns:
            if col.column_type in (ColumnType.FLOAT, ColumnType.GEOGRAPHY):
                tol = builder.tolerance_config.get_tolerance(col.name)
                if tol is not None:
                    tolerance_config_display[col.name] = tol
                rel_tol = builder.tolerance_config.get_rel_tolerance(col.name)
                if rel_tol is not None:
                    rel_tolerance_config_display[col.name] = rel_tol
        if not tolerance_config_display:
            tolerance_config_display = None
        if not rel_tolerance_config_display:
            rel_tolerance_config_display = None

    # Build diff queries
    diff_query = builder.build_diff_query(apply_tolerance=True)

    # Calculate post-tolerance counts (or all counts if no tolerance)
    summary_query = f"""
    WITH diff AS (
        {diff_query}
    )
    SELECT
        COUNTIF(in_a AND NOT in_b) AS rows_only_in_a,
        COUNTIF(NOT in_a AND in_b) AS rows_only_in_b,
        COUNTIF(in_a AND in_b) AS rows_in_both_with_differences
    FROM diff
    """

    result = list(client.query(summary_query).result())[0]
    rows_only_in_a = result.rows_only_in_a
    rows_only_in_b = result.rows_only_in_b
    rows_in_both_with_differences = result.rows_in_both_with_differences

    # Calculate identical rows: rows_in_both - rows_in_both_with_differences
    rows_identical = total_rows_a - rows_only_in_a - rows_in_both_with_differences

    # Also build unfiltered query for pre-tolerance counts
    unfiltered_diff_query = None
    rows_only_in_a_pretolerance = None
    rows_only_in_b_pretolerance = None
    rows_in_both_with_differences_pretolerance = None
    rows_identical_pretolerance = None

    if builder.tolerance_config:
        unfiltered_diff_query = builder.build_diff_query(apply_tolerance=False)

        pretolerance_summary_query = f"""
        WITH diff AS (
            {unfiltered_diff_query}
        )
        SELECT
            COUNTIF(in_a AND NOT in_b) AS rows_only_in_a,
            COUNTIF(NOT in_a AND in_b) AS rows_only_in_b,
            COUNTIF(in_a AND in_b) AS rows_in_both_with_differences
        FROM diff
        """

        pretolerance_result = list(client.query(pretolerance_summary_query).result())[0]
        rows_only_in_a_pretolerance = pretolerance_result.rows_only_in_a
        rows_only_in_b_pretolerance = pretolerance_result.rows_only_in_b
        rows_in_both_with_differences_pretolerance = (
            pretolerance_result.rows_in_both_with_differences
        )
        rows_identical_pretolerance = (
            total_rows_a - rows_only_in_a_pretolerance - rows_in_both_with_differences_pretolerance
        )

    # Get column statistics
    numeric_column_stats: dict[str, dict] = {}
    string_column_mismatches: dict[str, int] = {}
    geography_column_stats: dict[str, dict] = {}

    # Only consider supported, non-key columns
    value_columns = [
        c
        for c in builder.columns
        if c.name not in builder.key_columns and c.column_type != ColumnType.UNSUPPORTED
    ]

    # Build column_types mapping for all value columns
    column_types: dict[str, ColumnType] = {c.name: c.column_type for c in value_columns}

    # Build excluded columns list
    excluded_columns: list[tuple[str, str]] = [
        (c.name, c.bq_type) for c in builder.excluded_columns
    ]

    for col in value_columns:
        if col.column_type in (
            ColumnType.INTEGER,
            ColumnType.FLOAT,
            ColumnType.BOOLEAN,
            ColumnType.TIMESTAMP,
            ColumnType.DATE,
        ):
            has_tolerance = (
                col.column_type == ColumnType.FLOAT
                and builder.tolerance_config
                and builder.tolerance_config.has_any_tolerance(col.name)
            )

            stats_query = f"""
            WITH diff AS (
                {diff_query}
            )
            SELECT
                MAX({col.name}__abs_delta) AS max_abs_delta,
                MAX(ABS({col.name}__rel_delta)) AS max_rel_delta,
                AVG({col.name}__abs_delta) AS avg_abs_delta,
                SUM(ABS({col.name}__rel_delta)) AS sum_abs_rel_delta,
                COUNT(*) AS total_count
            FROM diff
            WHERE in_a AND in_b
            """
            stats_result = list(client.query(stats_query).result())[0]
            if stats_result.max_abs_delta is not None:
                stats_dict = {
                    "max_abs_delta": stats_result.max_abs_delta,
                    "max_rel_delta": stats_result.max_rel_delta,
                    "avg_abs_delta": stats_result.avg_abs_delta,
                    "sum_abs_rel_delta": stats_result.sum_abs_rel_delta,
                }

                if has_tolerance and unfiltered_diff_query:
                    tolerance_query = f"""
                    WITH diff AS (
                        {unfiltered_diff_query}
                    )
                    SELECT
                        COUNTIF({col.name}__within_tolerance)
                            AS within_tolerance_count,
                        COUNTIF(NOT {col.name}__within_tolerance)
                            AS outside_tolerance_count
                    FROM diff
                    WHERE in_a AND in_b
                    """
                    tolerance_result = list(client.query(tolerance_query).result())[0]
                    stats_dict["within_tolerance_count"] = tolerance_result.within_tolerance_count
                    stats_dict["outside_tolerance_count"] = tolerance_result.outside_tolerance_count

                numeric_column_stats[col.name] = stats_dict

        elif col.column_type == ColumnType.STRING:
            mismatch_query = f"""
            WITH diff AS (
                {diff_query}
            )
            SELECT COUNT(*) AS mismatches
            FROM diff
            WHERE in_a AND in_b AND NOT {col.name}__match
            """
            mismatch_result = list(client.query(mismatch_query).result())[0]
            if mismatch_result.mismatches > 0:
                string_column_mismatches[col.name] = mismatch_result.mismatches

        elif col.column_type == ColumnType.GEOGRAPHY:
            has_geo_tolerance = (
                builder.tolerance_config
                and builder.tolerance_config.has_any_tolerance(col.name)
            )

            geo_stats_query = f"""
            WITH diff AS (
                {diff_query}
            )
            SELECT
                MAX({col.name}__distance_meters) AS max_distance_meters,
                AVG({col.name}__distance_meters) AS avg_distance_meters
            FROM diff
            WHERE in_a AND in_b
            """
            geo_stats_result = list(client.query(geo_stats_query).result())[0]
            if geo_stats_result.max_distance_meters is not None:
                geo_stats_dict: dict = {
                    "max_distance_meters": geo_stats_result.max_distance_meters,
                    "avg_distance_meters": geo_stats_result.avg_distance_meters,
                }

                if has_geo_tolerance and unfiltered_diff_query:
                    geo_tol_query = f"""
                    WITH diff AS (
                        {unfiltered_diff_query}
                    )
                    SELECT
                        COUNTIF({col.name}__within_tolerance)
                            AS within_tolerance_count,
                        COUNTIF(NOT {col.name}__within_tolerance)
                            AS outside_tolerance_count
                    FROM diff
                    WHERE in_a AND in_b
                    """
                    geo_tol_result = list(client.query(geo_tol_query).result())[0]
                    geo_stats_dict["within_tolerance_count"] = geo_tol_result.within_tolerance_count
                    geo_stats_dict["outside_tolerance_count"] = (
                        geo_tol_result.outside_tolerance_count
                    )

                geography_column_stats[col.name] = geo_stats_dict

    return ComparisonSummary(
        table_a=builder.table_a,
        table_b=builder.table_b,
        key_columns=builder.key_columns,
        rows_only_in_a=rows_only_in_a,
        rows_only_in_b=rows_only_in_b,
        rows_in_both_with_differences=rows_in_both_with_differences,
        rows_identical=rows_identical,
        total_rows_a=total_rows_a,
        total_rows_b=total_rows_b,
        numeric_column_stats=numeric_column_stats,
        string_column_mismatches=string_column_mismatches,
        geography_column_stats=geography_column_stats,
        excluded_columns=excluded_columns,
        column_types=column_types,
        tolerance_config=tolerance_config_display,
        rel_tolerance_config=rel_tolerance_config_display,
        rows_only_in_a_pretolerance=rows_only_in_a_pretolerance,
        rows_only_in_b_pretolerance=rows_only_in_b_pretolerance,
        rows_in_both_with_differences_pretolerance=rows_in_both_with_differences_pretolerance,
        rows_identical_pretolerance=rows_identical_pretolerance,
        column_sort_order=column_sort_order,
        output_format=output_format,
    )


# ---------------------------------------------------------------------------
# Dimension Summary (unchanged)
# ---------------------------------------------------------------------------


@dataclass
class DimensionBucket:
    """Statistics for a single dimension value."""

    dimension_value: Any
    rows_only_in_a: int
    rows_only_in_b: int
    rows_with_differences: int
    total_differences: int

    # Optional: max absolute delta for a specific column of interest
    max_abs_delta: float | None = None
    max_rel_delta: float | None = None


@dataclass
class DimensionSummary:
    """Summary broken down by a dimension column."""

    table_a: str
    table_b: str
    key_columns: list[str]
    dimension_column: str
    delta_column: str | None  # Column to track deltas for

    buckets: list[DimensionBucket] = field(default_factory=list)

    # Overall totals
    total_rows_only_in_a: int = 0
    total_rows_only_in_b: int = 0
    total_rows_with_differences: int = 0

    @property
    def total_differences(self) -> int:
        return (
            self.total_rows_only_in_a + self.total_rows_only_in_b + self.total_rows_with_differences
        )

    @property
    def tables_identical(self) -> bool:
        return self.total_differences == 0

    def buckets_with_differences(self) -> list[DimensionBucket]:
        """Return only buckets that have differences."""
        return [b for b in self.buckets if b.total_differences > 0]

    def __str__(self) -> str:
        lines = [
            "=" * 80,
            "DIMENSION BREAKDOWN SUMMARY",
            "=" * 80,
            f"Table A: {self.table_a}",
            f"Table B: {self.table_b}",
            f"Key columns: {', '.join(self.key_columns)}",
            f"Dimension: {self.dimension_column}",
        ]
        if self.delta_column:
            lines.append(f"Delta column: {self.delta_column}")

        lines.extend(
            [
                "",
                "OVERALL TOTALS",
                "-" * 40,
                f"Total rows only in A: {self.total_rows_only_in_a:,}",
                f"Total rows only in B: {self.total_rows_only_in_b:,}",
                f"Total rows with value differences: {self.total_rows_with_differences:,}",
                f"TOTAL DIFFERENCES: {self.total_differences:,}",
            ]
        )

        diff_buckets = self.buckets_with_differences()
        if diff_buckets:
            lines.extend(
                [
                    "",
                    f"BREAKDOWN BY {self.dimension_column.upper()} (only showing differences)",
                    "-" * 80,
                ]
            )

            if self.delta_column:
                lines.append(
                    f"{'Dimension':<20} {'Only A':>10} {'Only B':>10}"
                    f" {'Diff':>10} "
                    f"{'Total':>10} {'Max Abs':>15} {'Max Rel':>15}"
                )
                lines.append("-" * 80)
                for bucket in diff_buckets:
                    max_abs = f"{bucket.max_abs_delta:.6g}" if bucket.max_abs_delta else "N/A"
                    max_rel = f"{bucket.max_rel_delta:.6g}" if bucket.max_rel_delta else "N/A"
                    lines.append(
                        f"{str(bucket.dimension_value):<20} "
                        f"{bucket.rows_only_in_a:>10,} "
                        f"{bucket.rows_only_in_b:>10,} "
                        f"{bucket.rows_with_differences:>10,} "
                        f"{bucket.total_differences:>10,} "
                        f"{max_abs:>15} "
                        f"{max_rel:>15}"
                    )
            else:
                lines.append(
                    f"{'Dimension':<20} {'Only A':>10} {'Only B':>10} {'Diff':>10} {'Total':>10}"
                )
                lines.append("-" * 80)
                for bucket in diff_buckets:
                    lines.append(
                        f"{str(bucket.dimension_value):<20} "
                        f"{bucket.rows_only_in_a:>10,} "
                        f"{bucket.rows_only_in_b:>10,} "
                        f"{bucket.rows_with_differences:>10,} "
                        f"{bucket.total_differences:>10,}"
                    )
        else:
            lines.extend(
                [
                    "",
                    "No differences found in any dimension bucket.",
                ]
            )

        lines.extend(
            [
                "",
                "=" * 80,
                f"RESULT: {'IDENTICAL' if self.tables_identical else 'DIFFERENCES FOUND'}",
                "=" * 80,
            ]
        )

        return "\n".join(lines)


def generate_dimension_summary(
    client: bigquery.Client,
    builder: QueryBuilder,
    dimension_column: str,
    delta_column: str | None = None,
    limit: int | None = None,
) -> DimensionSummary:
    """
    Generate a comparison summary broken down by a dimension column.

    Args:
        client: BigQuery client
        builder: QueryBuilder configured for the comparison
        dimension_column: Column to group results by (e.g., 'date', 'timestamp')
        delta_column: Optional numeric column to track max deltas for
        limit: Optional limit on number of dimension buckets to return

    Returns:
        DimensionSummary with per-dimension statistics
    """
    diff_query = builder.build_diff_query()

    delta_selects = ""
    if delta_column:
        col_info = next((c for c in builder.columns if c.name == delta_column), None)
        if col_info and col_info.column_type in (
            ColumnType.INTEGER,
            ColumnType.FLOAT,
            ColumnType.BOOLEAN,
            ColumnType.TIMESTAMP,
            ColumnType.DATE,
        ):
            delta_selects = f"""
                MAX({delta_column}__abs_delta) AS max_abs_delta,
                MAX(ABS({delta_column}__rel_delta)) AS max_rel_delta,
            """

    order_clause = f"ORDER BY {dimension_column}"
    limit_clause = f"LIMIT {limit}" if limit else ""

    summary_query = f"""
    WITH diff AS (
        {diff_query}
    )
    SELECT
        {dimension_column} AS dimension_value,
        COUNTIF(in_a AND NOT in_b) AS rows_only_in_a,
        COUNTIF(NOT in_a AND in_b) AS rows_only_in_b,
        COUNTIF(in_a AND in_b) AS rows_with_differences,
        {delta_selects}
        COUNT(*) AS total_differences
    FROM diff
    GROUP BY {dimension_column}
    {order_clause}
    {limit_clause}
    """

    result = client.query(summary_query).result()

    buckets = []
    total_only_a = 0
    total_only_b = 0
    total_with_diff = 0

    for row in result:
        bucket = DimensionBucket(
            dimension_value=row.dimension_value,
            rows_only_in_a=row.rows_only_in_a,
            rows_only_in_b=row.rows_only_in_b,
            rows_with_differences=row.rows_with_differences,
            total_differences=row.total_differences,
            max_abs_delta=getattr(row, "max_abs_delta", None),
            max_rel_delta=getattr(row, "max_rel_delta", None),
        )
        buckets.append(bucket)
        total_only_a += row.rows_only_in_a
        total_only_b += row.rows_only_in_b
        total_with_diff += row.rows_with_differences

    return DimensionSummary(
        table_a=builder.table_a,
        table_b=builder.table_b,
        key_columns=builder.key_columns,
        dimension_column=dimension_column,
        delta_column=delta_column,
        buckets=buckets,
        total_rows_only_in_a=total_only_a,
        total_rows_only_in_b=total_only_b,
        total_rows_with_differences=total_with_diff,
    )


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------


def to_json_dict(summary: ComparisonSummary) -> dict:
    """Serialize a ComparisonSummary to a JSON-friendly dict."""
    data = asdict(summary)
    data["column_types"] = {k: v.value for k, v in summary.column_types.items()}
    return data


def from_json_dict(data: dict) -> ComparisonSummary:
    """Reconstruct a ComparisonSummary from a dict produced by to_json_dict."""
    d = dict(data)
    d["column_types"] = {k: ColumnType(v) for k, v in d.get("column_types", {}).items()}
    d["excluded_columns"] = [tuple(item) for item in d.get("excluded_columns", [])]
    if d.get("duplicate_info_a") is not None:
        d["duplicate_info_a"] = DuplicateInfo(**d["duplicate_info_a"])
    if d.get("duplicate_info_b") is not None:
        d["duplicate_info_b"] = DuplicateInfo(**d["duplicate_info_b"])
    return ComparisonSummary(**d)


# ---------------------------------------------------------------------------
# EXCEPT DISTINCT verification query
# ---------------------------------------------------------------------------


def build_verify_query(summary: ComparisonSummary) -> str:
    """Build a SELECT * EXCEPT(...) + EXCEPT DISTINCT / UNION ALL verification SQL.

    Confirms the two tables are identical after excluding:
      * columns the pipeline skipped (unsupported types)
      * columns with pre-tolerance differences
      * GEOGRAPHY columns (not groupable, unusable with EXCEPT DISTINCT)

    For STRUCT-flattened columns (dot-notation), excludes the top-level parent.
    """
    exclude: set[str] = set()
    for col_name, _ in summary.excluded_columns:
        exclude.add(col_name.split(".")[0])
    for col_name, count in summary.column_diff_counts.items():
        if count > 0:
            exclude.add(col_name.split(".")[0])
    for col_name, col_type in summary.column_types.items():
        if col_type == ColumnType.GEOGRAPHY:
            exclude.add(col_name.split(".")[0])

    except_clause = f" EXCEPT({', '.join(sorted(exclude))})" if exclude else ""

    def _source(table: str, partition_filter: str | None) -> str:
        bt = f"`{table}`"
        if partition_filter:
            return f"(SELECT * FROM {bt} WHERE {partition_filter})"
        return bt

    a_src = _source(summary.table_a, summary.partition_filter_a)
    b_src = _source(summary.table_b, summary.partition_filter_b)

    return (
        f"WITH a AS (\n"
        f"  SELECT *{except_clause} FROM {a_src}\n"
        f"),\n\n"
        f"b AS (\n"
        f"  SELECT *{except_clause} FROM {b_src}\n"
        f"),\n\n"
        f"missing_in_a AS (\n"
        f"  SELECT * FROM b\n"
        f"  EXCEPT DISTINCT\n"
        f"  SELECT * FROM a\n"
        f"),\n\n"
        f"missing_in_b AS (\n"
        f"  SELECT * FROM a\n"
        f"  EXCEPT DISTINCT\n"
        f"  SELECT * FROM b\n"
        f")\n\n"
        f"SELECT 'missing_in_a' AS which, * FROM missing_in_a\n"
        f"UNION ALL\n"
        f"SELECT 'missing_in_b' AS which, * FROM missing_in_b"
    )
