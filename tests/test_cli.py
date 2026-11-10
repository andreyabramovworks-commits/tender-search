from types import SimpleNamespace

from tender_search.cli import run
from tender_search.model import merge, normalize
from tender_search.source import SourceError


def test_unavailable_old_tender_does_not_block_new_google_rows(monkeypatch, tmp_path):
    job = {"category": "test", "label": "Тест", "query": "лес", "mode": "Pro", "scope": "title"}
    rows = [{"id": "456", "number": "old"}]
    summary = {}
    delivered = []

    class FakeStore:
        def __init__(self, url):
            pass

        def begin_run(self):
            return "test-run"

        def upsert(self, incoming, raw, match, now):
            rows.append(merge(None, incoming, match, now))

        def record_job(self, *args):
            pass

        def records(self):
            return rows.copy()

        def finish_run(self, run_id, status, data):
            summary.update(data)

        def close(self):
            pass

    class FakeSource:
        request_count = 1

        def __init__(self, settings):
            pass

        def login(self):
            pass

        def search(self, job):
            yield normalize({"tender_id": 123, "tender_name": "Леса"}), {}

        def enrich(self, record):
            return record, {}

        def refresh(self, records, mode):
            raise SourceError("Old tender unavailable")

        def close(self):
            pass

    class FakeSheets:
        def __init__(self, settings):
            pass

        def metadata(self):
            return {}

        def sync_partitioned(self, buckets):
            delivered.extend(record for records in buckets.values() for record in records)
            return {"Тендеры": {"new": 1}}

    monkeypatch.setattr("tender_search.cli.Store", FakeStore)
    monkeypatch.setattr("tender_search.cli.TenderLand", FakeSource)
    monkeypatch.setattr("tender_search.cli.GoogleSheets", FakeSheets)
    settings = SimpleNamespace(
        database_url="fake", data_dir=tmp_path, jobs=lambda: iter([job]), search_modes=["Pro"]
    )
    result = run(settings, SimpleNamespace(collect_only=False, limit_jobs=None))
    # A single disappeared historical TenderLand card is reported as partial,
    # but must not fail the systemd job when all current data was delivered.
    assert result == 0
    assert summary["status"] == "partial" and summary["synced"]
    assert summary["not_refreshed"] == 1
    assert {x["id"] for x in delivered} == {"123", "456"}
    assert list((tmp_path / "reports").glob("*.json"))
