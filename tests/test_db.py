import os

import pytest
from sqlalchemy import text

from tender_search.db import Store
from tender_search.model import normalize


@pytest.fixture
def database_url():
    value = os.getenv("TEST_DATABASE_URL")
    if not value:
        pytest.skip("TEST_DATABASE_URL is required for real PostgreSQL integration tests")
    return value


def test_real_postgres_dedup_history_and_process_lock(database_url):
    store = Store(database_url)
    try:
        job = {"category": "test", "label": "Тест", "query": "test", "mode": "Pro", "scope": "title"}
        incoming = normalize({"tender_id": 99999999900001, "tender_name": "Integration fixture"})
        store.upsert(incoming, {}, job, "2026-11-05T06:00:00+00:00")
        store.upsert(incoming, {}, job, "2026-11-06T06:00:00+00:00")
        assert len([r for r in store.records() if r["id"] == incoming["id"]]) == 1
        with store.engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM tender_versions WHERE tender_id=99999999900001")
                ).scalar()
                == 1
            )
        with pytest.raises(RuntimeError, match="Another"):
            Store(database_url)
        store.upsert(
            {**incoming, "deadline_at": "2026-11-20T12:00:00+03:00"}, {}, None, "2026-11-07T06:00:00+00:00"
        )
        with store.engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM tender_versions WHERE tender_id=99999999900001")
                ).scalar()
                == 2
            )
    finally:
        with store.engine.begin() as connection:
            connection.execute(text("DELETE FROM tender_versions WHERE tender_id=99999999900001"))
            connection.execute(text("DELETE FROM tenders WHERE id=99999999900001"))
        store.close()
    again = Store(database_url)
    again.close()
