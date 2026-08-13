#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="${PDF_EXTRACTOR_RUNTIME_ROOT:-$HOME/.local/share/pdf-extractor}"
WORKER="${PDF_EXTRACTOR_WORKER:-$INSTALL_ROOT/pdf-extractor-linux-x64/run-worker}"

if [[ ! -x "$WORKER" ]]; then
  echo "Local Linux runtime not installed. Run install-runtime-linux.sh first." >&2
  exit 1
fi
"$WORKER" health
