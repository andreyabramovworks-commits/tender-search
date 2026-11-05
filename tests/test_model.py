from tender_search.model import HEADERS, merge, normalize, sheet_row

JOB = {"category": "scaffolding", "label": "Леса", "query": "строит++лес", "mode": "Pro", "scope": "title"}
NOW = "2026-11-05T06:00:00+00:00"


def sample():
    return normalize(
        {
            "tender_id": 123,
            "tender_name": "Поставка <span class='tl-highlighter'>лесов</span>",
            "tender_regNumber": "00123",
            "tender_beginPrice": 0,
        }
    )


def test_repeated_daily_match_preserves_identity_and_first_seen():
    first = merge(None, sample(), JOB, NOW)
    second = merge(first, sample(), JOB, "2026-11-06T06:00:00+00:00")
    assert second["id"] == "123"
    assert second["first_seen_at"] == first["first_seen_at"]
    assert second["changed_at"] == first["changed_at"]
    assert second["last_seen_at"] != first["last_seen_at"]
    assert len(second["matches"]) == 1
    assert second["title"] == "Поставка лесов"
    assert second["price"] == "0"
    assert len(sheet_row(second)) == len(HEADERS)


def test_all_categories_and_queries_are_retained_in_one_record():
    first = merge(None, sample(), JOB, NOW)
    other = {**JOB, "category": "uav_protection", "label": "БПЛА", "query": "БПЛА++лес", "mode": "Standard"}
    record = merge(first, sample(), other, NOW)
    assert len(record["matches"]) == 2
    assert "БПЛА++лес" in sheet_row(record)[7]
    assert "Pro" in sheet_row(record)[8] and "Standard" in sheet_row(record)[8]


def test_deadline_change_is_a_change_not_a_new_tender():
    first = merge(None, sample(), JOB, NOW)
    incoming = {**sample(), "deadline_at": "2026-11-20T09:00:00+03:00"}
    second = merge(first, incoming, None, "2026-11-06T06:00:00+00:00")
    assert first["content_hash"] != second["content_hash"]
    assert second["first_seen_at"] == first["first_seen_at"]
    assert len(second["matches"]) == 1
