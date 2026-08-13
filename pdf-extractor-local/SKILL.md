---
name: pdf-extractor-local
description: Extract complete text from local ordinary or scanned PDF files without uploading them. Use when a user asks to read, OCR, copy, search, export, or make a Word document from one or more local PDF files, including image-only scans.
---

# PDF Extractor Local

Use the bundled local runtime. Do not upload the PDF, send it to cloud OCR, or substitute a generative model for OCR.

## Run

1. Confirm the runtime for the current platform is present by running `bash ../../install.sh` when needed.
2. `install.sh` selects the verified Apple Silicon macOS or Linux x86_64 runtime. It downloads the fixed release archive and verifies its SHA-256 before unpacking.
3. Run `scripts/extract_pdf.py --output-dir <directory> <pdf-path> [...]`.
4. Read the JSON summary printed by the script and report the `exports` paths. Do not claim a scan is error-free when `open_issues` is nonzero.

Use `--mode quality` by default for scans. Use `--mode fast` only when the user explicitly prioritizes speed. Ordinary PDFs with usable embedded text are read directly and do not run OCR.

## Outputs

Each PDF produces `original.pdf`, `searchable.pdf`, `extracted.txt`, `extracted.md`, `extracted.json`, and `manifest.json` under the requested output directory. Preserve page headings in any derived Word document. Create a Word file only when requested, using the final `extracted.txt` verbatim rather than silently rewriting OCR text.

## Quality And Privacy

The runtime uses local PDFium, RapidOCR, and Tesseract. It keeps the original PDF unchanged and records OCR confidence and cross-engine disagreements in `extracted.json`. Treat low-confidence and disagreement flags as review requests, not proof that a page is wrong.

See `references/runtime.md` only for platform, storage, and troubleshooting details.
