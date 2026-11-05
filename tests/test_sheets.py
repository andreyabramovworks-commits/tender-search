from copy import deepcopy
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from tender_search.config import Settings
from tender_search.model import HEADERS, merge, normalize
from tender_search.sheets import GoogleSheets, SheetsError


class Response:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def json(self):
        return deepcopy(self.data)


class FakeSheet:
    def __init__(self):
        self.rows = [HEADERS.copy()]
        self.lose_next_response = False
        self.writes = []

    def request(self, method, url, json=None, timeout=None):
        parsed = urlparse(url)
        if parsed.query == "fields=spreadsheetId,sheets.properties":
            return Response(
                {
                    "sheets": [
                        {
                            "properties": {
                                "sheetId": 0,
                                "title": "Тендеры",
                                "gridProperties": {"rowCount": 1000, "columnCount": 27},
                            }
                        }
                    ]
                }
            )
        if "/values:batchGet" in parsed.path:
            ranges = parse_qs(parsed.query)["ranges"]
            return Response(
                {
                    "valueRanges": [
                        {"values": [self.rows[int(a.split("!A")[1].split(":")[0]) - 1]]} for a in ranges
                    ]
                }
            )
        if method == "GET" and "/values/" in parsed.path:
            assert unquote(parsed.path).endswith("!A1:AA1000")
            return Response({"values": self.rows})
        if method == "POST" and parsed.path.endswith("/values:batchUpdate"):
            assert json["valueInputOption"] == "RAW"
            for item in json["data"]:
                number = int(item["range"].split("!A")[1].split(":")[0])
                while len(self.rows) < number:
                    self.rows.append([])
                self.rows[number - 1] = item["values"][0].copy()
            self.writes.append(deepcopy(json))
            if self.lose_next_response:
                self.lose_next_response = False
                raise TimeoutError("Server wrote the cells, response was lost")
            return Response({})
        if method == "POST" and parsed.path.endswith(":batchUpdate"):
            return Response({})
        raise AssertionError((method, url, json))


def record():
    job = {
        "label": "Леса",
        "category": "scaffolding",
        "query": "строит++лес",
        "mode": "Pro",
        "scope": "title",
    }
    return merge(
        None,
        normalize({"tender_id": 123, "tender_name": "=IMPORTXML(untrusted)"}),
        job,
        "2026-11-05T06:00:00+00:00",
    )


def test_ambiguous_write_retry_and_second_run_do_not_duplicate(monkeypatch):
    monkeypatch.setattr("tender_search.sheets.time.sleep", lambda _: None)
    fake = FakeSheet()
    fake.lose_next_response = True
    sheets = GoogleSheets(Settings(spreadsheet_id="test"), session=fake)
    result = sheets.sync([record()])
    assert result["new"] == 1
    assert len(fake.rows) == 2
    assert fake.rows[1][1] == "=IMPORTXML(untrusted)"
    assert len(fake.writes) == 2
    assert fake.writes[0] == fake.writes[1]
    assert sheets.sync([record()])["written_rows"] == 0
    assert len(fake.rows) == 2


def test_duplicate_existing_ids_block_writes():
    fake = FakeSheet()
    fake.rows += [["123"], ["123"]]
    with pytest.raises(SheetsError, match="Duplicate"):
        GoogleSheets(Settings(spreadsheet_id="test"), session=fake).sync([record()])
    assert fake.writes == []


def test_wrong_headers_are_never_overwritten():
    fake = FakeSheet()
    fake.rows = [["Чужая таблица"]]
    with pytest.raises(SheetsError, match="headers"):
        GoogleSheets(Settings(spreadsheet_id="test"), session=fake).sync([record()])
    assert fake.writes == []
