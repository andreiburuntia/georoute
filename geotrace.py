#!/usr/bin/env python3
"""GeoTrace - Geographic Traceroute Visualizer.

Wraps `traceroute`, resolves each hop to a physical location using MaxMind
GeoLite2, and renders an interactive HTML map (Leaflet via Folium) showing the
route with per-leg latency annotations.
"""

import argparse
import ipaddress
import os
import re
import subprocess
import sys
import webbrowser
from dataclasses import dataclass, field

import folium
import geoip2.database
import geoip2.errors


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class Hop:
    number: int
    ip: str | None = None
    rtts: list[float] = field(default_factory=list)
    avg_rtt: float | None = None
    leg_latency: float | None = None
    city: str | None = None
    country: str | None = None
    lat: float | None = None
    lon: float | None = None
    is_private: bool = False


# ---------------------------------------------------------------------------
# 1. Traceroute runner & parser
# ---------------------------------------------------------------------------

def run_traceroute(target: str, max_hops: int = 30) -> str:
    """Execute ``traceroute -I -n`` (ICMP) and stream output line-by-line."""
    # -I uses ICMP echo which is far more reliable than the default UDP.
    # ICMP requires root on macOS/Linux, so prepend sudo when not already root.
    base = [
        "traceroute", "-I", "-n",
        "-m", str(max_hops),
        "-w", "2",
        "-q", "2",
        target,
    ]
    cmd = base if os.geteuid() == 0 else ["sudo"] + base
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        print("Error: 'traceroute' command not found. Install it first.", file=sys.stderr)
        sys.exit(1)

    lines: list[str] = []
    try:
        for line in proc.stdout:
            print(line, end="", flush=True)
            lines.append(line)
        proc.wait(timeout=120)
    except subprocess.TimeoutExpired:
        proc.kill()
        print("Error: traceroute timed out after 120 seconds.", file=sys.stderr)
        sys.exit(1)

    output = "".join(lines)
    if proc.returncode != 0 and not output:
        print(f"Error running traceroute:\n{proc.stderr.read()}", file=sys.stderr)
        sys.exit(1)

    return output


_HOP_RE = re.compile(
    r"^\s*(\d+)\s+"  # hop number
    r"(.+)$"         # rest of the line
)
_PROBE_RE = re.compile(
    r"(\d+\.\d+\.\d+\.\d+)\s+"  # IP address
    r"([\d.]+)\s*ms"             # RTT value
)
_STAR_ONLY_RE = re.compile(r"^[*\s]+$")


def parse_traceroute(raw: str) -> list[Hop]:
    """Parse raw traceroute output into a list of `Hop` objects."""
    hops: list[Hop] = []

    for line in raw.splitlines():
        m = _HOP_RE.match(line)
        if not m:
            continue

        hop_num = int(m.group(1))
        rest = m.group(2)

        # All-star line → timeout hop
        if _STAR_ONLY_RE.match(rest):
            hops.append(Hop(number=hop_num))
            continue

        # Extract (ip, rtt) pairs – take the first responding IP
        probes = _PROBE_RE.findall(rest)
        if not probes:
            hops.append(Hop(number=hop_num))
            continue

        first_ip = probes[0][0]
        rtts = [float(rtt) for ip, rtt in probes if ip == first_ip]

        hop = Hop(number=hop_num, ip=first_ip, rtts=rtts)
        hop.avg_rtt = round(sum(rtts) / len(rtts), 2) if rtts else None
        hops.append(hop)

    # Compute leg latency (delta between consecutive hops with RTTs)
    prev_rtt: float | None = None
    for hop in hops:
        if hop.avg_rtt is not None:
            if prev_rtt is not None:
                hop.leg_latency = round(hop.avg_rtt - prev_rtt, 2)
            prev_rtt = hop.avg_rtt

    return hops


# ---------------------------------------------------------------------------
# 2. Geo-IP resolver
# ---------------------------------------------------------------------------

def _is_private(ip_str: str) -> bool:
    """Return True if *ip_str* is a private / reserved address."""
    try:
        return ipaddress.ip_address(ip_str).is_private
    except ValueError:
        return False


def resolve_locations(hops: list[Hop], db_path: str) -> None:
    """Annotate each hop in-place with geolocation data."""
    try:
        reader = geoip2.database.Reader(db_path)
    except FileNotFoundError:
        print(f"Error: GeoLite2 database not found at '{db_path}'.", file=sys.stderr)
        print("Download it from https://dev.maxmind.com/geoip/geolite2-free-geolocation-data", file=sys.stderr)
        sys.exit(1)

    try:
        for hop in hops:
            if hop.ip is None:
                continue
            if _is_private(hop.ip):
                hop.is_private = True
                continue
            try:
                resp = reader.city(hop.ip)
                hop.city = resp.city.name
                hop.country = resp.country.iso_code
                hop.lat = resp.location.latitude
                hop.lon = resp.location.longitude
            except geoip2.errors.AddressNotFoundError:
                pass
    finally:
        reader.close()


# ---------------------------------------------------------------------------
# 3. Interactive HTML map
# ---------------------------------------------------------------------------

def _latency_color(ms: float | None) -> str:
    """Return a CSS color based on leg latency thresholds."""
    if ms is None:
        return "gray"
    if ms < 20:
        return "green"
    if ms < 80:
        return "#cccc00"  # yellow (visible on white)
    if ms < 150:
        return "orange"
    return "red"


def _latency_weight(ms: float | None) -> int:
    """Return polyline weight (thicker = higher latency)."""
    if ms is None:
        return 2
    if ms < 20:
        return 2
    if ms < 80:
        return 4
    if ms < 150:
        return 5
    return 7


def generate_map(hops: list[Hop], output_path: str) -> None:
    """Build a Folium map and save it to *output_path*."""
    located = [h for h in hops if h.lat is not None and h.lon is not None]
    if not located:
        print("Warning: no hops could be geolocated — map will be empty.", file=sys.stderr)
        m = folium.Map(location=[20, 0], zoom_start=2)
        m.save(output_path)
        return

    m = folium.Map()

    # Markers
    for hop in located:
        location_str = ", ".join(filter(None, [hop.city, hop.country])) or "Unknown"
        rtt_str = f"{hop.avg_rtt} ms" if hop.avg_rtt is not None else "n/a"
        leg_str = f"{hop.leg_latency} ms" if hop.leg_latency is not None else "\u2014"

        popup_html = (
            f"<b>Hop {hop.number}</b><br>"
            f"IP: {hop.ip}<br>"
            f"Location: {location_str}<br>"
            f"Avg RTT: {rtt_str}<br>"
            f"Leg \u0394: {leg_str}"
        )

        folium.Marker(
            location=[hop.lat, hop.lon],
            popup=folium.Popup(popup_html, max_width=250),
            tooltip=f"Hop {hop.number}: {hop.ip}",
            icon=folium.Icon(
                color="green" if hop.number == located[0].number
                else "red" if hop.number == located[-1].number
                else "blue",
                icon="info-sign",
            ),
        ).add_to(m)

    # Polylines between consecutive located hops
    for i in range(len(located) - 1):
        a, b = located[i], located[i + 1]
        color = _latency_color(b.leg_latency)
        weight = _latency_weight(b.leg_latency)
        leg_str = f"{b.leg_latency} ms" if b.leg_latency is not None else "n/a"

        folium.PolyLine(
            locations=[[a.lat, a.lon], [b.lat, b.lon]],
            color=color,
            weight=weight,
            opacity=0.8,
            tooltip=f"Leg {a.number}\u2192{b.number}: {leg_str}",
        ).add_to(m)

    # Fit bounds
    sw = [min(h.lat for h in located), min(h.lon for h in located)]
    ne = [max(h.lat for h in located), max(h.lon for h in located)]
    m.fit_bounds([sw, ne], padding=[30, 30])

    m.save(output_path)


# ---------------------------------------------------------------------------
# 4. Terminal summary table
# ---------------------------------------------------------------------------

def print_summary(hops: list[Hop]) -> None:
    """Print a formatted summary table to stdout."""
    header = f"{'Hop':<5} {'IP':<17} {'Location':<25} {'RTT(avg)':<10} {'Leg Δ':<10}"
    print()
    print(header)
    print("-" * len(header))

    for hop in hops:
        ip_str = hop.ip or "*"

        if hop.is_private:
            loc = "(private)"
        elif hop.ip is None:
            loc = "*"
        else:
            loc = ", ".join(filter(None, [hop.city, hop.country])) or "Unknown"

        rtt_str = f"{hop.avg_rtt}ms" if hop.avg_rtt is not None else "*"
        leg_str = f"{hop.leg_latency}ms" if hop.leg_latency is not None else "\u2014"

        print(f"{hop.number:<5} {ip_str:<17} {loc:<25} {rtt_str:<10} {leg_str:<10}")

    print()


# ---------------------------------------------------------------------------
# 5. CLI interface
# ---------------------------------------------------------------------------

def _default_db_path() -> str:
    """Return the default path to the GeoLite2 database.

    When running as a PyInstaller bundle the DB is extracted to a temp dir
    accessible via ``sys._MEIPASS``.  Otherwise fall back to ``./``.
    """
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "GeoLite2-City.mmdb")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="GeoTrace \u2014 Geographic Traceroute Visualizer",
    )
    parser.add_argument("target", help="Hostname or IP to trace")
    parser.add_argument(
        "--db",
        default=_default_db_path(),
        help="Path to GeoLite2-City.mmdb (default: bundled or ./GeoLite2-City.mmdb)",
    )
    parser.add_argument(
        "--max-hops",
        type=int,
        default=30,
        help="Maximum number of hops (default: 30)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output HTML file (default: geotrace_<target>.html)",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Don't auto-open the map in browser",
    )

    args = parser.parse_args()

    output_file = args.output or f"geotrace_{args.target}.html"

    # 1. Run traceroute
    print(f"Tracing route to {args.target} (max {args.max_hops} hops)...")
    raw = run_traceroute(args.target, args.max_hops)

    # 2. Parse output
    hops = parse_traceroute(raw)
    if not hops:
        print("No hops parsed from traceroute output.", file=sys.stderr)
        sys.exit(1)

    # 3. Resolve geo locations
    resolve_locations(hops, args.db)

    # 4. Print terminal summary
    print_summary(hops)

    # 5. Generate map
    generate_map(hops, output_file)
    print(f"Map saved to {output_file}")

    # 6. Open in browser
    if not args.no_open:
        webbrowser.open(output_file)


if __name__ == "__main__":
    main()
