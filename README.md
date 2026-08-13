# PDF Extractor Local Skill

An offline Codex Skill for extracting complete text from regular and scanned PDFs. The PDF stays on the user's computer. The runtime uses PDFium, RapidOCR, and Tesseract; it does not call cloud OCR or a generative AI service.

## Install In Codex

Ask Codex:

```text
Install the Skill from https://github.com/chuhanma2-coder/pdf-extractor-skill/tree/main/pdf-extractor-local
```

On first use, Codex runs the bundled installer. It downloads the matching local runtime from this repository's GitHub Release and verifies its SHA-256 before installing it.

## Use

Upload a PDF in an Agent conversation, or provide its local path, then ask:

```text
Use $pdf-extractor-local to extract this PDF in quality mode. Export all result files beside the PDF.
```

Each PDF gets `original.pdf`, `searchable.pdf`, `extracted.txt`, `extracted.md`, `extracted.json`, and `manifest.json`. Scan confidence and OCR disagreements are retained in JSON for review.

## Platform And Privacy

The initial release supports Apple Silicon Macs. Windows support will be published only after a separate Windows build and smoke test. No PDF is uploaded by the Skill; the only network request is the one-time download of the public runtime archive from GitHub.

## Release Asset

Version `v0.3.0` must contain the GitHub Release asset `PDF-Extractor-macOS-arm64.zip` with SHA-256:

```text
213acbd6803492b645a0b65cb723aad13085275d0fe78ac805e495718eb7dcbf
```

The archive contains third-party open-source components. Keep the corresponding notices with the release archive.
