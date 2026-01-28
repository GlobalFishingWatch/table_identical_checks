"""Query builder using SQLAlchemy Core for generating table diff SQL."""

from dataclasses import dataclass, field

from sqlalchemy import and_, column, func, literal_column, or_, select, table
from sqlalchemy.sql import Selectable
from sqlalchemy_bigquery import BigQueryDialect

from .schema import ColumnInfo, ColumnType


@dataclass
class QueryBuilder:
    """
    Builds BigQuery SQL to compare two tables using SQLAlchemy Core.

    The generated query performs a FULL OUTER JOIN on key columns and computes:
    - For numeric columns: delta, abs_delta, rel_delta
    - For string columns: match flag  
    - For all columns: values from both tables

    NULL values are treated as equal.
    """

    table_a: str
    table_b: str
    key_columns: list[str]
    columns: list[ColumnInfo]
    alias_a: str = "a"
    alias_b: str = "b"
    partition_filter_a: str | None = None
    partition_filter_b: str | None = None

    # Columns to exclude from comparison (only keys by default)
    _exclude_from_comparison: set[str] = field(default_factory=set)

    def __post_init__(self):
        self._exclude_from_comparison = set(self.key_columns)

    def _value_columns(self) -> list[ColumnInfo]:
        """Get non-key columns for comparison."""
        return [c for c in self.columns if c.name not in self._exclude_from_comparison]

    def _create_table_objects(self) -> tuple[Selectable, Selectable]:
        """
        Create SQLAlchemy table objects for both tables.
        
        Returns base selectable objects (either tables or filtered subqueries).
        """
        # Define lightweight table objects with all columns
        table_a_obj = table(
            self.table_a,
            *[column(col.name) for col in self.columns]
        )
        table_b_obj = table(
            self.table_b,
            *[column(col.name) for col in self.columns]
        )

        # Apply partition filters if present
        if self.partition_filter_a:
            # Create subquery with partition filter
            filter_expr = literal_column(self.partition_filter_a)
            table_a_obj = select(table_a_obj).where(filter_expr).subquery(self.alias_a)
        else:
            table_a_obj = table_a_obj.alias(self.alias_a)

        if self.partition_filter_b:
            filter_expr = literal_column(self.partition_filter_b)
            table_b_obj = select(table_b_obj).where(filter_expr).subquery(self.alias_b)
        else:
            table_b_obj = table_b_obj.alias(self.alias_b)

        return table_a_obj, table_b_obj

    def _null_safe_equal(self, col_a, col_b):
        """
        Generate NULL-safe equality check.
        
        BigQuery doesn't support IS NOT DISTINCT FROM, so use:
        (a = b) OR (a IS NULL AND b IS NULL)
        
        Both must be present for equality.
        """
        return or_(
            and_(col_a == col_b, col_a.isnot(None), col_b.isnot(None)),
            and_(col_a.is_(None), col_b.is_(None))
        )

    def _null_safe_not_equal(self, col_a, col_b):
        """
        Generate NULL-safe inequality check.
        
        They're different if:
        - One is NULL and the other isn't, OR
        - Both are not NULL and have different values
        """
        return or_(
            and_(col_a.isnot(None), col_b.is_(None)),
            and_(col_a.is_(None), col_b.isnot(None)),
            and_(col_a != col_b, col_a.isnot(None), col_b.isnot(None))
        )

    def _build_select_columns(self, table_a_obj, table_b_obj) -> list:
        """Build all SELECT columns for the diff query."""
        select_cols = []

        # Key columns (coalesced)
        for key in self.key_columns:
            select_cols.append(
                func.coalesce(table_a_obj.c[key], table_b_obj.c[key]).label(key)
            )

        # Existence flags
        first_key = self.key_columns[0]
        select_cols.extend([
            table_a_obj.c[first_key].isnot(None).label("in_a"),
            table_b_obj.c[first_key].isnot(None).label("in_b"),
        ])

        # Value columns with deltas/comparisons
        for col in self._value_columns():
            col_a = table_a_obj.c[col.name]
            col_b = table_b_obj.c[col.name]

            if col.column_type in (ColumnType.INTEGER, ColumnType.FLOAT):
                # Numeric columns: include both values and delta metrics
                select_cols.extend([
                    col_a.label(f"a__{col.name}"),
                    col_b.label(f"b__{col.name}"),
                    (col_a - col_b).label(f"{col.name}__delta"),
                    func.abs(col_a - col_b).label(f"{col.name}__abs_delta"),
                    # Use literal_column for SAFE_DIVIDE (BigQuery-specific)
                    literal_column(
                        f"SAFE_DIVIDE({self.alias_a}.{col.name} - {self.alias_b}.{col.name}, "
                        f"{self.alias_b}.{col.name})"
                    ).label(f"{col.name}__rel_delta"),
                ])

            elif col.column_type == ColumnType.BOOLEAN:
                # Boolean columns: cast to INT64 and treat as integer
                # This allows us to use the same delta logic as integers
                cast_a = literal_column(f"CAST({self.alias_a}.{col.name} AS INT64)")
                cast_b = literal_column(f"CAST({self.alias_b}.{col.name} AS INT64)")
                
                select_cols.extend([
                    col_a.label(f"a__{col.name}"),
                    col_b.label(f"b__{col.name}"),
                    literal_column(
                        f"CAST({self.alias_a}.{col.name} AS INT64) - "
                        f"CAST({self.alias_b}.{col.name} AS INT64)"
                    ).label(f"{col.name}__delta"),
                    literal_column(
                        f"ABS(CAST({self.alias_a}.{col.name} AS INT64) - "
                        f"CAST({self.alias_b}.{col.name} AS INT64))"
                    ).label(f"{col.name}__abs_delta"),
                    # rel_delta doesn't make sense for booleans, but include for consistency
                    literal_column(
                        f"SAFE_DIVIDE(CAST({self.alias_a}.{col.name} AS INT64) - "
                        f"CAST({self.alias_b}.{col.name} AS INT64), "
                        f"CAST({self.alias_b}.{col.name} AS INT64))"
                    ).label(f"{col.name}__rel_delta"),
                ])

            elif col.column_type == ColumnType.TIMESTAMP:
                # Timestamp columns: use TIMESTAMP_DIFF for delta in seconds
                select_cols.extend([
                    col_a.label(f"a__{col.name}"),
                    col_b.label(f"b__{col.name}"),
                    # TIMESTAMP_DIFF returns seconds as INT64
                    literal_column(
                        f"TIMESTAMP_DIFF({self.alias_a}.{col.name}, "
                        f"{self.alias_b}.{col.name}, SECOND)"
                    ).label(f"{col.name}__delta"),
                    literal_column(
                        f"ABS(TIMESTAMP_DIFF({self.alias_a}.{col.name}, "
                        f"{self.alias_b}.{col.name}, SECOND))"
                    ).label(f"{col.name}__abs_delta"),
                    # rel_delta: fraction of time difference relative to b
                    literal_column(
                        f"SAFE_DIVIDE(TIMESTAMP_DIFF({self.alias_a}.{col.name}, "
                        f"{self.alias_b}.{col.name}, SECOND), "
                        f"UNIX_SECONDS({self.alias_b}.{col.name}))"
                    ).label(f"{col.name}__rel_delta"),
                ])

            elif col.column_type == ColumnType.STRING:
                # String columns: include both values and match flag
                select_cols.extend([
                    col_a.label(f"a__{col.name}"),
                    col_b.label(f"b__{col.name}"),
                    self._null_safe_equal(col_a, col_b).label(f"{col.name}__match"),
                ])

        return select_cols

    def _build_where_clause(self, table_a_obj, table_b_obj):
        """Build WHERE clause to filter only rows with differences."""
        conditions = []

        # Check for differences in value columns
        for col in self._value_columns():
            col_a = table_a_obj.c[col.name]
            col_b = table_b_obj.c[col.name]
            conditions.append(self._null_safe_not_equal(col_a, col_b))

        # Include rows that exist in only one table
        first_key = self.key_columns[0]
        conditions.append(table_a_obj.c[first_key].is_(None))
        conditions.append(table_b_obj.c[first_key].is_(None))

        return or_(*conditions)

    def build_diff_query(self) -> str:
        """
        Build the complete diff query using SQLAlchemy Core.

        Returns:
            SQL query string that will return all differing rows with their deltas.
        """
        table_a_obj, table_b_obj = self._create_table_objects()

        # Build join condition
        join_conditions = [
            table_a_obj.c[key] == table_b_obj.c[key]
            for key in self.key_columns
        ]
        join_condition = and_(*join_conditions) if len(join_conditions) > 1 else join_conditions[0]

        # Build FULL OUTER JOIN
        full_join = table_a_obj.outerjoin(table_b_obj, onclause=join_condition, full=True)

        # Build SELECT statement
        select_cols = self._build_select_columns(table_a_obj, table_b_obj)
        where_clause = self._build_where_clause(table_a_obj, table_b_obj)

        stmt = select(*select_cols).select_from(full_join).where(where_clause)

        # Compile to SQL string for BigQuery dialect
        return str(stmt.compile(dialect=BigQueryDialect(), compile_kwargs={"literal_binds": True}))

    def build_count_query(self) -> str:
        """Build a query that counts the number of differing rows."""
        diff_query = self.build_diff_query()
        # Wrap in COUNT(*)
        count_stmt = f"SELECT COUNT(*) AS diff_count FROM ({diff_query})"
        return count_stmt

    def get_table_objects(self) -> tuple[Selectable, Selectable]:
        """
        Get the SQLAlchemy table objects for use in other queries.
        
        Useful for summary queries that need to reference the same filtered tables.
        """
        return self._create_table_objects()
