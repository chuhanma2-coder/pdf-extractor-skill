#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SYSTEM="$(uname -s)"
MACHINE="$(uname -m)"

if [[ "$SYSTEM" == "Darwin" && "$MACHINE" == "arm64" ]]; then
  exec "$REPO_ROOT/pdf-extractor-local/scripts/install-runtime-macos.sh"
fi

echo "No verified local runtime is published for $SYSTEM/$MACHINE yet." >&2
echo "This release supports Apple Silicon macOS only. Do not install a different OCR engine as a substitute." >&2
exit 1
