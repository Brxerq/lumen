#!/bin/sh
# Install Lumen from the latest GitHub release.
#
#   curl -fsSL https://brxerq.github.io/lumen/install.sh | sh
#
# Downloads the binary for this platform, checks it against the release's
# published SHA-256, and puts it in ~/.local/bin (override with LUMEN_BIN_DIR).
set -eu

REPO="Brxerq/lumen"
BIN_DIR="${LUMEN_BIN_DIR:-$HOME/.local/bin}"

case "$(uname -s)" in
    Darwin) ASSET="lumen-macos" ;;
    Linux)  ASSET="lumen-linux" ;;
    *)      echo "This script covers macOS and Linux. On Windows run the PowerShell line from the README." >&2; exit 1 ;;
esac

# The release builds one binary per OS on the GitHub runners: arm64 for macOS,
# x86_64 for Linux. Anything else has to build from source.
ARCH="$(uname -m)"
case "$(uname -s)-$ARCH" in
    Darwin-arm64|Darwin-x86_64|Linux-x86_64) ;;
    *) echo "No prebuilt binary for $ARCH. Install from source: pip install git+https://github.com/$REPO" >&2; exit 1 ;;
esac

TAG="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p')"
[ -n "$TAG" ] || { echo "Could not find the latest release of $REPO." >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
BASE="https://github.com/$REPO/releases/download/$TAG"

echo "Downloading Lumen $TAG ($ASSET)…"
curl -fsSL "$BASE/$ASSET" -o "$TMP/lumen"
curl -fsSL "$BASE/$ASSET.sha256" -o "$TMP/sum"

# The updater in the app refuses an unverified binary; so does this.
EXPECTED="$(cut -d' ' -f1 < "$TMP/sum")"
if command -v sha256sum >/dev/null 2>&1; then
    ACTUAL="$(sha256sum "$TMP/lumen" | cut -d' ' -f1)"
elif command -v shasum >/dev/null 2>&1; then
    ACTUAL="$(shasum -a 256 "$TMP/lumen" | cut -d' ' -f1)"
else
    echo "Neither sha256sum nor shasum is available; refusing to install unverified." >&2; exit 1
fi
[ "$EXPECTED" = "$ACTUAL" ] || { echo "Checksum mismatch — not installing." >&2; exit 1; }

mkdir -p "$BIN_DIR"
mv "$TMP/lumen" "$BIN_DIR/lumen"
chmod +x "$BIN_DIR/lumen"

# macOS quarantines anything downloaded, and the binary is unsigned; without
# this the first launch is a Gatekeeper dialog with no "open anyway" button.
[ "$(uname -s)" = "Darwin" ] && xattr -d com.apple.quarantine "$BIN_DIR/lumen" 2>/dev/null || true

echo "Installed $BIN_DIR/lumen"
case ":$PATH:" in
    *":$BIN_DIR:"*) echo "Run: lumen" ;;
    *) echo "$BIN_DIR is not on your PATH. Run it as $BIN_DIR/lumen, or add that directory to PATH." ;;
esac
