#!/usr/bin/env bash
set -euo pipefail

REPO="chuhanma2-coder/pdf-extractor-skill"
VERSION="v0.3.0"
ASSET_NAME="PDF-Extractor-macOS-arm64.zip"
EXPECTED_SHA256="213acbd6803492b645a0b65cb723aad13085275d0fe78ac805e495718eb7dcbf"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE_ASSET="${PDF_EXTRACTOR_MACOS_ASSET:-/Volumes/WD-Dev/Projects/微众工作/ODPC判决书提取工具/release/$ASSET_NAME}"

command -v gh >/dev/null || { echo "GitHub CLI (gh) is required." >&2; exit 1; }
gh auth status -h github.com >/dev/null
if [[ -z "$(git config user.name || true)" || -z "$(git config user.email || true)" ]]; then
  echo "Set the Git author once before publishing:" >&2
  echo "  git config user.name 'Your GitHub display name'" >&2
  echo "  git config user.email 'the email registered with GitHub'" >&2
  exit 1
fi
[[ -f "$SOURCE_ASSET" ]] || { echo "Release archive not found: $SOURCE_ASSET" >&2; exit 1; }
ACTUAL_SHA256="$(shasum -a 256 "$SOURCE_ASSET" | awk '{print $1}')"
[[ "$ACTUAL_SHA256" == "$EXPECTED_SHA256" ]] || {
  echo "Archive SHA-256 differs from the versioned installer manifest. Build a new version and update the manifest first." >&2
  exit 1
}

cd "$PROJECT_ROOT"
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git init -b main
fi
git add .
if ! git diff --cached --quiet; then
  git commit -m "Publish local PDF extractor Skill $VERSION"
fi

if ! gh repo view "$REPO" >/dev/null 2>&1; then
  gh repo create "$REPO" --public --source=. --remote=origin --push --description "Offline local PDF extraction Skill for Codex"
else
  git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/$REPO.git"
  git push -u origin main
fi

if ! gh release view "$VERSION" --repo "$REPO" >/dev/null 2>&1; then
  gh release create "$VERSION" "$SOURCE_ASSET#$ASSET_NAME" --repo "$REPO" \
    --title "PDF Extractor Local $VERSION" \
    --notes "Apple Silicon macOS offline runtime. SHA-256: $EXPECTED_SHA256"
else
  gh release upload "$VERSION" "$SOURCE_ASSET#$ASSET_NAME" --repo "$REPO" --clobber
fi

printf 'Published Skill: https://github.com/%s/tree/main/pdf-extractor-local\n' "$REPO"
