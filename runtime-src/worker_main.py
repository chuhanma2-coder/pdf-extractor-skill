#!/usr/bin/env python3
"""Offline worker for the ODPC determination desktop application."""

from __future__ import annotations

import csv
import concurrent.futures
import datetime as dt
import difflib
import hashlib
import io
import importlib.metadata
import json
import os
import re
import shutil
import ssl
import sqlite3
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

import certifi

WORKER_VERSION = "0.3.0"
DEFAULT_SOURCE = "https://www.odpc.go.ke/2025-determinations/"
USER_AGENT = "ODPC-Determination-Extractor/0.1 (offline research tool)"
STATUS_VALUES = {
    "discovered", "downloading", "downloaded", "ocr_running",
    "needs_review", "approved", "exported", "error",
}
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def recent_library_file() -> Path:
    return Path.home() / ".pdf-extractor" / "recent-library.json"


def remember_library(library: "Library", payload: dict[str, Any]) -> None:
    if not payload.get("remember"):
        return
    state_path = recent_library_file()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"library_path": str(library.root)}, ensure_ascii=False), encoding="utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_filename(value: str, fallback: str = "document.pdf") -> str:
    value = urllib.parse.unquote(value).strip()
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "-", value)
    value = re.sub(r"\s+", "-", value).strip(".-")
    if value.lower().endswith(".pdf"):
        value = value[:-4].rstrip(".-") + ".pdf"
    else:
        value = value.rstrip(".-") + ".pdf"
    return value[:180] or fallback


def find_tool(name: str) -> str | None:
    explicit = os.environ.get(f"ODPC_{name.upper()}_PATH")
    if explicit and Path(explicit).is_file():
        return explicit
    tools_dir = os.environ.get("ODPC_TOOLS_DIR", "").strip()
    executable_name = name + (".exe" if os.name == "nt" else "")
    executable_dir = Path(sys.executable).resolve().parent
    bundle_root = Path(getattr(sys, "_MEIPASS", executable_dir))
    candidates = [
        Path(tools_dir) / executable_name if tools_dir else None,
        bundle_root / "tools" / executable_name,
        executable_dir / "tools" / executable_name,
        executable_dir.parent / "Resources" / "tools" / executable_name,
        executable_dir.parent / "Resources" / "resources" / "tools" / executable_name,
        Path("/opt/homebrew/bin") / executable_name,
        Path("/usr/local/bin") / executable_name,
    ]
    for bundled in candidates:
        if bundled and bundled.is_file():
            return str(bundled)
    return shutil.which(name)


def run_command(args: list[str], timeout: int = 300, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False, env=env)


class DeterminationPageParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.in_row = False
        self.in_cell = False
        self.cell_text: list[str] = []
        self.cells: list[str] = []
        self.pdf_urls: list[str] = []
        self.all_pdf_urls: list[str] = []
        self.rows: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag.lower() == "tr":
            self.in_row = True
            self.cells = []
            self.pdf_urls = []
        elif tag.lower() in {"td", "th"} and self.in_row:
            self.in_cell = True
            self.cell_text = []
        elif tag.lower() == "a":
            href = attributes.get("href") or ""
            absolute = urllib.parse.urljoin(self.base_url, href)
            if re.search(r"\.pdf(?:$|[?#])", absolute, re.IGNORECASE):
                self.all_pdf_urls.append(absolute)
                if self.in_row:
                    self.pdf_urls.append(absolute)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"td", "th"} and self.in_cell:
            self.cells.append(re.sub(r"\s+", " ", " ".join(self.cell_text)).strip())
            self.cell_text = []
            self.in_cell = False
        elif tag.lower() == "tr" and self.in_row:
            if self.pdf_urls:
                self.rows.append({
                    "case_number": self.cells[0] if self.cells else "",
                    "parties": self.cells[1] if len(self.cells) > 1 else "",
                    "pdf_url": self.pdf_urls[0],
                    "raw_cells": list(self.cells),
                })
            self.in_row = False

    def handle_data(self, data: str) -> None:
        if self.in_cell and data.strip():
            self.cell_text.append(data.strip())


def parse_determination_page(html: str, base_url: str) -> list[dict[str, Any]]:
    parser = DeterminationPageParser(base_url)
    parser.feed(html)
    represented = {row["pdf_url"] for row in parser.rows}
    generic_rows = [{
        "case_number": "",
        "parties": Path(urllib.parse.urlsplit(url).path).stem,
        "pdf_url": url,
        "raw_cells": [],
    } for url in parser.all_pdf_urls if url not in represented]
    return parser.rows + generic_rows


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS source_snapshots (
  id INTEGER PRIMARY KEY,
  source_url TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  html_sha256 TEXT NOT NULL,
  html_path TEXT NOT NULL,
  entry_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  sha256 TEXT NOT NULL UNIQUE,
  filename TEXT NOT NULL,
  file_path TEXT NOT NULL,
  case_number TEXT NOT NULL DEFAULT '',
  parties TEXT NOT NULL DEFAULT '',
  page_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'downloaded',
  source_url TEXT NOT NULL DEFAULT '',
  error_message TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_entries (
  id INTEGER PRIMARY KEY,
  snapshot_id INTEGER NOT NULL REFERENCES source_snapshots(id),
  position INTEGER NOT NULL,
  case_number TEXT NOT NULL DEFAULT '',
  parties TEXT NOT NULL DEFAULT '',
  pdf_url TEXT NOT NULL,
  raw_cells_json TEXT NOT NULL,
  document_id INTEGER REFERENCES documents(id),
  download_error TEXT NOT NULL DEFAULT '',
  UNIQUE(snapshot_id, position)
);

CREATE TABLE IF NOT EXISTS pages (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page_number INTEGER NOT NULL,
  width REAL NOT NULL DEFAULT 0,
  height REAL NOT NULL DEFAULT 0,
  image_path TEXT NOT NULL DEFAULT '',
  primary_text TEXT NOT NULL DEFAULT '',
  secondary_text TEXT NOT NULL DEFAULT '',
  final_text TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0,
  blocks_json TEXT NOT NULL DEFAULT '[]',
  reviewed INTEGER NOT NULL DEFAULT 0,
  UNIQUE(document_id, page_number)
);

CREATE TABLE IF NOT EXISTS ocr_runs (
  id INTEGER PRIMARY KEY,
  page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  engine TEXT NOT NULL,
  engine_version TEXT NOT NULL DEFAULT '',
  preprocessing TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0,
  blocks_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quality_issues (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  page_number INTEGER NOT NULL,
  issue_type TEXT NOT NULL,
  severity TEXT NOT NULL DEFAULT 'warning',
  message TEXT NOT NULL,
  primary_candidate TEXT NOT NULL DEFAULT '',
  secondary_candidate TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open',
  resolved_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS revisions (
  id INTEGER PRIMARY KEY,
  page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  page_number INTEGER NOT NULL,
  before_text TEXT NOT NULL,
  after_text TEXT NOT NULL,
  reviewer TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exports (
  id INTEGER PRIMARY KEY,
  document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
  export_dir TEXT NOT NULL,
  manifest_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
  page_id UNINDEXED,
  document_id UNINDEXED,
  text,
  tokenize='unicode61'
);

CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
CREATE INDEX IF NOT EXISTS idx_source_entries_url ON source_entries(pdf_url);
CREATE INDEX IF NOT EXISTS idx_pages_document ON pages(document_id, page_number);
CREATE INDEX IF NOT EXISTS idx_issues_document_status ON quality_issues(document_id, status);
"""


@dataclass
class Library:
    root: Path

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "Library":
        raw = str(payload.get("library_path") or "").strip()
        if not raw:
            raise ValueError("未选择资料库目录")
        return cls(Path(raw).expanduser().resolve())

    @property
    def database(self) -> Path:
        return self.root / "odpc-library.sqlite3"

    def initialize(self) -> None:
        for name in ("originals", "renders", "snapshots", "work", "exports"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection


def page_count(path: Path) -> int:
    try:
        import pypdfium2 as pdfium
        document = pdfium.PdfDocument(path)
        count = len(document)
        document.close()
        return count
    except Exception:
        pass
    tool = find_tool("pdfinfo")
    if tool:
        result = run_command([tool, str(path)], timeout=60)
        match = re.search(r"^Pages:\s+(\d+)", result.stdout, re.MULTILINE)
        if result.returncode == 0 and match:
            return int(match.group(1))
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(path)).pages)
    except Exception as error:
        raise ValueError(f"PDF 无法读取：{error}") from error


def validate_pdf_copy(path: Path, expected_sha256: str = "") -> int:
    """Reject cloud placeholders and damaged files before OCR starts."""
    if not path.is_file():
        raise ValueError("资料库中的 PDF 副本不存在")
    stat = path.stat()
    if stat.st_size <= 5:
        raise ValueError("资料库中的 PDF 副本为空")
    if getattr(stat, "st_blocks", 1) == 0:
        raise ValueError("资料库中的 PDF 是 iCloud 云占位文件，磁盘上没有实际内容")
    try:
        with path.open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise ValueError("文件头不是有效的 PDF")
    except OSError as error:
        raise ValueError(f"资料库中的 PDF 无法读取：{error}") from error
    count = page_count(path)
    if count <= 0:
        raise ValueError("PDF 没有可读取的页面")
    if expected_sha256 and sha256_file(path) != expected_sha256:
        raise ValueError("资料库中的 PDF 副本校验失败，内容与导入时不一致")
    return count


def register_pdf(
    library: Library,
    source_path: Path,
    case_number: str = "",
    parties: str = "",
    source_url: str = "",
    move: bool = False,
    display_filename: str = "",
) -> tuple[int, bool]:
    if not source_path.is_file():
        raise FileNotFoundError(str(source_path))
    digest = sha256_file(source_path)
    count = validate_pdf_copy(source_path, digest)
    filename = safe_filename(display_filename or source_path.name)
    destination = library.root / "originals" / f"{digest[:12]}-{filename}"

    with library.connect() as connection:
        existing = connection.execute("SELECT id,file_path FROM documents WHERE sha256=?", (digest,)).fetchone()
        if existing:
            existing_path = Path(existing["file_path"])
            try:
                validate_pdf_copy(existing_path, digest)
                return int(existing["id"]), True
            except (OSError, ValueError):
                repair_path = destination.with_name(destination.name + ".repairing")
                shutil.copy2(source_path, repair_path)
                validate_pdf_copy(repair_path, digest)
                repair_path.replace(destination)
                if existing_path != destination:
                    existing_path.unlink(missing_ok=True)
                connection.execute(
                    """UPDATE documents SET filename=?,file_path=?,page_count=?,status='downloaded',
                       error_message='',updated_at=? WHERE id=?""",
                    (filename, str(destination), count, now_iso(), int(existing["id"])),
                )
                if move:
                    source_path.unlink(missing_ok=True)
                return int(existing["id"]), False
        if move:
            source_path.replace(destination)
        else:
            shutil.copy2(source_path, destination)
        validate_pdf_copy(destination, digest)
        stamp = now_iso()
        cursor = connection.execute(
            """INSERT INTO documents
               (sha256, filename, file_path, case_number, parties, page_count, status, source_url, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'downloaded', ?, ?, ?)""",
            (digest, filename, str(destination), case_number, parties, count, source_url, stamp, stamp),
        )
        return int(cursor.lastrowid), False


def fetch_url(url: str, headers: dict[str, str] | None = None, timeout: int = 60) -> tuple[bytes, dict[str, str]]:
    request_headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.5"}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(request, timeout=timeout, context=SSL_CONTEXT) as response:
        metadata = {key.lower(): value for key, value in response.headers.items()}
        return response.read(), metadata


def download_pdf(library: Library, url: str, fallback_name: str) -> tuple[int, bool]:
    filename = safe_filename(Path(urllib.parse.urlsplit(url).path).name, safe_filename(fallback_name))
    partial = library.root / "work" / (filename + ".part")

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            if partial.exists():
                partial_stat = partial.stat()
                if partial_stat.st_size <= 0 or getattr(partial_stat, "st_blocks", 1) == 0:
                    partial.unlink(missing_ok=True)
            offset = partial.stat().st_size if partial.exists() else 0
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            request_headers = {"User-Agent": USER_AGENT, "Accept": "application/pdf,*/*;q=0.5", **headers}
            request = urllib.request.Request(url, headers=request_headers)
            with urllib.request.urlopen(request, timeout=90, context=SSL_CONTEXT) as response:
                content_type = response.headers.get("Content-Type", "").lower()
                append = response.status == 206 and offset > 0
                if append:
                    content_range = response.headers.get("Content-Range", "")
                    if not content_range.startswith(f"bytes {offset}-"):
                        raise ValueError("服务器返回的断点位置与本地文件不一致")
                mode = "ab" if append else "wb"
                with partial.open(mode) as stream:
                    shutil.copyfileobj(response, stream, length=1024 * 1024)
                with partial.open("rb") as stream:
                    header = stream.read(5)
                if "pdf" not in content_type and header != b"%PDF-":
                    raise ValueError(f"服务器返回的不是 PDF（{content_type or 'unknown'}）")
            completed = library.root / "work" / filename
            partial.replace(completed)
            document_id, duplicate = register_pdf(library, completed, source_url=url, move=True)
            if duplicate:
                completed.unlink(missing_ok=True)
            return document_id, duplicate
        except Exception as error:
            last_error = error
            if isinstance(error, ValueError):
                partial.unlink(missing_ok=True)
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"下载失败：{last_error}")


def action_health(_: dict[str, Any]) -> dict[str, Any]:
    notes: list[str] = []
    try:
        import rapidocr  # noqa: F401
        rapid_available = True
    except Exception:
        rapid_available = False
        notes.append("RapidOCR 未安装；开发环境会暂时使用 Tesseract 主识别。")
    tesseract = bool(find_tool("tesseract"))
    pdftoppm = bool(find_tool("pdftoppm"))
    pdfinfo = bool(find_tool("pdfinfo"))
    try:
        import pypdfium2  # noqa: F401
        pdfium_available = True
    except Exception:
        pdfium_available = False
    return {
        "ok": True,
        "worker_version": WORKER_VERSION,
        "rapidocr": rapid_available,
        "tesseract": tesseract,
        "pdftoppm": pdftoppm,
        "pdfinfo": pdfinfo,
        "pdfium": pdfium_available,
        "offline_ocr_ready": (rapid_available or tesseract) and (pdfium_available or (pdftoppm and pdfinfo)),
        "notes": notes,
    }


def summary(library: Library) -> dict[str, Any]:
    with library.connect() as connection:
        row = connection.execute(
            """SELECT COUNT(*) total,
                      SUM(CASE WHEN status='downloaded' THEN 1 ELSE 0 END) downloaded,
                      SUM(CASE WHEN status='needs_review' THEN 1 ELSE 0 END) needs_review,
                      SUM(CASE WHEN status IN ('approved','exported') THEN 1 ELSE 0 END) approved,
                      SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) errors
               FROM documents"""
        ).fetchone()
        issues = connection.execute("SELECT COUNT(*) count FROM quality_issues WHERE status='open'").fetchone()["count"]
        stored_files = connection.execute("SELECT file_path FROM documents").fetchall()
    unavailable_files = 0
    for stored in stored_files:
        try:
            stat = Path(stored["file_path"]).stat()
            unavailable_files += int(stat.st_size <= 5 or getattr(stat, "st_blocks", 1) == 0)
        except OSError:
            unavailable_files += 1
    return {
        "library_path": str(library.root),
        "total": row["total"] or 0,
        "downloaded": row["downloaded"] or 0,
        "needs_review": row["needs_review"] or 0,
        "approved": row["approved"] or 0,
        "errors": row["errors"] or 0,
        "unresolved_issues": issues or 0,
        "unavailable_files": unavailable_files,
    }


def action_init(payload: dict[str, Any]) -> dict[str, Any]:
    library = Library.from_payload(payload)
    library.initialize()
    remember_library(library, payload)
    return summary(library)


def action_open_library(payload: dict[str, Any]) -> dict[str, Any]:
    library = Library.from_payload(payload)
    if not library.root.is_dir() or not library.database.is_file():
        raise ValueError(f"未找到原资料库：{library.root}。请重新选择包含 odpc-library.sqlite3 的目录。")
    remember_library(library, payload)
    return summary(library)


def action_recent_library(payload: dict[str, Any]) -> dict[str, Any]:
    state_path = recent_library_file()
    if not state_path.is_file():
        return {"library_path": "", "available": False}
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        library = Library(Path(str(raw.get("library_path") or "")).expanduser().resolve())
        return {"library_path": str(library.root), "available": library.root.is_dir() and library.database.is_file()}
    except Exception:
        return {"library_path": "", "available": False}


def action_summary(payload: dict[str, Any]) -> dict[str, Any]:
    library = Library.from_payload(payload)
    library.initialize()
    return summary(library)


def action_sync(payload: dict[str, Any]) -> dict[str, Any]:
    library = Library.from_payload(payload)
    library.initialize()
    source_url = str(payload.get("source_url") or DEFAULT_SOURCE).strip()
    html_bytes, _ = fetch_url(source_url)
    html_text = html_bytes.decode("utf-8", errors="replace")
    entries = parse_determination_page(html_text, source_url)
    if not entries:
        raise ValueError("来源页面中没有找到直接指向 PDF 的链接")

    stamp = now_iso()
    html_hash = sha256_bytes(html_bytes)
    snapshot_path = library.root / "snapshots" / f"{stamp[:10]}-{html_hash[:12]}.html"
    snapshot_path.write_bytes(html_bytes)
    with library.connect() as connection:
        cursor = connection.execute(
            "INSERT INTO source_snapshots(source_url,fetched_at,html_sha256,html_path,entry_count) VALUES(?,?,?,?,?)",
            (source_url, stamp, html_hash, str(snapshot_path), len(entries)),
        )
        snapshot_id = int(cursor.lastrowid)
        for position, entry in enumerate(entries, start=1):
            connection.execute(
                """INSERT INTO source_entries(snapshot_id,position,case_number,parties,pdf_url,raw_cells_json)
                   VALUES(?,?,?,?,?,?)""",
                (snapshot_id, position, entry["case_number"], entry["parties"], entry["pdf_url"], json.dumps(entry["raw_cells"], ensure_ascii=False)),
            )

    url_results: dict[str, tuple[int | None, bool, str]] = {}
    downloaded = duplicates = failed = 0
    for index, entry in enumerate(entries):
        url = entry["pdf_url"]
        if url in url_results:
            continue
        try:
            document_id, duplicate = download_pdf(library, url, entry["parties"] or entry["case_number"])
            with library.connect() as connection:
                existing = connection.execute("SELECT case_number, parties FROM documents WHERE id=?", (document_id,)).fetchone()
                connection.execute(
                    """UPDATE documents SET
                       case_number=CASE WHEN case_number='' THEN ? ELSE case_number END,
                       parties=CASE WHEN parties='' THEN ? ELSE parties END,
                       source_url=CASE WHEN source_url='' THEN ? ELSE source_url END,
                       updated_at=? WHERE id=?""",
                    (entry["case_number"], entry["parties"], url, now_iso(), document_id),
                )
            url_results[url] = (document_id, duplicate, "")
            duplicates += int(duplicate)
            downloaded += int(not duplicate)
        except Exception as error:
            url_results[url] = (None, False, str(error))
            failed += 1
        if index < len(entries) - 1:
            time.sleep(0.15)

    with library.connect() as connection:
        for position, entry in enumerate(entries, start=1):
            document_id, _, error = url_results[entry["pdf_url"]]
            connection.execute(
                "UPDATE source_entries SET document_id=?, download_error=? WHERE snapshot_id=? AND position=?",
                (document_id, error, snapshot_id, position),
            )

    return {
        "snapshot_id": snapshot_id,
        "entries_found": len(entries),
        "unique_urls": len(url_results),
        "downloaded": downloaded,
        "duplicates": duplicates,
        "failed": failed,
    }


def action_import_pdfs(payload: dict[str, Any]) -> dict[str, Any]:
    library = Library.from_payload(payload)
    library.initialize()
    imported = duplicates = 0
    failed: list[str] = []
    for raw_path in payload.get("paths") or []:
        try:
            _, duplicate = register_pdf(library, Path(str(raw_path)).expanduser().resolve())
            imported += int(not duplicate)
            duplicates += int(duplicate)
        except Exception as error:
            failed.append(f"{raw_path}: {error}")
    return {"imported": imported, "duplicates": duplicates, "failed": failed}


def document_dict(row: sqlite3.Row) -> dict[str, Any]:
    display_filename = str(row["filename"]).replace(".pdf.part.pdf", ".pdf")
    return {
        "id": row["id"],
        "sha256": row["sha256"],
        "filename": display_filename,
        "file_path": row["file_path"],
        "case_number": row["case_number"],
        "parties": row["parties"],
        "page_count": row["page_count"],
        "status": row["status"],
        "issue_count": row["issue_count"],
        "reviewed_pages": row["reviewed_pages"],
        "source_url": row["source_url"],
        "updated_at": row["updated_at"],
        "error_message": row["error_message"],
    }


DOCUMENT_SELECT = """
SELECT d.*,
       (SELECT COUNT(*) FROM quality_issues q WHERE q.document_id=d.id AND q.status='open') issue_count,
       (SELECT COUNT(*) FROM pages p WHERE p.document_id=d.id AND p.reviewed=1) reviewed_pages
FROM documents d
"""


def action_list_documents(payload: dict[str, Any]) -> dict[str, Any]:
    library = Library.from_payload(payload)
    library.initialize()
    search = str(payload.get("search") or "").strip()
    status = str(payload.get("status") or "all")
    clauses: list[str] = []
    params: list[Any] = []
    if status != "all":
        if status not in STATUS_VALUES:
            raise ValueError("未知文档状态")
        clauses.append("d.status=?")
        params.append(status)
    if search:
        clauses.append("(d.case_number LIKE ? OR d.parties LIKE ? OR d.filename LIKE ? OR d.id IN (SELECT document_id FROM pages_fts WHERE pages_fts MATCH ?))")
        like = f"%{search}%"
        params.extend([like, like, like, f'"{search.replace(chr(34), chr(34) * 2)}"'])
    sql = DOCUMENT_SELECT + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY d.updated_at DESC, d.id DESC"
    with library.connect() as connection:
        rows = connection.execute(sql, params).fetchall()
    return {"documents": [document_dict(row) for row in rows]}


def render_page(pdf_path: Path, output_path: Path, page_number: int, dpi: int = 300, enhance: bool = True) -> tuple[float, float]:
    from PIL import Image, ImageOps

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import pypdfium2 as pdfium
        document = pdfium.PdfDocument(pdf_path)
        page = document[page_number - 1]
        bitmap = page.render(scale=dpi / 72)
        bitmap.to_pil().convert("RGB").save(output_path)
        bitmap.close()
        page.close()
        document.close()
    except Exception:
        pass
    if output_path.exists():
        with Image.open(output_path) as image:
            width, height = image.size
            if enhance:
                enhanced = ImageOps.autocontrast(ImageOps.grayscale(image))
                enhanced.save(output_path.with_name(output_path.stem + "-enhanced.png"), optimize=True)
        return float(width), float(height)

    tool = find_tool("pdftoppm")
    if not tool:
        raise RuntimeError("缺少 pdftoppm，无法渲染扫描页")
    prefix = output_path.with_suffix("")
    result = run_command([
        tool, "-f", str(page_number), "-l", str(page_number), "-singlefile",
        "-r", str(dpi), "-png", str(pdf_path), str(prefix),
    ])
    if result.returncode != 0 or not output_path.exists():
        raise RuntimeError(result.stderr.strip() or f"第 {page_number} 页渲染失败")
    with Image.open(output_path) as image:
        width, height = image.size
        if enhance:
            enhanced = ImageOps.autocontrast(ImageOps.grayscale(image))
            enhanced.save(output_path.with_name(output_path.stem + "-enhanced.png"), optimize=True)
    return float(width), float(height)


def native_page_text(pdf_path: Path, page_number: int) -> str:
    try:
        import pypdfium2 as pdfium
        document = pdfium.PdfDocument(pdf_path)
        page = document[page_number - 1]
        text_page = page.get_textpage()
        text = text_page.get_text_range().strip()
        text_page.close()
        page.close()
        document.close()
        return text
    except Exception:
        pass
    tool = find_tool("pdftotext")
    if not tool:
        return ""
    result = run_command([tool, "-f", str(page_number), "-l", str(page_number), "-layout", str(pdf_path), "-"])
    return result.stdout.strip() if result.returncode == 0 else ""


def tesseract_ocr(image_path: Path) -> tuple[str, float, list[dict[str, Any]], str]:
    tool = find_tool("tesseract")
    if not tool:
        return "", 0.0, [], ""
    command_env = os.environ.copy()
    tessdata = Path(tool).resolve().parent / "tessdata"
    if tessdata.is_dir():
        command_env["TESSDATA_PREFIX"] = str(tessdata)
    version_result = run_command([tool, "--version"], timeout=30, env=command_env)
    version = (version_result.stdout or version_result.stderr).splitlines()[0] if (version_result.stdout or version_result.stderr) else ""
    result = run_command([tool, str(image_path), "stdout", "-l", "eng", "--psm", "3", "tsv"], timeout=600, env=command_env)
    if result.returncode != 0:
        return "", 0.0, [], version
    lines = result.stdout.splitlines()
    if not lines:
        return "", 0.0, [], version
    words_by_line: dict[tuple[str, str, str, str], list[tuple[str, float, list[int]]]] = {}
    for raw in lines[1:]:
        parts = raw.split("\t", 11)
        if len(parts) < 12 or not parts[11].strip():
            continue
        try:
            confidence = float(parts[10])
            bbox = [int(parts[6]), int(parts[7]), int(parts[8]), int(parts[9])]
        except ValueError:
            continue
        key = (parts[1], parts[2], parts[3], parts[4])
        words_by_line.setdefault(key, []).append((parts[11], confidence, bbox))
    blocks: list[dict[str, Any]] = []
    all_confidence: list[float] = []
    for words in words_by_line.values():
        text = " ".join(word[0] for word in words)
        left = min(word[2][0] for word in words)
        top = min(word[2][1] for word in words)
        right = max(word[2][0] + word[2][2] for word in words)
        bottom = max(word[2][1] + word[2][3] for word in words)
        valid = [word[1] for word in words if word[1] >= 0]
        confidence = sum(valid) / len(valid) if valid else 0.0
        all_confidence.extend(valid)
        blocks.append({"text": text, "confidence": confidence, "bbox": [left, top, right - left, bottom - top]})
    return "\n".join(block["text"] for block in blocks), (sum(all_confidence) / len(all_confidence) if all_confidence else 0.0), blocks, version


_rapid_engine: Any = None


def rapid_ocr(image_path: Path) -> tuple[str, float, list[dict[str, Any]], str]:
    global _rapid_engine
    try:
        import rapidocr
        from rapidocr import RapidOCR
    except Exception:
        return "", 0.0, [], ""
    if _rapid_engine is None:
        _rapid_engine = RapidOCR()
    output = _rapid_engine(str(image_path))
    raw_texts = getattr(output, "txts", None)
    raw_scores = getattr(output, "scores", None)
    raw_boxes = getattr(output, "boxes", None)
    texts = list(raw_texts) if raw_texts is not None else []
    scores = list(raw_scores) if raw_scores is not None else []
    boxes = list(raw_boxes) if raw_boxes is not None else []
    if not texts and isinstance(output, (tuple, list)) and output:
        records = output[0] or []
        for record in records:
            if len(record) >= 2:
                boxes.append(record[0])
                texts.append(record[1][0])
                scores.append(record[1][1])
    blocks: list[dict[str, Any]] = []
    for index, text in enumerate(texts):
        score = float(scores[index]) * 100 if index < len(scores) and float(scores[index]) <= 1 else float(scores[index] if index < len(scores) else 0)
        raw_box = boxes[index] if index < len(boxes) else [[0, 0], [0, 0], [0, 0], [0, 0]]
        points = raw_box.tolist() if hasattr(raw_box, "tolist") else raw_box
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        blocks.append({
            "text": str(text),
            "confidence": score,
            "bbox": [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)],
        })
    try:
        version = importlib.metadata.version("rapidocr")
    except importlib.metadata.PackageNotFoundError:
        version = ""
    return "\n".join(str(text) for text in texts), (sum(block["confidence"] for block in blocks) / len(blocks) if blocks else 0.0), blocks, version


def line_quality(text: str, confidence: float = 0.0) -> float:
    words = re.findall(r"[A-Za-z]+(?:['’-][A-Za-z]+)?|\d+(?:[.,/]\d+)*", text)
    letters = re.sub(r"[^A-Za-z]", "", text)
    if not letters:
        return -100.0
    singletons = sum(1 for word in words if len(word) == 1 and word.lower() not in {"a", "i"})
    long_consonants = len(re.findall(r"(?i)[bcdfghjklmnpqrstvwxyz]{5,}", text))
    useful_words = sum(1 for word in words if len(word) >= 3)
    alpha_ratio = len(letters) / max(1, len(text.replace(" ", "")))
    return useful_words * 2.0 + alpha_ratio * 8.0 + min(confidence, 100.0) * 0.04 - singletons * 4.0 - long_consonants * 6.0


def page_language_quality(text: str, confidence: float = 0.0) -> float:
    """Score completeness and English-like word structure without trusting OCR confidence alone."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return -10000.0
    tokens = re.findall(r"[A-Za-z]+(?:['’-][A-Za-z]+)?", text)
    meaningful = sum(1 for token in tokens if len(token) >= 3)
    singletons = sum(1 for token in tokens if len(token) == 1 and token.lower() not in {"a", "i"})
    vowelless = sum(
        1 for token in tokens
        if len(token) >= 4 and not re.search(r"[aeiouy]", token, re.IGNORECASE) and not token.isupper()
    )
    normalized_lines = [re.sub(r"[^a-z0-9]+", " ", line.lower()).strip() for line in lines]
    duplicate_lines = len(normalized_lines) - len(set(line for line in normalized_lines if line))
    line_score = sum(line_quality(line, confidence) for line in lines)
    return line_score + meaningful * 0.35 - singletons * 7.0 - vowelless * 5.0 - duplicate_lines * 20.0


def fuse_ocr_lines(
    rapid_blocks: list[dict[str, Any]],
    tess_blocks: list[dict[str, Any]],
) -> tuple[str, float, list[dict[str, Any]]]:
    """Choose the stronger candidate for each visual line and retain unmatched lines."""
    rapid = sorted(rapid_blocks, key=lambda block: (float(block["bbox"][1]), float(block["bbox"][0])))
    tess = sorted(tess_blocks, key=lambda block: (float(block["bbox"][1]), float(block["bbox"][0])))
    used_tess: set[int] = set()
    chosen: list[dict[str, Any]] = []

    for rapid_block in rapid:
        rx, ry, rw, rh = [float(value) for value in rapid_block["bbox"]]
        rapid_center = ry + rh / 2
        best_index = -1
        best_distance = float("inf")
        for index, tess_block in enumerate(tess):
            if index in used_tess:
                continue
            tx, ty, tw, th = [float(value) for value in tess_block["bbox"]]
            tess_center = ty + th / 2
            distance = abs(rapid_center - tess_center)
            horizontal_overlap = max(0.0, min(rx + rw, tx + tw) - max(rx, tx))
            if distance <= max(rh, th) * 0.9 and horizontal_overlap > min(rw, tw) * 0.2 and distance < best_distance:
                best_index = index
                best_distance = distance

        candidate = rapid_block
        if best_index >= 0:
            tess_block = tess[best_index]
            used_tess.add(best_index)
            rapid_score = line_quality(str(rapid_block["text"]), float(rapid_block.get("confidence", 0)))
            tess_score = line_quality(str(tess_block["text"]), float(tess_block.get("confidence", 0)))
            rapid_letters = len(re.sub(r"[^A-Za-z]", "", str(rapid_block["text"])))
            tess_letters = len(re.sub(r"[^A-Za-z]", "", str(tess_block["text"])))
            if tess_score > rapid_score + 1.5 or tess_letters > rapid_letters * 1.3:
                candidate = tess_block
        chosen.append(dict(candidate))

    for index, tess_block in enumerate(tess):
        if index not in used_tess and line_quality(str(tess_block["text"]), float(tess_block.get("confidence", 0))) > 2:
            chosen.append(dict(tess_block))

    chosen.sort(key=lambda block: (float(block["bbox"][1]), float(block["bbox"][0])))
    confidences = [float(block.get("confidence", 0)) for block in chosen if block.get("text")]
    return "\n".join(str(block["text"]) for block in chosen if block.get("text")), (sum(confidences) / len(confidences) if confidences else 0.0), chosen


def normalized_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def has_usable_text_layer(text: str) -> bool:
    without_footer = re.sub(r"Page\s+\d+\s+of\s+\d+", "", text, flags=re.IGNORECASE)
    compact = normalized_text(without_footer)
    words = re.findall(r"[A-Za-z0-9]+", compact)
    return len(compact) >= 20 or len(words) >= 5


def infer_document_metadata(text: str) -> tuple[str, str]:
    case_number = ""
    compact = re.sub(r"\s+", " ", text)
    complaint = re.search(r"ODPC\s+COMPLAINT\s+NO\.?\s*(\d+)\s+OF\s+(\d{4})", compact, re.IGNORECASE)
    direct = re.search(r"ODPC\s*/\s*(COMP|SM)\s*/\s*(\d+)\s*/\s*(\d{4})", compact, re.IGNORECASE)
    if complaint:
        case_number = f"ODPC/COMP/{complaint.group(1)}/{complaint.group(2)}"
    elif direct:
        case_number = f"ODPC/{direct.group(1).upper()}/{direct.group(2)}/{direct.group(3)}"

    lines = [re.sub(r"\.{2,}.*$", "", line).strip(" .:-") for line in text.splitlines()]
    versus_index = next((index for index, line in enumerate(lines) if "VERSUS" in line.upper()), -1)
    parties = ""
    if versus_index >= 0:
        excluded = ("COMPLAINT", "COMPLAINANT", "RESPONDENT", "COMMISSIONER", "REPUBLIC", "DETERMINATION")
        valid_party_line = lambda line: len(line) > 3 and line.upper() != "KENYA" and not any(word in line.upper() for word in excluded)
        before = [line for line in lines[max(0, versus_index - 5):versus_index] if valid_party_line(line)]
        after = [line for line in lines[versus_index + 1:versus_index + 7] if valid_party_line(line)]
        if before and after:
            parties = f"{before[-1]} vs {after[0]}"
    return case_number, parties


def issue(
    connection: sqlite3.Connection,
    document_id: int,
    page_id: int,
    page_number: int,
    issue_type: str,
    message: str,
    severity: str = "warning",
    primary: str = "",
    secondary: str = "",
) -> None:
    connection.execute(
        """INSERT INTO quality_issues
           (document_id,page_id,page_number,issue_type,severity,message,primary_candidate,secondary_candidate)
           VALUES(?,?,?,?,?,?,?,?)""",
        (document_id, page_id, page_number, issue_type, severity, message, primary, secondary),
    )


def process_document(library: Library, document_id: int, mode: str = "fast") -> None:
    if mode not in {"fast", "quality"}:
        raise ValueError("未知的提取模式")
    with library.connect() as connection:
        document = connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if not document:
            raise ValueError("文档不存在")
        pdf_path = Path(document["file_path"])
        try:
            verified_count = validate_pdf_copy(pdf_path, str(document["sha256"]))
        except (OSError, ValueError) as error:
            raise RuntimeError(f"OCR 尚未开始：{error}。请重新导入原始 PDF，或把资料库迁移到非 iCloud 目录。") from error
        connection.execute("UPDATE documents SET status='ocr_running',error_message='',updated_at=? WHERE id=?", (now_iso(), document_id))
        if verified_count != int(document["page_count"]):
            connection.execute("UPDATE documents SET page_count=? WHERE id=?", (verified_count, document_id))
        old_page_ids = [row["id"] for row in connection.execute("SELECT id FROM pages WHERE document_id=?", (document_id,))]
        for page_id in old_page_ids:
            connection.execute("DELETE FROM pages_fts WHERE page_id=?", (page_id,))
        connection.execute("DELETE FROM quality_issues WHERE document_id=?", (document_id,))
        connection.execute("DELETE FROM pages WHERE document_id=?", (document_id,))

    count = verified_count
    render_dir = library.root / "renders" / document["sha256"][:12]
    render_dir.mkdir(parents=True, exist_ok=True)
    rapid_available = action_health({})["rapidocr"]
    tess_available = action_health({})["tesseract"]

    ocr_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    for page_number in range(1, count + 1):
        native_text = native_page_text(pdf_path, page_number)
        image_path = render_dir / f"page-{page_number:04d}.png"
        comparison_primary = ""
        comparison_secondary = ""
        ocr_runs_data: list[tuple[str, str, str, str, float, list[dict[str, Any]]]] = []

        if has_usable_text_layer(native_text):
            width, height = render_page(pdf_path, image_path, page_number, dpi=144, enhance=False)
            primary_text, primary_conf, primary_blocks, primary_engine, primary_version = native_text, 100.0, [], "native_pdf", ""
            secondary_text = ""
            ocr_runs_data.append(("native_pdf", "", "embedded_text_layer", native_text, 100.0, []))
        else:
            width, height = render_page(pdf_path, image_path, page_number, dpi=300, enhance=True)
            enhanced_path = image_path.with_name(image_path.stem + "-enhanced.png")
            rapid_future = ocr_pool.submit(rapid_ocr, image_path) if rapid_available and (mode == "quality" or not tess_available) else None
            tess_future = ocr_pool.submit(tesseract_ocr, enhanced_path) if tess_available else None
            rapid_text, rapid_conf, rapid_blocks, rapid_version = rapid_future.result() if rapid_future else ("", 0.0, [], "")
            tess_text, tess_conf, tess_blocks, tess_version = tess_future.result() if tess_future else ("", 0.0, [], "")
            comparison_primary, comparison_secondary = rapid_text, tess_text
            if rapid_text and tess_text:
                rapid_quality = page_language_quality(rapid_text, rapid_conf)
                tess_quality = page_language_quality(tess_text, tess_conf)
                if tess_quality >= rapid_quality:
                    primary_text, primary_conf, primary_blocks = tess_text, tess_conf, tess_blocks
                    secondary_text = rapid_text
                    primary_engine, primary_version = "tesseract", tess_version
                else:
                    primary_text, primary_conf, primary_blocks = rapid_text, rapid_conf, rapid_blocks
                    secondary_text = tess_text
                    primary_engine, primary_version = "rapidocr", rapid_version
                ocr_runs_data.extend([
                    ("rapidocr", rapid_version, "original_300dpi", rapid_text, rapid_conf, rapid_blocks),
                    ("tesseract", tess_version, "grayscale_autocontrast_300dpi", tess_text, tess_conf, tess_blocks),
                    ("selected", primary_version, f"page_language_quality:{primary_engine}", primary_text, primary_conf, primary_blocks),
                ])
            elif rapid_text:
                primary_text, primary_conf, primary_blocks, primary_engine, primary_version = rapid_text, rapid_conf, rapid_blocks, "rapidocr", rapid_version
                secondary_text = ""
                ocr_runs_data.append(("rapidocr", rapid_version, "original_300dpi", rapid_text, rapid_conf, rapid_blocks))
            else:
                primary_text, primary_conf, primary_blocks, primary_engine, primary_version = tess_text, tess_conf, tess_blocks, "tesseract", tess_version
                secondary_text = ""
                ocr_runs_data.append(("tesseract", tess_version, "grayscale_autocontrast_300dpi", tess_text, tess_conf, tess_blocks))

        with library.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO pages
                   (document_id,page_number,width,height,image_path,primary_text,secondary_text,final_text,confidence,blocks_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (document_id, page_number, width, height, str(image_path), primary_text, secondary_text, primary_text, primary_conf, json.dumps(primary_blocks, ensure_ascii=False)),
            )
            page_id = int(cursor.lastrowid)
            for engine, engine_version, preprocessing, run_text, run_confidence, run_blocks in ocr_runs_data:
                connection.execute(
                    """INSERT INTO ocr_runs(page_id,engine,engine_version,preprocessing,text,confidence,blocks_json,created_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (page_id, engine, engine_version, preprocessing, run_text, run_confidence, json.dumps(run_blocks, ensure_ascii=False), now_iso()),
                )
            connection.execute("INSERT INTO pages_fts(page_id,document_id,text) VALUES(?,?,?)", (page_id, document_id, primary_text))

            compact_primary = normalized_text(primary_text)
            compact_secondary = normalized_text(comparison_secondary or secondary_text)
            if not compact_primary:
                issue(connection, document_id, page_id, page_number, "empty_page", "本页没有识别出正文，请确认原页是否为空白。", "required")
            if primary_conf < 82 and compact_primary:
                issue(connection, document_id, page_id, page_number, "low_confidence", f"本页平均置信度为 {primary_conf:.1f}%，低于 82%。", primary=primary_text)
            low_blocks = [block["text"] for block in primary_blocks if float(block.get("confidence", 100)) < 75 and block.get("text")]
            if low_blocks:
                preview = " | ".join(low_blocks[:4])
                issue(connection, document_id, page_id, page_number, "low_confidence", f"发现 {len(low_blocks)} 个低置信度文本行：{preview}", primary=primary_text, secondary=secondary_text)
            if normalized_text(comparison_primary) and normalized_text(comparison_secondary):
                similarity = difflib.SequenceMatcher(None, normalized_text(comparison_primary), normalized_text(comparison_secondary)).ratio()
                if similarity < 0.97:
                    issue(connection, document_id, page_id, page_number, "engine_disagreement", f"两个 OCR 引擎的一致度为 {similarity * 100:.1f}%，已选择整页语言完整度更高的结果，另一份保留供对照。", primary=comparison_primary, secondary=comparison_secondary)
            elif primary_engine != "native_pdf" and not (rapid_available and tess_available):
                missing = "RapidOCR" if not rapid_available else "Tesseract"
                issue(connection, document_id, page_id, page_number, "engine_missing", f"{missing} 不可用，本页尚未完成双引擎交叉验证。", "required")
            if "\ufffd" in primary_text or re.search(r"[^\x00-\x7F]{4,}", primary_text):
                issue(connection, document_id, page_id, page_number, "suspicious_text", "发现替换字符或连续异常字符。", primary=primary_text)
            footer_match = re.search(r"Page\s+(\d+)\s+of\s+(\d+)", primary_text, re.IGNORECASE)
            if footer_match and (int(footer_match.group(1)) != page_number or int(footer_match.group(2)) != count):
                issue(connection, document_id, page_id, page_number, "page_number", f"页脚显示 {footer_match.group(0)}，与 PDF 页序不一致。", "required")

            if page_number == 1:
                inferred_case, inferred_parties = infer_document_metadata(primary_text)
                connection.execute(
                    """UPDATE documents SET
                       case_number=CASE WHEN case_number='' THEN ? ELSE case_number END,
                       parties=CASE WHEN parties='' THEN ? ELSE parties END
                       WHERE id=?""",
                    (inferred_case, inferred_parties, document_id),
                )

    ocr_pool.shutdown(wait=True)
    with library.connect() as connection:
        open_issues = connection.execute("SELECT COUNT(*) count FROM quality_issues WHERE document_id=? AND status='open'", (document_id,)).fetchone()["count"]
        next_status = "needs_review" if open_issues else "approved"
        connection.execute("UPDATE documents SET status=?,updated_at=? WHERE id=?", (next_status, now_iso(), document_id))


def page_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "page_number": row["page_number"],
        "width": row["width"],
        "height": row["height"],
        "image_path": row["image_path"],
        "final_text": row["final_text"],
        "primary_text": row["primary_text"],
        "secondary_text": row["secondary_text"],
        "confidence": row["confidence"],
        "blocks": json.loads(row["blocks_json"] or "[]"),
        "reviewed": bool(row["reviewed"]),
    }


def get_document(library: Library, document_id: int) -> dict[str, Any]:
    with library.connect() as connection:
        document = connection.execute(DOCUMENT_SELECT + " WHERE d.id=?", (document_id,)).fetchone()
        if not document:
            raise ValueError("文档不存在")
        pages = [page_dict(row) for row in connection.execute("SELECT * FROM pages WHERE document_id=? ORDER BY page_number", (document_id,))]
        issues = [dict(row) for row in connection.execute("SELECT id,page_number,issue_type,severity,message,primary_candidate,secondary_candidate,status FROM quality_issues WHERE document_id=? ORDER BY CASE severity WHEN 'required' THEN 0 ELSE 1 END,page_number,id", (document_id,))]
        revisions = [dict(row) for row in connection.execute("""SELECT r.id,r.page_number,r.before_text,r.after_text,r.reviewer,r.reason,r.created_at
            FROM revisions r JOIN pages p ON p.id=r.page_id WHERE p.document_id=? ORDER BY r.created_at DESC""", (document_id,))]
    return {"document": document_dict(document), "pages": pages, "issues": issues, "revisions": revisions}


def action_get_document(payload: dict[str, Any]) -> dict[str, Any]:
    library = Library.from_payload(payload)
    library.initialize()
    return get_document(library, int(payload["document_id"]))


def action_delete_documents(payload: dict[str, Any]) -> dict[str, Any]:
    library = Library.from_payload(payload)
    library.initialize()
    document_ids = list(dict.fromkeys(int(value) for value in payload.get("document_ids") or []))
    if not document_ids:
        return {"deleted": 0, "missing": 0, "failed_paths": []}

    placeholders = ",".join("?" for _ in document_ids)
    with library.connect() as connection:
        documents = connection.execute(
            f"SELECT id,sha256,file_path,status FROM documents WHERE id IN ({placeholders})",
            document_ids,
        ).fetchall()
        processing = [row["id"] for row in documents if row["status"] in {"downloading", "ocr_running"}]
        if processing:
            raise ValueError(f"有 {len(processing)} 份文件正在处理，请暂停或等待完成后再删除")
        export_rows = connection.execute(
            f"SELECT export_dir FROM exports WHERE document_id IN ({placeholders})",
            document_ids,
        ).fetchall()
        connection.execute(
            f"UPDATE source_entries SET document_id=NULL WHERE document_id IN ({placeholders})",
            document_ids,
        )
        connection.execute(
            f"DELETE FROM pages_fts WHERE document_id IN ({placeholders})",
            document_ids,
        )
        connection.execute(
            f"DELETE FROM documents WHERE id IN ({placeholders})",
            document_ids,
        )

    failed_paths: list[str] = []

    def remove_inside(path: Path, allowed_root: Path) -> None:
        resolved = path.expanduser().resolve()
        root = allowed_root.resolve()
        if not resolved.is_relative_to(root):
            return
        try:
            if resolved.is_dir():
                shutil.rmtree(resolved)
            elif resolved.exists() or resolved.is_symlink():
                resolved.unlink()
        except OSError:
            failed_paths.append(str(resolved))

    for document in documents:
        remove_inside(Path(document["file_path"]), library.root / "originals")
        remove_inside(library.root / "renders" / document["sha256"][:12], library.root / "renders")
    for export_row in export_rows:
        remove_inside(Path(export_row["export_dir"]), library.root / "exports")
    for name in ("library-index.csv", "library-manifest.json", "odpc-library.sqlite3"):
        remove_inside(library.root / "exports" / name, library.root / "exports")

    return {
        "deleted": len(documents),
        "missing": len(document_ids) - len(documents),
        "failed_paths": failed_paths,
    }


def action_process_document(payload: dict[str, Any]) -> dict[str, Any]:
    library = Library.from_payload(payload)
    library.initialize()
    document_id = int(payload["document_id"])
    try:
        process_document(library, document_id, str(payload.get("mode") or "fast"))
    except Exception as error:
        with library.connect() as connection:
            connection.execute("UPDATE documents SET status='error',error_message=?,updated_at=? WHERE id=?", (str(error), now_iso(), document_id))
        raise
    return get_document(library, document_id)


def action_process_documents(payload: dict[str, Any]) -> dict[str, Any]:
    library = Library.from_payload(payload)
    library.initialize()
    document_ids = [int(value) for value in payload.get("document_ids") or []]
    mode = str(payload.get("mode") or "fast")
    completed: list[int] = []
    failed: list[dict[str, Any]] = []

    def process_one(document_id: int) -> tuple[int, str]:
        try:
            process_document(library, document_id, mode)
            return document_id, ""
        except Exception as error:
            with library.connect() as connection:
                connection.execute("UPDATE documents SET status='error',error_message=?,updated_at=? WHERE id=?", (str(error), now_iso(), document_id))
            return document_id, str(error)

    if mode == "fast" and len(document_ids) > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(2, len(document_ids))) as pool:
            results = list(pool.map(process_one, document_ids))
    else:
        results = [process_one(document_id) for document_id in document_ids]
    for document_id, error in results:
        if error:
            failed.append({"document_id": document_id, "error": error})
        else:
            completed.append(document_id)
    return {"completed": completed, "failed": failed}


def action_save_page(payload: dict[str, Any]) -> dict[str, Any]:
    library = Library.from_payload(payload)
    library.initialize()
    page_id = int(payload["page_id"])
    after = str(payload.get("text") or "")
    reviewer = str(payload.get("reviewer") or "local").strip()
    reason = str(payload.get("reason") or "本地修改").strip()
    with library.connect() as connection:
        page = connection.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()
        if not page:
            raise ValueError("页面不存在")
        connection.execute(
            "INSERT INTO revisions(page_id,page_number,before_text,after_text,reviewer,reason,created_at) VALUES(?,?,?,?,?,?,?)",
            (page_id, page["page_number"], page["final_text"], after, reviewer, reason, now_iso()),
        )
        connection.execute("UPDATE pages SET final_text=?,reviewed=1 WHERE id=?", (after, page_id))
        connection.execute("DELETE FROM pages_fts WHERE page_id=?", (page_id,))
        connection.execute("INSERT INTO pages_fts(page_id,document_id,text) VALUES(?,?,?)", (page_id, page["document_id"], after))
        connection.execute("UPDATE documents SET updated_at=? WHERE id=?", (now_iso(), page["document_id"]))
    return {"ok": True}


def action_resolve_issue(payload: dict[str, Any]) -> dict[str, Any]:
    library = Library.from_payload(payload)
    library.initialize()
    status = str(payload.get("status") or "resolved")
    if status not in {"resolved", "unreadable"}:
        raise ValueError("无效的疑点处理状态")
    with library.connect() as connection:
        issue_row = connection.execute("SELECT * FROM quality_issues WHERE id=?", (int(payload["issue_id"]),)).fetchone()
        if not issue_row:
            raise ValueError("疑点不存在")
        connection.execute("UPDATE quality_issues SET status=?,resolved_at=? WHERE id=?", (status, now_iso(), issue_row["id"]))
        connection.execute("UPDATE documents SET updated_at=? WHERE id=?", (now_iso(), issue_row["document_id"]))
    return {"ok": True}


def action_approve_document(payload: dict[str, Any]) -> dict[str, Any]:
    library = Library.from_payload(payload)
    library.initialize()
    document_id = int(payload["document_id"])
    with library.connect() as connection:
        page_total = connection.execute("SELECT COUNT(*) count FROM pages WHERE document_id=?", (document_id,)).fetchone()["count"]
        if not page_total:
            raise ValueError("文档尚未执行 OCR")
        connection.execute("UPDATE documents SET status='approved',updated_at=? WHERE id=?", (now_iso(), document_id))
    return {"ok": True}


def slug_for_document(document: sqlite3.Row) -> str:
    raw = document["case_number"] or Path(document["filename"]).stem
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-")
    return f"{document['id']:04d}-{slug or 'determination'}"


def make_searchable_pdf(document: sqlite3.Row, pages: list[sqlite3.Row], destination: Path) -> None:
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas

    if pages and all(not json.loads(page["blocks_json"] or "[]") for page in pages):
        shutil.copy2(document["file_path"], destination)
        return

    reader = PdfReader(document["file_path"])
    writer = PdfWriter()
    for index, original_page in enumerate(reader.pages):
        page = pages[index]
        width = float(original_page.mediabox.width)
        height = float(original_page.mediabox.height)
        packet = io.BytesIO()
        overlay = canvas.Canvas(packet, pagesize=(width, height))
        blocks = json.loads(page["blocks_json"] or "[]")
        edited_lines = [line for line in page["final_text"].splitlines() if line.strip()]
        if blocks:
            for block_index, block in enumerate(blocks):
                text = edited_lines[block_index] if block_index < len(edited_lines) else block.get("text", "")
                x, y, box_width, box_height = block.get("bbox", [0, 0, 0, 0])
                image_width = page["width"] or 1
                image_height = page["height"] or 1
                text_object = overlay.beginText()
                text_object.setTextRenderMode(3)
                text_object.setFont("Helvetica", max(4, box_height / image_height * height * 0.72))
                text_object.setTextOrigin(x / image_width * width, height - ((y + box_height) / image_height * height))
                text_object.textLine(text[:1000])
                overlay.drawText(text_object)
        else:
            text_object = overlay.beginText(24, height - 24)
            text_object.setTextRenderMode(3)
            text_object.setFont("Helvetica", 9)
            for line in edited_lines:
                text_object.textLine(line[:1000])
            overlay.drawText(text_object)
        overlay.save()
        packet.seek(0)
        overlay_page = PdfReader(packet).pages[0]
        original_page.merge_page(overlay_page)
        writer.add_page(original_page)
    if reader.metadata:
        writer.add_metadata({key: str(value) for key, value in reader.metadata.items() if value is not None})
    with destination.open("wb") as stream:
        writer.write(stream)


def export_document(library: Library, document_id: int) -> dict[str, Any]:
    with library.connect() as connection:
        document = connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if not document:
            raise ValueError("文档不存在")
        if document["status"] not in {"needs_review", "approved", "exported"}:
            raise ValueError("文档尚未完成文本提取")
        pages = connection.execute("SELECT * FROM pages WHERE document_id=? ORDER BY page_number", (document_id,)).fetchall()
        issues = [dict(row) for row in connection.execute("SELECT * FROM quality_issues WHERE document_id=? ORDER BY page_number,id", (document_id,))]
        revisions = [dict(row) for row in connection.execute("""SELECT r.* FROM revisions r JOIN pages p ON p.id=r.page_id
            WHERE p.document_id=? ORDER BY r.created_at""", (document_id,))]
        sources = [dict(row) for row in connection.execute("SELECT case_number,parties,pdf_url,raw_cells_json FROM source_entries WHERE document_id=?", (document_id,))]

    export_dir = library.root / "exports" / slug_for_document(document)
    export_dir.mkdir(parents=True, exist_ok=True)
    original_path = Path(document["file_path"])
    original_copy = export_dir / "original.pdf"
    shutil.copy2(original_path, original_copy)
    searchable_path = export_dir / "searchable.pdf"
    make_searchable_pdf(document, pages, searchable_path)

    page_texts = [row["final_text"] for row in pages]
    txt_path = export_dir / "extracted.txt"
    txt_path.write_text("\n\n".join(f"===== Page {index + 1} of {len(pages)} =====\n{text}" for index, text in enumerate(page_texts)), encoding="utf-8")
    md_path = export_dir / "extracted.md"
    md_path.write_text("\n\n".join(f"## Page {index + 1}\n\n{text}" for index, text in enumerate(page_texts)), encoding="utf-8")

    data = {
        "schema_version": 1,
        "exported_at": now_iso(),
        "document": {key: document[key] for key in document.keys()},
        "sources": sources,
        "pages": [{**{key: row[key] for key in row.keys() if key != "blocks_json"}, "blocks": json.loads(row["blocks_json"] or "[]")} for row in pages],
        "quality_issues": issues,
        "revisions": revisions,
    }
    json_path = export_dir / "extracted.json"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    files = [original_copy, searchable_path, txt_path, md_path, json_path]
    manifest = {
        "document_id": document_id,
        "source_sha256": document["sha256"],
        "review_complete": document["status"] in {"approved", "exported"},
        "generated_at": now_iso(),
        "files": [{"name": path.name, "sha256": sha256_file(path), "size": path.stat().st_size} for path in files],
    }
    manifest_path = export_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    files.append(manifest_path)
    with library.connect() as connection:
        connection.execute("INSERT INTO exports(document_id,export_dir,manifest_json,created_at) VALUES(?,?,?,?)", (document_id, str(export_dir), json.dumps(manifest, ensure_ascii=False), now_iso()))
        connection.execute("UPDATE documents SET status='exported',updated_at=? WHERE id=?", (now_iso(), document_id))
    return {"export_dir": str(export_dir), "files": [str(path) for path in files]}


def action_export_document(payload: dict[str, Any]) -> dict[str, Any]:
    library = Library.from_payload(payload)
    library.initialize()
    return export_document(library, int(payload["document_id"]))


def action_export_library(payload: dict[str, Any]) -> dict[str, Any]:
    library = Library.from_payload(payload)
    library.initialize()
    export_dir = library.root / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    with library.connect() as connection:
        rows = connection.execute(DOCUMENT_SELECT + " ORDER BY d.id").fetchall()
        index_path = export_dir / "library-index.csv"
        with index_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["document_id", "case_number", "parties", "status", "page_count", "open_issues", "sha256", "source_url", "file_path"])
            for row in rows:
                writer.writerow([row["id"], row["case_number"], row["parties"], row["status"], row["page_count"], row["issue_count"], row["sha256"], row["source_url"], row["file_path"]])
        manifest_path = export_dir / "library-manifest.json"
        manifest_path.write_text(json.dumps({
            "schema_version": 1,
            "generated_at": now_iso(),
            "library_path": str(library.root),
            "documents": [document_dict(row) for row in rows],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        connection.execute("PRAGMA wal_checkpoint(FULL)")
    database_copy = export_dir / "odpc-library.sqlite3"
    shutil.copy2(library.database, database_copy)
    return {"export_dir": str(export_dir), "files": [str(index_path), str(manifest_path), str(database_copy)]}


ACTIONS = {
    "health": action_health,
    "init": action_init,
    "open_library": action_open_library,
    "recent_library": action_recent_library,
    "summary": action_summary,
    "sync": action_sync,
    "import_pdfs": action_import_pdfs,
    "list_documents": action_list_documents,
    "get_document": action_get_document,
    "delete_documents": action_delete_documents,
    "process_document": action_process_document,
    "process_documents": action_process_documents,
    "save_page": action_save_page,
    "resolve_issue": action_resolve_issue,
    "approve_document": action_approve_document,
    "export_document": action_export_document,
    "export_library": action_export_library,
}


def main(argv: list[str]) -> int:
    try:
        if len(argv) < 2:
            raise ValueError("Usage: odpc-ocr-worker ACTION [JSON_PAYLOAD]")
        action = argv[1]
        if action not in ACTIONS:
            raise ValueError(f"Unknown action: {action}")
        payload = json.loads(argv[2]) if len(argv) > 2 else {}
        data = ACTIONS[action](payload)
        print(json.dumps({"ok": True, "data": data}, ensure_ascii=False))
        return 0
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
