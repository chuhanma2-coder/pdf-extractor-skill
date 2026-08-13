#!/usr/bin/env bash
set -euo pipefail

REPO="chuhanma2-coder/pdf-extractor-skill"
VERSION="v0.3.0"
ASSET="PDF-Extractor-linux-x64.tar.gz"
CHECKSUM_ASSET="PDF-Extractor-linux-x64.sha256"
INSTALL_ROOT="${PDF_EXTRACTOR_RUNTIME_ROOT:-$HOME/.local/share/pdf-extractor}"
RUNTIME_DIR="$INSTALL_ROOT/pdf-extractor-linux-x64"
TEMP_DIR="$(mktemp -d)"

cleanup() { rm -rf "$TEMP_DIR"; }
trap cleanup EXIT

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "x86_64" ]]; then
  echo "This runtime is for Linux x86_64 only." >&2
  exit 1
fi

BASE_URL="https://github.com/$REPO/releases/download/$VERSION"
curl --fail --location --retry 3 --output "$TEMP_DIR/$ASSET" "$BASE_URL/$ASSET"
curl --fail --location --retry 3 --output "$TEMP_DIR/$CHECKSUM_ASSET" "$BASE_URL/$CHECKSUM_ASSET"
(
  cd "$TEMP_DIR"
  sha256sum --check "$CHECKSUM_ASSET"
)

mkdir -p "$INSTALL_ROOT"
rm -rf "$RUNTIME_DIR"
tar -xzf "$TEMP_DIR/$ASSET" -C "$INSTALL_ROOT"
WORKER="$RUNTIME_DIR/run-worker"
if [[ ! -x "$WORKER" ]]; then
  echo "Installed archive is missing its bundled OCR launcher." >&2
  exit 1
fi
"$WORKER" health
printf 'Installed local PDF runtime: %s\n' "$RUNTIME_DIR"
