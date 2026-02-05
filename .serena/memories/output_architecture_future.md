# Future Output Architecture

## Three-Stage Output Strategy

The user has clarified the intended output architecture for table comparisons. This is NOT YET IMPLEMENTED but should guide future development:

### Stage 1: SetDiff Table (1h TTL)
- **Persisted BQ table** with 1-hour TTL
- Contains ALL rows from both tables that are not 100% identical
- This is essentially the raw diff before any delta calculations
- Should include rows that:
  - Exist only in table A
  - Exist only in table B
  - Exist in both but have ANY column difference

### Stage 2: Delta Table (24h TTL)
- **Persisted BQ table** with 24-hour TTL
- Based on setdiff, matched by common key (e.g., msgid)
- Calculates delta and rel_delta columns for each column:
  - For numeric: `(new - previous) / previous` (SAFE_DIVIDE for NULL/zero handling)
  - For non-numeric: TBD (need to define later)
- This adds the analytical layer on top of the raw diff

### Stage 3: Summary Table (In-Memory)
- Current implementation is acceptable
- Aggregated statistics over the delta table
- Summary stats per column (min, max, avg, etc.)
- Configurable and extensible in the future
- Only this stage needs to fit in memory

## Current State vs. Target

**Current Implementation:**
- Our diff query combines stages 1 & 2 (setdiff + deltas in one query)
- Results are ephemeral (not persisted)
- Summary stage works as described (in-memory)

**Migration Path:**
1. Keep current implementation working
2. Add option to persist stage 1 (setdiff) to BQ table
3. Add option to persist stage 2 (delta) to BQ table  
4. Make stages configurable via CLI args (--persist-diff, --persist-deltas, --ttl-hours)
5. Summary should work with either ephemeral or persisted delta tables

## Benefits of Persisted Tables
- Large comparisons can be explored incrementally
- Delta table can be queried directly with SQL
- Different users can analyze the same comparison
- TTLs ensure cleanup of temporary comparison data
- Enables iterative analysis without re-running expensive comparisons

## Implemented (2025-01)

The `--output-table` option on `diff` command now implements **Stage 2** (Delta Table):
- `--output-table=project.dataset.table` persists diff to BQ
- `--write-mode=replace` (default) or `if_not_exists`
- `--expiration-hours=N` sets TTL
- `--only-diffs` + `--output-table` persists a focused diff with only differing columns

**Stage 1 (SetDiff)** is not separately implemented; the current approach combines setdiff + deltas.

## Planned Improvements

### Diff Output UX Enhancements
1. **ORDER BY in_a AND in_b DESC**: Prioritize showing actual value differences before existence-only rows
2. **Summary column**: Add a column indicating which columns differ per row, or a label like "only_in_a", "only_in_b", "value_diff(col1,col2)"
3. This will make the `--only-diffs` output more informative when both value diffs and existence diffs are present
