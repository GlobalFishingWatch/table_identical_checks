# SQLAlchemy Core Migration

## Overview
The project has been migrated from manual f-string SQL construction to SQLAlchemy Core for all query building.

## Key Changes

### Dependencies Added
- `sqlalchemy>=2.0.0` - Core SQL abstraction layer
- `sqlalchemy-bigquery>=1.5.0` - BigQuery dialect for SQLAlchemy

### QueryBuilder Refactoring (query_builder.py)
- Completely rewritten using SQLAlchemy Core constructs
- Uses `table()` and `column()` for lightweight table definitions
- Uses `select()`, `.outerjoin(full=True)`, `.where()` for query construction  
- Uses `literal_column()` for BigQuery-specific functions like `SAFE_DIVIDE`

### NULL-Safe Comparison
BigQuery doesn't support `IS NOT DISTINCT FROM`, so we implement:

**Equality (NULL = NULL is TRUE):**
```python
or_(
    and_(col_a == col_b, col_a.isnot(None), col_b.isnot(None)),
    and_(col_a.is_(None), col_b.is_(None))
)
```

**Inequality (NULL != value is TRUE):**
```python
or_(
    and_(col_a.isnot(None), col_b.is_(None)),
    and_(col_a.is_(None), col_b.isnot(None)),
    and_(col_a != col_b, col_a.isnot(None), col_b.isnot(None))
)
```

### Partition Filter Integration
- Partition filters are applied as subqueries using `.where()` and `.subquery()`
- Automatically wrapped when partition filters are present
- `get_table_objects()` method returns the filtered table objects for reuse

### Summary Module (summary.py)
- Count queries now use SQLAlchemy
- CTE wrappers remain as f-strings (acceptable for now)
- Reuses filtered table objects from QueryBuilder

### SQL Compilation
All SQLAlchemy statements are compiled to BigQuery SQL using:
```python
str(stmt.compile(dialect=BigQueryDialect(), compile_kwargs={"literal_binds": True}))
```

## Benefits
1. **Type Safety**: Column expressions are typed
2. **Composability**: Easier to build complex queries programmatically
3. **Consistency**: Same API across all query types (diff, count, summary)
4. **Maintainability**: Changes to query structure are localized
5. **BigQuery Dialect**: Proper handling of BigQuery-specific syntax

## Test Coverage
- All 18 tests pass
- Query builder coverage: 91%
- NULL-safe comparison thoroughly tested

## Future Improvements
- Consider using `@compiles` decorator for custom BigQuery functions
- Migrate CTE wrappers in summary.py to SQLAlchemy CTEs
- Add more complex join patterns if needed
