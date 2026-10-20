/**
 * Sky Radar — dashboard.js
 * Handles Socket.IO connection, Leaflet map, data table, watchlist,
 * import/export, and all live/replay UI logic.
 */

"use strict";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let currentAircraft = [];      // Array of aircraft objects from latest update
let locationConfig = {};       // { name, lat, lon, radius_km }
let isReplayMode = false;
let sortCol = "distance_km";
let sortDir = "asc";
let searchQuery = "";
let categoryFilter = "";


// Map objects keyed by icao24
const markerMap = {};          // icao24 → Leaflet marker
let homeMarker = null;
let radiusCircle = null;
let leafletMap = null;

// ---------------------------------------------------------------------------
// DOM element references
// ---------------------------------------------------------------------------

const bannerReplay     = document.getElementById("banner-replay");
const bannerError      = document.getElementById("banner-error");
const bannerDisconnect = document.getElementById("banner-disconnect");

const inputLocationName = document.getElementById("input-location-name");
const inputRadius       = document.getElementById("input-radius");
const btnUpdateConfig   = document.getElementById("btn-update-config");
const lastPollLabel     = document.getElementById("last-poll-label");
const modeBadge         = document.getElementById("mode-badge");

const watchlistTags     = document.getElementById("watchlist-tags");
const inputWatchEntry   = document.getElementById("input-watchlist-entry");
const btnAddWatch       = document.getElementById("btn-add-watch");

const statTotal         = document.getElementById("stat-total");
const statAirborne      = document.getElementById("stat-airborne");
const statGround        = document.getElementById("stat-ground");
const statAlerts        = document.getElementById("stat-alerts");
const statLastPoll      = document.getElementById("stat-last-poll");
const statMode          = document.getElementById("stat-mode");

const categoryFilterEl  = document.getElementById("category-filter");
const connStatus        = document.getElementById("conn-status");
const btnStopServer     = document.getElementById("btn-stop-server");
const btnPausePolling   = document.getElementById("btn-pause-polling");
const importFileInput   = document.getElementById("import-file");
const btnExportCSV      = document.getElementById("btn-export-csv");

const tableSearch       = document.getElementById("table-search");
const aircraftTbody     = document.getElementById("aircraft-tbody");
const tableHeaders      = document.querySelectorAll("#aircraft-table thead th");

// ---------------------------------------------------------------------------
// Map initialisation
// ---------------------------------------------------------------------------

function initMap(lat, lon) {
  if (leafletMap) return; // already initialised

  leafletMap = L.map("map").setView([lat, lon], 7);

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap contributors",
    maxZoom: 18,
  }).addTo(leafletMap);
}

function updateMapLocation(lat, lon, radiusKm, name) {
  if (!leafletMap) initMap(lat, lon);

  // Home pin
  if (homeMarker) homeMarker.remove();
  homeMarker = L.marker([lat, lon], {
    icon: L.divIcon({
      className: "",
      html: `<div class="home-pin" title="${escHtml(name)}">📍</div>`,
      iconAnchor: [11, 22],
    }),
    zIndexOffset: 1000,
  }).addTo(leafletMap);
  homeMarker.bindPopup(`<div class="popup-title">${escHtml(name)}</div><div style="color:var(--text-muted);font-size:12px">${lat}, ${lon}</div>`);

  // Radius circle
  if (radiusCircle) radiusCircle.remove();
  radiusCircle = L.circle([lat, lon], {
    radius: radiusKm * 1000,
    color: "#6366f1",
    weight: 1.5,
    opacity: 0.5,
    fillOpacity: 0.04,
    dashArray: "5 5",
  }).addTo(leafletMap);
}

// ---------------------------------------------------------------------------
// Aircraft markers
// ---------------------------------------------------------------------------

function buildPlaneIcon(track, isAlert) {
  const deg = track != null ? track : 0;
  const cls = isAlert ? "plane-icon alert-marker" : "plane-icon";
  return L.divIcon({
    className: "",
    html: `<div class="${cls}" style="transform: rotate(${deg}deg)">✈</div>`,
    iconSize: [22, 22],
    iconAnchor: [11, 11],
    popupAnchor: [0, -14],
  });
}

function buildPopupHTML(ac) {
  const alertLine = ac.alert
    ? `<div class="popup-alert" style="margin-bottom:6px">⚠ WATCHLIST MATCH</div>` : "";
  const rows = [
    ["ICAO24",        escHtml(ac.icao24)],
    ["Callsign",      escHtml(ac.callsign)],
    ["Country",       escHtml(ac.origin_country)],
    ["Baro Alt (m)",  fmtNum(ac.baro_altitude)],
    ["Geo Alt (m)",   fmtNum(ac.geo_altitude)],
    ["Speed (m/s)",   fmtNum(ac.velocity)],
    ["Track (°)",     fmtNum(ac.true_track)],
    ["V/S (m/s)",     fmtNum(ac.vertical_rate)],
    ["On Ground",     ac.on_ground ? "Yes" : "No"],
    ["Distance (km)", fmtNum(ac.distance_km, 2)],
    ["Bearing (°)",   fmtNum(ac.bearing_deg, 1)],
    ["Squawk",        escHtml(ac.squawk)],
    ["Src",           escHtml(ac.position_source)],
    ["Type",          escHtml(ac.inferred_category)],
    ["Polled At",     escHtml(ac.polled_at)],
  ];
  const gridRows = rows.map(([l, v]) =>
    `<span class="popup-label">${l}</span><span class="popup-value">${v}</span>`
  ).join("");
  return `${alertLine}<div class="popup-title">${escHtml(ac.callsign || ac.icao24)}</div><div class="popup-grid">${gridRows}</div>`;
}

function updateMarkers(aircraftList) {
  const seen = new Set();

  for (const ac of aircraftList) {
    if (ac.latitude == null || ac.longitude == null) continue;
    const id = ac.icao24;
    seen.add(id);

    const icon = buildPlaneIcon(ac.true_track, ac.alert);

    if (markerMap[id]) {
      // Update existing marker
      markerMap[id].setLatLng([ac.latitude, ac.longitude]);
      markerMap[id].setIcon(icon);
      markerMap[id].getPopup()?.setContent(buildPopupHTML(ac));
    } else {
      // Create new marker
      const marker = L.marker([ac.latitude, ac.longitude], { icon })
        .addTo(leafletMap)
        .bindPopup(buildPopupHTML(ac), { maxWidth: 340 });
      markerMap[id] = marker;
    }
  }

  // Remove markers for aircraft that are no longer in range
  for (const id of Object.keys(markerMap)) {
    if (!seen.has(id)) {
      markerMap[id].remove();
      delete markerMap[id];
    }
  }
}

// ---------------------------------------------------------------------------
// Table rendering
// ---------------------------------------------------------------------------

function fmtNum(val, decimals = 1) {
  if (val == null) return "—";
  // Anything that is not a real number is escaped before it reaches the DOM.
  // Live OpenSky data is always numeric here, but an imported .jsonl file is
  // arbitrary user-supplied JSON and is the one untrusted input this app has.
  if (typeof val !== "number" || !Number.isFinite(val)) return escHtml(val);
  return val.toFixed(decimals);
}

function escHtml(val) {
  if (val == null) return "—";
  return String(val)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmtTimestamp(ts) {
  if (ts == null) return "—";
  return new Date(ts * 1000).toUTCString().slice(0, 25);
}

function sortAircraft(list) {
  return [...list].sort((a, b) => {
    let va = a[sortCol];
    let vb = b[sortCol];

    // Treat nulls as largest
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;

    // String comparison
    if (typeof va === "string") va = va.toLowerCase();
    if (typeof vb === "string") vb = vb.toLowerCase();

    if (va < vb) return sortDir === "asc" ? -1 : 1;
    if (va > vb) return sortDir === "asc" ?  1 : -1;
    return 0;
  });
}

function filterAircraft(list) {
  let result = list;
  if (searchQuery) {
    const q = searchQuery.toLowerCase();
    result = result.filter(ac =>
      (ac.icao24         || "").toLowerCase().includes(q) ||
      (ac.callsign       || "").toLowerCase().includes(q) ||
      (ac.origin_country || "").toLowerCase().includes(q) ||
      (ac.squawk         || "").toLowerCase().includes(q)
    );
  }
  if (categoryFilter !== "") {
    result = result.filter(ac => ac.inferred_category === categoryFilter);
  }
  return result;
}

function updateCategoryDropdown(list) {
  const counts = {};
  for (const ac of list) {
    const cat = ac.inferred_category || "Unknown";
    counts[cat] = (counts[cat] || 0) + 1;
  }
  const current = categoryFilterEl.value;
  const order = ["Commercial", "Cargo", "Private/GA", "Military", "Emergency", "Unknown"];
  const sorted = order.filter(c => counts[c]).concat(
    Object.keys(counts).filter(c => !order.includes(c)).sort()
  );
  categoryFilterEl.innerHTML = `<option value="">All types (${list.length})</option>` +
    sorted.map(cat =>
      `<option value="${cat}">${cat} (${counts[cat]})</option>`
    ).join("");
  if (sorted.includes(current)) categoryFilterEl.value = current;
}

function renderTable(list) {
  const filtered = filterAircraft(sortAircraft(list));
  const rows = filtered.map(ac => {
    const alertClass = ac.alert ? "alert-row" : "";
    const groundClass = ac.on_ground ? "on-ground" : "";
    return `<tr class="${alertClass} ${groundClass}" data-icao="${escHtml(ac.icao24)}">
      <td>${escHtml(ac.icao24)}</td>
      <td>${escHtml(ac.callsign)}</td>
      <td>${escHtml(ac.origin_country)}</td>
      <td>${fmtNum(ac.baro_altitude)}</td>
      <td>${fmtNum(ac.geo_altitude)}</td>
      <td>${fmtNum(ac.velocity)}</td>
      <td>${fmtNum(ac.true_track)}</td>
      <td>${fmtNum(ac.vertical_rate)}</td>
      <td>${ac.on_ground ? "Yes" : "No"}</td>
      <td>${fmtNum(ac.distance_km, 2)}</td>
      <td>${fmtNum(ac.bearing_deg, 1)}</td>
      <td>${escHtml(ac.squawk)}</td>
      <td>${escHtml(ac.position_source)}</td>
      <td>${fmtTimestamp(ac.last_contact)}</td>
      <td>${escHtml(ac.inferred_category)}</td>
      <td>${ac.alert ? "⚠" : ""}</td>
    </tr>`;
  });
  aircraftTbody.innerHTML = rows.join("");
}

// ---------------------------------------------------------------------------
// Stats update
// ---------------------------------------------------------------------------

function updateStats(list, polledAt) {
  const total    = list.length;
  const ground   = list.filter(a => a.on_ground).length;
  const airborne = total - ground;
  const alerts   = list.filter(a => a.alert).length;

  statTotal.textContent   = total;
  statAirborne.textContent = airborne;
  statGround.textContent  = ground;
  statAlerts.textContent  = alerts;

  if (polledAt) {
    const d = new Date(polledAt);
    const ts = d.toLocaleTimeString();
    statLastPoll.textContent = ts;
    lastPollLabel.textContent = `Last poll: ${ts}`;
  }
}

function setMode(replay) {
  isReplayMode = replay;
  if (replay) {
    modeBadge.className = "badge badge-amber";
    modeBadge.textContent = "REPLAY";
    statMode.className = "badge badge-amber";
    statMode.textContent = "REPLAY";
    bannerReplay.style.display = "";
  } else {
    modeBadge.className = "badge badge-green";
    modeBadge.textContent = "LIVE";
    statMode.className = "badge badge-green";
    statMode.textContent = "LIVE";
    bannerReplay.style.display = "none";
  }
}

// ---------------------------------------------------------------------------
// Handle aircraft update (live or replay)
// ---------------------------------------------------------------------------

function applyUpdate(aircraftList, polledAt, loc) {
  currentAircraft = aircraftList;

  if (loc) {
    locationConfig = loc;
    if (!leafletMap) initMap(loc.lat, loc.lon);
    updateMapLocation(loc.lat, loc.lon, loc.radius_km, loc.name);
    inputLocationName.value = loc.name;
    inputRadius.value = loc.radius_km;
  }

  updateCategoryDropdown(aircraftList);
  updateMarkers(aircraftList);
  renderTable(aircraftList);
  updateStats(aircraftList, polledAt);
}

// ---------------------------------------------------------------------------
// Socket.IO
// ---------------------------------------------------------------------------

const socket = io();

socket.on("connect", () => {
  connStatus.textContent = "● Connected";
  connStatus.className = "conn-status conn-connected";
  bannerDisconnect.style.display = "none";
});

socket.on("disconnect", () => {
  connStatus.textContent = "● Disconnected";
  connStatus.className = "conn-status conn-disconnected";
  bannerDisconnect.style.display = "";
});

socket.on("connect_error", () => {
  connStatus.textContent = "● Connecting…";
  connStatus.className = "conn-status conn-connecting";
});

socket.on("aircraft_update", (data) => {
  if (isReplayMode) return; // ignore live updates during replay
  applyUpdate(data.aircraft, data.polled_at, data.location);
});

socket.on("poll_error", (data) => {
  showErrorBanner(data.message || "Unknown poll error.");
});

// ---------------------------------------------------------------------------
// Error banner (auto-dismiss 30s)
// ---------------------------------------------------------------------------

let errorDismissTimer = null;

function showErrorBanner(msg) {
  bannerError.textContent = `⚠ ${msg}`;
  bannerError.style.display = "";
  if (errorDismissTimer) clearTimeout(errorDismissTimer);
  errorDismissTimer = setTimeout(() => {
    bannerError.style.display = "none";
  }, 30000);
}

// ---------------------------------------------------------------------------
// Config: load on page load, update on button click
// ---------------------------------------------------------------------------

async function loadConfig() {
  try {
    const res = await fetch("/api/config");
    const cfg = await res.json();
    locationConfig = { lat: cfg.lat, lon: cfg.lon, radius_km: cfg.radius_km, name: cfg.name };
    inputLocationName.value = cfg.name;
    inputRadius.value = cfg.radius_km;
    if (!leafletMap) {
      initMap(cfg.lat, cfg.lon);
      updateMapLocation(cfg.lat, cfg.lon, cfg.radius_km, cfg.name);
    }
  } catch (e) {
    console.error("Failed to load config", e);
  }
}

btnUpdateConfig.addEventListener("click", async () => {
  const name = inputLocationName.value.trim();
  const radius_km = parseFloat(inputRadius.value) || locationConfig.radius_km;
  if (!name) return;

  btnUpdateConfig.disabled = true;
  btnUpdateConfig.textContent = "Searching…";

  try {
    // Geocode the location name via Nominatim
    // User-Agent is a forbidden header in browser Fetch; the browser sends its own UA.
    // Nominatim rate-limit is 1 req/s; the Update button is disabled during the request.
    const geoRes = await fetch(
      `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(name)}&format=json&limit=1`,
      { headers: { "Accept-Language": "en" } }
    );
    const geoData = await geoRes.json();

    if (!geoData.length) {
      showErrorBanner(`Location not found: "${name}"`);
      return;
    }

    const lat = parseFloat(geoData[0].lat);
    const lon = parseFloat(geoData[0].lon);

    const res = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, lat, lon, radius_km }),
    });
    const data = await res.json();
    locationConfig = data.location;
    updateMapLocation(locationConfig.lat, locationConfig.lon, locationConfig.radius_km, locationConfig.name);
    leafletMap.setView([locationConfig.lat, locationConfig.lon], leafletMap.getZoom());
  } catch (e) {
    console.error("Config update failed", e);
    showErrorBanner("Failed to update location.");
  } finally {
    btnUpdateConfig.disabled = false;
    btnUpdateConfig.textContent = "Update";
  }
});

// ---------------------------------------------------------------------------
// Watchlist
// ---------------------------------------------------------------------------

async function loadWatchlist() {
  try {
    const res = await fetch("/api/watchlist");
    const entries = await res.json();
    renderWatchlist(entries);
  } catch (e) {
    console.error("Failed to load watchlist", e);
  }
}

function renderWatchlist(entries) {
  watchlistTags.innerHTML = entries.map(e =>
    `<span class="watch-tag">
      ${escHtml(e)}
      <button class="watch-tag-remove" data-entry="${escHtml(e)}" title="Remove">×</button>
    </span>`
  ).join("");

  watchlistTags.querySelectorAll(".watch-tag-remove").forEach(btn => {
    btn.addEventListener("click", async () => {
      const entry = btn.dataset.entry;
      await fetch(`/api/watchlist/${encodeURIComponent(entry)}`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      await loadWatchlist();
    });
  });
}

btnAddWatch.addEventListener("click", async () => {
  const entry = inputWatchEntry.value.trim();
  if (!entry) return;
  try {
    await fetch("/api/watchlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entry }),
    });
    inputWatchEntry.value = "";
    await loadWatchlist();
  } catch (e) {
    console.error("Add watchlist entry failed", e);
  }
});

inputWatchEntry.addEventListener("keydown", (e) => {
  if (e.key === "Enter") btnAddWatch.click();
});

// ---------------------------------------------------------------------------
// Table sort
// ---------------------------------------------------------------------------

tableHeaders.forEach(th => {
  th.addEventListener("click", () => {
    const col = th.dataset.col;
    if (sortCol === col) {
      sortDir = sortDir === "asc" ? "desc" : "asc";
    } else {
      sortCol = col;
      sortDir = "asc";
    }
    // Update header classes
    tableHeaders.forEach(h => h.classList.remove("sort-asc", "sort-desc"));
    th.classList.add(sortDir === "asc" ? "sort-asc" : "sort-desc");
    renderTable(currentAircraft);
  });
});

// Default sort indicator
document.querySelector(`th[data-col="${sortCol}"]`)?.classList.add("sort-asc");

// ---------------------------------------------------------------------------
// Table search
// ---------------------------------------------------------------------------

tableSearch.addEventListener("input", (e) => {
  searchQuery = e.target.value.trim();
  renderTable(currentAircraft);
});

categoryFilterEl.addEventListener("change", (e) => {
  categoryFilter = e.target.value;
  renderTable(currentAircraft);
});

// ---------------------------------------------------------------------------
// Import .jsonl
// ---------------------------------------------------------------------------

importFileInput.addEventListener("change", (e) => {
  const file = e.target.files?.[0];
  if (!file) return;

  const reader = new FileReader();
  reader.onload = (ev) => {
    const text = ev.target.result;
    const lines = text.split("\n").filter(l => l.trim());
    const records = [];

    for (const line of lines) {
      try {
        records.push(JSON.parse(line));
      } catch (err) {
        // Skip malformed lines silently
      }
    }

    if (records.length === 0) {
      alert("No valid records found in file.");
      return;
    }

    // Group by polled_at and take the most recent snapshot
    const byPoll = {};
    for (const r of records) {
      const key = r.polled_at || "unknown";
      if (!byPoll[key]) byPoll[key] = [];
      byPoll[key].push(r);
    }

    const sortedKeys = Object.keys(byPoll).sort();
    const latestKey = sortedKeys[sortedKeys.length - 1];
    const snapshot = byPoll[latestKey];

    setMode(true);
    applyUpdate(snapshot, latestKey, null);
  };
  reader.readAsText(file);

  // Reset so same file can be re-imported
  importFileInput.value = "";
});

// ---------------------------------------------------------------------------
// Export CSV
// ---------------------------------------------------------------------------

btnExportCSV.addEventListener("click", () => {
  if (currentAircraft.length === 0) {
    alert("No aircraft data to export.");
    return;
  }

  const columns = [
    "icao24", "callsign", "origin_country", "baro_altitude", "geo_altitude",
    "velocity", "true_track", "vertical_rate", "on_ground", "distance_km",
    "bearing_deg", "squawk", "position_source", "inferred_category", "last_contact", "alert", "polled_at",
  ];

  const headerRow = columns.join(",");
  const dataRows = currentAircraft.map(ac =>
    columns.map(col => {
      const val = ac[col];
      if (val == null) return "";
      const str = String(val);
      // Quote values that contain commas or quotes
      return str.includes(",") || str.includes('"') ? `"${str.replace(/"/g, '""')}"` : str;
    }).join(",")
  );

  const csv = [headerRow, ...dataRows].join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);

  const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const a = document.createElement("a");
  a.href = url;
  a.download = `skyradar_export_${ts}.csv`;
  a.click();
  URL.revokeObjectURL(url);
});

// ---------------------------------------------------------------------------
// Pause / Resume polling
// ---------------------------------------------------------------------------

function applyPausedState(paused) {
  if (paused) {
    btnPausePolling.textContent = "▶ Resume";
    btnPausePolling.classList.replace("btn-amber", "btn-primary");
    modeBadge.className = "badge badge-amber";
    modeBadge.textContent = "PAUSED";
    statMode.className = "badge badge-amber";
    statMode.textContent = "PAUSED";
  } else {
    btnPausePolling.textContent = "⏸ Pause";
    btnPausePolling.classList.replace("btn-primary", "btn-amber");
    if (!isReplayMode) {
      modeBadge.className = "badge badge-green";
      modeBadge.textContent = "LIVE";
      statMode.className = "badge badge-green";
      statMode.textContent = "LIVE";
    }
  }
}

btnPausePolling.addEventListener("click", async () => {
  const paused = btnPausePolling.textContent.includes("Pause");
  const endpoint = paused ? "/api/pause" : "/api/resume";
  try {
    const res = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const data = await res.json();
    applyPausedState(data.paused);
  } catch (e) {
    console.error("Pause/resume failed", e);
  }
});

// ---------------------------------------------------------------------------
// Stop server
// ---------------------------------------------------------------------------

btnStopServer.addEventListener("click", async () => {
  if (!confirm("Stop the Sky Radar server?\n\nThe page will become unreachable until you restart it.")) return;
  btnStopServer.disabled = true;
  btnStopServer.textContent = "Stopping…";
  try {
    await fetch("/api/shutdown", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
  } catch (_) {
    // Expected — server closes the connection before responding fully
  }
});

// ---------------------------------------------------------------------------
// Initialise on page load
// ---------------------------------------------------------------------------

(async () => {
  await loadConfig();
  await loadWatchlist();
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    applyPausedState(data.paused);
  } catch (e) {
    console.error("Failed to load status", e);
  }
})();
