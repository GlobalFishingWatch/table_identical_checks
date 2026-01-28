"""Tests for string column comparisons."""

from google.cloud import bigquery

from table_identical_checks.backend import QueryBuilder, get_table_schema

# Schema for string test tables
STRING_SCHEMA = [
    bigquery.SchemaField("id", "INT64"),
    bigquery.SchemaField("name", "STRING"),
    bigquery.SchemaField("category", "STRING"),
]


class TestIdenticalStringTables:
    """Test cases where string tables are identical."""

    def test_identical_string_values(self, bq_client, table_factory):
        """Two tables with identical string values should have no differences."""
        rows = [
            {"id": 1, "name": "Alice", "category": "A"},
            {"id": 2, "name": "Bob", "category": "B"},
        ]

        table_a = table_factory(STRING_SCHEMA, rows)
        table_b = table_factory(STRING_SCHEMA, rows)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
        )

        query = builder.build_count_query()
        result = bq_client.query(query).result()
        count = list(result)[0].diff_count

        assert count == 0

    def test_identical_empty_strings(self, bq_client, table_factory):
        """Empty strings should match."""
        rows = [
            {"id": 1, "name": "", "category": ""},
        ]

        table_a = table_factory(STRING_SCHEMA, rows)
        table_b = table_factory(STRING_SCHEMA, rows)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
        )

        query = builder.build_count_query()
        result = bq_client.query(query).result()
        count = list(result)[0].diff_count

        assert count == 0


class TestDifferentStringTables:
    """Test cases where string tables have differences."""

    def test_different_string_values(self, bq_client, table_factory):
        """Tables with different string values should report differences."""
        rows_a = [
            {"id": 1, "name": "Alice", "category": "A"},
        ]
        rows_b = [
            {"id": 1, "name": "Alicia", "category": "A"},  # Different name
        ]

        table_a = table_factory(STRING_SCHEMA, rows_a)
        table_b = table_factory(STRING_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
        )

        query = builder.build_diff_query()
        result = bq_client.query(query).result()
        rows = list(result)

        assert len(rows) == 1
        row = rows[0]
        assert row.a__name == "Alice"
        assert row.b__name == "Alicia"
        assert row.name__match is False
        assert row.category__match is True

    def test_case_sensitive_comparison(self, bq_client, table_factory):
        """String comparison should be case-sensitive."""
        rows_a = [
            {"id": 1, "name": "Alice", "category": "A"},
        ]
        rows_b = [
            {"id": 1, "name": "alice", "category": "A"},  # Different case
        ]

        table_a = table_factory(STRING_SCHEMA, rows_a)
        table_b = table_factory(STRING_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
        )

        query = builder.build_count_query()
        result = bq_client.query(query).result()
        count = list(result)[0].diff_count

        # Case difference should be detected
        assert count == 1

    def test_whitespace_differences(self, bq_client, table_factory):
        """Whitespace differences should be detected."""
        rows_a = [
            {"id": 1, "name": "Alice", "category": "A"},
        ]
        rows_b = [
            {"id": 1, "name": "Alice ", "category": "A"},  # Trailing space
        ]

        table_a = table_factory(STRING_SCHEMA, rows_a)
        table_b = table_factory(STRING_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
        )

        query = builder.build_count_query()
        result = bq_client.query(query).result()
        count = list(result)[0].diff_count

        # Whitespace difference should be detected
        assert count == 1


class TestStringNullValues:
    """Test cases for NULL string handling."""

    def test_null_string_equals_null(self, bq_client, table_factory):
        """NULL string values should be considered equal."""
        rows = [
            {"id": 1, "name": None, "category": "A"},
        ]

        table_a = table_factory(STRING_SCHEMA, rows)
        table_b = table_factory(STRING_SCHEMA, rows)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
        )

        query = builder.build_count_query()
        result = bq_client.query(query).result()
        count = list(result)[0].diff_count

        assert count == 0

    def test_null_vs_empty_string(self, bq_client, table_factory):
        """NULL and empty string should be different."""
        rows_a = [
            {"id": 1, "name": None, "category": "A"},
        ]
        rows_b = [
            {"id": 1, "name": "", "category": "A"},
        ]

        table_a = table_factory(STRING_SCHEMA, rows_a)
        table_b = table_factory(STRING_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
        )

        query = builder.build_count_query()
        result = bq_client.query(query).result()
        count = list(result)[0].diff_count

        # NULL != empty string
        assert count == 1


class TestCompositeKeys:
    """Test cases with composite key columns."""

    def test_composite_key_matching(self, bq_client, table_factory):
        """Tables should match correctly on composite keys."""
        rows = [
            {"id": 1, "name": "Alice", "category": "A"},
            {"id": 1, "name": "Bob", "category": "B"},  # Same id, different name as key
            {"id": 2, "name": "Alice", "category": "C"},  # Same name, different id as key
        ]

        # Use id and name as composite key
        table_a = table_factory(STRING_SCHEMA, rows)
        table_b = table_factory(STRING_SCHEMA, rows)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id", "name"],  # Composite key
            columns=columns,
        )

        query = builder.build_count_query()
        result = bq_client.query(query).result()
        count = list(result)[0].diff_count

        assert count == 0

    def test_composite_key_difference(self, bq_client, table_factory):
        """Differences should be detected with composite keys."""
        rows_a = [
            {"id": 1, "name": "Alice", "category": "A"},
        ]
        rows_b = [
            {"id": 1, "name": "Alice", "category": "B"},  # Different category
        ]

        table_a = table_factory(STRING_SCHEMA, rows_a)
        table_b = table_factory(STRING_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id", "name"],
            columns=columns,
        )

        query = builder.build_diff_query()
        result = bq_client.query(query).result()
        rows = list(result)

        assert len(rows) == 1
        row = rows[0]
        assert row.id == 1
        assert row.name == "Alice"
        assert row.a__category == "A"
        assert row.b__category == "B"
