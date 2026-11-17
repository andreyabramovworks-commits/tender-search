from datetime import date
from types import SimpleNamespace

from tender_search.bitrix import (
    deadline_is_future_day,
    deal_fields,
    google_document_links,
    preview,
    split_object_customer,
    price_as_opportunity,
)
from tender_search.sheets import AI_ANALYSIS_HEADERS, CRM_STATUS_HEADER


def test_split_object_and_customer_ignores_registry_and_lot():
    value = ('Поставка комплекта элементов для сценического комплекса; заказчик: '
             'ГОСУДАРСТВЕННОЕ БЮДЖЕТНОЕ УЧРЕЖДЕНИЕ "ЦЕНТР КУЛЬТУРЫ"; '
             'закупка 0335200010526000018; лот 2828022151')
    assert split_object_customer(value) == (
        'Поставка комплекта элементов для сценического комплекса',
        'ГОСУДАРСТВЕННОЕ БЮДЖЕТНОЕ УЧРЕЖДЕНИЕ "ЦЕНТР КУЛЬТУРЫ"',
    )


def test_deadline_requires_a_later_moscow_calendar_day():
    today = date(2026, 11, 11)
    assert not deadline_is_future_day("2026-11-10T23:59:00+03:00", today)
    assert not deadline_is_future_day("2026-11-11T23:59:00+03:00", today)
    assert deadline_is_future_day("2026-11-12T00:00:00+03:00", today)
    assert not deadline_is_future_day("2026-11-12T00:30:00+05:00", today)
    assert not deadline_is_future_day("", today)
    assert not deadline_is_future_day("unknown", today)


def test_preview_uses_new_ai_layout_and_skips_today_and_crm_status(monkeypatch):
    headers = AI_ANALYSIS_HEADERS + [CRM_STATUS_HEADER]
    def row(tender_id, deadline, status=""):
        values = [""] * 22
        values[0] = tender_id
        values[1] = "Поставка сцены; заказчик: ГБУ Культура; закупка 123"
        values[2] = deadline
        values[5] = "https://zakupki.gov.ru/source"
        values[6] = "Подходит"
        values[7] = "Ораева Алина"
        values[21] = status
        return values
    rows = [headers, row("1", "2026-11-11T23:59:00+03:00"),
            row("2", "2026-11-12T10:00:00+03:00"),
            row("3", "2026-11-12T10:00:00+03:00", "Сделка #77")]
    monkeypatch.setattr("tender_search.bitrix.GoogleSheets", lambda *args, **kwargs:
                        SimpleNamespace(values=lambda *_: rows))
    monkeypatch.setattr("tender_search.bitrix.deadline_is_future_day",
                        lambda value: deadline_is_future_day(value, date(2026, 11, 11)))
    records = [{"id": "2", "title": "Поставка сцены", "number": "123",
                "submission_starts_at": "2026-11-01T10:00:00+03:00"}]
    result = preview(SimpleNamespace(manager_spreadsheet_id="manager"),
                     SimpleNamespace(records=lambda: records))
    assert len(result) == 1
    assert result[0]["tender_id"] == "2"
    assert result[0]["customer"] == "ГБУ Культура"
    assert result[0]["source_url"] == "https://zakupki.gov.ru/source"
    assert "services" not in result[0]


def test_preview_uses_tender_customer_when_ai_object_has_no_customer(monkeypatch):
    row = [""] * 22
    row[0], row[1], row[2] = "2834719617", "Закупка ограждений", "2026-11-13T14:00:00+03:00"
    row[5], row[6], row[7] = "https://bidzaar.com/process/example", "Подходит", "Ораева Алина"
    monkeypatch.setattr("tender_search.bitrix.GoogleSheets", lambda *args, **kwargs:
                        SimpleNamespace(values=lambda *_: [AI_ANALYSIS_HEADERS + [CRM_STATUS_HEADER], row]))
    monkeypatch.setattr("tender_search.bitrix.deadline_is_future_day", lambda _: True)
    record = {"id": "2834719617", "title": "Закупка ограждений", "number": "2026-1",
              "customer": "ООО Заказчик", "submission_starts_at": ""}
    result = preview(SimpleNamespace(manager_spreadsheet_id="manager"),
                     SimpleNamespace(records=lambda: [record]))
    assert result[0]["object"] == "Закупка ограждений"
    assert result[0]["customer"] == "ООО Заказчик"


def test_payload_omits_services_and_close_date():
    candidate = {"title": "[RTM Radar - METAL] Тендер", "manager": "Ораева Алина",
                 "tender_id": "2834719617", "registry_number": "394-135", "object": "Ограждения",
                 "customer": "ООО Заказчик", "submission_deadline": "2026-11-13T14:00:00+03:00",
                 "source_url": "https://bidzaar.com/process/test", "submission_start": "2026-11-06T10:00:00+03:00",
                 "price": "11 656 742,8", "currency": "RUB",
                 "document_links": ["https://docs.google.com/document/d/test"]}
    fields = deal_fields(candidate)
    assert fields["ASSIGNED_BY_ID"] == 40
    assert fields["STAGE_ID"] == "C2:PREPAYMENT_INVOICE"
    assert fields["BEGINDATE"] == "2026-11-06"
    assert "CLOSEDATE" not in fields
    assert "UF_CRM_1774965362471" not in fields
    assert fields["OPPORTUNITY"] == 11656742.8
    assert fields["CURRENCY_ID"] == "RUB"


def test_price_as_opportunity_accepts_sheet_number_format_and_rejects_zero():
    assert price_as_opportunity("800 000,") == 800000.0
    assert price_as_opportunity("0,") is None
    assert price_as_opportunity("не указана") is None
    assert "docs.google.com" in fields["COMMENTS"]
    assert google_document_links("https://docs.google.com/document/d/test https://docs.google.com/document/d/test") == ["https://docs.google.com/document/d/test"]
    assert google_document_links("Источник карточки: https://docs.google.com/spreadsheets/d/source\nДокумент: https://docs.google.com/document/d/file") == ["https://docs.google.com/document/d/file"]
