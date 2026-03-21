#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f GeoLite2-City.mmdb ]; then
    echo "GeoLite2-City.mmdb not found. Downloading..."
    ./download-db.sh
fi

build_macos() {
    echo "==> Building macOS binary..."

    if [ ! -d .venv ]; then
        echo "Creating virtual environment..."
        python3 -m venv .venv
    fi

    source .venv/bin/activate
    pip install -q pyinstaller geoip2 folium
    pyinstaller --onefile --distpath dist/macos --name geotrace --add-data "GeoLite2-City.mmdb:." geotrace.py
    deactivate

    echo "Done! dist/macos/geotrace"
}

build_linux() {
    echo "==> Building Linux binary via Docker..."

    if ! command -v docker &>/dev/null; then
        echo "Error: Docker is required for the Linux build."
        exit 1
    fi

    docker build -t geotrace-builder -f - . <<'DOCKERFILE'
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends binutils && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pyinstaller geoip2 folium
WORKDIR /src
DOCKERFILE

    docker run --rm \
        -v "$PWD":/src \
        geotrace-builder \
        pyinstaller --onefile --distpath dist/linux --name geotrace --add-data "GeoLite2-City.mmdb:." geotrace.py

    echo "Done! dist/linux/geotrace"
}

case "${1:-all}" in
    --linux)  build_linux  ;;
    --macos)  build_macos  ;;
    all)      build_macos; echo; build_linux ;;
    *)
        echo "Usage: ./build.sh [--macos | --linux]"
        echo "  (no flag builds both)"
        exit 1
        ;;
esac
