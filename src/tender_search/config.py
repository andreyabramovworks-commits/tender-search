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
    sheet_title: str = field(default_factory=lambda: os.getenv("GOOGLE_SHEET_TITLE", "Тендеры"))
    queries_file: Path = field(default_factory=lambda: Path(os.getenv("QUERIES_FILE", "config/queries.json")))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", "data")))
    delay_seconds: float = field(default_factory=lambda: float(os.getenv("REQUEST_DELAY_SECONDS", "1.5")))
    timeout_seconds: int = field(default_factory=lambda: int(os.getenv("HTTP_TIMEOUT_SECONDS", "60")))
    search_modes: tuple = field(
        default_factory=lambda: tuple(os.getenv("SEARCH_MODES", "Pro,Standard").split(","))
    )
    scopes: tuple = field(
        default_factory=lambda: tuple(os.getenv("SEARCH_SCOPES", "title,documents").split(","))
    )
    page_size: int = field(default_factory=lambda: int(os.getenv("PAGE_SIZE", "100")))
    max_pages: int = field(default_factory=lambda: int(os.getenv("MAX_PAGES", "100")))

    def __post_init__(self):
        if not 1 <= self.page_size <= 1000 or self.max_pages < 1 or self.delay_seconds < 0:
            raise ValueError("Invalid pagination or delay settings")
        if set(self.search_modes) - {"Pro", "Standard"} or set(self.scopes) - {"title", "documents"}:
            raise ValueError("Unknown search mode or scope")

    def queries(self):
        return json.loads(self.queries_file.read_text(encoding="utf-8-sig"))

    def jobs(self):
        for category, group in self.queries().items():
            for query in dict.fromkeys(group["queries"].split()):
                for mode in self.search_modes:
                    for scope in self.scopes:
                        yield {
                            "category": category,
                            "label": group["label"],
                            "query": query,
                            "mode": mode,
                            "scope": scope,
                        }
