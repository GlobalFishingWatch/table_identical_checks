"""Tests for GEOGRAPHY column comparison support."""

import pytest
from google.cloud import bigquery

from table_identical_checks.backend import QueryBuilder, get_table_schema
from table_identical_checks.backend.tolerance import ToleranceConfig

# Schema for geography test tables
GEOGRAPHY_SCHEMA = [
    bigquery.SchemaField("id", "INT64"),
    bigquery.SchemaField("location", "GEOGRAPHY"),
]

GEOGRAPHY_MIXED_SCHEMA = [
    bigquery.SchemaField("id", "INT64"),
    bigquery.SchemaField("location", "GEOGRAPHY"),
    bigquery.SchemaField("name", "STRING"),
    bigquery.SchemaField("value", "FLOAT64"),
]

# Well-known points for testing (WKT format for BigQuery JSON insert)
# London: POINT(-0.1278 51.5074)
# Paris: POINT(2.3522 48.8566)
# Distance London-Paris ~ 343.5 km (343,500 m)
# Small offset: POINT(-0.1279 51.5074) ~ 7m from London


class TestGeographyIdentical:
    """Test cases where geography columns are identical."""

    def test_identical_geography_values(self, bq_client, table_factory):
        """Two tables with identical geography values should have no differences."""
        rows = [
            {"id": 1, "location": "POINT(-0.1278 51.5074)"},
            {"id": 2, "location": "POINT(2.3522 48.8566)"},
        ]

        table_a = table_factory(GEOGRAPHY_SCHEMA, rows)
        table_b = table_factory(GEOGRAPHY_SCHEMA, rows)

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


class TestGeographyDifferences:
    """Test cases where geography columns have differences."""

    def test_different_geography_values(self, bq_client, table_factory):
        """Tables with different geography values should report differences."""
        rows_a = [
            {"id": 1, "location": "POINT(-0.1278 51.5074)"},  # London
        ]
        rows_b = [
            {"id": 1, "location": "POINT(2.3522 48.8566)"},  # Paris
        ]

        table_a = table_factory(GEOGRAPHY_SCHEMA, rows_a)
        table_b = table_factory(GEOGRAPHY_SCHEMA, rows_b)

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
        assert row.id == 1
        # Distance London to Paris should be approximately 343 km
        assert row.location__distance_meters > 340_000
        assert row.location__distance_meters < 350_000

    def test_small_geography_difference(self, bq_client, table_factory):
        """Small geographic differences should be detected and measured."""
        rows_a = [
            {"id": 1, "location": "POINT(-0.1278 51.5074)"},
        ]
        rows_b = [
            # Shift by ~0.0001 degrees longitude ~ about 7 meters
            {"id": 1, "location": "POINT(-0.1279 51.5074)"},
        ]

        table_a = table_factory(GEOGRAPHY_SCHEMA, rows_a)
        table_b = table_factory(GEOGRAPHY_SCHEMA, rows_b)

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
        # Small shift should be a few meters
        assert row.location__distance_meters > 0
        assert row.location__distance_meters < 20  # Well under 20m

    def test_geography_with_text_output(self, bq_client, table_factory):
        """Geography values should be returned as WKT text."""
        rows_a = [
            {"id": 1, "location": "POINT(-0.1278 51.5074)"},
        ]
        rows_b = [
            {"id": 1, "location": "POINT(2.3522 48.8566)"},
        ]

        table_a = table_factory(GEOGRAPHY_SCHEMA, rows_a)
        table_b = table_factory(GEOGRAPHY_SCHEMA, rows_b)

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
        # ST_ASTEXT should return WKT strings
        assert "POINT" in row.a__location
        assert "POINT" in row.b__location


class TestGeographyNullHandling:
    """Test NULL handling with geography columns."""

    def test_null_geography_equals_null(self, bq_client, table_factory):
        """NULL geography in both tables should be considered equal."""
        rows = [
            {"id": 1, "location": None},
        ]

        table_a = table_factory(GEOGRAPHY_SCHEMA, rows)
        table_b = table_factory(GEOGRAPHY_SCHEMA, rows)

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

    def test_null_vs_value_geography(self, bq_client, table_factory):
        """NULL vs a geography value should be detected as different."""
        rows_a = [
            {"id": 1, "location": None},
        ]
        rows_b = [
            {"id": 1, "location": "POINT(0 0)"},
        ]

        table_a = table_factory(GEOGRAPHY_SCHEMA, rows_a)
        table_b = table_factory(GEOGRAPHY_SCHEMA, rows_b)

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
        # Distance should be NULL when one side is NULL
        assert row.location__distance_meters is None

    def test_value_vs_null_geography(self, bq_client, table_factory):
        """A geography value vs NULL should be detected as different."""
        rows_a = [
            {"id": 1, "location": "POINT(0 0)"},
        ]
        rows_b = [
            {"id": 1, "location": None},
        ]

        table_a = table_factory(GEOGRAPHY_SCHEMA, rows_a)
        table_b = table_factory(GEOGRAPHY_SCHEMA, rows_b)

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

        assert count == 1


class TestGeographyWithTolerance:
    """Test geography comparison with distance-based tolerance."""

    def test_within_tolerance_excluded(self, bq_client, table_factory):
        """Geography differences within tolerance (meters) should be excluded."""
        rows_a = [
            {"id": 1, "location": "POINT(-0.1278 51.5074)"},
        ]
        rows_b = [
            # Shift by ~0.0001 degrees longitude ~ about 7 meters
            {"id": 1, "location": "POINT(-0.1279 51.5074)"},
        ]

        table_a = table_factory(GEOGRAPHY_SCHEMA, rows_a)
        table_b = table_factory(GEOGRAPHY_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        # Set tolerance to 100 meters (the difference is ~7m)
        tolerance_config = ToleranceConfig.parse("location:100")
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
            tolerance_config=tolerance_config,
        )

        query = builder.build_count_query()
        result = bq_client.query(query).result()
        count = list(result)[0].diff_count

        # Should be excluded since ~7m < 100m tolerance
        assert count == 0

    def test_exceeds_tolerance_included(self, bq_client, table_factory):
        """Geography differences exceeding tolerance should be included."""
        rows_a = [
            {"id": 1, "location": "POINT(-0.1278 51.5074)"},  # London
        ]
        rows_b = [
            {"id": 1, "location": "POINT(2.3522 48.8566)"},  # Paris
        ]

        table_a = table_factory(GEOGRAPHY_SCHEMA, rows_a)
        table_b = table_factory(GEOGRAPHY_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        # Set tolerance to 1000 meters (the distance is ~343 km)
        tolerance_config = ToleranceConfig.parse("location:1000")
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
            tolerance_config=tolerance_config,
        )

        query = builder.build_diff_query()
        result = bq_client.query(query).result()
        rows = list(result)

        # Should be included since distance >> tolerance
        assert len(rows) == 1

    def test_null_geography_with_tolerance(self, bq_client, table_factory):
        """NULL vs value should still be detected even with tolerance configured."""
        rows_a = [
            {"id": 1, "location": None},
        ]
        rows_b = [
            {"id": 1, "location": "POINT(0 0)"},
        ]

        table_a = table_factory(GEOGRAPHY_SCHEMA, rows_a)
        table_b = table_factory(GEOGRAPHY_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        tolerance_config = ToleranceConfig.parse("location:100")
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
            tolerance_config=tolerance_config,
        )

        query = builder.build_count_query()
        result = bq_client.query(query).result()
        count = list(result)[0].diff_count

        # NULL vs value should always be different, regardless of tolerance
        assert count == 1


class TestGeographyMixed:
    """Test geography columns alongside other column types."""

    def test_geography_with_other_columns(self, bq_client, table_factory):
        """Geography diffs should work alongside numeric and string columns."""
        rows_a = [
            {"id": 1, "location": "POINT(0 0)", "name": "origin", "value": 1.0},
            {"id": 2, "location": "POINT(1 1)", "name": "point_a", "value": 2.0},
        ]
        rows_b = [
            {"id": 1, "location": "POINT(0 0)", "name": "origin", "value": 1.0},
            {"id": 2, "location": "POINT(1 1)", "name": "point_b", "value": 2.5},
        ]

        table_a = table_factory(GEOGRAPHY_MIXED_SCHEMA, rows_a)
        table_b = table_factory(GEOGRAPHY_MIXED_SCHEMA, rows_b)

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

        # Only id=2 should be different (name and value differ)
        assert len(rows) == 1
        row = rows[0]
        assert row.id == 2
        # Geography is identical, so distance should be 0
        assert row.location__distance_meters == pytest.approx(0.0)

    def test_geography_tolerance_with_other_differences(self, bq_client, table_factory):
        """Row with geography within tolerance but other columns different stays."""
        rows_a = [
            {"id": 1, "location": "POINT(-0.1278 51.5074)", "name": "a", "value": 1.0},
        ]
        rows_b = [
            {
                "id": 1,
                "location": "POINT(-0.1279 51.5074)",  # ~7m offset
                "name": "b",  # different name
                "value": 1.0,
            },
        ]

        table_a = table_factory(GEOGRAPHY_MIXED_SCHEMA, rows_a)
        table_b = table_factory(GEOGRAPHY_MIXED_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        tolerance_config = ToleranceConfig.parse("location:100")
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
            tolerance_config=tolerance_config,
        )

        query = builder.build_diff_query()
        result = bq_client.query(query).result()
        rows = list(result)

        # Should be included because name differs, even though geography is within tolerance
        assert len(rows) == 1


class TestGeographySummary:
    """Test geography statistics in summary output."""

    def test_summary_includes_geography_stats(self, bq_client, table_factory):
        """Summary should include geography distance statistics."""
        from table_identical_checks.backend.summary import generate_summary

        rows_a = [
            {"id": 1, "location": "POINT(-0.1278 51.5074)"},  # London
        ]
        rows_b = [
            {"id": 1, "location": "POINT(2.3522 48.8566)"},  # Paris
        ]

        table_a = table_factory(GEOGRAPHY_SCHEMA, rows_a)
        table_b = table_factory(GEOGRAPHY_SCHEMA, rows_b)

        columns = get_table_schema(bq_client, table_a)
        builder = QueryBuilder(
            table_a=table_a,
            table_b=table_b,
            key_columns=["id"],
            columns=columns,
        )

        summary = generate_summary(client=bq_client, builder=builder)

        # Should have geography stats
        assert "location" in summary.geography_column_stats
        geo_stats = summary.geography_column_stats["location"]
        assert "max_distance_meters" in geo_stats
        assert "avg_distance_meters" in geo_stats
        # Distance London-Paris ~343km
        assert geo_stats["max_distance_meters"] > 340_000
        assert geo_stats["max_distance_meters"] < 350_000
