"""Tests for the CSRF guard on state-changing API routes.

These cover the defect fixed alongside the design spec: before it, any page open in
the same browser could POST to this server and have the request take effect, because
a form-style cross-origin POST is a "simple request" needing no preflight.
"""
import pytest

from app import app, ALLOWED_ORIGINS


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestCrossOriginRejected:
    """A state-changing request carrying a foreign Origin must be refused."""

    @pytest.mark.parametrize("method,path", [
        ("post",   "/api/config"),
        ("post",   "/api/watchlist"),
        ("delete", "/api/watchlist/abc123"),
        ("post",   "/api/pause"),
        ("post",   "/api/resume"),
        ("post",   "/api/shutdown"),
    ])
    def test_foreign_origin_gets_403(self, client, method, path):
        res = getattr(client, method)(
            path,
            headers={"Origin": "https://evil.example"},
            json={},
        )
        assert res.status_code == 403
        assert "cross-site" in res.get_json()["error"]

    def test_foreign_origin_does_not_reach_handler(self, client, tmp_path, monkeypatch):
        """The guard must run before the route, not merely alongside it."""
        called = []
        monkeypatch.setattr("app.save_config", lambda cfg: called.append(cfg))
        client.post(
            "/api/config",
            headers={"Origin": "https://evil.example"},
            json={"lat": 1.0},
        )
        assert called == [], "handler ran despite a rejected origin"


class TestSimpleRequestRejected:
    """A cross-origin POST that avoids preflight by using a simple content type
    must be refused on content type alone, even with no Origin header."""

    @pytest.mark.parametrize("content_type", [
        "text/plain",
        "application/x-www-form-urlencoded",
        "multipart/form-data",
    ])
    def test_simple_content_type_gets_415(self, client, content_type):
        res = client.post(
            "/api/shutdown",
            data='{"anything": true}',
            content_type=content_type,
        )
        assert res.status_code == 415

    def test_no_content_type_gets_415(self, client):
        res = client.post("/api/pause")
        assert res.status_code == 415


class TestLegitimateRequestsAllowed:
    """The dashboard's own calls, and non-browser clients, must still work."""

    @pytest.mark.parametrize("origin", sorted(ALLOWED_ORIGINS))
    def test_dashboard_origin_allowed(self, client, origin, monkeypatch):
        monkeypatch.setattr("app.save_config", lambda cfg: None)
        res = client.post("/api/watchlist", headers={"Origin": origin}, json={"entry": "kl123"})
        assert res.status_code == 200

    def test_no_origin_header_allowed(self, client, monkeypatch):
        """curl and local scripts send no Origin; CSRF does not apply to them."""
        monkeypatch.setattr("app.save_config", lambda cfg: None)
        res = client.post("/api/watchlist", json={"entry": "kl123"})
        assert res.status_code == 200

    def test_get_requests_unaffected(self, client):
        assert client.get("/api/config").status_code == 200
        assert client.get("/api/watchlist").status_code == 200
        assert client.get("/api/status").status_code == 200

    def test_non_api_routes_unaffected(self, client):
        assert client.get("/").status_code == 200


class TestConfigValidationStillEnforced:
    """The guard must not have displaced the existing bounds checks."""

    @pytest.mark.parametrize("body,field", [
        ({"lat": 91.0},        "lat"),
        ({"lat": -91.0},       "lat"),
        ({"lon": 181.0},       "lon"),
        ({"lon": -181.0},      "lon"),
        ({"radius_km": 0.5},   "radius_km"),
        ({"radius_km": 1001},  "radius_km"),
    ])
    def test_out_of_range_rejected(self, client, body, field, monkeypatch):
        monkeypatch.setattr("app.save_config", lambda cfg: None)
        res = client.post("/api/config", json=body)
        assert res.status_code == 400
        assert field in res.get_json()["error"]
