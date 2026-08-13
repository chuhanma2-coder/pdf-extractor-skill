# Runtime Reference

## Supported Runtime

The first public release supports Apple Silicon macOS (M1, M2, M3, M4). It installs into `~/.local/share/pdf-extractor` and requires about 130 MB for the archive plus extracted application files. The PDF and results remain in user-selected folders.

## Extraction Modes

- `quality`: for scanned PDFs. Renders at 300 DPI and compares RapidOCR with Tesseract.
- `fast`: for batches where time matters. Uses the standard fast workflow.
- Embedded-text PDFs: reads native text regardless of mode.

## Troubleshooting

- If macOS blocks the app, run the installer again. It removes the download quarantine marker only from the installed runtime.
- If `verify-runtime-macos.sh` reports a missing worker, reinstall the runtime; do not download model files from another source.
- `open_issues` in the JSON summary means OCR needs human attention. The extracted files are still available.
- Windows support requires the separately verified Windows release asset. Do not attempt to run the macOS package on Windows.
