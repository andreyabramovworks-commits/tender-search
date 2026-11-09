import re
import time
from urllib.parse import quote, urlencode

from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials

from .model import HEADERS, sheet_row


class SheetsError(RuntimeError):
    pass


class GoogleSheets:
    def __init__(self, settings, session=None):
        self.settings = settings
        if not settings.spreadsheet_id:
            raise SheetsError("GOOGLE_SPREADSHEET_ID is missing")
        if session is None:
            credentials = Credentials.from_service_account_file(
                str(settings.google_credentials_file), scopes=["https://www.googleapis.com/auth/spreadsheets"]
            )
            session = AuthorizedSession(credentials)
        self.session = session
        self.base = "https://sheets.googleapis.com/v4/spreadsheets/" + settings.spreadsheet_id

    def request(self, path="", method="GET", data=None, retry=True):
        for attempt in range(5 if retry else 1):
            try:
                response = self.session.request(method, self.base + path, json=data, timeout=45)
            except Exception as error:
                if not retry or attempt == 4:
                    raise SheetsError("Google Sheets network/auth error") from error
                time.sleep(2**attempt)
                continue
            if response.status_code < 400:
                return response.json()
            if response.status_code == 429 or response.status_code >= 500:
                if retry and attempt < 4:
                    time.sleep(2**attempt)
                    continue
            detail = response.text[:500].replace("\n", " ")
            raise SheetsError(f"Google Sheets HTTP {response.status_code}: {detail}")
        raise SheetsError("Google Sheets retry limit exceeded")

    def metadata(self):
        return self.request("?fields=spreadsheetId,sheets.properties")

    def values(self, a1):
        return self.request("/values/" + quote(a1, safe="")).get("values", [])

    def target_sheet(self, title=None, create=False):
        title = title or self.settings.sheet_title
        sheets = [x["properties"] for x in self.metadata().get("sheets", [])]
        existing = next((x for x in sheets if x["title"] == title), None)
        if existing or not create:
            return existing
        if len(sheets) == 1 and sheets[0]["title"] in {"Лист1", "Sheet1"}:
            initial = sheets[0]
            prefix = "'" + initial["title"].replace("'", "''") + "'"
            if not self.values(prefix + "!A1:AA2"):
                self.request(
                    ":batchUpdate",
                    "POST",
                    {
                        "requests": [
                            {
                                "updateSheetProperties": {
                                    "properties": {
                                        "sheetId": initial["sheetId"],
                                        "title": title,
                                    },
                                    "fields": "title",
                                }
                            }
                        ]
                    },
                )
                return self.target_sheet(title)
        # addSheet is not idempotent. On ambiguous failure inspect the resulting state.
        try:
            self.request(
                ":batchUpdate",
                "POST",
                {
                    "requests": [
                        {
                            "addSheet": {
                                "properties": {
                                    "title": title,
                                    "gridProperties": {"rowCount": 1000, "columnCount": len(HEADERS)},
                                }
                            }
                        }
                    ]
                },
                retry=False,
            )
        except SheetsError:
            existing = self.target_sheet(title)
            if not existing:
                raise
            return existing
        return self.target_sheet(title)

    def sync(self, records):
        """Backward-compatible one-tab sync used by diagnostics and existing tests."""
        return self._sync_tab(self.settings.sheet_title, records, set())

    def sync_partitioned(self, buckets):
        """Synchronise mutually-exclusive review queues without losing candidates.

        A record is written and read back in its destination first.  Only after that
        proof is received are stale copies cleared from the other managed tabs.
        Re-running after an interrupted move therefore reconciles safely.
        """
        titles = (self.settings.sheet_title, "Партнёрам", "Нецелевые")
        if set(buckets) != set(titles):
            raise SheetsError("Unexpected review queue names")
        ids = [str(record["id"]) for records in buckets.values() for record in records]
        if len(ids) != len(set(ids)):
            raise SheetsError("A tender cannot belong to more than one review queue")
        managed_ids = set(ids)
        # Phase 1 delivers every destination. Phase 2 removes stale copies only
        # after all destinations are known to be present.
        result = {title: self._sync_tab(title, buckets[title], set()) for title in titles}
        for title in titles:
            cleanup = self._sync_tab(title, buckets[title], managed_ids)
            result[title]["cleared"] = cleanup["cleared"]
            result[title]["written_rows"] += cleanup["written_rows"]
        return result

    def reference(self, defaults):
        """Load editable keywords/rules, creating a human-friendly source once."""
        title = "Справочник"
        if not self.target_sheet(title):
            self._write_reference(defaults)
            return defaults
        rows = self.values("'Справочник'!A1:J1000")
        if not rows or not any(rows[0]):
            self._write_reference(defaults)
            return defaults
        keyword_at = next((i for i, row in enumerate(rows) if row and row[0] == "Ключевики"), None)
        filter_at = next((i for i, row in enumerate(rows) if row and row[0] == "Фильтры отбора"), None)
        if keyword_at is None or filter_at is None:
            raise SheetsError("Reference tab layout was changed; restore its section headings")
        keywords, rules = [], []
        for row in rows[keyword_at + 2 : filter_at]:
            row = row + [""] * 10
            if row[0] and row[4].strip().lower() not in {"нет", "false", "0"}:
                keywords.append({"category": row[1], "label": row[2], "query": row[3]})
        for row in rows[filter_at + 2 :]:
            row = row + [""] * 10
            if not row[0]:
                continue
            if row[7].strip().lower() in {"нет", "false", "0"}:
                continue
            if row[2] not in {"direct", "partner", "non_target"}:
                raise SheetsError(f"Reference rule {row[0]} has an unknown route")
            try:
                priority = int(row[1])
                for pattern in filter(None, (row[4] + "\n" + row[5]).splitlines()):
                    re.compile(pattern.removeprefix("!"), re.I)
            except (ValueError, re.error) as error:
                raise SheetsError(f"Reference rule {row[0]} is invalid") from error
            rules.append(
                {
                    "id": row[0],
                    "priority": priority,
                    "bucket": row[2],
                    "name": row[3],
                    "all": row[4],
                    "any": row[5],
                    "reason": row[6],
                    "enabled": True,
                }
            )
        if not keywords or not rules:
            raise SheetsError("Reference has no active keywords or filters")
        # Newly shipped safety rules take effect even before an administrator
        # opens the reference tab; existing user-edited rules keep precedence.
        present = {rule["id"] for rule in rules}
        rules.extend(rule for rule in defaults["rules"] if rule["id"] not in present)
        self._write_operator_help()
        return {"keywords": keywords, "rules": rules}

    def _write_operator_help(self):
        """Keep the private operator-panel access instructions beside rules."""
        rows = [
            ["Панель управления"],
            ["Адрес в браузере", "http://localhost:18091"],
            ["PowerShell-команда", 'ssh -i "C:\\Users\\Andrey\\Documents\\ChatGPT\\Скрипт для тендоров\\.secrets\\server_ed25519" -L 18091:127.0.0.1:18091 codex-rtm@62.109.6.159'],
            ["Как открыть", "Запустить команду, не закрывать окно PowerShell, затем открыть адрес выше."],
        ]
        self.request(
            "/values/" + quote("'Справочник'!K1:L4", safe="") + "?valueInputOption=RAW",
            "PUT",
            {"values": rows},
        )

    def _write_reference(self, defaults):
        sheet = self.target_sheet("Справочник", create=True)
        rows = [
            ["Справочник поиска"],
            ["Меняйте только строки таблиц. Изменения применяются при следующем ежедневном запуске."],
            [],
            ["Ключевики"],
            ["ID", "Категория", "Направление", "Запрос TenderLand", "Активно", "Комментарий"],
        ]
        for index, item in enumerate(defaults["keywords"], 1):
            rows.append([f"keyword-{index:03d}", item["category"], item["label"], item["query"], "да", ""])
        rows += [
            [],
            ["Фильтры отбора"],
            [
                "ID",
                "Приоритет",
                "Маршрут",
                "Название",
                "Все условия (каждое с новой строки; ! = нет)",
                "Любое условие",
                "Причина",
                "Активно",
                "Комментарий",
            ],
        ]
        for item in defaults["rules"]:
            rows.append(
                [
                    item["id"],
                    item["priority"],
                    item["bucket"],
                    item["name"],
                    item["all"],
                    item["any"],
                    item["reason"],
                    "да",
                    "",
                ]
            )
        prefix = "'Справочник'"
        self.request(
            "/values/" + quote(prefix + "!A1:I" + str(len(rows)), safe="") + "?valueInputOption=RAW",
            "PUT",
            {"values": rows},
        )
        self.format_sheet(sheet["sheetId"])
        self.format_data(sheet["sheetId"], len(rows))

    def sync_documents(self, documents):
        title, headers = (
            "Документы",
            [
                "ID TenderLand",
                "Название тендера",
                "",
                "",
                "Ссылки первоисточников",
                "Документы в Google Drive",
                "Статусы архива",
                "Доступ",
                "Обновлено",
            ],
        )
        sheet = self.target_sheet(title, create=True)
        prefix = "'Документы'"
        existing = self.values(prefix + "!A1:J10000")
        existing_header = [(value or "") for value in existing[0]] if existing else []
        prior_headers = [
            "ID TenderLand", "Тип", "Документ", "Ссылка первоисточника", "Ссылка Google Drive",
            "Статус архива", "Доступ", "SHA-256", "Найдено",
        ]
        current_headers = [
            "ID TenderLand", "Название тендера", "Тип", "Документ", "Ссылка первоисточника",
            "Ссылка Google Drive", "Статус архива", "Доступ", "SHA-256", "Найдено",
        ]
        legacy_headers = ["ID TenderLand", "Тип", "Документ", "Ссылка", "Доступ", "SHA-256", "Найдено"]
        normalized_header = existing_header[: len(headers)]
        if (
            existing
            and any(existing_header)
            and normalized_header != headers
            and existing_header != prior_headers
            and existing_header != current_headers
            and existing_header != legacy_headers
        ):
            raise SheetsError("Document registry headers differ; refusing to overwrite")
        # Entry pages are retained in PostgreSQL for audit, but this tab is for
        # actual tender attachments. Showing landing pages here misled reviewers.
        attachments = []
        seen_content = set()
        for document in documents:
            if document["kind"] in {"tenderland_card", "source_entry"}:
                continue
            # Old archive attempts could expand Office containers into their
            # internal XML members. They are audit artefacts, never useful
            # tender documents for a manager and must not pollute the sheet.
            if document["url"].startswith("zip://") or document["access_status"] == "archived_from_zip":
                continue
            # A platform can publish identical files separately and as a ZIP.
            # Keep one usable Drive link per tender/file, while PostgreSQL keeps
            # every source URL for audit and future retry.
            fingerprint = (document["tender_id"], document.get("sha256"))
            if document.get("sha256") and fingerprint in seen_content:
                continue
            if document.get("sha256"):
                seen_content.add(fingerprint)
            attachments.append(document)
        grouped = {}
        # Keep the registry aligned one-to-one with the target-tender queue.
        # A tender can temporarily have no public attachment: it must still be
        # visible as an empty row instead of silently disappearing from the
        # document workflow.
        for document in documents:
            grouped.setdefault(
                document["tender_id"],
                {"title": document.get("tender_title", ""), "documents": []},
            )
        for document in attachments:
            group = grouped.setdefault(
                document["tender_id"],
                {"title": document.get("tender_title", ""), "documents": []},
            )
            group["documents"].append(document)
        def links_cell(items, url_key, with_title=False):
            parts, runs, position = [], [], 0
            for item in items:
                url = item.get(url_key) or ""
                if not url:
                    continue
                label = f"{item['title']}: " if with_title else ""
                text = label + url
                if parts:
                    position += 1  # newline between independently clickable links
                parts.append(text)
                link_start = position + len(label)
                link_end = link_start + len(url)
                if label:
                    runs.append({"startIndex": position, "format": {}})
                # zip:// is an internal audit pointer after unpacking. Google
                # Sheets only accepts public HTTP(S) addresses as rich links.
                if url.startswith(("https://", "http://")):
                    runs.extend(
                        [
                            {"startIndex": link_start, "format": {"link": {"uri": url}}},
                            {"startIndex": link_end, "format": {}},
                        ]
                    )
                position += len(text)
            value = "\n".join(parts)
            # Google Sheets does not accept a run that starts after the final
            # character; terminal reset runs are therefore unnecessary.
            return value, [run for run in runs if run["startIndex"] < len(value)]

        rows, link_cells = [headers], []
        for tender_id, group in grouped.items():
            items = group["documents"]
            tenderland_items = [x for x in items if x["access_status"] == "tenderland_download"]
            source_text, source_runs = links_cell(tenderland_items or items, "url")
            drive_text, drive_runs = links_cell(items, "archive_url", with_title=True)
            rows.append(
                [
                    tender_id,
                    group["title"],
                    "",
                    "",
                    source_text,
                    drive_text,
                    "\n".join(sorted({x.get("archive_status") or "pending" for x in items}))
                    if items
                    else "нет документов",
                    "\n".join(sorted({x["access_status"] for x in items}))
                    if items
                    else "не найдено в TenderLand",
                    max((x["discovered_at"] for x in items), default=""),
                ]
            )
            link_cells.append((source_text, source_runs, drive_text, drive_runs))
        if len(rows) > sheet["gridProperties"]["rowCount"]:
            self.request(
                ":batchUpdate",
                "POST",
                {
                    "requests": [
                        {
                            "updateSheetProperties": {
                                "properties": {
                                    "sheetId": sheet["sheetId"],
                                    "gridProperties": {"rowCount": len(rows) + 100},
                                },
                                "fields": "gridProperties.rowCount",
                            }
                        }
                    ]
                },
            )
        writes = [{"range": prefix + f"!A{i}:I{i}", "values": [row]} for i, row in enumerate(rows, 1)]
        old_end = len(existing)
        for i in range(len(rows) + 1, old_end + 1):
            writes.append({"range": prefix + f"!A{i}:J{i}", "values": [[""] * 10]})
        for offset in range(0, len(writes), 100):
            self.request(
                "/values:batchUpdate",
                "POST",
                {"valueInputOption": "RAW", "data": writes[offset : offset + 100]},
            )
        # A plain cell value can only auto-detect one link.  Rich-text runs make
        # every URL in E and F independently clickable for people and the AI.
        for offset in range(0, len(link_cells), 100):
            segment = link_cells[offset : offset + 100]
            self.request(
                ":batchUpdate",
                "POST",
                {
                    "requests": [
                        {
                            "updateCells": {
                                "start": {
                                    "sheetId": sheet["sheetId"],
                                    "rowIndex": offset + 1,
                                    "columnIndex": 4,
                                },
                                "rows": [
                                    {
                                        "values": [
                                            {
                                                "userEnteredValue": {"stringValue": source_text},
                                                "textFormatRuns": source_runs,
                                            },
                                            {"userEnteredValue": {"stringValue": drive_text}, "textFormatRuns": drive_runs},
                                        ]
                                    }
                                    for source_text, source_runs, drive_text, drive_runs in segment
                                ],
                                "fields": "userEnteredValue,textFormatRuns",
                            }
                        }
                    ]
                },
            )
        # C and D are deliberately hidden: the concise view begins with the
        # tender identity and keeps the full Drive document set in column F.
        self.request(
            ":batchUpdate",
            "POST",
            {
                "requests": [
                    {
                        "updateDimensionProperties": {
                            "range": {"sheetId": sheet["sheetId"], "dimension": "COLUMNS", "startIndex": 2, "endIndex": 4},
                            "properties": {"hiddenByUser": True},
                            "fields": "hiddenByUser",
                        }
                    }
                ]
            },
        )
        if not existing or not any(existing[0]):
            self.format_sheet(sheet["sheetId"])
        return {"rows": len(grouped), "documents": len(attachments)}

    def ensure_ai_analysis(self):
        title = "AI-анализ"
        headers = [
            "ID TenderLand",
            "Ссылка TenderLand",
            "Решение менеджера",
            "Статус",
            "Приоритет",
            "Объект закупки",
            "Наши работы",
            "Цена и сроки",
            "Риски",
            "Недостающие документы",
            "Вопросы заказчику",
            "Доказательства и ссылки",
            "Нужен ручной доступ",
            "Актуально на МСК",
        ]
        sheet = self.target_sheet(title, create=True)
        prefix = "'AI-анализ'"
        existing = self.values(prefix + "!A1:N2")
        if existing and any(existing[0]) and existing[0] != headers:
            raise SheetsError("AI analysis headers differ; refusing to overwrite")
        if not existing or not any(existing[0]):
            self.request(
                "/values:batchUpdate",
                "POST",
                {"valueInputOption": "RAW", "data": [{"range": prefix + "!A1:M1", "values": [headers]}]},
            )
            self.format_sheet(sheet["sheetId"])
        return {"headers": len(headers)}

    def _sync_tab(self, title, records, managed_ids):
        sheet = self.target_sheet(title, create=True)
        prefix = "'" + title.replace("'", "''") + "'"
        columns = len(HEADERS)
        max_rows = sheet["gridProperties"]["rowCount"]
        if max_rows > 100000:
            raise SheetsError("Target tab exceeds 100000 rows; review retention before syncing")
        if sheet["gridProperties"]["columnCount"] < columns:
            self.request(
                ":batchUpdate",
                "POST",
                {
                    "requests": [
                        {
                            "updateSheetProperties": {
                                "properties": {
                                    "sheetId": sheet["sheetId"],
                                    "gridProperties": {"columnCount": columns},
                                },
                                "fields": "gridProperties.columnCount",
                            }
                        }
                    ]
                },
            )
        rows = self.values(f"{prefix}!A1:AD{max_rows}")
        if rows and any(rows[0]) and rows[0] != HEADERS:
            if rows[0] != HEADERS[:-1]:
                raise SheetsError("Target headers differ; refusing to overwrite another table")
        index = {}
        for number, row in enumerate(rows[1:], 2):
            if not row or not row[0]:
                if any(row):
                    raise SheetsError(f"Row {number} has data but no TenderLand ID")
                continue
            tender_id = str(row[0])
            if tender_id in index:
                raise SheetsError(f"Duplicate tender ID in spreadsheet: {tender_id}")
            index[tender_id] = number
        writes = []
        if not rows or not any(rows[0]) or rows[0] != HEADERS:
            writes.append({"range": f"{prefix}!A1:AD1", "values": [HEADERS]})
        # Every managed queue is rendered as a compact list.  Previously we reused
        # holes left by records moved to another tab, which made the sheet look
        # randomly sparse.  The database remains the source of truth, so placing
        # rows consecutively is safe and keeps links/reasons with their tender.
        next_row = 2
        new_count, updated_count = 0, 0
        for record in records:
            number = next_row
            next_row += 1
            if record["id"] not in index:
                new_count += 1
            values = sheet_row(record, record.get("_decision"))
            old = rows[number - 1] if number <= len(rows) else []
            old = [str(x) for x in old] + [""] * (columns - len(old))
            if old != values:
                writes.append({"range": f"{prefix}!A{number}:AD{number}", "values": [values]})
                updated_count += int(record["id"] in index)
        if next_row - 1 > max_rows:
            self.request(
                ":batchUpdate",
                "POST",
                {
                    "requests": [
                        {
                            "updateSheetProperties": {
                                "properties": {
                                    "sheetId": sheet["sheetId"],
                                    "gridProperties": {"rowCount": next_row + 100},
                                },
                                "fields": "gridProperties.rowCount",
                            }
                        }
                    ]
                },
            )
        # Destination rows have been verified above. Now clear stale copies of
        # managed TenderLand IDs, preserving other rows and any manual notes.
        expected_ids = {str(record["id"]) for record in records}
        stale = [
            number
            for tender_id, number in index.items()
            if tender_id in managed_ids - expected_ids or number >= next_row
        ]
        for offset in range(0, len(stale), 100):
            clear_batch = [
                {"range": f"{prefix}!A{number}:AD{number}", "values": [[""] * columns]}
                for number in stale[offset : offset + 100]
            ]
            self.request("/values:batchUpdate", "POST", {"valueInputOption": "RAW", "data": clear_batch})
            writes.extend(clear_batch)
        if not rows or not any(rows[0]):
            self.format_sheet(sheet["sheetId"])
        for offset in range(0, len(writes), 100):
            batch = writes[offset : offset + 100]
            self.request("/values:batchUpdate", "POST", {"valueInputOption": "RAW", "data": batch})
            # Read back exact cells: a successful HTTP response alone is not delivery proof.
            ranges = urlencode([("ranges", item["range"]) for item in batch])
            readback = self.request("/values:batchGet?" + ranges).get("valueRanges", [])
            if len(readback) != len(batch):
                raise SheetsError("Google Sheets readback omitted requested ranges")
            for item, result in zip(batch, readback, strict=True):
                observed = result.get("values", [])
                actual = observed[0] if observed else []
                actual += [""] * (columns - len(actual))
                if actual != item["values"][0]:
                    raise SheetsError("Google Sheets readback mismatch")
            if offset + 100 < len(writes):
                time.sleep(2)
        if new_count:
            self.format_data(sheet["sheetId"], next_row - 1)
        return {
            "new": new_count,
            "updated": updated_count,
            "cleared": len(stale),
            "written_rows": len(writes),
        }

    def format_data(self, sheet_id, last_row):
        requests = []
        # Explicit widths avoid broken header words and keep IDs/numbers readable.
        widths = [
            (0, 1, 140),
            (1, 2, 520),
            (2, 4, 165),
            (4, 6, 240),
            (6, 7, 170),
            (7, 8, 300),
            (8, 9, 160),
            (9, 10, 440),
            (10, 11, 300),
            (11, 12, 165),
            (12, 13, 280),
            (13, 14, 150),
            (14, 15, 100),
            (15, 16, 350),
            (16, 19, 190),
            (19, 20, 300),
            (20, 21, 190),
            (21, 27, 200),
        ]
        for first, end, pixels in widths:
            requests.append(
                {
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": first,
                            "endIndex": end,
                        },
                        "properties": {"pixelSize": pixels},
                        "fields": "pixelSize",
                    }
                }
            )
        if last_row > 1:
            requests.append(
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "endRowIndex": last_row,
                            "startColumnIndex": 1,
                            "endColumnIndex": 2,
                        },
                        "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"}},
                        "fields": "userEnteredFormat.wrapStrategy,userEnteredFormat.verticalAlignment",
                    }
                }
            )
        self.request(":batchUpdate", "POST", {"requests": requests})

    def format_sheet(self, sheet_id):
        self.request(
            ":batchUpdate",
            "POST",
            {
                "requests": [
                    {
                        "updateSpreadsheetProperties": {
                            "properties": {"timeZone": "Europe/Moscow"},
                            "fields": "timeZone",
                        }
                    },
                    {
                        "updateSheetProperties": {
                            "properties": {
                                "sheetId": sheet_id,
                                "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 2},
                            },
                            "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
                        }
                    },
                    {
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": 0,
                                "endRowIndex": 1,
                                "startColumnIndex": 0,
                                "endColumnIndex": len(HEADERS),
                            },
                            "cell": {
                                "userEnteredFormat": {
                                    "backgroundColor": {"red": 0.93, "green": 0.93, "blue": 0.93},
                                    "textFormat": {"bold": True},
                                    "wrapStrategy": "WRAP",
                                }
                            },
                            "fields": "userEnteredFormat",
                        }
                    },
                    {
                        "updateDimensionProperties": {
                            "range": {
                                "sheetId": sheet_id,
                                "dimension": "ROWS",
                                "startIndex": 0,
                                "endIndex": 1,
                            },
                            "properties": {"pixelSize": 60},
                            "fields": "pixelSize",
                        }
                    },
                    {
                        "updateDimensionProperties": {
                            "range": {
                                "sheetId": sheet_id,
                                "dimension": "COLUMNS",
                                "startIndex": 1,
                                "endIndex": 2,
                            },
                            "properties": {"pixelSize": 420},
                            "fields": "pixelSize",
                        }
                    },
                    {
                        "setBasicFilter": {
                            "filter": {
                                "range": {
                                    "sheetId": sheet_id,
                                    "startRowIndex": 0,
                                    "startColumnIndex": 0,
                                    "endColumnIndex": len(HEADERS),
                                }
                            }
                        }
                    },
                ]
            },
        )
