#!/bin/bash
# Build, sign and notarize "ANT+ Recorder.app" for Apple Silicon, from the repo root:
#
#     just app
#
# Needs the Developer ID certificate in the keychain and, to notarize, a stored
# credentials profile (once per Mac):
#
#     xcrun notarytool store-credentials antplus-recorder \
#         --apple-id YOU@EXAMPLE.COM --team-id H95JUDBHF7
#
# It asks for an app-specific password from https://account.apple.com. Without
# the profile the app is signed but not notarized, which only runs on this Mac.
set -euo pipefail

identity="${SIGN_IDENTITY:-Developer ID Application: Martin Mahner (H95JUDBHF7)}"
profile="${NOTARY_PROFILE:-antplus-recorder}"

root="$(cd "$(dirname "$0")/../.." && pwd)"
here="$root/packaging/macos"
work="$root/build/macos"
out="$root/dist/macos"
name="ANT+ Recorder"
app="$out/$name.app"
version="$(python3 -c "import tomllib; print(tomllib.load(open('$root/pyproject.toml', 'rb'))['project']['version'])")"
zip="$out/ANT+-Recorder-$version-macOS-arm64.zip"

rm -rf "$work" "$out"
mkdir -p "$work" "$out"

echo "==> Freezing the recorder"
cd "$root"
uv run --with pyinstaller pyinstaller --noconfirm --onedir --name antplus-recorder \
    --collect-all textual --collect-all libusb_package --collect-all openant \
    --collect-all garmin_fit_sdk --collect-all usb \
    --distpath "$work/dist" --workpath "$work/build" --specpath "$work" \
    "$here/entry.py"

echo "==> Assembling the app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
cp "$here/launcher" "$app/Contents/MacOS/$name"
cp "$here/run.command" "$app/Contents/Resources/run.command"
cp -R "$work/dist/antplus-recorder" "$app/Contents/Resources/antplus-recorder"
chmod +x "$app/Contents/MacOS/$name" "$app/Contents/Resources/run.command"
cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>$name</string>
    <key>CFBundleDisplayName</key><string>$name</string>
    <key>CFBundleIdentifier</key><string>org.mahner.antplus-recorder</string>
    <key>CFBundleExecutable</key><string>$name</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$version</string>
    <key>CFBundleVersion</key><string>$version</string>
    <key>LSMinimumSystemVersion</key><string>12.0</string>
    <key>LSApplicationCategoryType</key><string>public.app-category.sports</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

echo "==> Signing"
sign=(codesign --force --timestamp --options runtime --sign "$identity")
# Everything with machine code inside, deepest first, then the app itself.
find "$app/Contents/Resources/antplus-recorder" -type f -print0 |
    while IFS= read -r -d '' file; do
        if file -b "$file" | grep -q "Mach-O"; then
            "${sign[@]}" "$file"
        fi
    done
"${sign[@]}" "$app"
codesign --verify --deep --strict --verbose=2 "$app"

if ! xcrun notarytool history --keychain-profile "$profile" >/dev/null 2>&1; then
    echo "==> No notary profile '$profile', so the app is signed but not notarized."
    echo "    See the top of this script."
    exit 0
fi

echo "==> Notarizing"
ditto -c -k --keepParent "$app" "$work/notarize.zip"
xcrun notarytool submit "$work/notarize.zip" --keychain-profile "$profile" --wait
xcrun stapler staple "$app"
spctl --assess --type execute --verbose=2 "$app"

echo "==> Zipping"
ditto -c -k --keepParent "$app" "$zip"
echo "Done: $zip"
