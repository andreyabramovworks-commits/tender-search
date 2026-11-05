from datetime import date

import pytest

from tender_search.config import Settings
from tender_search.source import SourceError, TenderLand, json_value


def row(tender_id):
    return {"tender_id": tender_id, "tender_name": "Леса"}


def test_pagination_does_not_drop_the_last_page(monkeypatch):
    client = TenderLand(Settings(page_size=2, max_pages=5))
    calls = []

    def find(mode, filters, session_id, skip=0, take=None):
        calls.append(skip)
        return {"totalCount": 3, "data": [row(1), row(2)] if skip == 0 else [row(3)]}

    monkeypatch.setattr(client, "find", find)
    result = list(client._range("Pro", [], date(2026, 1, 1), date(2026, 2, 1)))
    assert [x[0]["id"] for x in result] == ["1", "2", "3"]
    assert calls == [0, 2]


def test_repeated_page_is_an_error_not_a_success(monkeypatch):
    client = TenderLand(Settings(page_size=1, max_pages=5))
    monkeypatch.setattr(client, "find", lambda *a, **kw: {"totalCount": 2, "data": [row(1)]})
    with pytest.raises(SourceError, match="repeated"):
        list(client._range("Pro", [], date(2026, 1, 1), date(2026, 2, 1)))


def test_native_10000_cap_triggers_date_partition(monkeypatch):
    client = TenderLand(Settings())
    calls = []

    def find(mode, filters, session_id, **kw):
        calls.append(filters)
        return {"totalCount": 10000 if len(calls) == 1 else 0, "data": []}

    monkeypatch.setattr(client, "find", find)
    assert list(client._range("Pro", [], date(2026, 1, 1), date(2026, 2, 1))) == []
    assert len(calls) == 3


def test_html_login_page_is_never_an_empty_search():
    class Response:
        def json(self):
            raise ValueError("HTML")

    with pytest.raises(SourceError, match="HTML"):
        json_value(Response())


def test_query_syntax_is_preserved_for_native_search():
    value = TenderLand.text_filter("БПЛА++строител++лес", "documents")
    assert value["name"] == "tender_files"
    assert value["include"] == "БПЛА++строител++лес"
