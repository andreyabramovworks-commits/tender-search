from types import SimpleNamespace

from tender_search.classification import DIRECT
from tender_search.cli import archive_documents, has_time_to_bid, run, sync_document_registry
from tender_search.drive import DriveError
from tender_search.model import merge, normalize
from tender_search.source import SourceError


def test_only_explicitly_expired_deadlines_are_excluded():
    assert not has_time_to_bid({"deadline_at": "2020-01-01T10:00:00+03:00"})
    assert has_time_to_bid({"deadline_at": "неизвестно"})
    assert has_time_to_bid({"deadline_at": ""})


def test_document_registry_retains_history_but_hides_deadlines_inside_24_hours(monkeypatch):
    records = [
        {"id": "active", "deadline_at": "2099-01-01T12:00:00+03:00"},
        {"id": "stale", "deadline_at": "2020-01-01T12:00:00+03:00"},
    ]
    captured = {}

    class Store:
        def documents(self, ids):
            captured["document_ids"] = ids
            return []

    class Sheets:
        def document_tender_ids(self):
            return {"stale"}

        def sync_documents(self, documents, tenders):
            captured["registry_ids"] = [record["id"] for record in tenders]
            return {"rows": len(tenders)}

        def set_documents_visibility(self, ids):
            captured["visible_ids"] = ids
            return {"hidden": 1, "shown": 1}

    monkeypatch.setattr(
        "tender_search.cli.classify", lambda record, rules: SimpleNamespace(bucket=DIRECT)
    )
    outcome = sync_document_registry(Store(), Sheets(), records, rules=[])
    assert captured["document_ids"] == ["active", "stale"]
    assert captured["registry_ids"] == ["active", "stale"]
    assert captured["visible_ids"] == {"active"}
    assert outcome["visibility"] == {"hidden": 1, "shown": 1}


def test_unpacked_zip_is_not_uploaded_again(monkeypatch):
    uploaded = []

    class Store:
        def documents(self, ids):
            return [
                {
                    "tender_id": "1",
                    "url": "https://tenderland.ru/File/Get?storageId=1",
                    "title": "archive.zip",
                    "kind": "zip",
                    "archive_status": "unpacked",
                },
                {
                    "tender_id": "1",
                    "url": "zip://https://tenderland.ru/File/Get?storageId=1#inside.pdf",
                    "title": "inside.pdf",
                    "kind": "pdf",
                    "access_status": "archived_from_zip",
                    "archive_status": "archived",
                },
            ]

        def set_archive_result(self, *args):
            pass

    class Archive:
        def __init__(self, settings):
            pass

        def archive(self, record, document):
            uploaded.append(document)

    monkeypatch.setattr("tender_search.cli.DriveArchive", Archive)
    outcome = archive_documents(
        Store(),
        [{"id": "1"}],
        SimpleNamespace(google_drive_folder_id="folder"),
    )
    assert outcome == {"archived": 0}
    assert uploaded == []


def test_stale_unpacked_zip_is_retried_when_drive_members_are_missing(monkeypatch):
    uploaded = []

    class Store:
        def documents(self, ids):
            return [{
                "tender_id": "1",
                "url": "https://tenderland.ru/File/Get?storageId=1",
                "title": "archive.zip",
                "kind": "zip",
                "archive_status": "unpacked",
            }]

        def set_archive_result(self, *args):
            pass

        def add_extracted_document(self, *args):
            pass

    class Archive:
        def __init__(self, settings):
            pass

        def archive(self, record, document):
            uploaded.append(document)
            return [("inside.pdf", "pdf", "https://drive.example/inside", "digest")], True

    monkeypatch.setattr("tender_search.cli.DriveArchive", Archive)
    outcome = archive_documents(
        Store(), [{"id": "1"}], SimpleNamespace(google_drive_folder_id="folder")
    )
    assert outcome == {"archived": 1}
    assert uploaded[0]["title"] == "archive.zip"


def test_invalid_tenderland_attachment_is_marked_terminal_and_not_retried(monkeypatch):
    updates = []

    class Store:
        def documents(self, ids):
            return [{
                "tender_id": "1",
                "url": "https://tenderland.ru/File/Get?storageId=1",
                "title": "not-a-pdf.pdf",
                "kind": "pdf",
                "archive_status": "pending",
            }]

        def set_archive_result(self, *args):
            updates.append(args)

    class Archive:
        def __init__(self, settings):
            pass

        def archive(self, record, document):
            raise DriveError("PDF attachment does not have a PDF signature")

    monkeypatch.setattr("tender_search.cli.DriveArchive", Archive)
    outcome = archive_documents(
        Store(), [{"id": "1"}], SimpleNamespace(google_drive_folder_id="folder")
    )
    assert outcome == {"archived": 0}
    assert updates[0][3] == "invalid_content"

    class TerminalStore:
        def documents(self, ids):
            return [{
                "tender_id": "1",
                "url": "https://tenderland.ru/File/Get?storageId=1",
                "title": "not-a-pdf.pdf",
                "kind": "pdf",
                "archive_status": "invalid_content",
            }]

    assert archive_documents(
        TerminalStore(), [{"id": "1"}], SimpleNamespace(google_drive_folder_id="folder")
    ) == {"archived": 0}


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

        def search(self, job, earliest=None):
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
