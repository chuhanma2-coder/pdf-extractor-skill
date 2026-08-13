#!/usr/bin/env bash
set -euo pipefail

REPO="chuhanma2-coder/pdf-extractor-skill"
VERSION="v0.3.0"
ASSET="PDF-Extractor-macOS-arm64.zip"
EXPECTED_SHA256="213acbd6803492b645a0b65cb723aad13085275d0fe78ac805e495718eb7dcbf"
INSTALL_ROOT="${PDF_EXTRACTOR_RUNTIME_ROOT:-$HOME/.local/share/pdf-extractor}"
APP_PATH="$INSTALL_ROOT/PDF 提取器.app"
TEMP_DIR="$(mktemp -d)"

cleanup() { rm -rf "$TEMP_DIR"; }
trap cleanup EXIT

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "This runtime is for Apple Silicon macOS only. Windows and Intel Mac releases are separate." >&2
  exit 1
fi

URL="https://github.com/$REPO/releases/download/$VERSION/$ASSET"
curl --fail --location --retry 3 --output "$TEMP_DIR/$ASSET" "$URL"
ACTUAL_SHA256="$(shasum -a 256 "$TEMP_DIR/$ASSET" | awk '{print $1}')"
if [[ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]]; then
  echo "Release checksum mismatch. Download was not installed." >&2
  exit 1
fi

mkdir -p "$INSTALL_ROOT"
rm -rf "$APP_PATH"
ditto -x -k "$TEMP_DIR/$ASSET" "$INSTALL_ROOT"
xattr -dr com.apple.quarantine "$APP_PATH" 2>/dev/null || true
WORKER="$APP_PATH/Contents/Resources/worker/odpc-ocr-worker"
if [[ ! -x "$WORKER" ]]; then
  echo "Installed archive is missing its OCR worker." >&2
  exit 1
fi
"$WORKER" health
printf 'Installed local PDF runtime: %s\n' "$APP_PATH"
