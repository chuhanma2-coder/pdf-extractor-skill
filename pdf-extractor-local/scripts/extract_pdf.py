#!/usr/bin/env python3
"""Run the bundled local worker and return a compact JSON result for an Agent."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def runtime_worker() -> Path:
    explicit = os.environ.get("PDF_EXTRACTOR_WORKER", "").strip()
    if explicit:
        worker = Path(explicit).expanduser().resolve()
    else:
        root = Path(os.environ.get("PDF_EXTRACTOR_RUNTIME_ROOT", "~/.local/share/pdf-extractor")).expanduser()
        worker = root / "PDF 提取器.app/Contents/Resources/worker/odpc-ocr-worker"
    if not worker.is_file() or not os.access(worker, os.X_OK):
        raise RuntimeError("Local runtime is missing. Run install-runtime-macos.sh first.")
    return worker


def invoke(worker: Path, action: str, payload: dict) -> dict:
    result = subprocess.run(
        [str(worker), action, json.dumps(payload, ensure_ascii=False)],
        check=False,
        capture_output=True,
        text=True,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(result.stderr.strip() or f"Worker exited with code {result.returncode}.")
    reply = json.loads(lines[-1])
    if result.returncode or not reply.get("ok"):
        raise RuntimeError(str(reply.get("error") or result.stderr.strip() or "Extraction failed."))
    return reply["data"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract local PDFs with the bundled offline runtime.")
    parser.add_argument("pdf", nargs="+", help="One or more PDF paths")
    parser.add_argument("--output-dir", required=True, help="Directory that receives exports and local processing data")
    parser.add_argument("--mode", choices=("quality", "fast"), default="quality")
    args = parser.parse_args()

    pdfs = [Path(item).expanduser().resolve() for item in args.pdf]
    invalid = [str(item) for item in pdfs if not item.is_file() or item.suffix.lower() != ".pdf"]
    if invalid:
        raise RuntimeError("Not readable PDF files: " + ", ".join(invalid))

    output_dir = Path(args.output_dir).expanduser().resolve()
    library = output_dir / ".pdf-extractor-library"
    worker = runtime_worker()
    health = invoke(worker, "health", {})
    if not health.get("offline_ocr_ready"):
        raise RuntimeError("Offline OCR runtime is incomplete: " + "; ".join(health.get("notes") or []))

    invoke(worker, "init", {"library_path": str(library), "remember": False})
    source_hashes = {sha256(path): path for path in pdfs}
    imported = invoke(worker, "import_pdfs", {"library_path": str(library), "paths": [str(path) for path in pdfs]})
    if imported.get("failed"):
        raise RuntimeError("Import failed: " + " | ".join(imported["failed"]))

    documents = invoke(worker, "list_documents", {"library_path": str(library)})["documents"]
    selected = [document for document in documents if document.get("sha256") in source_hashes]
    if len(selected) != len(source_hashes):
        raise RuntimeError("Could not resolve every imported PDF in the local library.")

    results = []
    for document in selected:
        document_id = int(document["id"])
        processed = invoke(worker, "process_document", {"library_path": str(library), "document_id": document_id, "mode": args.mode})
        exported = invoke(worker, "export_document", {"library_path": str(library), "document_id": document_id})
        results.append({
            "source_pdf": str(source_hashes[document["sha256"]]),
            "document_id": document_id,
            "status": processed["document"]["status"],
            "open_issues": len(processed.get("issues") or []),
            "exports": exported["files"],
        })

    print(json.dumps({"ok": True, "mode": args.mode, "output_dir": str(output_dir), "documents": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
