# Sky Radar

A local web-based real-time flight tracking dashboard. Polls the [OpenSky Network](https://opensky-network.org) REST API every 60 seconds and displays all aircraft within a configurable radius on an interactive map and sortable data table.

---

## Prerequisites

- Python 3.10 or later
- pip

---

## Installation

```bash
pip install -r requirements.txt
```

---

## Configuration

Edit `skyradar.ini` before starting the server:

```ini
[opensky]
; Leave blank for anonymous access, or fill in for authenticated access
username =
password =

[location]
name = Amsterdam, Netherlands
latitude = 52.3676
longitude = 4.9041
radius_km = 100.0

[watchlist]
; Comma-separated ICAO24 hex codes or callsigns
entries =
```

**Location** — set your home latitude, longitude, and the radius (in km) you want to monitor. The name is displayed in the header and on the map pin.

**Credentials** — anonymous access is limited to roughly one request per 10 seconds minimum; the app polls every 60 seconds so this is fine for casual use. Authenticated access has higher rate limits. See [OpenSky Network — Free Account](https://opensky-network.org/index.php?option=com_users&view=registration) to register.

---

## Running

```bash
python app.py
```

Then open your browser at:

```
http://localhost:5000
```

The dashboard will connect automatically via Socket.IO and begin displaying live data after the first poll (within 60 seconds of startup; the first poll runs immediately on start).

---

## Watchlist

Add ICAO24 hex codes or callsigns to monitor specific aircraft. Matching aircraft are highlighted in amber on both the map and the data table, and the Alerts counter in the Stats panel increments.

**Via `skyradar.ini`** (before starting):
```ini
[watchlist]
entries = KLM123, 484b9f, TOM1234
```

**Via the dashboard** (live, no restart required):
- Type an ICAO24 or callsign into the Watchlist input and click **Add**.
- Click **×** on any tag to remove an entry.
- Changes are immediately persisted to `skyradar.ini`.

---

## Log files

Every successful poll appends one JSON Lines record per aircraft to:

```
logs/skyradar.log.jsonl
```

Each record contains all 17 OpenSky state vector fields plus `distance_km`, `bearing_deg`, `alert`, and `polled_at`.

---

## Importing a log file for replay

1. Click **📂 Import .jsonl** in the toolbar.
2. Select a `.jsonl` log file (from `logs/` or exported from another session).
3. The dashboard switches to **REPLAY MODE**, showing the most recent snapshot from the file on the map and table.
4. Reload the page to return to live mode.

---

## Exporting data

Click **💾 Export CSV** to download the current table data (live or replay) as a CSV file named `skyradar_export_<timestamp>.csv`.

---

## Rate limits

| Access type   | Minimum interval | Notes                         |
|---------------|-----------------|-------------------------------|
| Anonymous     | ~10 seconds     | App polls every 60s — fine    |
| Authenticated | ~5 seconds      | Higher limits, more reliable  |

Anonymous access may occasionally return HTTP 429 (too many requests) during busy periods. The app handles this gracefully — it logs the error, skips the cycle, and shows a warning banner in the dashboard.

---

## Security — read before changing the bind address

Sky Radar binds to `127.0.0.1` and has **no authentication**. It is built to run on a
trusted personal machine.

- **Do not expose it.** Changing the host to `0.0.0.0` gives anyone who can reach the
  port full control, including the ability to stop the process.
- **Cross-site requests are rejected.** State-changing API calls must come from the
  dashboard's own origin and must be `application/json`, so a hostile page open in the
  same browser cannot stop the server or move your configured location. See
  [DESIGN_SPEC §7.3](docs/DESIGN_SPEC.md#73-cross-site-request-forgery-and-how-it-is-blocked).
  This defends against a hostile *web page*, not a hostile *client on your network* —
  which is why the first point still stands.

---

## Design

[docs/DESIGN_SPEC.md](docs/DESIGN_SPEC.md) explains how Sky Radar is built and why —
the two-stage circular/rectangular geographic filter, how flight types are inferred
from callsigns and how far to trust that, the security model, and an honest account of
what is and is not covered by tests.

Note that [`SKYRADAR_BRIEF.md`](SKYRADAR_BRIEF.md) is the **pre-implementation** brief
and no longer describes the shipped code; the design spec's §0.2 lists every
divergence.

---

## Troubleshooting

- **No aircraft appear** — verify your lat/lon and radius. The bounding box may not cover the area you expect. Try increasing `radius_km`.
- **Connection lost banner** — the Socket.IO connection dropped. The client reconnects automatically; the banner dismisses when reconnected.
- **Poll error banner** — the OpenSky API returned an error. The banner auto-dismisses after 30 seconds. Check the terminal log for details.

---

## Reporting issues

Found a bug, a mistake in the documentation, or want to suggest something?
**[Open an issue](https://github.com/t-philip/sky-radar/issues/new/choose)** — pick a
template and it will ask for the details that actually help.

Before filing, it's worth checking whether the **OpenSky Network API** is
responding — it's a free public service and does go down or rate-limit, which
looks identical to a bug from inside the app.

Please don't paste your `skyradar.ini` into an issue; it contains your own
coordinates. Describe the setting instead.

---

## Licence

Licensed under [AGPL-3.0](LICENSE). Please credit **t-philip** if you use or share this.
Because Sky Radar runs as a network service, if you modify it and let others use it over
a network (not just run it locally yourself), you must offer those users the source of
your modified version.

Built and maintained by [t-philip](https://github.com/t-philip).
