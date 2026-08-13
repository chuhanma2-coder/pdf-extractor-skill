#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SYSTEM="$(uname -s)"
MACHINE="$(uname -m)"

if [[ "$SYSTEM" == "Darwin" && "$MACHINE" == "arm64" ]]; then
  exec "$REPO_ROOT/pdf-extractor-local/scripts/install-runtime-macos.sh"
fi

if [[ "$SYSTEM" == "Linux" && "$MACHINE" == "x86_64" ]]; then
  exec "$REPO_ROOT/pdf-extractor-local/scripts/install-runtime-linux.sh"
fi

echo "No verified local runtime is published for $SYSTEM/$MACHINE yet." >&2
echo "Verified releases currently support Apple Silicon macOS and Linux x86_64 only." >&2
echo "Do not install a different OCR engine as a substitute." >&2
exit 1
