import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from geotrace import (
    Hop,
    _is_private,
    _latency_color,
    _latency_weight,
    generate_map,
    parse_traceroute,
    print_summary,
    resolve_locations,
)


# ---------------------------------------------------------------------------
# parse_traceroute
# ---------------------------------------------------------------------------

SAMPLE_OUTPUT = """\
traceroute to 185.213.155.73 (185.213.155.73), 64 hops max, 48 byte packets
 1  192.168.2.1  3.423 ms  2.669 ms  2.755 ms
 2  109.99.219.1  4.017 ms  4.096 ms  3.942 ms
 3  10.0.245.17  4.874 ms  7.114 ms  4.592 ms
 4  * * *
 5  10.0.240.54  24.110 ms  24.259 ms  25.231 ms
 6  80.81.195.179  28.402 ms  23.954 ms  24.331 ms
 7  185.213.155.73  22.170 ms  22.073 ms  21.689 ms
"""


def test_parse_hop_count():
    hops = parse_traceroute(SAMPLE_OUTPUT)
    assert len(hops) == 7


def test_parse_timeout_hop():
    hops = parse_traceroute(SAMPLE_OUTPUT)
    hop4 = hops[3]
    assert hop4.number == 4
    assert hop4.ip is None
    assert hop4.avg_rtt is None


def test_parse_ip_and_rtt():
    hops = parse_traceroute(SAMPLE_OUTPUT)
    hop1 = hops[0]
    assert hop1.ip == "192.168.2.1"
    assert len(hop1.rtts) >= 1
    assert hop1.avg_rtt is not None


def test_parse_leg_latency():
    hops = parse_traceroute(SAMPLE_OUTPUT)
    # Hop 1 is first → no leg latency
    assert hops[0].leg_latency is None
    # Hop 2 should have a delta from hop 1
    assert hops[1].leg_latency is not None
    assert hops[1].leg_latency > 0


def test_parse_leg_latency_skips_timeout():
    """Leg latency for hop 5 should be relative to hop 3 (hop 4 timed out)."""
    hops = parse_traceroute(SAMPLE_OUTPUT)
    hop5 = hops[4]
    hop3 = hops[2]
    assert hop5.leg_latency == pytest.approx(hop5.avg_rtt - hop3.avg_rtt, abs=0.01)


def test_parse_empty_input():
    assert parse_traceroute("") == []


def test_parse_header_only():
    assert parse_traceroute("traceroute to 8.8.8.8 (8.8.8.8), 30 hops max\n") == []


NUMERIC_OUTPUT = """\
traceroute to 8.8.8.8 (8.8.8.8), 30 hops max, 60 byte packets
 1  192.168.1.1  1.123 ms  0.987 ms
 2  10.0.0.1  5.456 ms  5.789 ms
"""


def test_parse_two_probes():
    """Handles lines with only 2 RTT values instead of 3."""
    hops = parse_traceroute(NUMERIC_OUTPUT)
    assert len(hops) == 2
    assert len(hops[0].rtts) >= 1


NUMERIC_N_OUTPUT = """\
traceroute to 8.8.8.8 (8.8.8.8), 30 hops max, 60 byte packets
 1  192.168.1.1  1.123 ms  192.168.1.1  0.987 ms  192.168.1.1  1.050 ms
 2  10.0.0.1  5.456 ms  10.0.0.1  5.789 ms  10.0.0.1  5.600 ms
"""


def test_parse_numeric_n_format():
    """The -n format repeats the IP before each RTT."""
    hops = parse_traceroute(NUMERIC_N_OUTPUT)
    assert len(hops) == 2
    assert hops[0].ip == "192.168.1.1"
    assert len(hops[0].rtts) == 3
    assert hops[0].avg_rtt == pytest.approx(1.05, abs=0.01)


MULTI_IP_OUTPUT = """\
traceroute to 8.8.8.8 (8.8.8.8), 30 hops max, 60 byte packets
 1  10.0.0.1  1.0 ms  10.0.0.2  2.0 ms  10.0.0.1  1.5 ms
"""


def test_parse_multi_ip_takes_first():
    """When multiple IPs appear on a hop, take the first one."""
    hops = parse_traceroute(MULTI_IP_OUTPUT)
    assert hops[0].ip == "10.0.0.1"
    # Should only include RTTs for the first IP
    assert len(hops[0].rtts) == 2
    assert 2.0 not in hops[0].rtts


# ---------------------------------------------------------------------------
# _is_private
# ---------------------------------------------------------------------------

def test_private_ips():
    assert _is_private("192.168.1.1") is True
    assert _is_private("10.0.0.1") is True
    assert _is_private("172.16.0.1") is True
    assert _is_private("127.0.0.1") is True


def test_public_ips():
    assert _is_private("8.8.8.8") is False
    assert _is_private("185.213.155.73") is False


def test_invalid_ip():
    assert _is_private("not-an-ip") is False


# ---------------------------------------------------------------------------
# _latency_color / _latency_weight
# ---------------------------------------------------------------------------

def test_latency_color_thresholds():
    assert _latency_color(None) == "gray"
    assert _latency_color(5) == "green"
    assert _latency_color(19.9) == "green"
    assert _latency_color(20) == "#cccc00"
    assert _latency_color(50) == "#cccc00"
    assert _latency_color(80) == "orange"
    assert _latency_color(149) == "orange"
    assert _latency_color(150) == "red"
    assert _latency_color(500) == "red"


def test_latency_weight_thresholds():
    assert _latency_weight(None) == 2
    assert _latency_weight(5) == 2
    assert _latency_weight(50) == 4
    assert _latency_weight(100) == 5
    assert _latency_weight(200) == 7


# ---------------------------------------------------------------------------
# resolve_locations
# ---------------------------------------------------------------------------

def test_resolve_marks_private():
    hops = [Hop(number=1, ip="192.168.1.1"), Hop(number=2, ip="10.0.0.1")]
    mock_reader = MagicMock()

    with patch("geotrace.geoip2.database.Reader", return_value=mock_reader):
        resolve_locations(hops, "fake.mmdb")

    assert hops[0].is_private is True
    assert hops[1].is_private is True
    mock_reader.city.assert_not_called()


def test_resolve_skips_none_ip():
    hops = [Hop(number=1)]  # timeout hop, ip=None
    mock_reader = MagicMock()

    with patch("geotrace.geoip2.database.Reader", return_value=mock_reader):
        resolve_locations(hops, "fake.mmdb")

    mock_reader.city.assert_not_called()


def test_resolve_public_ip():
    hops = [Hop(number=1, ip="8.8.8.8")]
    mock_reader = MagicMock()
    mock_resp = MagicMock()
    mock_resp.city.name = "Mountain View"
    mock_resp.country.iso_code = "US"
    mock_resp.location.latitude = 37.386
    mock_resp.location.longitude = -122.084
    mock_reader.city.return_value = mock_resp

    with patch("geotrace.geoip2.database.Reader", return_value=mock_reader):
        resolve_locations(hops, "fake.mmdb")

    assert hops[0].city == "Mountain View"
    assert hops[0].country == "US"
    assert hops[0].lat == 37.386
    assert hops[0].lon == -122.084


def test_resolve_address_not_found():
    hops = [Hop(number=1, ip="1.2.3.4")]
    mock_reader = MagicMock()
    mock_reader.city.side_effect = __import__("geoip2.errors", fromlist=["AddressNotFoundError"]).AddressNotFoundError("not found")

    with patch("geotrace.geoip2.database.Reader", return_value=mock_reader):
        resolve_locations(hops, "fake.mmdb")

    assert hops[0].lat is None
    assert hops[0].city is None


# ---------------------------------------------------------------------------
# generate_map
# ---------------------------------------------------------------------------

def test_generate_map_creates_file():
    hops = [
        Hop(number=1, ip="8.8.8.8", avg_rtt=10.0, lat=37.0, lon=-122.0, city="A", country="US"),
        Hop(number=2, ip="1.1.1.1", avg_rtt=25.0, leg_latency=15.0, lat=48.0, lon=2.0, city="B", country="FR"),
    ]
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
        path = f.name

    try:
        generate_map(hops, path)
        assert os.path.exists(path)
        content = open(path).read()
        assert "Hop 1" in content
        assert "Hop 2" in content
        assert "leaflet" in content.lower()
    finally:
        os.unlink(path)


def test_generate_map_no_located_hops():
    hops = [Hop(number=1, ip="192.168.1.1", is_private=True)]
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
        path = f.name

    try:
        generate_map(hops, path)
        assert os.path.exists(path)
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# print_summary
# ---------------------------------------------------------------------------

def test_print_summary(capsys):
    hops = [
        Hop(number=1, ip="192.168.1.1", avg_rtt=1.2, is_private=True),
        Hop(number=2, ip="8.8.8.8", avg_rtt=12.0, leg_latency=10.8, city="Somewhere", country="US"),
        Hop(number=3),  # timeout
    ]
    print_summary(hops)
    out = capsys.readouterr().out
    assert "(private)" in out
    assert "Somewhere, US" in out
    assert "12.0ms" in out
    assert "10.8ms" in out
