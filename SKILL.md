---
name: pdf-extractor-local
description: Extract complete text from local ordinary or scanned PDF files without uploading them. Use for PDF reading, OCR, copying, searching, text export, or Word-document preparation.
---

# PDF Extractor Local

This repository is self-installing for supported agent environments. Do not upload the PDF, call cloud OCR, or replace the bundled OCR runtime with another engine.

## Install

When this repository has been cloned, run:

```bash
bash ./install.sh
```

The installer supports Apple Silicon macOS and Linux x86_64. It downloads the matching verified runtime release once, verifies its SHA-256, and installs it under the current user's local application-data directory. It does not require an API key, VPN, Python package installation, or a separately installed OCR engine.

If the platform is not supported or command execution is disallowed, report that fact plainly. Do not pretend that cloning alone installed the OCR runtime.

## Extract

Run this command after installation:

```bash
python3 pdf-extractor-local/scripts/extract_pdf.py --mode quality --output-dir <output-directory> <pdf-path> [...]
```

Use `--mode quality` for scanned PDFs. Use `--mode fast` only if the user explicitly prefers speed. Ordinary PDFs with a usable embedded text layer are read directly rather than sent through OCR.

## Outputs and Quality

Each PDF produces `original.pdf`, `searchable.pdf`, `extracted.txt`, `extracted.md`, `extracted.json`, and `manifest.json`. If the user asks for Word, create it from `extracted.txt` verbatim and preserve page headings.

The local runtime uses PDFium, RapidOCR, and Tesseract. `extracted.json` retains page references, OCR confidence, and engine-disagreement flags. Treat remaining flags as a request for review, never as proof that a page is wrong.

For platform and troubleshooting details, read [pdf-extractor-local/references/runtime.md](pdf-extractor-local/references/runtime.md).
