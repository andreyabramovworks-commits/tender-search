import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))
    credentials_file: Path = field(
        default_factory=lambda: Path(os.getenv("TENDERLAND_CREDENTIALS_FILE", ".secrets/tenderland.json"))
    )
    google_credentials_file: Path = field(
        default_factory=lambda: Path(
            os.getenv("GOOGLE_APPLICATION_CREDENTIALS", ".secrets/google-service-account.json")
        )
    )
    spreadsheet_id: str = field(default_factory=lambda: os.getenv("GOOGLE_SPREADSHEET_ID", ""))
    google_drive_folder_id: str = field(default_factory=lambda: os.getenv("GOOGLE_DRIVE_FOLDER_ID", ""))
    sheet_title: str = field(default_factory=lambda: os.getenv("GOOGLE_SHEET_TITLE", "Тендеры"))
    queries_file: Path = field(default_factory=lambda: Path(os.getenv("QUERIES_FILE", "config/queries.json")))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", "data")))
    delay_seconds: float = field(default_factory=lambda: float(os.getenv("REQUEST_DELAY_SECONDS", "1.5")))
    timeout_seconds: int = field(default_factory=lambda: int(os.getenv("HTTP_TIMEOUT_SECONDS", "60")))
    search_modes: tuple = field(
        default_factory=lambda: tuple(os.getenv("SEARCH_MODES", "Pro,Standard").split(","))
    )
    scopes: tuple = field(default_factory=lambda: tuple(os.getenv("SEARCH_SCOPES", "title").split(",")))
    page_size: int = field(default_factory=lambda: int(os.getenv("PAGE_SIZE", "100")))
    max_pages: int = field(default_factory=lambda: int(os.getenv("MAX_PAGES", "100")))
    archive_max_bytes: int = field(default_factory=lambda: int(os.getenv("ARCHIVE_MAX_BYTES", "52428800")))

    def __post_init__(self):
        if (
            not 1 <= self.page_size <= 1000
            or self.max_pages < 1
            or self.delay_seconds < 0
            or self.archive_max_bytes < 1
        ):
            raise ValueError("Invalid pagination or delay settings")
        if set(self.search_modes) - {"Pro", "Standard"} or set(self.scopes) - {"title", "documents"}:
            raise ValueError("Unknown search mode or scope")

    def queries(self):
        return json.loads(self.queries_file.read_text(encoding="utf-8-sig"))

    def jobs(self, keywords=None):
        for item in keywords or self.reference_keywords():
            for mode in self.search_modes:
                for scope in self.scopes:
                    yield {
                        "category": item["category"],
                        "label": item["label"],
                        "query": item["query"],
                        "mode": mode,
                        "scope": scope,
                    }

    def reference_keywords(self):
        return [
            {"category": category, "label": group["label"], "query": query}
            for category, group in self.queries().items()
            for query in dict.fromkeys(group["queries"].split())
        ]
