#!/bin/bash
# 把源图做成 macOS 应用图标。
set -euo pipefail
cd "$(dirname "$0")/.."
SRC="App/Resources/AppIcon.jpg"
DEST="${1:-App/Resources/AppIcon.icns}"
WORK="$(mktemp -d)"
SET="$WORK/AppIcon.iconset"
mkdir -p "$SET"
BASE="$WORK/base.png"
sips -s format png -z 1024 1024 "$SRC" --out "$BASE" >/dev/null

make() {
  local size="$1" name="$2"
  sips -z "$size" "$size" "$BASE" --out "$SET/$name" >/dev/null
}
make 16 icon_16x16.png
make 32 icon_16x16@2x.png
make 32 icon_32x32.png
make 64 icon_32x32@2x.png
make 128 icon_128x128.png
make 256 icon_128x128@2x.png
make 256 icon_256x256.png
make 512 icon_256x256@2x.png
make 512 icon_512x512.png
make 1024 icon_512x512@2x.png
iconutil -c icns "$SET" -o "$DEST"
rm -rf "$WORK"
echo "icon $DEST"
