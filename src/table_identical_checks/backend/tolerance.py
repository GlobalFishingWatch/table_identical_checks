"""Tolerance configuration for float comparison filtering."""

from dataclasses import dataclass, field


@dataclass
class ToleranceConfig:
    """Configuration for tolerance-based filtering of float comparisons.

    Supports both absolute and relative tolerance:
    - Absolute: rows where ABS(a - b) <= tolerance
    - Relative: rows where ABS(a - b) / GREATEST(ABS(a), ABS(b)) <= rel_tolerance

    When both are configured, a value is within tolerance if EITHER condition holds.
    Rows where ALL toleranced columns are within tolerance are excluded from diff.

    Attributes:
        global_tolerance: Default absolute tolerance applied to all float columns
        column_tolerances: Column-specific absolute tolerances that override global
        global_rel_tolerance: Default relative tolerance applied to all float columns
        column_rel_tolerances: Column-specific relative tolerances that override global
    """

    global_tolerance: float | None = None
    column_tolerances: dict[str, float] = field(default_factory=dict)
    global_rel_tolerance: float | None = None
    column_rel_tolerances: dict[str, float] = field(default_factory=dict)

    def get_tolerance(self, column_name: str) -> float | None:
        """Get absolute tolerance for a specific column.

        Args:
            column_name: Name of the column

        Returns:
            Absolute tolerance value for the column, or None if not configured
        """
        if column_name in self.column_tolerances:
            return self.column_tolerances[column_name]
        return self.global_tolerance

    def get_rel_tolerance(self, column_name: str) -> float | None:
        """Get relative tolerance for a specific column.

        Args:
            column_name: Name of the column

        Returns:
            Relative tolerance value for the column, or None if not configured
        """
        if column_name in self.column_rel_tolerances:
            return self.column_rel_tolerances[column_name]
        return self.global_rel_tolerance

    def has_any_tolerance(self, column_name: str) -> bool:
        """Check if a column has any tolerance configured (absolute or relative)."""
        return (
            self.get_tolerance(column_name) is not None
            or self.get_rel_tolerance(column_name) is not None
        )

    @classmethod
    def parse(cls, tolerance_str: str | None) -> "ToleranceConfig":
        """Parse tolerance string from CLI argument.

        Args:
            tolerance_str: Tolerance specification. Can be:
                - None: No tolerance filtering
                - "1e-9": Global tolerance for all float columns
                - "col1:1e-9,col2:1e-6": Per-column tolerances

        Returns:
            ToleranceConfig instance

        Raises:
            ValueError: If tolerance string is malformed or invalid

        Examples:
            >>> ToleranceConfig.parse(None)
            ToleranceConfig(global_tolerance=None, column_tolerances={})

            >>> ToleranceConfig.parse("1e-9")
            ToleranceConfig(global_tolerance=1e-9, column_tolerances={})

            >>> ToleranceConfig.parse("float_a:1e-9,float_b:1e-6")
            ToleranceConfig(global_tolerance=None,
                          column_tolerances={'float_a': 1e-9, 'float_b': 1e-6})
        """
        if not tolerance_str:
            return cls()

        # Check if it's global tolerance (no colons)
        if ":" not in tolerance_str:
            try:
                global_tol = float(tolerance_str)
                return cls(global_tolerance=global_tol)
            except ValueError as e:
                raise ValueError(
                    f"Invalid tolerance value: '{tolerance_str}'. Expected a number (e.g., '1e-9')."
                ) from e

        # Parse per-column tolerances
        column_tolerances = {}
        for part in tolerance_str.split(","):
            part = part.strip()
            if not part:
                continue

            if ":" not in part:
                raise ValueError(
                    f"Invalid tolerance format: '{part}'. "
                    f"Expected 'column:value' (e.g., 'float_a:1e-9')."
                )

            split_parts = part.split(":")
            if len(split_parts) != 2:
                raise ValueError(
                    f"Invalid tolerance format: '{part}'. "
                    f"Expected exactly one colon separating column and value."
                )

            col_name, tol_str = split_parts
            col_name = col_name.strip()
            tol_str = tol_str.strip()

            try:
                tol_value = float(tol_str)
            except ValueError as e:
                raise ValueError(
                    f"Invalid tolerance value for column '{col_name}': '{tol_str}'. "
                    f"Expected a number (e.g., '1e-9')."
                ) from e

            column_tolerances[col_name] = tol_value

        return cls(column_tolerances=column_tolerances)

    @classmethod
    def parse_rel(cls, rel_tolerance_str: str | None) -> "ToleranceConfig":
        """Parse relative tolerance string from CLI argument.

        Same format as parse() but populates relative tolerance fields.
        """
        if not rel_tolerance_str:
            return cls()

        if ":" not in rel_tolerance_str:
            try:
                global_rel_tol = float(rel_tolerance_str)
                return cls(global_rel_tolerance=global_rel_tol)
            except ValueError as e:
                raise ValueError(
                    f"Invalid relative tolerance value: '{rel_tolerance_str}'. "
                    f"Expected a number (e.g., '1e-12')."
                ) from e

        column_rel_tolerances = {}
        for part in rel_tolerance_str.split(","):
            part = part.strip()
            if not part:
                continue

            if ":" not in part:
                raise ValueError(
                    f"Invalid relative tolerance format: '{part}'. "
                    f"Expected 'column:value' (e.g., 'float_a:1e-12')."
                )

            split_parts = part.split(":")
            if len(split_parts) != 2:
                raise ValueError(
                    f"Invalid relative tolerance format: '{part}'. "
                    f"Expected exactly one colon separating column and value."
                )

            col_name, tol_str = split_parts
            col_name = col_name.strip()
            tol_str = tol_str.strip()

            try:
                tol_value = float(tol_str)
            except ValueError as e:
                raise ValueError(
                    f"Invalid relative tolerance value for column '{col_name}': '{tol_str}'. "
                    f"Expected a number (e.g., '1e-12')."
                ) from e

            column_rel_tolerances[col_name] = tol_value

        return cls(column_rel_tolerances=column_rel_tolerances)

    def merge(self, other: "ToleranceConfig") -> "ToleranceConfig":
        """Merge two ToleranceConfig instances.

        Combines absolute and relative tolerance settings. Values from `other`
        take precedence for overlapping column-specific settings.
        """
        return ToleranceConfig(
            global_tolerance=self.global_tolerance or other.global_tolerance,
            column_tolerances={**self.column_tolerances, **other.column_tolerances},
            global_rel_tolerance=self.global_rel_tolerance or other.global_rel_tolerance,
            column_rel_tolerances={
                **self.column_rel_tolerances,
                **other.column_rel_tolerances,
            },
        )
