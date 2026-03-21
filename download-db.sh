#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

URL="https://cdn.jsdelivr.net/npm/geolite2-city/GeoLite2-City.mmdb.gz"
OUT="GeoLite2-City.mmdb"

if [ -f "$OUT" ]; then
    echo "GeoLite2-City.mmdb already exists. Delete it first to re-download."
    exit 0
fi

echo "Downloading GeoLite2-City.mmdb..."
curl -L --progress-bar "$URL" | gunzip > "$OUT"
echo "Done! Saved to $OUT"
