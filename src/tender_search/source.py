"""Use the same authenticated search requests as TenderLand's ordinary web UI.

Routes and payloads were verified against common.min.js/user.min.js and a live
Pro search on 2026-11-05. This is not the separately licensed export API.
"""

import json
import random
import re
import time
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .model import normalize

BASE = "https://tenderland.ru"
DESIRED_FIELDS = [
    "tender_id",
    "tender_name",
    "tender_regNumber",
    "tender_beginPrice",
    "tender_lotCurrency",
    "tender_lotCustomerFullName",
    "tender_lotCustomerInn",
    "tender_lotDeliveryPlacesText",
    "tender_publishDate",
    "tender_beginDate",
    "tender_endDate",
    "tender_biddingDate",
    "tender_region",
    "tender_status",
    "tender_etpName",
    "tender_etpLink",
    "tender_sourceLink",
    "tender_utcTimeOffset",
    "tender_lotDeliveryTerm",
    "tender_lotOrdinalNumber",
    "tender_lotName",
    "tender_sysUpdateDate",
    "tender_isCompleted",
]


class SourceError(RuntimeError):
    pass


def json_value(response):
    try:
        result = response.json()
        return json.loads(result) if isinstance(result, str) else result
    except (ValueError, TypeError) as error:
        raise SourceError(
            "TenderLand returned HTML/non-JSON instead of search data; check session/access"
        ) from error


class TenderLand:
    def __init__(self, settings):
        self.settings = settings
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "Mozilla/5.0 tender-search/0.1", "X-Requested-With": "XMLHttpRequest"}
        )
        self.pages = {}
        self.available_fields = {}
        self.request_count = 0

    def request(self, method, path, **kwargs):
        for attempt in range(3):
            if self.request_count:
                time.sleep(self.settings.delay_seconds * (2**attempt))
            self.request_count += 1
            try:
                response = self.session.request(
                    method, BASE + path, timeout=self.settings.timeout_seconds, **kwargs
                )
                if response.status_code in (401, 403):
                    raise SourceError(f"TenderLand access denied ({response.status_code})")
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 2:
                        continue
                response.raise_for_status()
                return response
            except requests.RequestException as error:
                if attempt == 2:
                    raise SourceError(
                        "TenderLand network/HTTP error; request details omitted to protect credentials"
                    ) from error
        raise SourceError("TenderLand retry limit exceeded")

    def login(self):
        credentials = json.loads(self.settings.credentials_file.read_text(encoding="utf-8-sig"))
        result = json_value(
            self.request(
                "POST",
                "/Account/Login",
                data={
                    "login": credentials["username"],
                    "password": credentials["password"],
                    "returnUrl": "/Search/Entities?type=Pro",
                },
            )
        )
        if not result.get("Status"):
            raise SourceError("TenderLand rejected credentials or account access")

    def open_mode(self, mode):
        if mode in self.pages:
            return
        response = self.request("GET", "/Search/Entities", params={"type": mode})
        if "/Home/Landing" in response.url or "filtersArray = " not in response.text:
            raise SourceError(f"TenderLand {mode} search is unavailable for this session")
        html = response.text
        try:
            workspace = json.JSONDecoder().raw_decode(html.split("workspace: ", 1)[1])[0]
            field_hash = re.search(r'fieldsHash:\s*"([^"]+)"', html).group(1)
        except (ValueError, IndexError, AttributeError) as error:
            raise SourceError("TenderLand page structure changed") from error
        if workspace.get("Name") != mode:
            raise SourceError(f"Requested {mode}, received another search workspace")
        fields = json_value(
            self.request(
                "GET",
                "/Search/LoadGlobalParameters",
                params={"hash": field_hash, "workspaceId": workspace["Id"]},
            )
        )
        available = {x["SystemName"] for x in fields.get("Fields", []) if x.get("ParentId") == "tender"}
        if not {"tender_id", "tender_name", "tender_regNumber", "tender_status"} <= available:
            raise SourceError("Required TenderLand fields are missing")
        self.pages[mode] = {"workspace_id": workspace["Id"]}
        self.available_fields[mode] = [x for x in DESIRED_FIELDS if x in available]

    def validate_syntax(self, query):
        result = json_value(
            self.request(
                "POST",
                "/Dictionaries/GetSearchSyntaxAndCount",
                data=json.dumps(
                    {
                        "includeString": query,
                        "excludeString": "",
                        "syntax_analyze": True,
                    }
                ),
            )
        )
        if result.get("Include", 0) < 1 or any(
            x.get("Status", 0) for x in result.get("IncludeSyntaxResult", [])
        ):
            raise SourceError(f"TenderLand rejected keyword syntax: {query}")
        return result

    def find(self, mode, filters, session_id, skip=0, take=None):
        self.open_mode(mode)
        params = {
            "sessionId": session_id,
            "showHidden": True,
            "fields": self.available_fields[mode],
            "details": [],
            "sortingDirection": "desc",
            "orderBy": "tender_publishDate.desc",
            "filters": filters,
            "interval": [0, 5],
        }
        result = json_value(
            self.request(
                "POST",
                "/Search/Find",
                data={
                    "userData": json.dumps(params, ensure_ascii=False, separators=(",", ":")),
                    "skip": skip,
                    "take": take or self.settings.page_size,
                    "requireTotalCount": "true",
                },
                headers={"Referer": BASE + "/Search/Entities?type=" + mode},
            )
        )
        if (
            isinstance(result, dict)
            and result.get("totalCount") == 0
            and "data" in result
            and result["data"] is None
        ):
            result["data"] = []
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("totalCount"), int)
            or not isinstance(result.get("data"), list)
        ):
            raise SourceError(
                "TenderLand response has no valid data/totalCount; refusing a false empty result"
            )
        return result

    @staticmethod
    def text_filter(query, scope):
        return {
            "id": 100 if scope == "title" else 101,
            "isEnable": True,
            "name": "tender_name" if scope == "title" else "tender_files",
            "type": "text",
            "include": query,
            "exclude": "",
            "transliteration": False,
            "description": query,
        }

    def search(self, job):
        base_filters = [
            self.text_filter(job["query"], job["scope"]),
            {"id": 115, "isEnable": True, "name": "tender_status", "type": "value", "value": 1},
        ]
        today = datetime.now(UTC).date()
        earliest = date(today.year - 5, 1, 1)
        yield from self._range(job["mode"], base_filters, earliest, today + timedelta(days=2))

    def _range(self, mode, base_filters, start, end):
        filters = {
            "and": base_filters
            + [
                {
                    "id": 110,
                    "isEnable": True,
                    "name": "tender_publishDate",
                    "type": "range",
                    "from": start.isoformat(),
                    "to": end.isoformat(),
                }
            ]
        }
        session_id = random.SystemRandom().randint(100000, 999999999)
        result = self.find(mode, filters, session_id)
        count = result["totalCount"]
        # The web UI caps a single result set at 10,000. Partition before that cap.
        if count >= 10000 or count > self.settings.max_pages * self.settings.page_size:
            if (end - start).days <= 1:
                raise SourceError("A one-day search exceeds the retrieval limit; result is incomplete")
            middle = start + (end - start) // 2
            yield from self._range(mode, base_filters, start, middle)
            yield from self._range(mode, base_filters, middle, end)
            return
        received = 0
        seen = set()
        for page in range(self.settings.max_pages):
            if page:
                result = self.find(mode, filters, session_id, skip=received)
            data = result["data"]
            if not data and received < count:
                raise SourceError("TenderLand pagination ended before totalCount")
            for raw in data:
                record = normalize(raw)
                if record["id"] in seen:
                    raise SourceError(
                        "TenderLand repeated a tender during pagination; refusing incomplete success"
                    )
                seen.add(record["id"])
                yield record, raw
            received += len(data)
            if received >= count:
                return
        raise SourceError("Search pagination limit reached")

    def refresh(self, records, mode="Pro"):
        numbered = [r for r in records if r.get("number")]
        for offset in range(0, len(numbered), 25):
            batch = numbered[offset : offset + 25]
            by_id = {x["id"] for x in batch}
            query = " ".join(dict.fromkeys(x["number"] for x in batch))
            filters = {
                "and": [
                    {
                        "id": 107,
                        "isEnable": True,
                        "name": "tender_regNumber",
                        "type": "text",
                        "include": query,
                        "exclude": "",
                        "transliteration": False,
                        "strict_compliance": True,
                    }
                ]
            }
            session_id = random.SystemRandom().randint(100000, 999999999)
            result = self.find(mode, filters, session_id, take=1000)
            if result["totalCount"] > len(result["data"]):
                raise SourceError("Refresh search was truncated")
            for raw in result["data"]:
                record = normalize(raw)
                if record["id"] in by_id:
                    yield record, raw

    def enrich(self, record):
        response = self.request("GET", "/Entity/Tender", params={"id": record["id"]})
        try:
            entity = json.JSONDecoder().raw_decode(response.text.split("let entityModel = ", 1)[1])[0]
        except (IndexError, ValueError) as error:
            raise SourceError("TenderLand detail page changed or session expired") from error
        if str(entity.get("MainInfo", {}).get("Id")) != record["id"]:
            raise SourceError("TenderLand detail is unavailable or belongs to another tender")
        from .model import plain

        lots = entity.get("EntityLots", [])
        record = dict(record)
        record["lots"] = "; ".join(str(x["Id"]) for x in lots if x.get("Id"))
        record["lot_names"] = plain([x.get("Name") for x in lots])
        record["currency"] = plain([x.get("CurrencyCode") or x.get("CurrencyName") for x in lots])
        record["execution_period"] = "; ".join(
            f"Лот ID {x['Id']}: {plain(x.get('DeliveryTerm')) or plain(x.get('DeliveryStart')) + ' — ' + plain(x.get('DeliveryEnd'))}"
            for x in lots
            if x.get("DeliveryTerm") or x.get("DeliveryStart") or x.get("DeliveryEnd")
        )
        # Do not retain account/session data or invoke any /Ai route.
        detail = {
            "MainInfo": {k: v for k, v in entity["MainInfo"].items() if "AI" not in k},
            "EntityLots": lots,
        }
        return record, detail

    def discover_documents(self, record):
        """Find direct attachment links on a public procurement source page.

        This deliberately records URLs only: it never downloads tender files.
        Pages behind a login or dynamically unavailable pages return no attachments
        and are left for the manual-access queue.
        """
        source_url = record.get("source_url", "")
        if not source_url.startswith(("https://", "http://")):
            return []
        try:
            self.request_count += 1
            response = self.session.get(source_url, timeout=self.settings.timeout_seconds)
            if response.status_code >= 400 or "html" not in response.headers.get("Content-Type", "text/html"):
                return []
        except requests.RequestException:
            return []
        found = self._document_links(response.text, response.url)
        # Many procurement systems add their attachment cards after JavaScript
        # opens an accordion. Render only pages with no static attachment at all
        # so an unavailable platform cannot stall the full daily run. No payload
        # is downloaded.
        if not found:
            found = self._merge_document_links(found, self._rendered_document_links(source_url))
        return found

    @staticmethod
    def _merge_document_links(*groups):
        merged, seen = [], set()
        for group in groups:
            for document in group:
                if document["url"] not in seen:
                    seen.add(document["url"])
                    merged.append(document)
        return merged

    @staticmethod
    def _document_links(html, page_url):
        """Extract all likely attachments, including links inside a docs block."""
        soup = BeautifulSoup(html, "html.parser")
        found, seen = [], set()
        extensions = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar", ".7z")
        markers = re.compile(r"документ|документац|извещен|тз|технич|договор|смет|чертеж|проект", re.I)
        excluded = re.compile(r"политик|согласи|реклам|cookie|конфиденц|privacy|презентац.*этп", re.I)
        base_host = urlparse(page_url).hostname or ""
        base_zone = ".".join(base_host.split(".")[-2:])

        document_sections = []
        for element in soup.find_all(["details", "section", "article", "div", "li"]):
            attributes = " ".join([element.get("id", ""), *element.get("class", [])])
            if re.search(r"document|doc|attach|file|влож", attributes, re.I):
                document_sections.append(element)
        section_links = {
            id(anchor) for section in document_sections for anchor in section.find_all("a", href=True)
        }

        for anchor in soup.find_all("a", href=True):
            href = urljoin(page_url, anchor["href"].strip())
            parsed = urlparse(href)
            if parsed.scheme not in {"http", "https"}:
                continue
            raw_title = anchor.get_text(" ", strip=True) or anchor.get("title", "")
            signal = (href + " " + raw_title).lower()
            document_path = re.search(
                r"/(?:download|file|files|document|documents|attachment|attachments)(?:/|$|[?])",
                parsed.path,
                re.I,
            )
            same_zone = (parsed.hostname or "").endswith(base_zone) if base_zone else False
            candidate = (
                any(extension in signal for extension in extensions)
                or bool(document_path)
                or (id(anchor) in section_links and same_zone)
                or (bool(raw_title) and markers.search(raw_title) and same_zone)
            )
            if not candidate or excluded.search(signal):
                continue
            if href in seen:
                continue
            seen.add(href)
            path = urlparse(href).path.lower()
            kind = next((extension[1:] for extension in extensions if path.endswith(extension)), "other")
            found.append(
                {
                    "title": (raw_title or "Документ закупки")[:1000],
                    "url": href,
                    "kind": kind,
                    "access_status": "public_link",
                }
            )
        return found

    def _rendered_document_links(self, source_url):
        """Read links from a rendered public page; do not click file download links."""
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()
                try:
                    page.goto(source_url, wait_until="domcontentloaded", timeout=12_000)
                    page.locator("button, summary, [role=button]").evaluate_all(
                        """elements => elements.filter(x => /документ|документац|файл/i.test(x.innerText || ''))
                        .forEach(x => x.click())"""
                    )
                    page.wait_for_timeout(750)
                    return self._document_links(page.content(), page.url)
                finally:
                    browser.close()
        except Exception:
            return []

    def close(self):
        self.session.close()
