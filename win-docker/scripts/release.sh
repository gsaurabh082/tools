#!/usr/bin/env bash
# Build a WinDock release: the Go control plane + the NSIS installer (and
# optionally an MSI), then optionally tag and push to trigger the GitHub
# Release workflow. Created and owned by Saurabh Gupta.
#
# Usage:
#   release.sh [version] [--patch|--minor|--major] [--msi] [--tag] [--skip-install]
# Version resolution:
#   - explicit version (e.g. 0.2.0) sets that version
#   - --patch / --minor / --major bumps the current package.json version
#   - nothing given rebuilds the current version as-is
# Examples:
#   release.sh                  # rebuild current version
#   release.sh --patch          # 0.1.0 -> 0.1.1
#   release.sh --minor --tag    # 0.1.x -> 0.2.0, then publish via CI
#   release.sh 1.0.0 --msi
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

VERSION=""; BUMP=""
MSI=0; TAG=0; SKIP_INSTALL=0
for arg in "$@"; do
  case "$arg" in
    --patch) BUMP="patch" ;;
    --minor) BUMP="minor" ;;
    --major) BUMP="major" ;;
    --msi) MSI=1 ;;
    --tag) TAG=1 ;;
    --skip-install) SKIP_INSTALL=1 ;;
    -h|--help) awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "${BASH_SOURCE[0]}"; exit 0 ;;
    -*) echo "Unknown option: $arg" >&2; exit 1 ;;
    *) VERSION="$arg" ;;
  esac
done

for tool in go node npm; do
  command -v "$tool" >/dev/null 2>&1 || { echo "Required tool '$tool' was not found on PATH." >&2; exit 1; }
done

PKG="apps/desktop/package.json"
CURRENT="$(node -p "require('./$PKG').version" 2>/dev/null || true)"

# Resolve the target version: explicit arg > bump of current > current as-is.
if [[ -z "$VERSION" ]]; then
  if [[ -z "$CURRENT" ]]; then echo "Could not read current version from $PKG; pass a version explicitly, e.g. release.sh 0.2.0" >&2; exit 1; fi
  if [[ -n "$BUMP" ]]; then
    IFS='.' read -r MA MI PA <<< "$CURRENT"
    case "$BUMP" in
      patch) PA=$((PA + 1)) ;;
      minor) MI=$((MI + 1)); PA=0 ;;
      major) MA=$((MA + 1)); MI=0; PA=0 ;;
    esac
    VERSION="$MA.$MI.$PA"
    echo "Bumping $BUMP: $CURRENT -> $VERSION"
  else
    VERSION="$CURRENT"
    echo "No version given; rebuilding current version $VERSION"
  fi
fi
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Version must look like 0.2.0 (no leading 'v'); got '$VERSION'." >&2; exit 1
fi

echo "== WinDock release $VERSION =="

# 1. Bump the desktop package version (only the first "version": line).
PKG="apps/desktop/package.json"
awk -v v="$VERSION" 'BEGIN{done=0}
  { if (!done && $0 ~ /"version"[[:space:]]*:/) { sub(/"version"[[:space:]]*:[[:space:]]*"[^"]*"/, "\"version\": \"" v "\""); done=1 } print }' \
  "$PKG" > "$PKG.tmp" && mv "$PKG.tmp" "$PKG"
echo "package.json version -> $VERSION"

# 2. Build the Go control plane (bundled into the app as extraResources).
echo "Building control plane -> dist/wincolima.exe"
go build -trimpath -ldflags "-s -w -X main.version=v$VERSION" -o dist/wincolima.exe ./cmd/wincolima

# 3. Build the Electron dashboard + NSIS installer.
echo "Building dashboard installer (electron-builder nsis)"
(
  cd apps/desktop
  [[ "$SKIP_INSTALL" -eq 1 ]] || npm install
  npm run package:nsis
)

SETUP="apps/desktop/release/WinDock-$VERSION-setup.exe"
[[ -f "$SETUP" ]] || { echo "Expected installer not found: $SETUP" >&2; exit 1; }

# 4. Optional MSI (requires WiX v4 'wix' on PATH). build-msi.ps1 is a helper.
MSI_PATH=""
if [[ "$MSI" -eq 1 ]]; then
  command -v wix >/dev/null 2>&1 || { echo "WiX v4 'wix' is required for --msi. Install: dotnet tool install --global wix --version 4.*" >&2; exit 1; }
  echo "Building MSI"
  mkdir -p dist/msi-input
  cp -f dist/wincolima.exe dist/msi-input/wincolima.exe
  cp -f "apps/desktop/release/win-unpacked/WinDock.exe" dist/msi-input/WinDock.exe
  WINROOT="$(pwd -W 2>/dev/null || pwd)"
  MSI_PATH="dist/WinDock-$VERSION.msi"
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File installer/build-msi.ps1 \
    -PublishDir "$WINROOT/dist/msi-input" -OutputPath "$WINROOT/$MSI_PATH"
fi

# 5. Optional tag + push -> triggers .github/workflows/release.yml.
if [[ "$TAG" -eq 1 ]]; then
  command -v git >/dev/null 2>&1 || { echo "git is required for --tag." >&2; exit 1; }
  T="v$VERSION"
  echo "Tagging and pushing $T"
  git add apps/desktop/package.json
  git commit -m "Release $T" || true
  git tag "$T"
  git push
  git push origin "$T"
  echo "Pushed $T. GitHub Actions will build and publish the Release."
fi

echo ""
echo "== Done =="
echo "Installer: $SETUP"
[[ -n "$MSI_PATH" ]] && echo "MSI:       $MSI_PATH"
[[ "$TAG" -eq 1 ]] || echo "Tip: add --tag to publish a GitHub Release, or --msi to also build the MSI."
