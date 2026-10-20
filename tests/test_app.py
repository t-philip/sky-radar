"""Unit tests for Sky Radar pure-function logic."""
import pytest
from app import compute_bearing, compute_bbox, infer_category, parse_state_vector


class TestComputeBearing:
    def test_north(self):
        assert compute_bearing(0, 0, 1, 0) == pytest.approx(0.0, abs=0.1)

    def test_east(self):
        assert compute_bearing(0, 0, 0, 1) == pytest.approx(90.0, abs=0.5)

    def test_south(self):
        assert compute_bearing(1, 0, 0, 0) == pytest.approx(180.0, abs=0.1)

    def test_west(self):
        assert compute_bearing(0, 1, 0, 0) == pytest.approx(270.0, abs=0.5)

    def test_result_in_range(self):
        b = compute_bearing(52.37, 4.90, 51.51, -0.13)  # AMS → LHR
        assert 0 <= b < 360

    def test_never_negative(self):
        for lat2, lon2 in [(-10, -10), (10, -10), (-10, 10)]:
            assert compute_bearing(0, 0, lat2, lon2) >= 0


class TestComputeBbox:
    def test_contains_origin(self):
        bbox = compute_bbox(52.37, 4.90, 100)
        assert bbox["lamin"] < 52.37 < bbox["lamax"]
        assert bbox["lomin"] < 4.90 < bbox["lomax"]

    def test_equatorial_symmetry(self):
        bbox = compute_bbox(0, 0, 100)
        assert abs(bbox["lamax"] + bbox["lamin"]) < 0.01
        assert abs(bbox["lomax"] + bbox["lomin"]) < 0.01

    def test_larger_radius_wider_box(self):
        small = compute_bbox(52, 5, 50)
        large = compute_bbox(52, 5, 200)
        assert large["lamax"] > small["lamax"]
        assert large["lomax"] > small["lomax"]


class TestInferCategory:
    def test_emergency_7700(self):
        assert infer_category({"squawk": "7700", "callsign": ""}) == "Emergency"

    def test_comm_loss_7600(self):
        assert infer_category({"squawk": "7600", "callsign": "KLM123"}) == "Emergency"

    def test_hijack_7500(self):
        assert infer_category({"squawk": "7500", "callsign": ""}) == "Emergency"

    def test_emergency_takes_priority_over_callsign(self):
        assert infer_category({"squawk": "7700", "callsign": "REACH123"}) == "Emergency"

    def test_commercial(self):
        assert infer_category({"squawk": "", "callsign": "KLM123"}) == "Commercial"

    def test_commercial_with_letter_suffix(self):
        assert infer_category({"squawk": None, "callsign": "BAW123A"}) == "Commercial"

    def test_cargo_fedex(self):
        assert infer_category({"squawk": "", "callsign": "FDX1234"}) == "Cargo"

    def test_cargo_ups(self):
        assert infer_category({"squawk": "", "callsign": "UPS42"}) == "Cargo"

    def test_cargo_dhl(self):
        assert infer_category({"squawk": None, "callsign": "DHK456"}) == "Cargo"

    def test_military_reach(self):
        assert infer_category({"squawk": "", "callsign": "REACH123"}) == "Military"

    def test_military_nato(self):
        assert infer_category({"squawk": None, "callsign": "NATO01"}) == "Military"

    def test_private_ph_prefix(self):
        assert infer_category({"squawk": "", "callsign": "PHABCD"}) == "Private/GA"

    def test_private_n_prefix(self):
        assert infer_category({"squawk": None, "callsign": "N12345"}) == "Private/GA"

    def test_empty_callsign(self):
        assert infer_category({"squawk": "", "callsign": ""}) == "Unknown"

    def test_none_values(self):
        assert infer_category({"squawk": None, "callsign": None}) == "Unknown"

    def test_unknown_pattern(self):
        assert infer_category({"squawk": "", "callsign": "XYZXYZ"}) == "Unknown"


class TestParseStateVector:
    def test_full_vector(self):
        sv = ["abc123", "KLM123 ", "Netherlands", 1000, 1001,
              4.9, 52.3, 10000.0, False, 250.0, 90.0, 0.5,
              None, 10500.0, "1234", False, 0, 1]
        rec = parse_state_vector(sv)
        assert rec["icao24"] == "abc123"
        assert rec["callsign"] == "KLM123"  # trailing whitespace stripped
        assert rec["latitude"] == 52.3
        assert rec["longitude"] == 4.9

    def test_callsign_stripped(self):
        sv = ["abc123", "KLM123  "] + [None] * 16
        rec = parse_state_vector(sv)
        assert rec["callsign"] == "KLM123"

    def test_null_callsign_not_stripped(self):
        sv = ["abc123", None] + [None] * 16
        rec = parse_state_vector(sv)
        assert rec["callsign"] is None

    def test_short_vector_pads_none(self):
        rec = parse_state_vector(["abc123"])
        assert rec["icao24"] == "abc123"
        assert rec["latitude"] is None
        assert rec["squawk"] is None

    def test_all_fields_present(self):
        sv = [None] * 18
        rec = parse_state_vector(sv)
        assert len(rec) == 18
