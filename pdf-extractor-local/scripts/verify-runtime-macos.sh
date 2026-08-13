#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="${PDF_EXTRACTOR_RUNTIME_ROOT:-$HOME/.local/share/pdf-extractor}"
WORKER="${PDF_EXTRACTOR_WORKER:-$INSTALL_ROOT/PDF 提取器.app/Contents/MacOS/odpc-ocr-worker}"

if [[ ! -x "$WORKER" ]]; then
  echo "Local runtime not installed. Run install-runtime-macos.sh first." >&2
  exit 1
fi
"$WORKER" health
