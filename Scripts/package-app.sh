#!/bin/bash
# 打成双击即用的「解压缩工具.app」。应用内带上 unar，不依赖 Python。
set -euo pipefail
cd "$(dirname "$0")/.."

UNAR="$(python3 -c 'import os; print(os.path.realpath("/opt/homebrew/bin/unar"))')"
LSAR="$(python3 -c 'import os; print(os.path.realpath("/opt/homebrew/bin/lsar"))')"

echo "编译…"
swift build -c release --package-path App

APP="${APP_NAME:-解压缩工具.app}"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp App/.build/release/ArchiveUnpacker "$APP/Contents/MacOS/ArchiveUnpacker"
cp "$UNAR" "$APP/Contents/MacOS/unar"
cp "$LSAR" "$APP/Contents/MacOS/lsar"
cp passwords.txt "$APP/Contents/Resources/passwords.txt"
bash Scripts/make-icon.sh "$APP/Contents/Resources/AppIcon.icns"
chmod +x "$APP/Contents/MacOS/ArchiveUnpacker" "$APP/Contents/MacOS/unar" "$APP/Contents/MacOS/lsar"

cat > "$APP/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>ArchiveUnpacker</string>
  <key>CFBundleIdentifier</key>
  <string>local.archiveunpacker</string>
  <key>CFBundleName</key>
  <string>解压缩工具</string>
  <key>CFBundleDisplayName</key>
  <string>解压缩工具</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>14.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
EOF

codesign --force --sign - "$APP" >/dev/null
echo "built $APP"
