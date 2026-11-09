import hashlib
import html
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup


def plain(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(dict.fromkeys(plain(x) for x in value if plain(x)))
    if isinstance(value, dict):
        return plain(
            value.get("fullName")
            or value.get("Name")
            or value.get("name")
            or value.get("shortName")
            or value.get("Link")
            or ""
        )
    value = str(value)
    return (
        BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
        if "<" in value
        else html.unescape(value).strip()
    )


def normalize(row):
    tender_id = str(row.get("tender_id") or "")
    if not tender_id.isdigit() or not row.get("tender_name"):
        raise ValueError("TenderLand result has no valid ID or title")
    customers = row.get("tender_lotCustomerFullName") or row.get("tender_lotCustomerShortName") or []
    return {
        "id": tender_id,
        "title": plain(row["tender_name"]),
        "number": plain(row.get("tender_regNumber")),
        "url": f"https://tenderland.ru/Entity/Tender?id={tender_id}",
        "source_url": link(row.get("tender_sourceLink")) or link(row.get("tender_etpLink")),
        "customer": plain(customers),
        "customer_inn": plain(row.get("tender_lotCustomerInn"))
        or plain([x.get("inn") for x in customers if isinstance(x, dict)]),
        "platform": plain(row.get("tender_etpName")),
        "price": plain(row.get("tender_beginPrice")),
        "currency": plain(row.get("tender_lotCurrency")),
        "region": plain(row.get("tender_lotDeliveryPlacesText")) or plain(row.get("tender_region")),
        "published_at": plain(row.get("tender_publishDate")),
        "submission_starts_at": plain(row.get("tender_beginDate")),
        "bidding_at": plain(row.get("tender_biddingDate")),
        "deadline_at": plain(row.get("tender_endDate")),
        "execution_period": plain(row.get("tender_lotDeliveryTerm")),
        "status": plain(row.get("tender_status")),
        "timezone_offset": plain(row.get("tender_utcTimeOffset")),
        "lots": plain(row.get("tender_lotOrdinalNumber")),
        "lot_names": plain(row.get("tender_lotName")),
    }


def link(value):
    if isinstance(value, dict):
        value = value.get("Link") or value.get("link") or value.get("Url")
    if isinstance(value, list):
        return "; ".join(filter(None, (link(x) for x in value)))
    value = plain(value)
    return value if value.startswith(("https://", "http://")) else ""


def merge(previous, incoming, match, now):
    record = {**(previous or {}), **incoming}
    for key, value in incoming.items():
        if value == "" and previous and previous.get(key):
            record[key] = previous[key]
    matches = {
        json.dumps(x, sort_keys=True, ensure_ascii=False): x for x in (previous or {}).get("matches", [])
    }
    if match:
        matches[json.dumps(match, sort_keys=True, ensure_ascii=False)] = match
    record["matches"] = [matches[k] for k in sorted(matches)]
    record["first_seen_at"] = (previous or {}).get("first_seen_at", now)
    record["last_seen_at"] = now
    content = {
        k: v
        for k, v in record.items()
        if k not in {"first_seen_at", "last_seen_at", "changed_at", "content_hash"}
    }
    record["content_hash"] = hashlib.sha256(
        json.dumps(content, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    record["changed_at"] = (
        previous["changed_at"] if previous and previous["content_hash"] == record["content_hash"] else now
    )
    return record


HEADERS = [
    "ID TenderLand",
    "Название",
    "Номер закупки",
    "Номера лотов",
    "Ссылка TenderLand",
    "Первоисточник",
    "Категории",
    "Найдено по запросам",
    "Режимы поиска",
    "Причина включения",
    "Заказчик",
    "ИНН заказчика",
    "Площадка",
    "Цена",
    "Валюта",
    "Место исполнения",
    "Опубликовано",
    "Начало подачи заявок",
    "Окончание подачи заявок",
    "Срок исполнения",
    "Статус",
    "Впервые найдено МСК",
    "Последнее изменение МСК",
    "Последняя проверка МСК",
    "Дата торгов",
    "Часовой пояс источника",
    "Названия лотов",
    "Фильтр распределения",
]


def moscow(value):
    return (
        datetime.fromisoformat(value).astimezone(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M:%S")
        if value
        else ""
    )


def sheet_row(record, decision=None):
    def unique(key):
        return "; ".join(dict.fromkeys(x[key] for x in record["matches"]))

    source_reasons = "\n".join(
        f"TenderLand вернул по «{x['query']}»; {x['mode']}; {'название' if x['scope'] == 'title' else 'документация'}"
        for x in record["matches"]
    )
    reasons = (
        (decision.reason + ("; нужна проверка\n" if decision.needs_review else "\n")) if decision else ""
    ) + source_reasons
    return [
        str(x or "")[:45000]
        for x in [
            record["id"],
            record["title"],
            record["number"],
            record["lots"],
            record["url"],
            record["source_url"],
            unique("label"),
            unique("query"),
            unique("mode"),
            reasons,
            record["customer"],
            record["customer_inn"],
            record["platform"],
            record["price"],
            record["currency"],
            record["region"],
            record["published_at"],
            record["submission_starts_at"],
            record["deadline_at"],
            record["execution_period"],
            record["status"],
            moscow(record["first_seen_at"]),
            moscow(record["changed_at"]),
            moscow(record["last_seen_at"]),
            record["bidding_at"],
            record["timezone_offset"],
            record["lot_names"],
        decision.rule_name if decision else "",
        ]
    ]
