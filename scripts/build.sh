#!/bin/bash
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
app_dir="$project_dir/Rekordbox Bridge.app"
mkdir -p "$app_dir/Contents/MacOS" "$project_dir/.build-cache"
swiftc -swift-version 5 -O -module-cache-path "$project_dir/.build-cache" \
  -target arm64-apple-macos14.0 "$project_dir/Sources/Bridge.swift" \
  -o "$app_dir/Contents/MacOS/rekordbox-bridge"
cat > "$app_dir/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>local.rekordbox.bridge</string>
<key>CFBundleName</key><string>Rekordbox Bridge</string>
<key>CFBundleExecutable</key><string>rekordbox-bridge</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>1</string>
<key>CFBundleShortVersionString</key><string>0.1.0</string>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>LSUIElement</key><true/>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
codesign --force --sign - --identifier local.rekordbox.bridge "$app_dir"
echo "$app_dir"
