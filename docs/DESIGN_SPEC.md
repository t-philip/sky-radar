# Sky Radar
## Design Specification v1.0 — As-Built

**Author:** T. Philip — <https://github.com/t-philip>
**Date:** 5 August 2026
**Status:** Reconciled against the shipped code. This describes what Sky Radar
actually does, including where it diverges from the original build brief and where
it falls short.
**Licence:** AGPL-3.0, same as the code. Relicensed from GPL-3.0 on 2026-08-06 — see §12.

---

## 0. How to read this document

[README.md](../README.md) explains how to *run* Sky Radar. This explains how it is
*built* and why — the polling and filtering model, the flight-type inference, and the
consequences of running an unauthenticated control API on localhost.

### 0.1 Relationship to `SKYRADAR_BRIEF.md`

[`SKYRADAR_BRIEF.md`](../SKYRADAR_BRIEF.md) is in this repository and is a
**pre-implementation** document — the brief the project was built *from*, written
before any code existed. It was never updated afterwards.

**It should not be read as a description of the shipped software.** Where the two
disagree, this document and the code are correct and the brief is out of date. The
brief is kept because the starting intent is genuinely interesting next to the outcome,
not because it is accurate; it now carries a notice at the top saying so (§10.6).

Section 0.2 lists every divergence found by reading the two side by side.

### 0.2 Brief vs. as-built — full divergence list

**Additions — features in the code that the brief never mentions:**

| # | Brief § | What was added | Why |
|---|---|---|---|
| 1 | §5.3 | **Flight-type inference** (`infer_category`) and the `inferred_category` field — Emergency / Military / Cargo / Commercial / Private/GA / Unknown. The brief has no concept of it at all. | A raw callsign is meaningless to a reader. This is the single largest addition and gets its own section (§5). |
| 2 | §5.6 | **Four endpoints**: `GET /api/status`, `POST /api/pause`, `POST /api/resume`, `POST /api/shutdown`. | Runtime control from the dashboard instead of restarting the process. See §7.3 for the security consequence. |
| 3 | §6.2 | **Category filter dropdown**, **pause/resume button**, **stop-server button** in the UI. | Front-ends for #1 and #2. |
| 4 | §2 | **`tests/`** — 64 unit tests. The brief specifies no tests whatsoever; its only stated verification is "run it and open a browser". | §9. |
| 5 | §2 | **`skyradar.ini.example`**, `.gitignore`, `LICENSE`, `run.bat`, `.github/` issue templates. | The brief's structure assumed a private project; publishing needs a config template that is not the live config. |
| 6 | §5.1 | **Python 3.10+ runtime guard** — `app.py` exits with a clear message on an older interpreter. | The brief names 3.10+ as a prerequisite but nothing enforced it; the failure was otherwise an obscure syntax error. |

**Changes — where the build deliberately departs from the brief:**

| # | Brief § | Brief said | As-built | Why |
|---|---|---|---|---|
| 7 | §5.3 | "all **17** ... fields", indices 0–16 | **18 fields** — `category` at index 17 | OpenSky's state vector gained an aircraft-category field. `parse_state_vector` pads absent trailing fields with `None`, so it tolerates either width. |
| 8 | §5.4 | One log: `logs/skyradar.log.jsonl` | **Date-stamped**: `logs/skyradar-YYYY-MM-DD.jsonl`, with retention | A single append-only file grows without bound and cannot be pruned or archived by date. Date-stamping made pruning possible; a `retention_days` setting now does it (§10.3). |
| 9 | §4 | `flask-cors>=4.0` in requirements | **Not a dependency.** Socket.IO gets `cors_allowed_origins`; the REST API is protected by an explicit origin + content-type guard | The brief specified the package without specifying a policy, and CORS is the wrong tool for this anyway — it governs reading *responses*, not sending *requests*. See §7.3. |
| 10 | §4 | Lower bounds only (`flask>=3.0`) | **Upper bounds too** (`flask>=3.0,<4.0`) | An unattended `pip install` should not pull a major version across a breaking change. |
| 11 | §2 | "Do not deviate" from the file layout | Repository root **is** the project; no `sky_radar/` wrapper | A published repository is the package. |
| 13 | §4, §5.1 | `eventlet` as the Socket.IO async mode | **`threading`**, plus `simple-websocket` | eventlet is deprecated by its own maintainers, and its monkey-patching made import order load-bearing. See §2.2. |

**Not met:**

| # | Brief § | Requirement | Status |
|---|---|---|---|
| 12 | §8 | "Code comments on every non-trivial function and block" | **Partially met.** Every function has a docstring, but the reasoning behind the heuristics in `infer_category` and the two-stage filter in §4.2 lives only in this document. |

---

## 1. Purpose and scope

### 1.1 In scope

A local, single-user web dashboard showing every aircraft currently within a
configurable radius of a configurable point — on a live map and a sortable table — with
a watchlist that highlights specific aircraft, and a JSONL log of everything seen for
later analysis or replay.

### 1.2 Out of scope

- **Not a receiver.** There is no SDR, no antenna, no ADS-B decoding. Sky Radar is a
  client of the OpenSky Network's aggregated feed and sees only what OpenSky publishes.
- **Not multi-user.** One process, one config file, one location. Any connected browser
  can change the settings for every other (§7.3).
- **Not internet-facing.** Binds to `127.0.0.1` and has no authentication of any kind.
  It rejects cross-site requests (§7.3), but that is not the same as authenticating
  anyone.
- **No historical queries.** Replay reads a local JSONL file; there is no time-series
  database and no OpenSky historical API use.

### 1.3 Design priorities, in order

1. **Never crash on a network failure.** The OpenSky API is free, public, and
   intermittently unavailable. A poll failure must degrade to a banner, not an exit.
2. **No build step.** Plain HTML, CSS and JS served by Flask; dependencies from a CDN.
   The project stays approachable and diffable.
3. **Config is live.** Changing the location or watchlist takes effect on the next poll
   without a restart (§3.3).

---

## 2. Architecture

```
   OpenSky Network REST API
       │  HTTPS GET /api/states/all?lamin&lomin&lamax&lomax
       │  every 60 s, APScheduler BackgroundScheduler
       ▼
   ┌─────────────────────────────────────────┐
   │ app.py                                  │
   │   poll_opensky()                        │
   │     reload config  ──────────────┐      │
   │     compute bbox (geopy)         │      │
   │     parse 18-field state vectors │      │
   │     filter: no position          │      │
   │     filter: exact geodesic > r   │      │      skyradar.ini
   │     derive distance, bearing     │◄─────┼────  (configparser,
   │     match watchlist              │      │       atomic writes)
   │     infer category               │      │
   └──────┬──────────────────┬────────┘      │
          │                  │               │
          │ Socket.IO        │ append        │
          │ "aircraft_update"│               │
          ▼                  ▼               │
    Browser dashboard   logs/skyradar-        │
    (Leaflet + table)   YYYY-MM-DD.jsonl      │
          │                                   │
          └── REST: /api/config, /api/watchlist, ──┘
              /api/status, /api/pause, /api/resume, /api/shutdown
```

### 2.1 Why Socket.IO rather than polling from the browser

The server already polls OpenSky on a fixed 60-second cycle, so the browser has nothing
to gain by asking — there is no new data between cycles. A push means one upstream
request serves every connected tab, and the number of browser tabs open can never
affect the rate at which Sky Radar hits a free public API (§3.2).

### 2.2 Why `threading` async mode

Flask-SocketIO needs an async mode to hold WebSocket connections open alongside
ordinary request handling. Sky Radar uses `threading`.

The original build used `eventlet`, which was the conventional choice and is now
deprecated by its own maintainers — importing it emits a warning recommending against
new use. It also made import order load-bearing: `eventlet.monkey_patch()` had to run
before Flask and `requests` were imported, so an ordinary tidy-up of the import block
was a real bug waiting to happen.

`threading` removes both problems. It needs no monkey-patching, so imports are just
imports. WebSocket transport is preserved by `simple-websocket`; without that package
Socket.IO silently degrades to HTTP long-polling, which works but reconnects more
often — hence its presence in `requirements.txt` rather than being left optional.

The concurrency trade normally cited against threading mode — a thread per connection
rather than a green thread — is irrelevant here. This is a single-user localhost
dashboard whose realistic peak is a handful of browser tabs, and the upstream poll rate
is fixed by the scheduler regardless of how many clients are attached (§2.1).

One consequence: in threading mode Socket.IO serves through Werkzeug, which refuses to
start outside debug mode unless `allow_unsafe_werkzeug=True` is passed. The usual
reason for that guard — never expose the development server to the internet — does not
apply to a process bound to `127.0.0.1` for one local user, which is the only supported
deployment (§7.1).

---

## 3. Polling design

### 3.1 The two-stage geographic filter

OpenSky's API accepts a **rectangular** bounding box; the dashboard wants a **circular**
radius. These are reconciled in two stages:

1. **Server-side bounding box** — `compute_bbox()` offsets the origin by `radius_km` in
   all four cardinal directions using `geopy`'s geodesic destination, producing the
   smallest rectangle enclosing the circle. This is what the API is asked for.
2. **Client-side exact filter** — every returned aircraft's true geodesic distance is
   computed and anything beyond `radius_km` is discarded.

**Why both.** Stage 1 alone over-reports: the box's corners lie up to ~41% further from
the origin than the radius, so aircraft outside the circle would appear. Stage 2 alone
is impossible — the API has no radius parameter, and fetching the whole world to filter
locally would be abusive. Stage 1 minimises what is transferred; stage 2 makes the
answer correct.

The geodesic (not Euclidean, not spherical) calculation matters at the box edges: at
52°N a degree of longitude is ~61% of a degree of latitude, so treating coordinates as a
plane distorts distance badly at a 100 km radius.

### 3.2 Poll cadence and rate limits

Fixed at 60 seconds. OpenSky's anonymous limit is one request per 10 seconds, so a
single 60-second poll sits comfortably inside it with room for the occasional retry, and
authenticated credentials (5 req/s) are optional rather than required. The interval is
deliberately not user-configurable in the UI: exposing it invites setting it to 5
seconds, which risks the shared free API for every user of it.

Credentials, when present in `skyradar.ini`, are sent as HTTP Basic Auth. When absent
the poll is anonymous — the tool is fully functional without an account.

### 3.3 Config reloaded every poll

`poll_opensky()` calls `load_config()` on every cycle rather than caching. The comment
in the source marks this as intentional. It costs one small file read per minute and
buys the property that any change — from the dashboard, or by editing the `.ini` by
hand — takes effect on the next poll with no restart. For a tool whose whole purpose is
watching a place you might want to change, that is the right trade.

### 3.4 Failure handling

Timeout, HTTP error and unexpected exception are caught separately, each logged and
each emitted to every client as a `poll_error` event carrying a message. The cycle is
skipped; the scheduler is untouched and fires again in 60 seconds. There is no state in
which a failed poll stops future polls.

---

## 4. Data model

### 4.1 State vector parsing

OpenSky returns each aircraft as a **positional array**, not an object.
`parse_state_vector()` zips it against `STATE_FIELDS` by index, padding with `None` for
any field the API did not send. That padding is what makes the 17→18 field change
(§0.2 #7) a non-event: a shorter array simply yields `None` for the trailing fields
rather than an `IndexError`.

### 4.2 Derived fields

| Field | Derivation |
|---|---|
| `distance_km` | Geodesic distance from the configured origin, 2 dp |
| `bearing_deg` | Initial great-circle bearing from origin, 0°=N clockwise, 1 dp |
| `alert` | `icao24` **or** `callsign` (stripped, lowercased) is in the watchlist |
| `inferred_category` | §5 |
| `polled_at` | ISO 8601 UTC timestamp, identical across every record in one cycle |

`polled_at` being shared across a cycle is load-bearing: it is the grouping key that
lets the replay importer reconstruct discrete snapshots from a flat JSONL file (§8.2).

---

## 5. Flight-type inference

Entirely absent from the brief, and the part of the code most worth explaining because
it is the only place Sky Radar *guesses*.

### 5.1 The rules, in priority order

1. **Squawk 7700 / 7600 / 7500 → `Emergency`.** Checked first and unconditionally.
   These are the internationally assigned emergency, radio-failure and unlawful-
   interference codes; when one is set it is the only thing about that aircraft worth
   surfacing. It deliberately overrides an otherwise perfectly good airline match —
   `test_emergency_takes_priority_over_callsign` pins exactly that.
2. **No callsign → `Unknown`.** Nothing to reason from.
3. **Military callsign prefix → `Military`.** Matched against a fixed list of tactical
   callsigns (`REACH`, `NATO`, `SPAR`, …).
4. **`^[A-Z]{3}\d{1,4}[A-Z]?$` → `Cargo` or `Commercial`.** The ICAO airline callsign
   shape: a three-letter operator code plus a flight number. The operator code is looked
   up against a cargo-carrier set (`FDX`, `UPS`, `CLX`, …) — cargo if present,
   commercial otherwise.
5. **Registration-shaped callsign → `Private/GA`.** A national registration prefix
   (`PH`, `N`, `G`, `D`, …) followed by 2–5 alphanumerics. General aviation flies under
   its registration; airlines do not.
6. **Otherwise → `Unknown`.** Deliberately not a catch-all guess.

### 5.2 Why ordering matters

Rule 3 before rule 4 is intentional: a military callsign is not an ICAO airline callsign
and would fall through to `Unknown` rather than being misfiled, but checking it first
also means a tactical callsign that *happens* to fit the airline shape is still called
military.

In the registration pattern the alternation lists **two-letter prefixes before
one-letter** ones (`PH|OO|…|B|C|G|N|D|F`). Python's regex alternation is
leftmost-first, so `PHABCD` matches the `PH` branch rather than being consumed by a
later single-letter branch. Reordering that list silently changes classifications.

### 5.3 Honest limits

This is a heuristic over a free-text field, not a lookup against an aircraft registry:

- **The cargo list is a fixed set of 13 operators.** Any cargo carrier not in it reads as
  `Commercial`. It is not, and cannot be, complete.
- **The military list is callsign folklore.** Words like `KING`, `SWIFT`, `BARON` and
  `NOBLE` are real tactical callsigns but are not reserved; a match is suggestive, not
  authoritative.
- **A registration-shaped callsign only means the operator filed one.** Business jets,
  air taxis and ferry flights all do.
- **`Unknown` is common and correct.** Roughly anything with an unusual callsign format
  lands there by design, because a wrong label is worse than no label.

Treat `inferred_category` as a filter for browsing, never as a fact about an aircraft.

---

## 6. Configuration

`skyradar.ini`, read and written with `configparser`, git-ignored, with
`skyradar.ini.example` as the template.

**Atomic writes.** `save_config()` writes to `skyradar.ini.tmp` and then `Path.replace()`s
it over the original — an atomic rename on both POSIX and Windows. Because the poll loop
re-reads the file every 60 seconds (§3.3), a non-atomic write has a real window in which
the poller reads a half-written file. This is the brief's one quality requirement
(§8, "all ini writes must be atomic") that mattered most, and it is met.

**Validation on write.** `POST /api/config` bounds-checks latitude to ±90, longitude to
±180 and radius to 1–1000 km, and truncates the location name to 200 characters,
returning HTTP 400 rather than persisting nonsense. A bad radius would otherwise
propagate straight into a bounding box and an API query.

---

## 7. Security model

### 7.1 Stated plainly

Sky Radar binds to `127.0.0.1:5000` and has **no authentication and no authorisation**.
It rejects cross-site requests (§7.3), but anyone who can reach the port directly has
full control. It is designed to run on a trusted personal machine and is not safe to
expose: changing the bind address to `0.0.0.0` hands every control below to anyone on
the network, and the CSRF guard does nothing about that — it defends against a hostile
*web page*, not a hostile *client*.

### 7.2 What it does protect

- **Credentials never leave the ini file.** No secret is logged, rendered, or sent to
  the browser; `/api/config` returns only the location block.
- **Output is escaped.** All string fields pass through `escHtml()` before entering the
  table or a map popup, so a hostile `callsign` or `origin_country` from the upstream
  feed cannot inject markup.
- **Bounded config input** (§6).

### 7.3 Cross-site request forgery, and how it is blocked

**The defect this section used to describe.** Until the fix in §10.2, the REST API had
no origin check and parsed request bodies with `force=True` regardless of
`Content-Type`. That made every state-changing endpoint reachable by CSRF: while Sky
Radar was running, any website open in the same browser could silently terminate the
process, pause polling so the dashboard went stale, move the tracked location anywhere
on earth, or alter the watchlist. It could not read anything back, so the impact was
disruption and silent misconfiguration rather than data theft — but it was a real
defect.

`flask-cors`, which the brief specified and the build omitted (§0.2 #9), would not have
fixed it. **CORS governs whether a browser lets a page read a cross-origin *response*;
it does not stop the *request* from being sent and taking effect.** A form-style POST
is a "simple request" that needs no preflight, so the side effect lands before CORS is
ever consulted.

**The guard.** `block_cross_site_requests()` runs `before_request` on every
state-changing method (`POST`, `PUT`, `PATCH`, `DELETE`) under `/api/`, and applies two
independent checks, either sufficient alone:

1. **Origin allow-list.** Browsers set `Origin` on cross-origin requests and page script
   cannot forge it. A request carrying an origin outside
   `{http://localhost:5000, http://127.0.0.1:5000}` is rejected with 403 and logged.
   Both loopback spellings are listed because the browser treats them as distinct
   origins and either may be in the address bar.
2. **`Content-Type: application/json` required.** This is deliberately *not* a simple
   content type, so a cross-origin request using it triggers a CORS preflight — which
   this server never answers, so the browser blocks the real request before it is sent.
   Anything else gets 415.

Requests carrying **no** `Origin` header at all are allowed: `curl`, `run.bat` and local
scripts are not browser-driven, and CSRF does not apply to them. This is a deliberate
choice, not an oversight — the threat model is a hostile *web page*, not a hostile local
process, which §7.1 already concedes is out of scope.

`request.get_json(force=True)` is correspondingly gone; handlers use `silent=True`,
since the guard has already established the content type.

### 7.4 `/api/shutdown`

An HTTP endpoint that calls `os._exit(0)` deserves its own note. It exists so the
dashboard's stop button works without a terminal, and it is reachable only through the
§7.3 guard.

The exit is hooked to the response's `call_on_close` callback, which the WSGI server
invokes once the response body has been sent — so the client is guaranteed to receive
its acknowledgement before the process dies. The original implementation spawned a
thread that slept 400 ms and then exited, which was a race rather than a
synchronisation: under load the response could be lost while the client still treated
the request as successful (§10.5).

The exit remains hard, skipping `atexit` handlers and any in-flight write. That is
acceptable because the only writers are the JSONL appender, which flushes per line, and
the config writer, which is atomic (§6) — there is no buffered state a graceful teardown
would protect.

---

## 8. Frontend

### 8.1 Rendering

No framework and no build step. `renderTable()` rebuilds the table body as a single
HTML string per update, which for the hundreds of aircraft in a 100 km radius is well
inside one frame and avoids a diffing layer entirely. Map markers, by contrast, are
reconciled incrementally — added, moved and removed — because tearing down and
rebuilding every Leaflet marker each cycle visibly flickers and drops open popups.

Sorting, text search and category filtering all run client-side against the last
snapshot. Nothing re-queries the server, so filtering cannot affect the upstream API
rate (§3.2).

### 8.2 Replay

Importing a `.jsonl` file parses each line (silently skipping malformed ones), groups
records by `polled_at`, and renders the **most recent** group as a static snapshot,
switching the UI into REPLAY mode. Live updates continue to arrive over Socket.IO but
are ignored until the page is reloaded.

Only the newest snapshot is shown — there is no scrubbing back through a log's
timeline. This matches the brief and is the honest limit of the feature: it answers
"what did the sky look like at the end of this file", not "play this file back".

---

## 9. Verification status

**Stated honestly.**

### 9.1 Covered

64 unit tests, all passing — `pytest tests/ -q`, Python 3.13.13, 1.16 s.

**Pure logic** (`test_app.py`, 30 tests) — the four functions where a silent error would
corrupt every record:

- `compute_bearing` — the four cardinal directions, range invariants, and never
  returning a negative angle.
- `compute_bbox` — the box contains the origin, is symmetric at the equator, and grows
  with the radius.
- `infer_category` — 16 tests covering every branch and the emergency-priority rule.
- `parse_state_vector` — callsign stripping, `None` handling, and short-vector padding.

**CSRF guard** (`test_security.py`, 24 tests) — a foreign origin rejected on every
state-changing route, the handler proven not to run when the origin is rejected, each
simple content type rejected, both dashboard origins accepted, `Origin`-less requests
accepted, `GET` and non-API routes unaffected, and the config bounds checks confirmed
still enforced behind the new guard. See §7.3.

**Log retention** (`test_log_retention.py`, 10 tests) — the retention setting including
an explicit `0`, an invalid value falling back to the default, age-based pruning with a
boundary case, unrelated files left untouched, and append-triggers-prune. See §10.3.

### 9.2 Not covered

No test exercises `poll_opensky()` (the poll cycle and its error handling), the
OpenSky HTTP interaction, `load_config`/`save_config`, the scheduler, or any Socket.IO
event. There are **no frontend tests at all** — the ~700-line `dashboard.js`, including
replay, CSV export and all rendering, is verified only by using it.

The suite tests pure logic and the request-guard layer. It does not test the network,
the filesystem beyond log pruning, or the browser. That is a deliberate boundary and a
real gap, not a claim of coverage.

### 9.3 Verified by hand against a running instance

The fixes in §10 were each exercised against the live server, not only the test client:

- **CSRF guard** — a forged `Origin` returns 403 and a simple `text/plain` POST returns
  415, with the process surviving both; legitimate dashboard-origin calls return 200.
  Confirmed under **both** the old eventlet server and the current threading one.
- **Shutdown** — the client receives `{"status": "shutting down"}` with HTTP 200 and the
  process is gone immediately afterwards, confirming `call_on_close` fires after the
  body is sent.
- **Socket.IO after the eventlet removal** — a real Socket.IO client connected,
  negotiated the **`websocket`** transport (not degraded to long-polling) and received a
  live `aircraft_update` carrying 11 aircraft with all derived fields populated.
- **Live polling** — real OpenSky data, JSONL log written.

Earlier manual verification of the UI stands: map, markers, table, sorting, search,
watchlist add/remove, category filter, pause/resume, CSV export and JSONL replay.

Not deliberately exercised: an OpenSky outage lasting hours, an authenticated
(credentialled) poll, behaviour at a radius near the 1000 km bound, the replay-mode XSS
fix against a crafted file, and any browser other than the development one.

---

## 10. Gaps found while writing this document — and fixed

Writing this specification surfaced six defects. All six are fixed, before this
repository's first spec release. They are recorded rather than deleted, because the
reasoning behind each fix is part of the design.

### 10.1 `eventlet` was deprecated — **fixed**

Importing it emitted `EventletDeprecationWarning`: *"Eventlet is deprecated. It is
currently being maintained in bugfix mode, and we strongly recommend against using it
for new projects."* (confirmed, eventlet 0.41.1 on Python 3.13.13). Its monkey-patching
also made import order load-bearing.

**Fix.** Migrated to `threading` async mode with `simple-websocket` for WebSocket
transport; `eventlet` is removed from `requirements.txt` entirely. Full reasoning and
the `allow_unsafe_werkzeug` consequence in §2.2.

*Verified:* 64 tests pass with eventlet uninstalled; a live Socket.IO client negotiated
the `websocket` transport and received real aircraft data (§9.3).

### 10.2 No CSRF protection — **fixed**

Any website open in the same browser could stop the process, pause polling, or move the
tracked location. Full description and the guard that replaced it in §7.3.

*Verified:* 24 unit tests plus live-server confirmation of both attack shapes (§9.3).

### 10.3 Logs grew without bound — **fixed**

Date-stamping (§0.2 #8) made the log tractable but nothing pruned it. A continuously
running instance writes one JSON line per aircraft per minute — tens of thousands of
lines a day — indefinitely.

**Fix.** A `[logging] retention_days` setting (default 30) prunes older date-stamped
files after each poll. The date is read from the **filename**, not the file's mtime, so
an unrelated file in `logs/` is never matched and never deleted. `0` disables pruning
for anyone who wants the full history.

*Verified:* 10 unit tests including the boundary, the disable case, and unrelated-file
safety.

### 10.4 Numeric fields were not escaped on the replay path — **fixed**

`renderTable()` escaped every string field but passed numerics through `fmtNum()`, which
returned a non-numeric value unchanged and unescaped. Live data could not trigger it —
OpenSky sends numbers — but **an imported `.jsonl` is arbitrary user-supplied JSON**, so
a crafted file with markup in `baro_altitude` injected into the DOM. The import path was
the one untrusted input and the one place escaping was incomplete.

**Fix.** `fmtNum()` now escapes anything that is not a finite number, and takes a
`decimals` argument so the two remaining raw `.toFixed()` call sites (distance and
bearing, in both the table and the map popup) route through it as well. Those sites had
a second bug: a string value would have thrown `TypeError` on `.toFixed`.

*Verified:* by inspection. Not exercised against a crafted file — noted in §9.3.

### 10.5 The shutdown response was a race — **fixed**

A fixed 400 ms sleep before `os._exit(0)`. **Fix:** `call_on_close`, §7.4.

*Verified:* live server returns the response and then exits (§9.3).

### 10.6 The brief was published unreconciled — **fixed**

`SKYRADAR_BRIEF.md` sat in the repository describing software that no longer matched it,
with nothing saying so.

**Fix.** The brief now opens with a prominent notice that it is a historical
pre-implementation document, summarising the main divergences and pointing here. §0.1
states the same from this side.

### 10.7 Bonus: config writes crashed without an ini file — **fixed**

Found by the new tests rather than by reading. Reads all use `fallback=`, so a missing
config section is harmless — but `ConfigParser.set()` raises `NoSectionError`, so
**every config write returned HTTP 500 when `skyradar.ini` did not exist or predated a
section**. That is precisely the state of a fresh clone: `skyradar.ini` is git-ignored,
so a user who has not yet copied `skyradar.ini.example` had no sections at all, and
adding a watchlist entry from the dashboard failed.

**Fix.** `ensure_section()` creates the section before any write.

*Verified:* the three tests that exposed it now pass; they exercise `/api/config` and
`/api/watchlist` with no ini file present.

---

## 11. Possible future work

1. **Frontend tests** — currently zero (§9.2), and the largest remaining gap now that
   §10 is closed.
2. Timeline scrubbing in replay rather than only the last snapshot (§8.2).
3. A test for `poll_opensky()` against a mocked OpenSky response.
4. Aircraft type lookup from an ICAO registry, which would turn most of §5's heuristics
   into facts.
5. Retiring `SKYRADAR_BRIEF.md` altogether once it stops being interesting as history.

---

## 12. Licence and provenance

Published under **AGPL-3.0** alongside the code it describes.

**Licence history.** This repository was published under GPL-3.0 until 2026-08-06, when
it was relicensed to AGPL-3.0. This matters here because Sky Radar runs as a persistent network server, so §13 (Remote Network
Interaction) is the clause with real teeth — anyone hosting a modified version must offer
its users the corresponding source. Copyright is held solely by T. Philip, so the change
was made without third-party consent. Releases already published under GPL-3.0 remain
available under GPL-3.0 to anyone who received them — that grant is not revocable.

Written and built by **T. Philip** — <https://github.com/t-philip>.
Repository: <https://github.com/t-philip/sky-radar>.
Flight data from the [OpenSky Network](https://opensky-network.org/). Not affiliated
with or endorsed by the OpenSky Network.
