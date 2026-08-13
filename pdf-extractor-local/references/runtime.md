# Runtime Reference

## Supported Runtime

Verified runtimes support Apple Silicon macOS (M1, M2, M3, M4) and Linux x86_64. Both install into `~/.local/share/pdf-extractor`. The PDF and results remain in user-selected folders.

## Extraction Modes

- `quality`: for scanned PDFs. Renders at 300 DPI and compares RapidOCR with Tesseract.
- `fast`: for batches where time matters. Uses the standard fast workflow.
- Embedded-text PDFs: reads native text regardless of mode.

## Troubleshooting

- If macOS blocks the app, run the installer again. It removes the download quarantine marker only from the installed runtime.
- If the platform verifier reports a missing worker, reinstall the runtime; do not download model files from another source.
- `open_issues` in the JSON summary means OCR needs human attention. The extracted files are still available.
- Windows and Intel Mac support require separately verified release assets. Do not attempt to run a package for another platform.
