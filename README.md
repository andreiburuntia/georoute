# GeoTrace - Geographic Traceroute Visualizer

A Python CLI tool that wraps `traceroute`, resolves each hop to a physical location using MaxMind GeoLite2, and renders an interactive HTML map showing the route with per-leg latency annotations.

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Download the GeoLite2 database

GeoTrace requires the **GeoLite2-City** database from MaxMind (free):

1. Create an account at https://www.maxmind.com/en/geolite2/signup
2. Navigate to **Download Databases** and download `GeoLite2-City.mmdb`
3. Place the `.mmdb` file in the project directory (or pass its path via `--db`)

### 3. Ensure `traceroute` is installed

- **macOS**: included by default
- **Debian/Ubuntu**: `sudo apt install traceroute`
- **Fedora/RHEL**: `sudo dnf install traceroute`

**Note:** GeoTrace uses ICMP mode (`traceroute -I`) for reliable and fast results. ICMP requires root privileges, so the tool will automatically invoke `sudo` if not already running as root. You may be prompted for your password on first run.

## Usage

```bash
python geotrace.py <target> [options]
```

### Examples

```bash
# Trace route to Google DNS, print table and open map
python geotrace.py 8.8.8.8

# Trace to a hostname, save map to specific file
python geotrace.py google.com --output google_route.html

# Use a custom database path, don't open browser
python geotrace.py 1.1.1.1 --db /path/to/GeoLite2-City.mmdb --no-open

# Limit to 15 hops
python geotrace.py example.com --max-hops 15
```

### Options

| Flag | Description | Default |
|------|-------------|---------|
| `--db PATH` | Path to GeoLite2-City.mmdb | `./GeoLite2-City.mmdb` |
| `--max-hops N` | Maximum number of hops | `30` |
| `--output FILE` | Output HTML file | `geotrace_<target>.html` |
| `--no-open` | Don't auto-open the map in browser | off |

## Building standalone binaries

The `build.sh` script produces a self-contained binary with the GeoLite2 database bundled in — no Python required on the target machine.

```bash
# macOS (native)
./build.sh

# Linux (via Docker)
./build.sh --linux
```

Binaries are written to `dist/`:
- `dist/geotrace` — macOS (arm64)
- `dist/geotrace-linux` — Linux (x86_64)

Prerequisites: Python 3, the `GeoLite2-City.mmdb` file in the project root, and Docker for the Linux build.

## Output

- **Terminal**: a formatted table showing each hop's IP, location, average RTT, and leg latency
- **HTML map**: an interactive Leaflet map with:
  - Markers at each geolocated hop (green = first, red = last, blue = intermediate)
  - Polylines color-coded by leg latency (green < 20ms, yellow < 80ms, orange < 150ms, red > 150ms)
  - Popups with detailed hop information
  - Auto-fitted bounds to show the full route
