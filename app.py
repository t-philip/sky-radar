"""
Sky Radar — app.py
Flask + Socket.IO backend for real-time flight tracking via OpenSky Network.
"""

import json
import logging
import math
import os
import re
import sys
import threading
import configparser
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.version_info < (3, 10):
    sys.exit(f"Python 3.10+ required (found {sys.version_info.major}.{sys.version_info.minor})")

from flask import Flask, request, jsonify, render_template
from flask_socketio import SocketIO
from apscheduler.schedulers.background import BackgroundScheduler
import requests
from geopy.distance import geodesic
from geopy.point import Point

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "skyradar.ini"
LOG_DIR = BASE_DIR / "logs"

# Days of JSONL flight logs to keep when [logging] retention_days is unset.
DEFAULT_LOG_RETENTION_DAYS = 30

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Flask / Socket.IO setup
# ---------------------------------------------------------------------------

app = Flask(__name__)

# Sky Radar binds to localhost only. These are the origins a legitimate dashboard
# can be served from — both spellings of loopback, because the browser treats them
# as distinct origins and either may appear in the address bar.
ALLOWED_ORIGINS = frozenset({
    "http://localhost:5000",
    "http://127.0.0.1:5000",
})

# async_mode="threading": eventlet's own maintainers deprecated it and recommend
# against new use, and its monkey-patching had to happen before every other import,
# which made import order load-bearing. Threading mode needs no patching at all.
# WebSocket transport is preserved by simple-websocket (see requirements.txt);
# without that package Socket.IO silently degrades to HTTP long-polling, which
# still works but reconnects more often.
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins=sorted(ALLOWED_ORIGINS))

# Methods that change server state and therefore need CSRF protection.
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@app.before_request
def block_cross_site_requests():
    """Reject state-changing API requests that did not come from the dashboard.

    Without this, any page open in the same browser could POST to this server —
    stopping the process, pausing polling, or moving the tracked location — because
    a form-style cross-origin POST is a "simple request" that needs no preflight,
    and the browser sends it regardless of whether the response can be read.

    Two independent checks, either of which is sufficient on its own:

    1. Origin allow-list. Browsers set `Origin` on every cross-origin request and it
       cannot be forged by page script. A request carrying someone else's origin is
       rejected outright.
    2. Require `Content-Type: application/json`. This is deliberately not a "simple"
       content type, so a cross-origin request using it triggers a CORS preflight —
       which this server never answers, so the browser blocks the real request before
       it is ever sent.

    Requests with no `Origin` header at all (curl, the run.bat helper, a local
    script) are allowed through: they are not browser-driven and CSRF does not
    apply to them.
    """
    if request.method not in _UNSAFE_METHODS:
        return None
    if not request.path.startswith("/api/"):
        return None

    origin = request.headers.get("Origin")
    if origin is not None and origin not in ALLOWED_ORIGINS:
        logger.warning("Rejected cross-site %s %s from origin %r",
                       request.method, request.path, origin)
        return jsonify({"error": "cross-site request rejected"}), 403

    if not request.is_json:
        return jsonify({
            "error": "Content-Type: application/json is required for this request"
        }), 415

    return None

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config() -> configparser.ConfigParser:
    """Read skyradar.ini and return a ConfigParser instance."""
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_PATH)
    return cfg


def ensure_section(cfg: configparser.ConfigParser, section: str) -> None:
    """Create a config section if it is absent.

    Reads all use `fallback=`, so a missing section is harmless on the read path —
    but `ConfigParser.set()` raises NoSectionError, which made every config write
    fail with a 500 when skyradar.ini did not exist or predated a newer section.
    That is the state of a fresh clone: skyradar.ini is git-ignored, so a user who
    has not yet copied skyradar.ini.example has no sections at all.
    """
    if not cfg.has_section(section):
        cfg.add_section(section)


def save_config(cfg: configparser.ConfigParser) -> None:
    """Write the full config back to skyradar.ini atomically."""
    tmp = CONFIG_PATH.with_suffix(".ini.tmp")
    with open(tmp, "w") as f:
        cfg.write(f)
    tmp.replace(CONFIG_PATH)


def get_location(cfg: configparser.ConfigParser) -> dict:
    """Extract location section from config as a plain dict."""
    return {
        "name": cfg.get("location", "name", fallback=""),
        "lat": cfg.getfloat("location", "latitude", fallback=0.0),
        "lon": cfg.getfloat("location", "longitude", fallback=0.0),
        "radius_km": cfg.getfloat("location", "radius_km", fallback=100.0),
    }


def get_watchlist(cfg: configparser.ConfigParser) -> list[str]:
    """Return watchlist entries as a list of lowercase stripped strings."""
    raw = cfg.get("watchlist", "entries", fallback="")
    return [e.strip().lower() for e in raw.split(",") if e.strip()]


# ---------------------------------------------------------------------------
# Bounding box calculation
# ---------------------------------------------------------------------------

def compute_bbox(lat: float, lon: float, radius_km: float) -> dict:
    """
    Compute a bounding box around (lat, lon) with the given radius in km.
    Returns dict with lamin, lomin, lamax, lomax.
    Uses geopy to offset by radius in cardinal directions.
    """
    origin = Point(lat, lon)

    north = geodesic(kilometers=radius_km).destination(origin, bearing=0)
    south = geodesic(kilometers=radius_km).destination(origin, bearing=180)
    east  = geodesic(kilometers=radius_km).destination(origin, bearing=90)
    west  = geodesic(kilometers=radius_km).destination(origin, bearing=270)

    return {
        "lamin": south.latitude,
        "lomin": west.longitude,
        "lamax": north.latitude,
        "lomax": east.longitude,
    }


# ---------------------------------------------------------------------------
# Callsign-based flight type inference
# ---------------------------------------------------------------------------

# ICAO 3-letter operator prefixes known to be cargo-only carriers
_CARGO_PREFIXES = {
    "FDX",  # FedEx
    "UPS",  # UPS Airlines
    "DHK",  # DHL Aviation (EU)
    "BCS",  # European Air Transport (DHL)
    "CLX",  # Cargolux
    "MPH",  # Martinair Cargo
    "ABX",  # ABX Air
    "GTI",  # Atlas Air
    "POL",  # Polar Air Cargo
    "TAY",  # ASL Airlines / TNT
    "SQC",  # Singapore Airlines Cargo
    "AFC",  # Air France Cargo
    "LCI",  # Lufthansa Cargo
}

# Callsign prefixes strongly associated with military operations
_MILITARY_PREFIXES = (
    "RFF", "NATO", "REACH", "DUKE", "VIPER", "IRON", "ROCKY",
    "NOBLE", "VALOR", "SWIFT", "BLADE", "HOMER", "BARON", "KING",
    "DODGER", "KNIFE", "SPAR", "LAGR", "HAVOC",
)

# ICAO airline callsign: exactly 3 letters followed by 1–4 digits and an optional letter
_AIRLINE_CS = re.compile(r'^([A-Z]{3})(\d{1,4}[A-Z]?)$')

# Registration-style callsign: national prefix + 2–5 alphanumeric chars, no embedded digits
# after an all-letter prefix (catches PH-ABC, GABCD, N12345, etc.)
_REGISTRATION_CS = re.compile(
    r'^(PH|OO|OE|LX|HB|SE|OH|OY|EI|SP|OK|OM|YR|LY|ES|YL|HA|'
    r'SX|TC|CS|EC|EK|EP|ET|4X|JY|AP|VT|B|JA|VH|ZK|ZS|'
    r'C|G|N|D|F)[A-Z0-9]{2,5}$'
)


def infer_category(record: dict) -> str:
    """
    Infer a plain-language flight type from callsign and squawk.
    Returns one of: Emergency, Military, Cargo, Commercial, Private/GA, Unknown.
    """
    squawk   = (record.get("squawk")   or "").strip()
    callsign = (record.get("callsign") or "").strip().upper()

    # Emergency transponder codes take top priority
    if squawk in ("7700", "7600", "7500"):
        return "Emergency"

    if not callsign:
        return "Unknown"

    # Military callsign prefix check
    if any(callsign.startswith(p) for p in _MILITARY_PREFIXES):
        return "Military"

    # ICAO airline pattern: AAA123 or AAA123A
    m = _AIRLINE_CS.match(callsign)
    if m:
        return "Cargo" if m.group(1) in _CARGO_PREFIXES else "Commercial"

    # Registration-style callsign → private / general aviation
    if _REGISTRATION_CS.match(callsign):
        return "Private/GA"

    return "Unknown"

# ---------------------------------------------------------------------------
# Bearing calculation
# ---------------------------------------------------------------------------

def compute_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Compute the initial bearing (0° = North, clockwise) from point 1 to point 2.
    Returns degrees rounded to 1 decimal place.
    """
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlon_r = math.radians(lon2 - lon1)

    x = math.sin(dlon_r) * math.cos(lat2_r)
    y = (math.cos(lat1_r) * math.sin(lat2_r)
         - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlon_r))

    bearing = math.degrees(math.atan2(x, y))
    return round((bearing + 360) % 360, 1)


# ---------------------------------------------------------------------------
# OpenSky state vector field names (index matches API response order)
# ---------------------------------------------------------------------------

STATE_FIELDS = [
    "icao24", "callsign", "origin_country", "time_position", "last_contact",
    "longitude", "latitude", "baro_altitude", "on_ground", "velocity",
    "true_track", "vertical_rate", "sensors", "geo_altitude", "squawk",
    "spi", "position_source", "category",
]


def parse_state_vector(sv: list) -> dict:
    """Convert a raw OpenSky state vector list into a named dict."""
    record = {}
    for i, field in enumerate(STATE_FIELDS):
        record[field] = sv[i] if i < len(sv) else None
    # Strip trailing whitespace from string fields
    if record.get("callsign"):
        record["callsign"] = record["callsign"].strip()
    return record


# ---------------------------------------------------------------------------
# Poll cycle
# ---------------------------------------------------------------------------

def poll_opensky() -> None:
    """
    Fetch aircraft states from OpenSky Network, process them, emit via
    Socket.IO, and append to the JSONL log.
    Called by APScheduler on a 60-second interval.
    """
    cfg = load_config()  # intentional: reread per-poll so live edits take effect
    loc = get_location(cfg)
    watchlist = get_watchlist(cfg)

    bbox = compute_bbox(loc["lat"], loc["lon"], loc["radius_km"])
    polled_at = datetime.now(timezone.utc).isoformat()

    params = {
        "lamin": bbox["lamin"],
        "lomin": bbox["lomin"],
        "lamax": bbox["lamax"],
        "lomax": bbox["lomax"],
    }

    # Build request kwargs — use Basic Auth if credentials are present
    username = cfg.get("opensky", "username", fallback="").strip()
    password = cfg.get("opensky", "password", fallback="").strip()
    req_kwargs: dict = {"params": params, "timeout": 15}
    if username and password:
        req_kwargs["auth"] = (username, password)

    try:
        response = requests.get(
            "https://opensky-network.org/api/states/all",
            **req_kwargs,
        )
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.Timeout:
        msg = "OpenSky API request timed out."
        logger.error(msg)
        socketio.emit("poll_error", {"message": msg})
        return
    except requests.exceptions.HTTPError as exc:
        msg = f"OpenSky API HTTP error: {exc}"
        logger.error(msg)
        socketio.emit("poll_error", {"message": msg})
        return
    except Exception as exc:
        msg = f"OpenSky API unexpected error: {exc}"
        logger.error(msg)
        socketio.emit("poll_error", {"message": msg})
        return

    raw_states = data.get("states") or []
    aircraft_list = []

    home = (loc["lat"], loc["lon"])

    for sv in raw_states:
        record = parse_state_vector(sv)

        # Skip aircraft without valid position
        if record["latitude"] is None or record["longitude"] is None:
            continue

        ac_pos = (record["latitude"], record["longitude"])

        # Distance filter
        distance_km = round(geodesic(home, ac_pos).km, 2)
        if distance_km > loc["radius_km"]:
            continue

        record["distance_km"] = distance_km
        record["bearing_deg"] = compute_bearing(
            loc["lat"], loc["lon"],
            record["latitude"], record["longitude"],
        )

        # Watchlist match on icao24 or callsign
        icao = (record.get("icao24") or "").strip().lower()
        callsign = (record.get("callsign") or "").strip().lower()
        record["alert"] = icao in watchlist or callsign in watchlist

        record["inferred_category"] = infer_category(record)
        record["polled_at"] = polled_at
        aircraft_list.append(record)

    logger.info("Poll complete: %d aircraft in range at %s", len(aircraft_list), polled_at)

    # Emit to all connected clients
    payload = {
        "aircraft": aircraft_list,
        "polled_at": polled_at,
        "count": len(aircraft_list),
        "location": loc,
    }
    socketio.emit("aircraft_update", payload)

    # Append to JSONL log, then prune anything past the retention window
    append_to_log(aircraft_list, get_log_retention_days(cfg))


def get_log_retention_days(cfg: configparser.ConfigParser) -> int:
    """Days of JSONL logs to keep. 0 (or negative) disables pruning entirely."""
    try:
        return cfg.getint("logging", "retention_days", fallback=DEFAULT_LOG_RETENTION_DAYS)
    except ValueError:
        logger.warning(
            "Invalid [logging] retention_days in config — falling back to %d days.",
            DEFAULT_LOG_RETENTION_DAYS,
        )
        return DEFAULT_LOG_RETENTION_DAYS


def prune_logs(retention_days: int) -> None:
    """Delete date-stamped JSONL logs older than the retention window.

    Sky Radar writes one JSON line per aircraft per poll — on the order of tens of
    thousands of lines a day — so an instance left running accumulates indefinitely
    without this. The date is read from the filename rather than the file's mtime,
    so an unrelated file in logs/ is never matched and never deleted.
    """
    if retention_days <= 0:
        return

    cutoff = datetime.now(timezone.utc).date() - timedelta(days=retention_days)
    pruned = 0
    for path in LOG_DIR.glob("skyradar-*.jsonl"):
        try:
            stamp = path.stem.removeprefix("skyradar-")
            file_date = datetime.strptime(stamp, "%Y-%m-%d").date()
        except ValueError:
            continue  # not one of ours — leave it alone
        if file_date < cutoff:
            try:
                path.unlink()
                pruned += 1
            except OSError as exc:
                logger.warning("Could not prune log %s: %s", path.name, exc)

    if pruned:
        logger.info("Pruned %d log file(s) older than %d days.", pruned, retention_days)


def append_to_log(aircraft_list: list[dict], retention_days: int) -> None:
    """Append one JSON line per aircraft to today's date-stamped JSONL log."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"skyradar-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
    with open(log_file, "a", encoding="utf-8") as f:
        for record in aircraft_list:
            f.write(json.dumps(record, default=str) + "\n")

    prune_logs(retention_days)


# ---------------------------------------------------------------------------
# REST API endpoints
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Serve the main dashboard page."""
    return render_template("index.html")


@app.route("/api/config", methods=["GET"])
def api_config_get():
    """Return current location configuration as JSON."""
    cfg = load_config()
    return jsonify(get_location(cfg))


@app.route("/api/config", methods=["POST"])
def api_config_post():
    """Update location config at runtime and persist to ini."""
    # silent=True (not force=True): the before_request guard has already required
    # application/json, so a body that fails to parse is a malformed request, not a
    # reason to parse it anyway.
    body = request.get_json(silent=True) or {}
    cfg = load_config()
    ensure_section(cfg, "location")

    try:
        if "name" in body:
            cfg.set("location", "name", str(body["name"])[:200])
        if "lat" in body:
            lat = float(body["lat"])
            if not -90 <= lat <= 90:
                return jsonify({"error": "lat must be between -90 and 90"}), 400
            cfg.set("location", "latitude", str(lat))
        if "lon" in body:
            lon = float(body["lon"])
            if not -180 <= lon <= 180:
                return jsonify({"error": "lon must be between -180 and 180"}), 400
            cfg.set("location", "longitude", str(lon))
        if "radius_km" in body:
            r = float(body["radius_km"])
            if not 1 <= r <= 1000:
                return jsonify({"error": "radius_km must be between 1 and 1000"}), 400
            cfg.set("location", "radius_km", str(r))
    except (TypeError, ValueError) as exc:
        return jsonify({"error": f"invalid value: {exc}"}), 400

    save_config(cfg)
    return jsonify({"status": "ok", "location": get_location(cfg)})


@app.route("/api/watchlist", methods=["GET"])
def api_watchlist_get():
    """Return current watchlist as a JSON array."""
    cfg = load_config()
    return jsonify(get_watchlist(cfg))


@app.route("/api/watchlist", methods=["POST"])
def api_watchlist_post():
    """Add an entry to the watchlist and persist to ini."""
    body = request.get_json(silent=True) or {}
    entry = (body.get("entry") or "").strip().lower()
    if not entry:
        return jsonify({"error": "entry is required"}), 400

    cfg = load_config()
    ensure_section(cfg, "watchlist")
    wl = get_watchlist(cfg)
    if entry not in wl:
        wl.append(entry)
        cfg.set("watchlist", "entries", ", ".join(wl))
        save_config(cfg)

    return jsonify({"status": "ok", "watchlist": wl})


@app.route("/api/watchlist/<entry>", methods=["DELETE"])
def api_watchlist_delete(entry: str):
    """Remove an entry from the watchlist and persist to ini."""
    entry = entry.strip().lower()
    cfg = load_config()
    ensure_section(cfg, "watchlist")
    wl = get_watchlist(cfg)

    if entry in wl:
        wl.remove(entry)
        cfg.set("watchlist", "entries", ", ".join(wl))
        save_config(cfg)

    return jsonify({"status": "ok", "watchlist": wl})


@app.route("/api/status", methods=["GET"])
def api_status():
    """Return current polling status (running or paused)."""
    job = scheduler.get_job("opensky_poll") if scheduler else None
    paused = job is not None and job.next_run_time is None
    return jsonify({"paused": paused})


@app.route("/api/pause", methods=["POST"])
def api_pause():
    """Pause the OpenSky polling job."""
    if scheduler:
        scheduler.pause_job("opensky_poll")
        logger.info("Polling paused.")
    return jsonify({"paused": True})


@app.route("/api/resume", methods=["POST"])
def api_resume():
    """Resume the OpenSky polling job."""
    if scheduler:
        scheduler.resume_job("opensky_poll")
        logger.info("Polling resumed.")
    return jsonify({"paused": False})


@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    """Gracefully stop the scheduler and exit the process.

    The exit is hooked to the response's close callback rather than fired from a
    thread after a fixed sleep. `call_on_close` runs once the WSGI server has
    finished sending the response body, so the client is guaranteed to receive the
    acknowledgement — a timing assumption that a `sleep()` could only approximate
    and would lose under load.
    """
    logger.info("Shutdown requested via /api/shutdown.")
    response = jsonify({"status": "shutting down"})

    @response.call_on_close
    def _shutdown() -> None:
        if scheduler:
            scheduler.shutdown(wait=False)
        # os._exit skips interpreter cleanup deliberately: the only writers are the
        # JSONL appender (flushed per line) and the config writer (atomic rename),
        # so there is no buffered state that a graceful teardown would protect.
        os._exit(0)

    return response


# ---------------------------------------------------------------------------
# Socket.IO connection events
# ---------------------------------------------------------------------------

@socketio.on("connect")
def on_connect():
    logger.info("Client connected: %s", request.sid)


@socketio.on("disconnect")
def on_disconnect():
    logger.info("Client disconnected: %s", request.sid)


# ---------------------------------------------------------------------------
# Scheduler setup
# ---------------------------------------------------------------------------

scheduler: BackgroundScheduler | None = None


def start_scheduler() -> None:
    """Start the APScheduler background scheduler for the poll cycle."""
    global scheduler
    scheduler = BackgroundScheduler()
    # Run immediately on startup, then every 60 seconds
    scheduler.add_job(poll_opensky, "interval", seconds=60, id="opensky_poll",
                      next_run_time=datetime.now(timezone.utc))
    scheduler.start()
    logger.info("Scheduler started — polling every 60 seconds.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    start_scheduler()
    logger.info("Starting Sky Radar on http://localhost:5000")
    # allow_unsafe_werkzeug: in threading mode Socket.IO serves through Werkzeug,
    # which refuses to start outside debug mode unless told explicitly. The usual
    # objection — never expose the development server to the internet — does not
    # apply to a process bound to 127.0.0.1 for a single local user, which is the
    # only supported deployment (see docs/DESIGN_SPEC.md §7).
    socketio.run(
        app,
        host="127.0.0.1",
        port=5000,
        debug=False,
        allow_unsafe_werkzeug=True,
    )
