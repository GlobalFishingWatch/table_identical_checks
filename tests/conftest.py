"""Shared test fixtures for BigQuery testing.

The BQ-integration tests need a GCP project and a sandbox dataset where the
test fixtures can create and delete short-lived tables. Override the
defaults via the ``TABLE_CHECK_TEST_PROJECT`` and ``TABLE_CHECK_TEST_DATASET``
environment variables before running ``pytest -m bq``.
"""

import os
import uuid

import pytest
from google.cloud import bigquery

# Test configuration -- override via env vars when running BQ tests externally.
TEST_PROJECT = os.environ.get("TABLE_CHECK_TEST_PROJECT", "world-fishing-827")
TEST_DATASET = os.environ.get("TABLE_CHECK_TEST_DATASET", "tech_great_expectations")

# Fixtures whose presence marks a test as requiring BigQuery
_BQ_FIXTURES = {"bq_client", "table_factory", "test_dataset"}


def pytest_collection_modifyitems(config, items):
    """Auto-mark tests that use BigQuery fixtures with the 'bq' marker."""
    bq_marker = pytest.mark.bq
    for item in items:
        if _BQ_FIXTURES & set(item.fixturenames):
            item.add_marker(bq_marker)


@pytest.fixture(scope="session")
def bq_client():
    """Create a BigQuery client using application-default credentials."""
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
