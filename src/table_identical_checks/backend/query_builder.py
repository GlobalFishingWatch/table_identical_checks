"""Query builder using SQLAlchemy Core for generating table diff SQL."""

from dataclasses import dataclass, field

from sqlalchemy import and_, column, func, literal_column, or_, select, table
from sqlalchemy.sql import Selectable
from sqlalchemy_bigquery import BigQueryDialect

from .schema import ColumnInfo, ColumnType
from .tolerance import ToleranceConfig


@dataclass
class QueryBuilder:
    """
    Builds BigQuery SQL to compare two tables using SQLAlchemy Core.

    The generated query performs a FULL OUTER JOIN on key columns and computes:
    - For numeric columns: delta, abs_delta, rel_delta
    - For string columns: match flag
    - For geography columns: ST_DISTANCE (meters), ST_EQUALS
    - For all columns: values from both tables

    NULL values are treated as equal.
    Unsupported column types (ARRAY, STRUCT, JSON, etc.) are automatically excluded.

    Tolerance filtering:
    - Applied to FLOAT64 columns based on abs_delta
    - Applied to GEOGRAPHY columns based on ST_DISTANCE in meters
    - Rows where ALL toleranced columns are within tolerance are excluded
    """

    table_a: str
    table_b: str
    key_columns: list[str]
    columns: list[ColumnInfo]
    alias_a: str = "a"
    alias_b: str = "b"
    partition_filter_a: str | None = None
    partition_filter_b: str | None = None
    tolerance_config: ToleranceConfig | None = None

    # Columns to exclude from comparison (only keys by default)
    _exclude_from_comparison: set[str] = field(default_factory=set)

    # Columns excluded because their type is unsupported
    _excluded_unsupported: list[ColumnInfo] = field(default_factory=list)

    def __post_init__(self):
        self._exclude_from_comparison = set(self.key_columns)
        # Identify and exclude unsupported columns
        self._excluded_unsupported = [
            c for c in self.columns if c.column_type == ColumnType.UNSUPPORTED
        ]
        self._exclude_from_comparison.update(c.name for c in self._excluded_unsupported)

    @property
    def excluded_columns(self) -> list[ColumnInfo]:
        """Columns excluded from comparison due to unsupported types."""
        return list(self._excluded_unsupported)

    def _value_columns(self) -> list[ColumnInfo]:
        """Get non-key columns for comparison."""
        return [c for c in self.columns if c.name not in self._exclude_from_comparison]

    def _supported_columns(self) -> list[ColumnInfo]:
        """Get all columns with supported types (excludes UNSUPPORTED)."""
        return [c for c in self.columns if c.column_type != ColumnType.UNSUPPORTED]

    def _create_table_objects(self) -> tuple[Selectable, Selectable]:
        """
        Create SQLAlchemy table objects for both tables.

        Returns base selectable objects (either tables or filtered subqueries).
        Only includes columns with supported types.
        """
        # Define lightweight table objects with supported columns only
        supported = self._supported_columns()
        table_a_obj = table(self.table_a, *[column(col.name) for col in supported])
        table_b_obj = table(self.table_b, *[column(col.name) for col in supported])

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
            and_(col_a.is_(None), col_b.is_(None)),
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
            and_(col_a != col_b, col_a.isnot(None), col_b.isnot(None)),
        )

    def _build_select_columns(self, table_a_obj, table_b_obj) -> list:
        """Build all SELECT columns for the diff query."""
        select_cols = []

        # Key columns (coalesced)
        for key in self.key_columns:
            select_cols.append(func.coalesce(table_a_obj.c[key], table_b_obj.c[key]).label(key))

        # Existence flags
        first_key = self.key_columns[0]
        select_cols.extend(
            [
                table_a_obj.c[first_key].isnot(None).label("in_a"),
                table_b_obj.c[first_key].isnot(None).label("in_b"),
            ]
        )

        # Value columns with deltas/comparisons
        for col in self._value_columns():
            col_a = table_a_obj.c[col.name]
            col_b = table_b_obj.c[col.name]

            if col.column_type in (ColumnType.INTEGER, ColumnType.FLOAT):
                # Numeric columns: include both values and delta metrics
                select_cols.extend(
                    [
                        col_a.label(f"a__{col.name}"),
                        col_b.label(f"b__{col.name}"),
                        (col_a - col_b).label(f"{col.name}__delta"),
                        func.abs(col_a - col_b).label(f"{col.name}__abs_delta"),
                        # Use literal_column for SAFE_DIVIDE (BigQuery-specific)
                        literal_column(
                            f"SAFE_DIVIDE({self.alias_a}.{col.name} - {self.alias_b}.{col.name}, "
                            f"{self.alias_b}.{col.name})"
                        ).label(f"{col.name}__rel_delta"),
                    ]
                )

                # Add within_tolerance flag for FLOAT columns if tolerance configured
                if col.column_type == ColumnType.FLOAT and self.tolerance_config:
                    tolerance = self.tolerance_config.get_tolerance(col.name)
                    if tolerance is not None:
                        # Add boolean column indicating if abs_delta is within tolerance
                        within_tol_expr = (
                            f"ABS({self.alias_a}.{col.name} - "
                            f"{self.alias_b}.{col.name}) <= {tolerance}"
                        )
                        select_cols.append(
                            literal_column(within_tol_expr).label(f"{col.name}__within_tolerance")
                        )

            elif col.column_type == ColumnType.BOOLEAN:
                # Boolean columns: cast to INT64 and treat as integer
                # This allows us to use the same delta logic as integers
                select_cols.extend(
                    [
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
                    ]
                )

            elif col.column_type == ColumnType.TIMESTAMP:
                # Timestamp columns: use TIMESTAMP_DIFF for delta in microseconds
                select_cols.extend(
                    [
                        col_a.label(f"a__{col.name}"),
                        col_b.label(f"b__{col.name}"),
                        # TIMESTAMP_DIFF returns microseconds as INT64
                        literal_column(
                            f"TIMESTAMP_DIFF({self.alias_a}.{col.name}, "
                            f"{self.alias_b}.{col.name}, MICROSECOND)"
                        ).label(f"{col.name}__delta"),
                        literal_column(
                            f"ABS(TIMESTAMP_DIFF({self.alias_a}.{col.name}, "
                            f"{self.alias_b}.{col.name}, MICROSECOND))"
                        ).label(f"{col.name}__abs_delta"),
                        # rel_delta: fraction relative to b (in microseconds)
                        literal_column(
                            f"SAFE_DIVIDE(TIMESTAMP_DIFF({self.alias_a}.{col.name}, "
                            f"{self.alias_b}.{col.name}, MICROSECOND), "
                            f"UNIX_MICROS({self.alias_b}.{col.name}))"
                        ).label(f"{col.name}__rel_delta"),
                    ]
                )

            elif col.column_type == ColumnType.STRING:
                # String columns: include both values and match flag
                select_cols.extend(
                    [
                        col_a.label(f"a__{col.name}"),
                        col_b.label(f"b__{col.name}"),
                        self._null_safe_equal(col_a, col_b).label(f"{col.name}__match"),
                    ]
                )

            elif col.column_type == ColumnType.GEOGRAPHY:
                # Geography columns: ST_ASTEXT for values, ST_DISTANCE for delta (meters)
                select_cols.extend(
                    [
                        literal_column(f"ST_ASTEXT({self.alias_a}.{col.name})").label(
                            f"a__{col.name}"
                        ),
                        literal_column(f"ST_ASTEXT({self.alias_b}.{col.name})").label(
                            f"b__{col.name}"
                        ),
                        # ST_DISTANCE in meters (use_spheroid=TRUE for WGS84)
                        # Only compute when both values are not NULL
                        literal_column(
                            f"IF({self.alias_a}.{col.name} IS NOT NULL "
                            f"AND {self.alias_b}.{col.name} IS NOT NULL, "
                            f"ST_DISTANCE({self.alias_a}.{col.name}, "
                            f"{self.alias_b}.{col.name}, TRUE), NULL)"
                        ).label(f"{col.name}__distance_meters"),
                    ]
                )

                # Add within_tolerance flag for GEOGRAPHY columns if tolerance configured
                if self.tolerance_config:
                    tolerance = self.tolerance_config.get_tolerance(col.name)
                    if tolerance is not None:
                        # Note: ST_DISTANCE without use_spheroid because BQ
                        # rewrites <= comparisons to ST_DWITHIN which does not
                        # support use_spheroid=TRUE. Spherical approximation
                        # is sufficient for tolerance checks.
                        within_tol_expr = (
                            f"IF({self.alias_a}.{col.name} IS NOT NULL "
                            f"AND {self.alias_b}.{col.name} IS NOT NULL, "
                            f"ST_DISTANCE({self.alias_a}.{col.name}, "
                            f"{self.alias_b}.{col.name}) <= {tolerance}, "
                            f"({self.alias_a}.{col.name} IS NULL "
                            f"AND {self.alias_b}.{col.name} IS NULL))"
                        )
                        select_cols.append(
                            literal_column(within_tol_expr).label(f"{col.name}__within_tolerance")
                        )

        return select_cols

    def _null_safe_geography_not_equal(self, col_name):
        """
        Generate NULL-safe inequality check for GEOGRAPHY columns using ST_EQUALS.

        They're different if:
        - One is NULL and the other isn't, OR
        - Both are not NULL and ST_EQUALS returns FALSE
        """
        a = f"{self.alias_a}.{col_name}"
        b = f"{self.alias_b}.{col_name}"
        return literal_column(
            f"(({a} IS NOT NULL AND {b} IS NULL) OR "
            f"({a} IS NULL AND {b} IS NOT NULL) OR "
            f"({a} IS NOT NULL AND {b} IS NOT NULL AND NOT ST_EQUALS({a}, {b})))"
        )

    def _null_safe_geography_equal(self, col_name):
        """
        Generate NULL-safe equality check for GEOGRAPHY columns using ST_EQUALS.

        Equal if:
        - Both are NULL, OR
        - Both are not NULL and ST_EQUALS returns TRUE
        """
        a = f"{self.alias_a}.{col_name}"
        b = f"{self.alias_b}.{col_name}"
        return literal_column(
            f"(({a} IS NULL AND {b} IS NULL) OR "
            f"({a} IS NOT NULL AND {b} IS NOT NULL AND ST_EQUALS({a}, {b})))"
        )

    def _build_where_clause(self, table_a_obj, table_b_obj):
        """Build WHERE clause to filter only rows with differences.

        If tolerance is configured, excludes rows where:
        - ALL non-float/non-geography columns are equal, AND
        - ALL float columns (with tolerance) have abs_delta <= tolerance, AND
        - ALL geography columns (with tolerance) have ST_DISTANCE <= tolerance
        """
        conditions = []

        # Check for differences in value columns
        for col in self._value_columns():
            if col.column_type == ColumnType.GEOGRAPHY:
                conditions.append(self._null_safe_geography_not_equal(col.name))
            else:
                col_a = table_a_obj.c[col.name]
                col_b = table_b_obj.c[col.name]
                conditions.append(self._null_safe_not_equal(col_a, col_b))

        # Include rows that exist in only one table
        first_key = self.key_columns[0]
        conditions.append(table_a_obj.c[first_key].is_(None))
        conditions.append(table_b_obj.c[first_key].is_(None))

        base_condition = or_(*conditions)

        # Apply tolerance filtering if configured
        if self.tolerance_config:
            tolerance_exclusion = self._build_tolerance_exclusion(table_a_obj, table_b_obj)
            if tolerance_exclusion is not None:
                # Exclude rows that are only different due to float values within tolerance
                return and_(base_condition, ~tolerance_exclusion)

        return base_condition

    def _build_tolerance_exclusion(self, table_a_obj, table_b_obj):
        """Build condition to identify rows that should be excluded due to tolerance.

        A row is excluded if:
        - ALL non-float/non-geography columns are equal (NULL-safe), AND
        - ALL float columns WITHOUT tolerance are equal (NULL-safe), AND
        - ALL float columns WITH tolerance have abs_delta <= tolerance, AND
        - ALL geography columns WITHOUT tolerance are equal (ST_EQUALS), AND
        - ALL geography columns WITH tolerance have ST_DISTANCE <= tolerance

        Returns:
            SQLAlchemy expression, or None if no tolerance filtering needed
        """
        # Separate toleranced columns from non-toleranced ones
        float_cols_with_tolerance = []
        float_cols_without_tolerance = []
        geo_cols_with_tolerance = []
        geo_cols_without_tolerance = []

        for col in self._value_columns():
            if col.column_type == ColumnType.FLOAT:
                tolerance = self.tolerance_config.get_tolerance(col.name)
                if tolerance is not None:
                    float_cols_with_tolerance.append(col)
                else:
                    float_cols_without_tolerance.append(col)
            elif col.column_type == ColumnType.GEOGRAPHY:
                tolerance = self.tolerance_config.get_tolerance(col.name)
                if tolerance is not None:
                    geo_cols_with_tolerance.append(col)
                else:
                    geo_cols_without_tolerance.append(col)

        if not float_cols_with_tolerance and not geo_cols_with_tolerance:
            return None

        exclusion_conditions = []

        # Condition 1: All non-float/non-geography columns must be equal
        for col in self._value_columns():
            if col.column_type not in (ColumnType.FLOAT, ColumnType.GEOGRAPHY):
                col_a = table_a_obj.c[col.name]
                col_b = table_b_obj.c[col.name]
                exclusion_conditions.append(self._null_safe_equal(col_a, col_b))

        # Condition 2: All float columns WITHOUT tolerance must be equal
        for col in float_cols_without_tolerance:
            col_a = table_a_obj.c[col.name]
            col_b = table_b_obj.c[col.name]
            exclusion_conditions.append(self._null_safe_equal(col_a, col_b))

        # Condition 3: All float columns WITH tolerance must be within tolerance
        for col in float_cols_with_tolerance:
            tolerance = self.tolerance_config.get_tolerance(col.name)
            col_a = table_a_obj.c[col.name]
            col_b = table_b_obj.c[col.name]

            # abs_delta <= tolerance OR both are NULL
            # Only calculate ABS when both values are not NULL to avoid SQL errors
            within_tolerance_or_null = or_(
                and_(col_a.is_(None), col_b.is_(None)),
                and_(
                    col_a.isnot(None),
                    col_b.isnot(None),
                    literal_column(
                        f"ABS({self.alias_a}.{col.name} - {self.alias_b}.{col.name}) <= {tolerance}"
                    ),
                ),
            )
            exclusion_conditions.append(within_tolerance_or_null)

        # Condition 4: All geography columns WITHOUT tolerance must be equal
        for col in geo_cols_without_tolerance:
            exclusion_conditions.append(self._null_safe_geography_equal(col.name))

        # Condition 5: All geography columns WITH tolerance must be within tolerance
        # Note: ST_DISTANCE without use_spheroid because BQ rewrites <=
        # comparisons to ST_DWITHIN which does not support use_spheroid=TRUE.
        for col in geo_cols_with_tolerance:
            tolerance = self.tolerance_config.get_tolerance(col.name)
            a = f"{self.alias_a}.{col.name}"
            b = f"{self.alias_b}.{col.name}"
            # ST_DISTANCE <= tolerance OR both are NULL
            within_tolerance_or_null = literal_column(
                f"(({a} IS NULL AND {b} IS NULL) OR "
                f"({a} IS NOT NULL AND {b} IS NOT NULL AND "
                f"ST_DISTANCE({a}, {b}) <= {tolerance}))"
            )
            exclusion_conditions.append(within_tolerance_or_null)

        # Row should be excluded if ALL conditions are met
        return and_(*exclusion_conditions)

    def build_diff_query(self, apply_tolerance: bool = True) -> str:
        """
        Build the complete diff query using SQLAlchemy Core.

        Args:
            apply_tolerance: If True, apply tolerance-based filtering.
                           If False, include all differences (useful for statistics).

        Returns:
            SQL query string that will return all differing rows with their deltas.
        """
        table_a_obj, table_b_obj = self._create_table_objects()

        # Build join condition
        join_conditions = [table_a_obj.c[key] == table_b_obj.c[key] for key in self.key_columns]
        join_condition = and_(*join_conditions) if len(join_conditions) > 1 else join_conditions[0]

        # Build FULL OUTER JOIN
        full_join = table_a_obj.outerjoin(table_b_obj, onclause=join_condition, full=True)

        # Build SELECT statement
        select_cols = self._build_select_columns(table_a_obj, table_b_obj)

        # Build WHERE clause (optionally disable tolerance filtering)
        if apply_tolerance:
            where_clause = self._build_where_clause(table_a_obj, table_b_obj)
        else:
            # Build basic where clause without tolerance filtering
            where_clause = self._build_where_clause_no_tolerance(table_a_obj, table_b_obj)

        stmt = select(*select_cols).select_from(full_join).where(where_clause)

        # Compile to SQL string for BigQuery dialect
        return str(stmt.compile(dialect=BigQueryDialect(), compile_kwargs={"literal_binds": True}))

    def _build_where_clause_no_tolerance(self, table_a_obj, table_b_obj):
        """Build WHERE clause without tolerance filtering (for statistics)."""
        conditions = []

        # Check for differences in value columns
        for col in self._value_columns():
            if col.column_type == ColumnType.GEOGRAPHY:
                conditions.append(self._null_safe_geography_not_equal(col.name))
            else:
                col_a = table_a_obj.c[col.name]
                col_b = table_b_obj.c[col.name]
                conditions.append(self._null_safe_not_equal(col_a, col_b))

        # Include rows that exist in only one table
        first_key = self.key_columns[0]
        conditions.append(table_a_obj.c[first_key].is_(None))
        conditions.append(table_b_obj.c[first_key].is_(None))

        return or_(*conditions)

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

    def _backtick(self, table_ref: str) -> str:
        """Wrap a table reference in backticks for BigQuery."""
        return f"`{table_ref}`"

    def _table_source(self, table_ref: str, alias: str, partition_filter: str | None) -> str:
        """Return a FROM-clause fragment: backticked table or filtered subquery."""
        if partition_filter:
            cols = ", ".join(c.name for c in self._supported_columns())
            bt = self._backtick(table_ref)
            return f"(SELECT {cols} FROM {bt} WHERE {partition_filter}) AS {alias}"
        return f"{self._backtick(table_ref)} AS {alias}"

    def _l1_null_safe_eq(self, col_name: str) -> str:
        """Generate raw SQL NULL-safe equality for Layer 1 flags."""
        a = f"a.{col_name}"
        b = f"b.{col_name}"
        return (
            f"(({a} IS NOT NULL AND {b} IS NOT NULL AND {a} = {b}) "
            f"OR ({a} IS NULL AND {b} IS NULL))"
        )

    def _l1_geography_eq(self, col_name: str) -> str:
        """Generate raw SQL NULL-safe ST_EQUALS for Layer 1 geography flags."""
        a = f"a.{col_name}"
        b = f"b.{col_name}"
        return (
            f"(({a} IS NULL AND {b} IS NULL) "
            f"OR ({a} IS NOT NULL AND {b} IS NOT NULL AND ST_EQUALS({a}, {b})))"
        )

    def _join_keys(self) -> str:
        """Build the ON clause for FULL OUTER JOIN on key columns."""
        return " AND ".join(f"a.{k} = b.{k}" for k in self.key_columns)

    def _coalesced_keys(self) -> str:
        """Build COALESCE(a.key, b.key) AS key for each key column."""
        return ",\n  ".join(f"COALESCE(a.{k}, b.{k}) AS {k}" for k in self.key_columns)

    def build_pipeline_script(self, max_diff_pct: float = 0.1) -> str:
        """Build the 3-layer multi-statement SQL script.

        This generates a BigQuery scripting block that:
          1. Counts rows in each table (cheap, no join)
          2. Layer 1: Identifies non-identical rows with per-column equality flags
          3. Circuit breaker: aborts if diff % exceeds threshold
          4. Layer 2: Computes all deltas for non-identical rows (INNER JOIN)
          5. Layer 3: Aggregates all statistics into a single output row

        Args:
            max_diff_pct: Circuit breaker threshold as a fraction (0.1 = 10%).
                         If more than this fraction of rows differ, abort after Layer 1.

        Returns:
            A multi-statement SQL script string to execute as a single BQ job.
        """
        value_cols = self._value_columns()
        first_key = self.key_columns[0]

        # Build tolerance config for display
        tol_config: dict[str, float] = {}
        if self.tolerance_config:
            for col in value_cols:
                if col.column_type in (ColumnType.FLOAT, ColumnType.GEOGRAPHY):
                    tol = self.tolerance_config.get_tolerance(col.name)
                    if tol is not None:
                        tol_config[col.name] = tol

        # --- Preamble: all DECLARE statements first, then SET ---
        source_a = self._table_source(self.table_a, "t", self.partition_filter_a)
        source_b = self._table_source(self.table_b, "t", self.partition_filter_b)

        preamble = (
            "DECLARE total_rows_a INT64;\n"
            "DECLARE total_rows_b INT64;\n"
            "DECLARE rows_in_both_diff INT64;\n"
            f"SET total_rows_a = (SELECT COUNT(*) FROM {source_a});\n"
            f"SET total_rows_b = (SELECT COUNT(*) FROM {source_b});\n"
        )

        # --- Layer 1: non-identical rows with equality flags ---
        l1_select_parts = [self._coalesced_keys()]

        # Existence flags
        l1_select_parts.append(f"  a.{first_key} IS NOT NULL AS in_a")
        l1_select_parts.append(f"  b.{first_key} IS NOT NULL AS in_b")

        # Per-column equality flags
        for col in value_cols:
            if col.column_type == ColumnType.GEOGRAPHY:
                eq_expr = self._l1_geography_eq(col.name)
            else:
                eq_expr = self._l1_null_safe_eq(col.name)
            l1_select_parts.append(f"  {eq_expr} AS {col.name}__eq")

        l1_select = ",\n".join(l1_select_parts)

        # WHERE: at least one column differs OR row only in one table
        l1_where_parts = []
        for col in value_cols:
            if col.column_type == ColumnType.GEOGRAPHY:
                l1_where_parts.append(f"NOT {self._l1_geography_eq(col.name)}")
            else:
                l1_where_parts.append(f"NOT {self._l1_null_safe_eq(col.name)}")

        l1_where_parts.append(f"a.{first_key} IS NULL")
        l1_where_parts.append(f"b.{first_key} IS NULL")

        l1_where = "\n  OR ".join(l1_where_parts)

        source_a_join = self._table_source(self.table_a, "a", self.partition_filter_a)
        source_b_join = self._table_source(self.table_b, "b", self.partition_filter_b)

        l1_stmt = (
            "CREATE TEMP TABLE _l1 AS\n"
            "SELECT\n"
            f"{l1_select}\n"
            f"FROM {source_a_join}\n"
            f"FULL OUTER JOIN {source_b_join}\n"
            f"  ON {self._join_keys()}\n"
            f"WHERE\n  {l1_where};\n"
        )

        # --- Circuit breaker ---
        circuit_breaker = (
            "SET rows_in_both_diff = (SELECT COUNT(*) FROM _l1 WHERE in_a AND in_b);\n"
        )

        # --- Aborted branch: Layer 1 summary only ---
        abort_select_parts = [
            "  'ABORTED' AS pipeline_status",
            "  total_rows_a",
            "  total_rows_b",
            "  COUNTIF(in_a AND NOT in_b) AS rows_only_in_a",
            "  COUNTIF(NOT in_a AND in_b) AS rows_only_in_b",
            "  rows_in_both_diff AS rows_in_both_with_differences",
        ]
        for col in value_cols:
            abort_select_parts.append(
                f"  COUNTIF(in_a AND in_b AND NOT {col.name}__eq) AS {col.name}__diff_count"
            )
        abort_select = ",\n".join(abort_select_parts)
        abort_stmt = f"SELECT\n{abort_select}\nFROM _l1;\n"

        # --- Layer 2: compute deltas ---
        l2_select_parts = ["  l1.in_a", "  l1.in_b"]
        for k in self.key_columns:
            l2_select_parts.append(f"  l1.{k}")
        # Per-column equality flags passed through for Layer 3
        for col in value_cols:
            l2_select_parts.append(f"  l1.{col.name}__eq")

        for col in value_cols:
            a = f"a.{col.name}"
            b = f"b.{col.name}"

            if col.column_type in (ColumnType.INTEGER, ColumnType.FLOAT):
                l2_select_parts.extend(
                    [
                        f"  ({a} - {b}) AS {col.name}__delta",
                        f"  ABS({a} - {b}) AS {col.name}__abs_delta",
                        f"  SAFE_DIVIDE({a} - {b}, {b}) AS {col.name}__rel_delta",
                    ]
                )
                # within_tol for FLOAT columns with tolerance
                if col.column_type == ColumnType.FLOAT and col.name in tol_config:
                    tol = tol_config[col.name]
                    l2_select_parts.append(f"  ABS({a} - {b}) <= {tol} AS {col.name}__within_tol")

            elif col.column_type == ColumnType.BOOLEAN:
                ca = f"CAST({a} AS INT64)"
                cb = f"CAST({b} AS INT64)"
                l2_select_parts.extend(
                    [
                        f"  ({ca} - {cb}) AS {col.name}__delta",
                        f"  ABS({ca} - {cb}) AS {col.name}__abs_delta",
                        f"  SAFE_DIVIDE({ca} - {cb}, {cb}) AS {col.name}__rel_delta",
                    ]
                )

            elif col.column_type == ColumnType.TIMESTAMP:
                ts = f"TIMESTAMP_DIFF({a}, {b}, MICROSECOND)"
                l2_select_parts.extend(
                    [
                        f"  {ts} AS {col.name}__delta",
                        f"  ABS({ts}) AS {col.name}__abs_delta",
                        f"  SAFE_DIVIDE({ts}, UNIX_MICROS({b}))"
                        f" AS {col.name}__rel_delta",
                    ]
                )

            elif col.column_type == ColumnType.STRING:
                l2_select_parts.append(
                    f"  NOT {self._l1_null_safe_eq(col.name)} AS {col.name}__mismatch"
                )

            elif col.column_type == ColumnType.GEOGRAPHY:
                dist = f"IF({a} IS NOT NULL AND {b} IS NOT NULL, ST_DISTANCE({a}, {b}, TRUE), NULL)"
                l2_select_parts.append(f"  {dist} AS {col.name}__distance_m")
                if col.name in tol_config:
                    tol = tol_config[col.name]
                    # Use spherical (no use_spheroid) for tolerance to avoid ST_DWITHIN rewrite
                    l2_select_parts.append(
                        f"  IF({a} IS NOT NULL AND {b} IS NOT NULL, "
                        f"ST_DISTANCE({a}, {b}) <= {tol}, "
                        f"{a} IS NULL AND {b} IS NULL) AS {col.name}__within_tol"
                    )

        l2_select = ",\n".join(l2_select_parts)

        # Layer 2 joins non-identical rows back to source tables (INNER JOIN)
        l2_join_a = " AND ".join(f"l1.{k} = a.{k}" for k in self.key_columns)
        l2_join_b = " AND ".join(f"l1.{k} = b.{k}" for k in self.key_columns)

        l2_stmt = (
            "CREATE TEMP TABLE _l2 AS\n"
            "SELECT\n"
            f"{l2_select}\n"
            "FROM _l1 l1\n"
            f"JOIN {source_a_join}\n"
            f"  ON {l2_join_a}\n"
            f"JOIN {source_b_join}\n"
            f"  ON {l2_join_b}\n"
            "WHERE l1.in_a AND l1.in_b;\n"
        )

        # --- Layer 3: final output ---
        # L1 summary subquery
        l1_summary_parts = [
            "  COUNTIF(in_a AND NOT in_b) AS rows_only_in_a",
            "  COUNTIF(NOT in_a AND in_b) AS rows_only_in_b",
            "  rows_in_both_diff AS rows_in_both_with_differences",
        ]
        for col in value_cols:
            l1_summary_parts.append(
                f"  COUNTIF(in_a AND in_b AND NOT {col.name}__eq) AS {col.name}__diff_count"
            )
        l1_summary = ",\n".join(l1_summary_parts)

        # L3 stats subquery (from _l2)
        l3_stats_parts = [
            "  COUNT(*) AS total_differing_rows",
        ]

        # Columns with tolerance (for post-tolerance aggregation)
        tol_col_within_flags = []

        for col in value_cols:
            numeric_types = (
                ColumnType.INTEGER,
                ColumnType.FLOAT,
                ColumnType.BOOLEAN,
                ColumnType.TIMESTAMP,
            )
            if col.column_type in numeric_types:
                l3_stats_parts.extend(
                    [
                        f"  MAX({col.name}__abs_delta) AS {col.name}__max_abs_delta",
                        f"  MAX(ABS({col.name}__rel_delta)) AS {col.name}__max_rel_delta",
                        f"  AVG({col.name}__abs_delta) AS {col.name}__avg_abs_delta",
                        f"  SUM(ABS({col.name}__rel_delta)) AS {col.name}__sum_abs_rel_delta",
                    ]
                )
                if col.column_type == ColumnType.FLOAT and col.name in tol_config:
                    l3_stats_parts.extend(
                        [
                            f"  COUNTIF({col.name}__within_tol) AS {col.name}__within_tol_count",
                            f"  COUNTIF(NOT {col.name}__within_tol)"
                            f" AS {col.name}__outside_tol_count",
                        ]
                    )
                    tol_col_within_flags.append(f"{col.name}__within_tol")

            elif col.column_type == ColumnType.STRING:
                l3_stats_parts.append(f"  COUNTIF({col.name}__mismatch) AS {col.name}__mismatches")

            elif col.column_type == ColumnType.GEOGRAPHY:
                l3_stats_parts.extend(
                    [
                        f"  MAX({col.name}__distance_m) AS {col.name}__max_distance_m",
                        f"  AVG({col.name}__distance_m) AS {col.name}__avg_distance_m",
                    ]
                )
                if col.name in tol_config:
                    l3_stats_parts.extend(
                        [
                            f"  COUNTIF({col.name}__within_tol) AS {col.name}__within_tol_count",
                            f"  COUNTIF(NOT {col.name}__within_tol)"
                            f" AS {col.name}__outside_tol_count",
                        ]
                    )
                    tol_col_within_flags.append(f"{col.name}__within_tol")

        # Post-tolerance diff count: rows where NOT all toleranced columns are within tol
        if tol_col_within_flags:
            all_within = " AND ".join(tol_col_within_flags)
            l3_stats_parts.append(f"  COUNTIF(NOT ({all_within})) AS post_tol_diff_count")

        l3_stats = ",\n".join(l3_stats_parts)

        l3_stmt = (
            "SELECT\n"
            "  'COMPLETED' AS pipeline_status,\n"
            "  total_rows_a,\n"
            "  total_rows_b,\n"
            "  l1_summary.*,\n"
            "  l3_stats.*\n"
            "FROM (\n"
            f"  SELECT\n  {l1_summary}\n  FROM _l1\n"
            ") l1_summary\n"
            "CROSS JOIN (\n"
            f"  SELECT\n  {l3_stats}\n  FROM _l2\n"
            ") l3_stats;\n"
        )

        # --- Assemble the full script ---
        script = (
            f"{preamble}\n"
            f"{l1_stmt}\n"
            f"{circuit_breaker}\n"
            f"IF rows_in_both_diff > GREATEST(total_rows_a, total_rows_b) * {max_diff_pct} THEN\n"
            f"  {abort_stmt}\n"
            "ELSE\n"
            f"  {l2_stmt}\n"
            f"  {l3_stmt}\n"
            "END IF;\n"
        )

        return script
