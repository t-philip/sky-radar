# SKYRADAR_BRIEF.md
# Sky Radar — Build Brief
# Author: T. Philip

---

> ## ⚠️ This is a historical document. It does not describe the shipped software.
>
> This is the **pre-implementation** brief Sky Radar was built *from*, kept because the
> starting intent is interesting next to the outcome. It was never updated afterwards,
> and the code has moved on: flight-type inference was added and is not mentioned here
> at all, the state vector is 18 fields rather than 17, logs are date-stamped and
> pruned rather than one unbounded file, four control endpoints exist that this brief
> does not list, and the Socket.IO async mode is `threading` rather than the now-
> deprecated `eventlet` specified below.
>
> **For an accurate description of what Sky Radar actually does, read
> [docs/DESIGN_SPEC.md](docs/DESIGN_SPEC.md).** Its §0.2 lists every divergence between
> this brief and the build.

---

## 1. Project Overview

Build a local web-based real-time flight tracking dashboard called **Sky Radar**.

- Polls the OpenSky Network REST API every 60 seconds
- Displays all aircraft within a configurable radius of a configured location
- Shows live data on an interactive map and sortable data table
- Supports a watchlist for alerting on specific aircraft
- Logs all data to JSON Lines format for later processing
- Supports importing a `.jsonl` log file for replay/analysis

---

## 2. File Structure

Create the following structure. Do not deviate.

```
sky_radar/
├── app.py
├── skyradar.ini
├── requirements.txt
├── README.md
├── logs/
│   └── .gitkeep
├── static/
│   ├── css/
│   │   └── style.css
│   └── js/
│       └── dashboard.js
└── templates/
    └── index.html
```

---

## 3. Configuration File — skyradar.ini

```ini
[opensky]
; Leave username and password blank for anonymous access (1 request per 10s min, poll every 60s)
; Fill in credentials for authenticated access
username =
password =

[location]
name = Amsterdam, Netherlands
latitude = 52.3676
longitude = 4.9041
radius_km = 100.0

[watchlist]
; Comma-separated list of ICAO24 hex codes or callsigns to watch
; Example: entries = KLM123, 484b9f
entries =
```

---

## 4. Dependencies — requirements.txt

Use these packages with pinned major versions:

```
flask>=3.0
flask-socketio>=5.3
flask-cors>=4.0
requests>=2.31
geopy>=2.4
apscheduler>=3.10
eventlet>=0.35
```

---

## 5. Backend — app.py

### 5.1 General
- Use `pathlib.Path` for all file paths. No hardcoded OS-specific separators.
- All config read/write via `configparser` against `skyradar.ini`.
- Use `eventlet` as the async mode for Flask-Socket.IO.
- Add clear comments throughout.

### 5.2 OpenSky Polling
- Endpoint: `https://opensky-network.org/api/states/all`
- Derive bounding box (lamin, lomin, lamax, lomax) from configured lat/lon/radius_km using geopy.
- Pass bbox as query parameters.
- If `username` and `password` are non-empty in ini, use HTTP Basic Auth.
- If credentials are absent, poll anonymously.
- Poll every 60 seconds via APScheduler `BackgroundScheduler`.
- On API error (timeout, HTTP error, auth failure): log the error, skip the cycle, emit a `poll_error` Socket.IO event to clients with a message string. Do not crash.

### 5.3 Data Processing

Capture all 17 OpenSky state vector fields per aircraft:

| Index | Field name      | Type        |
|-------|-----------------|-------------|
| 0     | icao24          | string      |
| 1     | callsign        | string      |
| 2     | origin_country  | string      |
| 3     | time_position   | int/null    |
| 4     | last_contact    | int/null    |
| 5     | longitude       | float/null  |
| 6     | latitude        | float/null  |
| 7     | baro_altitude   | float/null  |
| 8     | on_ground       | bool        |
| 9     | velocity        | float/null  |
| 10    | true_track      | float/null  |
| 11    | vertical_rate   | float/null  |
| 12    | sensors         | list/null   |
| 13    | geo_altitude    | float/null  |
| 14    | squawk          | string/null |
| 15    | spi             | bool        |
| 16    | position_source | int         |

Add these derived fields for each aircraft:
- `distance_km`: great-circle distance from configured location (via geopy), rounded to 2 decimal places
- `bearing_deg`: bearing in degrees from configured location to aircraft (0°=North, clockwise), rounded to 1 decimal place
- `alert`: boolean — true if icao24 or callsign (stripped, lowercase) matches any entry in the watchlist
- `polled_at`: ISO 8601 timestamp of the poll cycle

Filter out aircraft where latitude or longitude is null.
Filter out aircraft beyond radius_km.

### 5.4 Logging
- After every successful poll, append one JSON object per aircraft to `logs/skyradar.log.jsonl`.
- Each record contains all 17 fields + `distance_km` + `bearing_deg` + `alert` + `polled_at`.
- Create the `logs/` directory if it does not exist.
- Use append mode. Never overwrite the log.

### 5.5 Socket.IO Events
- Emit `aircraft_update` to all clients after every successful poll. Payload:
  ```json
  {
    "aircraft": [...],
    "polled_at": "1999-01-01T12:00:00Z",
    "count": 42,
    "location": {
      "name": "Test Data Location",
      "lat": 52.3676,
      "lon": 4.9041,
      "radius_km": 150
    }
  }
  ```
- Emit `poll_error` to all clients on API failure. Payload: `{ "message": "..." }`

### 5.6 REST API Endpoints

| Method | Path                        | Description                                              |
|--------|-----------------------------|----------------------------------------------------------|
| GET    | `/`                         | Serve index.html                                         |
| GET    | `/api/config`               | Return current location config as JSON                   |
| POST   | `/api/config`               | Update location name, lat, lon, radius_km at runtime. Persist to ini. |
| GET    | `/api/watchlist`            | Return current watchlist as JSON array                   |
| POST   | `/api/watchlist`            | Add an entry (icao24 or callsign). Persist to ini.       |
| DELETE | `/api/watchlist/<entry>`    | Remove an entry. Persist to ini.                         |

---

## 6. Frontend

### 6.1 index.html
- Dark theme. Background `#0f172a`, accent colour `#6366f1` (indigo).
- Use Inter font from Google Fonts.
- Layout (top to bottom):
  1. **Header bar**: "🛩 Sky Radar" title, editable location name field, editable radius (km) field, last poll timestamp, live/replay mode badge.
  2. **Two-column row**: Left = Leaflet map (65% width). Right = Watchlist panel + Stats panel (35% width).
  3. **Toolbar**: Import `.jsonl` button, Export CSV button, connection status indicator.
  4. **Data table**: Full width, all fields, sortable, with search/filter input.
- Load Leaflet.js from CDN: `https://unpkg.com/leaflet@1.9.4/dist/leaflet.js`
- Load Socket.IO client from CDN: `https://cdn.socket.io/4.7.2/socket.io.min.js`
- All logic in `static/js/dashboard.js`. No inline JS in HTML.

### 6.2 dashboard.js

**Map:**
- Centre on configured location. Zoom level 7.
- Draw a circle representing the radius_km boundary.
- Place a distinct pin marker for the configured location with a popup showing name.
- For each aircraft: place a marker using a plane icon (Unicode ✈ rotated via CSS transform to true_track heading, or use a custom DivIcon).
- Clicking a marker opens a popup showing all 17 fields + distance_km + bearing_deg in a clean formatted layout.
- Watchlist-matched aircraft markers use amber colour.
- On each `aircraft_update`, update marker positions smoothly (add new, update existing, remove departed).

**Table:**
- Columns: icao24, callsign, origin_country, baro_altitude (m), geo_altitude (m), velocity (m/s), true_track (°), vertical_rate (m/s), on_ground, distance_km, bearing_deg, squawk, position_source, last_contact, alert.
- Sortable by clicking column headers.
- Search bar filters rows by any text field (callsign, icao24, origin_country, squawk).
- Watchlist-matched rows highlighted with amber left border and subtle amber background.
- Rows with `on_ground: true` shown with reduced opacity (0.6).

**Watchlist panel:**
- Shows current watchlist entries as removable tags (× button per entry).
- Input field + Add button to add a new entry live.
- On add: call `POST /api/watchlist`, refresh list.
- On remove: call `DELETE /api/watchlist/<entry>`, refresh list.
- Entries pre-loaded on page load via `GET /api/watchlist`.

**Stats panel (below watchlist):**
- Aircraft in range: count
- On ground: count
- Airborne: count
- Alerts active: count
- Last poll: human-readable timestamp
- Mode: LIVE (green badge) or REPLAY (amber badge)

**Import function:**
- File input accepts `.jsonl` files only.
- On load: parse all lines, group by `polled_at`, display the most recent snapshot in map + table.
- Show "REPLAY MODE" amber banner across top of page. Stats reflect imported data.
- Page reload returns to live mode.

**Export function:**
- Converts current table data (live or replay) to CSV.
- Triggers browser download as `skyradar_export_<timestamp>.csv`.

**Error handling:**
- On `poll_error` Socket.IO event: show a yellow warning banner at top of page with the error message. Auto-dismiss after 30 seconds.
- On Socket.IO disconnect: show a red "Connection lost — reconnecting…" banner.

### 6.3 style.css
- Dark theme consistent with index.html.
- Amber highlight class `.alert-row` for table rows and `.alert-marker` for map icons.
- Responsive layout — map and watchlist panel stack vertically on narrow screens.
- Smooth transitions on row highlight and banner appearance.
- Style the Leaflet popup to match dark theme.

---

## 7. README.md

Include:
1. Prerequisites (Python 3.10+, pip)
2. Installation steps (`pip install -r requirements.txt`)
3. Configuration instructions (skyradar.ini — credentials, location, watchlist)
4. How to run (`python app.py`, then open `http://localhost:5000`)
5. How to use the watchlist (ini + dashboard)
6. How to import a `.jsonl` log file for replay
7. OpenSky Network free account link for authenticated access
8. Notes on rate limits (anonymous vs authenticated)

---

## 8. Quality Requirements

- Platform agnostic. Use `pathlib.Path` everywhere. No `os.path.join` with string separators.
- Graceful API error handling — never crash on network failure.
- All ini writes must be atomic (write full file, not partial).
- No hardcoded secrets. Credentials only from ini.
- Code comments on every non-trivial function and block.
- Frontend must work without a build step — pure HTML/CSS/JS, CDN dependencies only.
- Test by running `python app.py` and opening `http://localhost:5000` in a browser.

---

## 9. Build Instructions for Claude Code

1. Read this entire file before writing any code.
2. Create all files and folders in the structure defined in Section 2.
3. Build in this order: `skyradar.ini` → `requirements.txt` → `app.py` → `templates/index.html` → `static/css/style.css` → `static/js/dashboard.js` → `README.md`.
4. After all files are created, confirm the full file list.
5. Offer to run `pip install -r requirements.txt` to validate the environment.
6. Offer to run `python app.py` to confirm the server starts without errors.
