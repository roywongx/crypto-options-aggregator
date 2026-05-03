"""Pytest configuration — ensure DB schema exists before tests run."""
import sys
from pathlib import Path

# Add dashboard root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from db.connection import get_db_connection
from db.schema import init_database_schema


@pytest.fixture(autouse=True, scope="session")
def _init_db():
    """Create all tables once per test session."""
    conn = get_db_connection(read_only=False)
    try:
        init_database_schema(conn)
    finally:
        conn.close()
