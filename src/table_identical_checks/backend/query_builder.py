"""Query builder for generating table diff SQL."""

from dataclasses import dataclass, field

from .schema import ColumnInfo, ColumnType


@dataclass
class QueryBuilder:
    """
    Builds BigQuery SQL to compare two tables and generate a diff.

    The generated query performs a FULL OUTER JOIN on key columns and computes:
    - For numeric columns: delta, abs_delta, rel_delta
    - For string columns: match flag
    - For all columns: values from both tables

    NULL values are treated as equal (using IS NOT DISTINCT FROM).
    """

    table_a: str
    table_b: str
    key_columns: list[str]
    columns: list[ColumnInfo]
    alias_a: str = "a"
    alias_b: str = "b"

    # Columns to exclude from comparison (only keys by default)
    _exclude_from_comparison: set[str] = field(default_factory=set)

    def __post_init__(self):
        self._exclude_from_comparison = set(self.key_columns)

    def _value_columns(self) -> list[ColumnInfo]:
        """Get non-key columns for comparison."""
        return [c for c in self.columns if c.name not in self._exclude_from_comparison]

    def _build_key_select(self) -> list[str]:
        """Build SELECT clauses for key columns (coalesced)."""
        parts = []
        for key in self.key_columns:
            parts.append(f"COALESCE({self.alias_a}.{key}, {self.alias_b}.{key}) AS {key}")
        return parts

    def _build_existence_flags(self) -> list[str]:
        """Build SELECT clauses for existence flags."""
        first_key = self.key_columns[0]
        return [
            f"{self.alias_a}.{first_key} IS NOT NULL AS in_a",
            f"{self.alias_b}.{first_key} IS NOT NULL AS in_b",
        ]

    def _build_numeric_columns(self, col: ColumnInfo) -> list[str]:
        """Build SELECT clauses for a numeric column (int or float)."""
        name = col.name
        a_col = f"{self.alias_a}.{name}"
        b_col = f"{self.alias_b}.{name}"

        return [
            f"{a_col} AS a__{name}",
            f"{b_col} AS b__{name}",
            f"({a_col} - {b_col}) AS {name}__delta",
            f"ABS({a_col} - {b_col}) AS {name}__abs_delta",
            f"SAFE_DIVIDE({a_col} - {b_col}, {b_col}) AS {name}__rel_delta",
        ]

    def _build_string_column(self, col: ColumnInfo) -> list[str]:
        """Build SELECT clauses for a string column."""
        name = col.name
        a_col = f"{self.alias_a}.{name}"
        b_col = f"{self.alias_b}.{name}"

        return [
            f"{a_col} AS a__{name}",
            f"{b_col} AS b__{name}",
            f"{a_col} IS NOT DISTINCT FROM {b_col} AS {name}__match",
        ]

    def _build_value_columns(self) -> list[str]:
        """Build SELECT clauses for all value columns."""
        parts = []
        for col in self._value_columns():
            if col.column_type in (ColumnType.INTEGER, ColumnType.FLOAT):
                parts.extend(self._build_numeric_columns(col))
            elif col.column_type == ColumnType.STRING:
                parts.extend(self._build_string_column(col))
            # Skip unsupported types
        return parts

    def _build_join_condition(self) -> str:
        """Build the JOIN ON clause.

        Note: BigQuery FULL OUTER JOIN requires simple equality conditions.
        Key columns should not contain NULLs, so this is acceptable.
        """
        conditions = [f"{self.alias_a}.{key} = {self.alias_b}.{key}" for key in self.key_columns]
        return " AND ".join(conditions)

    def _build_where_clause(self) -> str:
        """Build WHERE clause to filter only rows with differences."""
        conditions = []

        for col in self._value_columns():
            a_col = f"{self.alias_a}.{col.name}"
            b_col = f"{self.alias_b}.{col.name}"
            # NOT (a IS NOT DISTINCT FROM b) means they ARE different
            conditions.append(f"NOT ({a_col} IS NOT DISTINCT FROM {b_col})")

        # Also include rows that exist in only one table
        first_key = self.key_columns[0]
        conditions.append(f"{self.alias_a}.{first_key} IS NULL")
        conditions.append(f"{self.alias_b}.{first_key} IS NULL")

        return " OR ".join(conditions)

    def build_diff_query(self) -> str:
        """
        Build the complete diff query.

        Returns:
            SQL query string that will return all differing rows with their deltas.
        """
        select_parts = []
        select_parts.extend(self._build_key_select())
        select_parts.extend(self._build_existence_flags())
        select_parts.extend(self._build_value_columns())

        select_clause = ",\n  ".join(select_parts)
        join_condition = self._build_join_condition()
        where_clause = self._build_where_clause()

        query = f"""SELECT
  {select_clause}
FROM `{self.table_a}` AS {self.alias_a}
FULL OUTER JOIN `{self.table_b}` AS {self.alias_b}
  ON {join_condition}
WHERE {where_clause}"""

        return query

    def build_count_query(self) -> str:
        """Build a query that just counts the number of differing rows."""
        diff_query = self.build_diff_query()
        return f"SELECT COUNT(*) AS diff_count FROM ({diff_query})"
