"""Read-only preparation of manager-approved tenders for Bitrix24."""

import os
import re
from datetime import date, datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from .sheets import AI_ANALYSIS_HEADERS, CRM_STATUS_HEADER, MANAGER_ACCEPTED, GoogleSheets

MANAGER_IDS = {"Ораева Алина": 40, "Сметанин Олег": 48,
               "Колесникова Анастасия": 18, "Колесников Данил": 68,
               "Прокопьев Александр": 120}
CRM_FIELDS = {"registry_number": "UF_CRM_1775828080057",
              "object": "UF_CRM_1775828178357",
              "customer": "UF_CRM_1775828279108",
              "submission_deadline": "UF_CRM_1775828555673",
              "source_url": "UF_CRM_1775828570890"}


def google_document_links(value):
    links = []
    for line in (value or "").splitlines():
        if "Источник карточки:" in line:
            continue
        links.extend(re.findall(r"https://(?:docs\.google\.com|drive\.google\.com)/[^\s<>\"']+", line))
    return list(dict.fromkeys(link.rstrip(".,;)") for link in links))


def split_object_customer(value):
    value = str(value or "").strip()
    match = re.search(r"(?:^|;)\s*заказчик\s*:\s*", value, re.IGNORECASE)
    if not match:
        return value, ""
    object_name = value[:match.start()].strip(" ;")
    customer = re.split(r";\s*(?:закупка|лот)\b", value[match.end():], maxsplit=1, flags=re.IGNORECASE)[0]
    return object_name, customer.strip(" ;")


def deadline_is_future_day(value, today=None):
    """A tender is eligible only when its submission date is after today in Moscow."""
    today = today or datetime.now(ZoneInfo("Europe/Moscow")).date()
    value = str(value or "").strip()
    if not value:
        return False
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        try:
            moment = datetime.strptime(value, "%d.%m.%Y %H:%M:%S")
        except ValueError:
            try:
                return date.fromisoformat(value) > today
            except ValueError:
                return False
    if moment.tzinfo is not None:
        moment = moment.astimezone(ZoneInfo("Europe/Moscow"))
    return moment.date() > today


def preview(settings, store, limit=3):
    manager = GoogleSheets(settings, spreadsheet_id=settings.manager_spreadsheet_id)
    rows = manager.values("'AI-анализ'!A1:V1000")
    if not rows or rows[0][:len(AI_ANALYSIS_HEADERS)] != AI_ANALYSIS_HEADERS:
        raise ValueError("Manager AI sheet headers differ")
    if len(rows[0]) > 21 and rows[0][21] not in ("", CRM_STATUS_HEADER):
        raise ValueError("Manager CRM status column is occupied")
    columns = {name: index for index, name in enumerate(rows[0]) if name}
    def cell(row, name):
        index = columns[name]
        return str(row[index]).strip() if len(row) > index else ""
    records = {record["id"]: record for record in store.records()}
    result = []
    for number, row in enumerate(rows[1:], 2):
        tender_id = cell(row, "ID TenderLand")
        decision = cell(row, "Решение менеджера")
        assignee = cell(row, "Менеджер")
        status = str(row[21]).strip() if len(row) > 21 else ""
        if decision != MANAGER_ACCEPTED or not assignee or status:
            continue
        deadline = cell(row, "Окончание подачи заявок")
        if not deadline_is_future_day(deadline):
            continue
        if assignee not in MANAGER_IDS:
            raise ValueError(f"Manager {assignee!r} in row {number} has no Bitrix24 mapping")
        record = records.get(tender_id)
        if not record:
            raise ValueError(f"Tender {tender_id} missing from database")
        object_name, customer = split_object_customer(cell(row, "Объект закупки"))
        customer = customer or str(record.get("customer") or "").strip()
        if not customer:
            raise ValueError(f"Customer missing in AI row and tender record {tender_id}")
        source_url = cell(row, "Ссылка на первоисточник") or record.get("source_url", "")
        if not source_url.startswith(("https://", "http://")):
            raise ValueError(f"Source URL missing in row {number}")
        result.append({"row": number, "tender_id": tender_id, "manager": assignee,
            "title": "[RTM Radar - METAL] " + record["title"],
            "registry_number": record["number"],
            "object": object_name, "customer": customer,
            "source_url": source_url, "submission_start": record.get("submission_starts_at", ""),
            "submission_deadline": deadline, "close_date": None,
            "document_links": google_document_links(cell(row, "Доказательства и ссылки"))})
        if len(result) >= limit:
            break
    return result


def deal_fields(candidate):
    """Construct the exact prospective CRM payload without sending it."""
    fields = {"TITLE": candidate["title"], "CATEGORY_ID": 2,
              "STAGE_ID": "C2:PREPAYMENT_INVOICE",
              "ASSIGNED_BY_ID": MANAGER_IDS[candidate["manager"]],
              CRM_FIELDS["registry_number"]: candidate["registry_number"],
              CRM_FIELDS["object"]: candidate["object"],
              CRM_FIELDS["customer"]: candidate["customer"],
              CRM_FIELDS["submission_deadline"]: candidate["submission_deadline"],
              CRM_FIELDS["source_url"]: candidate["source_url"],
              "COMMENTS": "ID TenderLand: " + candidate["tender_id"] + "\nДокументы:\n" +
                          "\n".join(candidate["document_links"])}
    if candidate.get("submission_start"):
        fields["BEGINDATE"] = candidate["submission_start"][:10]
    return fields


def bitrix_read(method, params=None):
    base = os.getenv("BITRIX_WEBHOOK_URL", "").strip()
    parsed = urlparse(base)
    if parsed.scheme != "https" or not parsed.netloc.endswith(".bitrix24.ru") or not parsed.path.startswith("/rest/"):
        raise ValueError("BITRIX_WEBHOOK_URL is missing or invalid")
    allowed = {"crm.category.list", "crm.status.list", "crm.deal.fields", "crm.deal.userfield.list", "crm.deal.list", "crm.deal.get", "user.get"}
    if method not in allowed:
        raise ValueError("Method is not permitted in read-only preview")
    response = requests.post(base.rstrip("/") + "/" + method + ".json", json=params or {}, timeout=30)
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        raise ValueError("Bitrix24 read failed: " + str(data["error"]))
    return data.get("result")
