"""Tolerance configuration for float comparison filtering."""

from dataclasses import dataclass, field


@dataclass
class ToleranceConfig:
    """Configuration for tolerance-based filtering of float comparisons.

    Tolerance is applied to the absolute delta (abs_delta) of float columns.
    Rows where ALL float columns have abs_delta <= tolerance are excluded from diff.

    Attributes:
        global_tolerance: Default tolerance applied to all float columns
        column_tolerances: Column-specific tolerances that override global
    """

    global_tolerance: float | None = None
    column_tolerances: dict[str, float] = field(default_factory=dict)

    def get_tolerance(self, column_name: str) -> float | None:
        """Get tolerance for a specific column.

        Args:
            column_name: Name of the column

        Returns:
            Tolerance value for the column, or None if no tolerance configured
        """
        # Column-specific tolerance takes precedence over global
        if column_name in self.column_tolerances:
            return self.column_tolerances[column_name]
        return self.global_tolerance

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
