#!/bin/bash
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
app_dir="$project_dir/DJ Jev.app"
cache_dir="$project_dir/../../work/widget-swift-cache"
mkdir -p "$app_dir/Contents/MacOS" "$app_dir/Contents/Resources" "$cache_dir"
swiftc -swift-version 5 -O -parse-as-library -module-cache-path "$cache_dir" \
  -target arm64-apple-macos14.0 "$project_dir/Sources/JevWidget.swift" "$project_dir/Sources/JevControls.swift" "$project_dir/Sources/JevProbeControls.swift" \
  -o "$app_dir/Contents/MacOS/jev-widget"
cp "$project_dir/assets/dj-jev-background.png" "$app_dir/Contents/Resources/dj-jev-background.png"
rm -f "$app_dir/Contents/Resources/dj-jev-footer.jpg"
cat > "$app_dir/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>local.rekordbox.jev-widget</string>
<key>CFBundleName</key><string>DJ Jev</string>
<key>CFBundleExecutable</key><string>jev-widget</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>3</string>
<key>CFBundleShortVersionString</key><string>0.4.0</string>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>LSUIElement</key><true/>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
python3 "$project_dir/scripts/sign_app.py" "$app_dir" local.rekordbox.jev-widget
echo "$app_dir"
