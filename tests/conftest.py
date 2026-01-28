"""Shared test fixtures for BigQuery testing."""

import os
import uuid

import pytest
from google.cloud import bigquery

# Test configuration
TEST_PROJECT = "world-fishing-827"
TEST_DATASET = "tech_great_expectations"


@pytest.fixture(scope="session")
def bq_client():
    """Create a BigQuery client using the SA credentials."""
    # Look for sa.json in project root
    sa_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sa.json")
    if os.path.exists(sa_path):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa_path

    return bigquery.Client(project=TEST_PROJECT)


@pytest.fixture(scope="session")
def test_dataset():
    """Return the test dataset reference."""
    return f"{TEST_PROJECT}.{TEST_DATASET}"


def create_temp_table(
    client: bigquery.Client,
    dataset: str,
    schema: list[bigquery.SchemaField],
    rows: list[dict],
) -> str:
    """
    Create a temporary table with the given schema and data.

    Returns the fully qualified table ID.
    """
    table_id = f"{dataset}._test_{uuid.uuid4().hex[:8]}"
    table = bigquery.Table(table_id, schema=schema)

    client.create_table(table)

    if rows:
        errors = client.insert_rows_json(table_id, rows)
        if errors:
            raise RuntimeError(f"Failed to insert rows: {errors}")

    return table_id


def delete_table(client: bigquery.Client, table_id: str) -> None:
    """Delete a table."""
    client.delete_table(table_id, not_found_ok=True)


@pytest.fixture
def table_factory(bq_client, test_dataset):
    """
    Factory fixture for creating temporary test tables.

    Tables are automatically cleaned up after the test.
    """
    created_tables = []

    def _create(schema: list[bigquery.SchemaField], rows: list[dict]) -> str:
        table_id = create_temp_table(bq_client, test_dataset, schema, rows)
        created_tables.append(table_id)
        return table_id

    yield _create

    # Cleanup
    for table_id in created_tables:
        delete_table(bq_client, table_id)
