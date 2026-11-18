#!/bin/sh
# Signs Count-In.app inside out with the hardened runtime, as notarization requires.
#   packaging/sign_macos.sh <Count-In.app> "<Developer ID Application: …>"
set -eu
app=$1
id=$2
ent=$(cd "$(dirname "$0")" && pwd)/entitlements.plist
sign() { codesign --force --timestamp --options runtime --entitlements "$ent" --sign "$id" "$1"; }

# Loose Mach-O files first: libraries, extension modules, helper tools
find "$app/Contents" -type f ! -path "*/Contents/MacOS/*" ! -path "*.framework/*" -print0 \
  | xargs -0 file --no-pad --separator '|' | grep '|.*Mach-O' | cut -d'|' -f1 \
  | while IFS= read -r f; do sign "$f"; done

# Then frameworks, nested ones before their parents
find "$app/Contents" -type d -name "*.framework" ! -path "*/Resources/*" \
  | awk '{ print gsub("/", "/"), $0 }' | sort -rn | cut -d' ' -f2- \
  | while IFS= read -r f; do sign "$f"; done

sign "$app/Contents/MacOS/countin"
sign "$app"
codesign --verify --strict --deep "$app"
