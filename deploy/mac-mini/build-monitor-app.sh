#!/bin/bash
# Build ~/Applications/BirdID Monitor.app — a mic-permission wrapper for birdid monitor.
# macOS grants microphone access to .app bundles (not bare python/ffmpeg under launchd).
# Run this on the Mac mini, double-click the app once to allow the mic, then load
# com.birdid.monitor.plist.

set -euo pipefail

BIRD_ID="${BIRD_ID:-$HOME/bird-id}"
APP="$HOME/Applications/BirdID Monitor.app"
MACOS="$APP/Contents/MacOS"
RES="$APP/Contents/Resources"

rm -rf "$APP"
mkdir -p "$MACOS" "$RES"

cat >"$MACOS/birdid-monitor" <<EOF
#!/bin/bash
set -euo pipefail
cd "$BIRD_ID"
export PATH="$HOME/bin:/usr/bin:/bin:/usr/sbin:/sbin"
"$BIRD_ID/.venv/bin/python" "$BIRD_ID/birdid.py" monitor -d 0
exit $?
EOF
chmod +x "$MACOS/birdid-monitor"

cat >"$APP/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleDevelopmentRegion</key>
	<string>en</string>
	<key>CFBundleExecutable</key>
	<string>birdid-monitor</string>
	<key>CFBundleIdentifier</key>
	<string>com.birdid.monitor-app</string>
	<key>CFBundleInfoDictionaryVersion</key>
	<string>6.0</string>
	<key>CFBundleName</key>
	<string>BirdID Monitor</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>CFBundleShortVersionString</key>
	<string>1.0</string>
	<key>CFBundleVersion</key>
	<string>1</string>
	<key>LSBackgroundOnly</key>
	<true/>
	<key>LSUIElement</key>
	<true/>
	<key>NSMicrophoneUsageDescription</key>
	<string>Records outdoor audio to identify birds with BirdNET.</string>
</dict>
</plist>
EOF

echo "Built $APP"
echo "Next: open the app once (Screen Sharing) and allow microphone access."
