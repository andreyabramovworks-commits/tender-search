"""Temporary download and permanent Google Drive archive for public tender files."""

import hashlib
import io
import json
import mimetypes
import re
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials as UserCredentials
from google.oauth2.service_account import Credentials

FOLDER_MIME = "application/vnd.google-apps.folder"
NATIVE_TYPES = {
    ".doc": "application/vnd.google-apps.document",
    ".docx": "application/vnd.google-apps.document",
    ".rtf": "application/vnd.google-apps.document",
    ".xls": "application/vnd.google-apps.spreadsheet",
    ".xlsx": "application/vnd.google-apps.spreadsheet",
    ".ods": "application/vnd.google-apps.spreadsheet",
    ".csv": "application/vnd.google-apps.spreadsheet",
    ".ppt": "application/vnd.google-apps.presentation",
    ".pptx": "application/vnd.google-apps.presentation",
    ".odp": "application/vnd.google-apps.presentation",
}


class DriveError(RuntimeError):
    pass


def safe_name(value, fallback="Документ"):
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", value).strip(" ._")
    return (value or fallback)[:180]


class DriveArchive:
    """Uploads after successful in-memory retrieval; no tender files persist on disk."""

    def __init__(self, settings):
        if not settings.google_drive_folder_id:
            raise DriveError("GOOGLE_DRIVE_FOLDER_ID is missing")
        scopes = ["https://www.googleapis.com/auth/drive"]
        if settings.google_drive_oauth_token_file.exists():
            credentials = UserCredentials.from_authorized_user_file(
                str(settings.google_drive_oauth_token_file), scopes=scopes
            )
        else:
            credentials = Credentials.from_service_account_file(
                str(settings.google_credentials_file), scopes=scopes
            )
        self.settings = settings
        self.session = AuthorizedSession(credentials)
        self.base = "https://www.googleapis.com/drive/v3"
        self.upload_base = "https://www.googleapis.com/upload/drive/v3"
        self.folders = {}
        self.tenderland_session = requests.Session()
        self.tenderland_session.headers.update({"User-Agent": "Mozilla/5.0 tender-search/1.0"})
        self.tenderland_credentials = json.loads(settings.credentials_file.read_text(encoding="utf-8-sig"))
        self.login_tenderland()

    def login_tenderland(self):
        login = self.tenderland_session.post(
            "https://tenderland.ru/Account/Login",
            data={
                "login": self.tenderland_credentials["username"],
                "password": self.tenderland_credentials["password"],
                "returnUrl": "/Search/Entities?type=Pro",
            },
            timeout=60,
        )
        login.raise_for_status()

    def request(self, method, url, **kwargs):
        response = self.session.request(method, url, timeout=60, **kwargs)
        if response.status_code >= 400:
            detail = response.text.replace("\n", " ").replace("\r", " ")[:300]
            raise DriveError(f"Google Drive HTTP {response.status_code}: {detail}")
        return response.json() if response.content else {}

    def folder(self, parent_id, name):
        key = (parent_id, name)
        if key in self.folders:
            return self.folders[key]
        escaped = name.replace("'", "\\'")
        query = f"name = '{escaped}' and '{parent_id}' in parents and mimeType = '{FOLDER_MIME}' and trashed = false"
        data = self.request(
            "GET", self.base + "/files", params={"q": query, "fields": "files(id,name)", "pageSize": 1}
        )
        folder_id = (data.get("files") or [{}])[0].get("id")
        if not folder_id:
            data = self.request(
                "POST",
                self.base + "/files",
                json={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
                params={"fields": "id"},
            )
            folder_id = data["id"]
        self.folders[key] = folder_id
        return folder_id

    def tender_folder(self, record):
        try:
            published = datetime.fromisoformat(record.get("published_at", "").replace("Z", "+00:00"))
        except ValueError:
            published = datetime.now(UTC)
        year = self.folder(self.settings.google_drive_folder_id, str(published.year))
        month = self.folder(year, f"{published.month:02d}")
        return self.folder(month, safe_name(f"{record['id']} — {record['title']}"))

    def purge_office_artifacts(self, record):
        """Move only pre-existing Office-container debris to Drive trash."""
        folder_id = self.tender_folder(record)
        data = self.request(
            "GET", self.base + "/files", params={"q": f"'{folder_id}' in parents and trashed = false", "fields": "files(id,name)", "pageSize": 1000}
        )
        deleted = 0
        for item in data.get("files", []):
            name = item["name"].lower()
            if name in {"[content_types].xml", "rels"} or name.endswith((".xml", ".xml.rels")):
                self.request("PATCH", self.base + "/files/" + item["id"], json={"trashed": True})
                deleted += 1
        return deleted

    def purge_exact_duplicates(self, folder_id):
        """Move older exact-name duplicates to Drive trash, keeping newest."""
        data = self.request(
            "GET",
            self.base + "/files",
            params={
                "q": f"'{folder_id}' in parents and trashed = false",
                "fields": "files(id,name,createdTime)",
                "pageSize": 1000,
            },
        )
        groups = {}
        for item in data.get("files", []):
            groups.setdefault(item["name"], []).append(item)
        removed = 0
        for files in groups.values():
            files.sort(key=lambda item: item.get("createdTime", ""), reverse=True)
            for duplicate in files[1:]:
                self.request("PATCH", self.base + "/files/" + duplicate["id"], json={"trashed": True})
                removed += 1
        return removed

    def download(self, url):
        """Download an attachment and resolve a public download landing page."""
        if urlparse(url).scheme not in {"http", "https"}:
            raise DriveError("Tender link is not an HTTP(S) document URL")
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (compatible; tender-search/1.0)",
                "Accept": "text/html,application/pdf,application/octet-stream,*/*;q=0.8",
            }
        )
        active_session = self.tenderland_session if url.startswith("https://tenderland.ru/") else session
        response = active_session.get(url, timeout=60, allow_redirects=True)
        # File storage uses a separate authenticated session.  It may expire
        # while a document queue is running, so retry the same attachment
        # after a fresh login instead of permanently losing it.
        if active_session is self.tenderland_session and response.status_code in {401, 403}:
            self.login_tenderland()
            response = active_session.get(url, timeout=60, allow_redirects=True)
        response.raise_for_status()
        if "text/html" in response.headers.get("Content-Type", "").lower():
            page = BeautifulSoup(response.text, "html.parser")
            candidates = []
            refresh = page.find("meta", attrs={"http-equiv": lambda value: value and value.lower() == "refresh"})
            if refresh and "url=" in refresh.get("content", "").lower():
                candidates.append(refresh["content"].split("=", 1)[1].strip())
            for node in page.select("a[href], iframe[src], embed[src], object[data]"):
                candidate = node.get("href") or node.get("src") or node.get("data")
                if candidate:
                    candidates.append(candidate)
            for candidate in candidates:
                target = urljoin(response.url, candidate)
                if target == response.url or urlparse(target).scheme not in {"http", "https"}:
                    continue
                follow = session.get(target, timeout=60, allow_redirects=True, headers={"Referer": response.url})
                follow.raise_for_status()
                if "text/html" not in follow.headers.get("Content-Type", "").lower():
                    response = follow
                    break
        data = response.content
        if not data or len(data) > self.settings.archive_max_bytes:
            raise DriveError("Tender file is empty or exceeds ARCHIVE_MAX_BYTES")
        if "text/html" in response.headers.get("Content-Type", "").lower():
            raise DriveError("Tender link returned a web page, not a file")
        return data, response.headers.get("Content-Type", "application/octet-stream")

    def upload(self, folder_id, name, content, source_mime, convert=True):
        extension = PurePosixPath(name).suffix.lower()
        metadata = {"name": safe_name(name), "parents": [folder_id]}
        if convert and extension in NATIVE_TYPES:
            metadata["mimeType"] = NATIVE_TYPES[extension]
        boundary = "tender-search-" + uuid.uuid4().hex
        body = b"\r\n".join(
            [
                f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
                + json.dumps(metadata, ensure_ascii=False).encode(),
                f"--{boundary}\r\nContent-Type: {source_mime}\r\n\r\n".encode() + content,
                f"--{boundary}--".encode(),
            ]
        )
        data = self.request(
            "POST",
            self.upload_base + "/files",
            params={"uploadType": "multipart", "fields": "id,webViewLink"},
            data=body,
            headers={"Content-Type": f"multipart/related; boundary={boundary}"},
        )
        return data["webViewLink"]

    def archive(self, record, document):
        """Return archive rows. ZIP members become individual Google Drive files."""
        content, source_mime = self.download(document["url"])
        source_name = safe_name(document["title"])
        # TenderLand's file endpoint commonly replies as octet-stream.  Drive
        # needs the actual Office MIME type to convert DOCX/XLSX/PPTX.
        source_mime = mimetypes.guess_type(source_name)[0] or source_mime
        folder_id = self.tender_folder(record)
        digest = hashlib.sha256(content).hexdigest()
        # DOCX/XLSX/PPTX are ZIP-based internally, but must stay whole so
        # Drive can convert them to native Google files.  Unpack only an
        # attachment explicitly identified as a .zip archive.
        is_zip = document["kind"].lower() == "zip" or source_name.lower().endswith(".zip")
        if not is_zip:
            try:
                drive_url = self.upload(folder_id, source_name, content, source_mime)
            except DriveError as error:
                # Some supplier-generated Office files use an unsupported
                # variant. Keep the original file in Drive rather than lose
                # the tender; it remains viewable/downloadable by the manager.
                if "Conversion of the uploaded content" not in str(error):
                    raise
                drive_url = self.upload(folder_id, source_name, content, source_mime, convert=False)
            return [(source_name, document["kind"], drive_url, digest)], False
        results = []
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for member in archive.infolist():
                if member.is_dir() or member.file_size > self.settings.archive_max_bytes:
                    continue
                name = safe_name(PurePosixPath(member.filename).name)
                if not name:
                    continue
                member_content = archive.read(member)
                member_digest = hashlib.sha256(member_content).hexdigest()
                member_mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
                results.append(
                    (
                        name,
                        PurePosixPath(name).suffix.lower().lstrip(".") or "other",
                        self.upload(folder_id, name, member_content, member_mime),
                        member_digest,
                    )
                )
        if not results:
            raise DriveError("ZIP contains no eligible files")
        return results, True
