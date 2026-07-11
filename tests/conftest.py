import pytest

from state.db import connect


@pytest.fixture
def fund_db(tmp_path):
    """Temp SQLite with the full contracts.md §2 DDL applied (acceptance §0)."""
    conn = connect(tmp_path / "fund.sqlite")
    yield conn
    conn.close()
