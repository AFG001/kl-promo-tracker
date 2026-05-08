/* ===== State ===== */
let calendar    = null;
let allEvents   = [];   // raw FullCalendar event objects from events.json
let filters     = { site: "", category: "", tier: "", search: "" };
let searchTimer = null;
let lastUpdated = "";
let hiddenMeta  = {};   // id → { title, site } for hidden events modal

const SITE_TIER = {
  "HOMEDEC":           "Tier1-Exhibition",
  "HomeLove MY":       "Tier1-Exhibition",
  "PIKOM":             "Tier1-Exhibition",
  "Expolah":           "Tier1-Exhibition",
  "MTE":               "Tier1-Exhibition",
  "10times":           "Tier1-Exhibition",
  "MyCEB":             "Tier1-Exhibition",
  "Senheng":           "Tier2-Retail",
  "Harvey Norman MY":  "Tier2-Retail",
  "Courts MY":         "Tier2-Retail",
  "TMT":               "Tier2-Retail",
  "MITEC":             "Tier3-Venue",
  "KLCC Convention":   "Tier3-Venue",
  "MVEC":              "Tier3-Venue",
  "ExhibitionsForYou": "Tier3-Venue",
  "PWTC":              "Tier3-Venue",
  "Mid Valley":        "Tier3-Venue",
  "Sunway Pyramid":    "Tier3-Venue",
  "Suria KLCC":        "Tier3-Venue",
  "Starling Mall":     "Tier3-Venue",
  "Lowyat.net":        "Tier4-Media",
  "SoyaCincau":        "Tier4-Media",
  "Samsung MY":        "Tier5-Brand",
  "LG MY":             "Tier5-Brand",
  "Panasonic MY":      "Tier5-Brand",
  "Lazada MY":         "Tier6-Online",
  "Shopee MY":         "Tier6-Online",
};

/* ===== Hidden events (localStorage) ===== */
const HIDDEN_KEY = "kl_hidden";
let hiddenIds = new Set(JSON.parse(localStorage.getItem(HIDDEN_KEY) || "[]"));

function _saveHidden() {
  localStorage.setItem(HIDDEN_KEY, JSON.stringify([...hiddenIds]));
  _updateHiddenBadge();
}

function _updateHiddenBadge() {
  const badge = document.getElementById("hidden-badge");
  if (!badge) return;
  if (hiddenIds.size > 0) {
    badge.textContent = `${hiddenIds.size} hidden  👁 Manage`;
    badge.style.display = "inline-flex";
  } else {
    badge.style.display = "none";
  }
}

function hideEvent(id, title, site) {
  hiddenIds.add(id);
  hiddenMeta[id] = { title, site: site || "" };
  _saveHidden();
  applyFilters();
  document.getElementById("side-panel").innerHTML = `
    <div class="side-placeholder"><span>🗑</span><p>Event hidden.<br>Click <strong>${hiddenIds.size} hidden 👁 Manage</strong> to restore.</p></div>`;
  showToast(`Hidden: "${title}"`);
}

function restoreEvent(id) {
  hiddenIds.delete(id);
  delete hiddenMeta[id];
  _saveHidden();
  applyFilters();
  _renderHiddenModal();
}

function restoreAllHidden() {
  const count = hiddenIds.size;
  hiddenIds.clear();
  hiddenMeta = {};
  _saveHidden();
  applyFilters();
  _closeHiddenModal();
  showToast(`Restored ${count} hidden event${count !== 1 ? "s" : ""}`);
}

function openHiddenModal() {
  const overlay = document.getElementById("hidden-modal-overlay");
  overlay.classList.add("active");
  _renderHiddenModal();
}

function _closeHiddenModal() {
  document.getElementById("hidden-modal-overlay").classList.remove("active");
}

function _renderHiddenModal() {
  const list = document.getElementById("hidden-events-list");
  if (!list) return;
  if (hiddenIds.size === 0) {
    list.innerHTML = `<p style="color:var(--muted);font-size:.83rem;text-align:center;padding:20px 0">No hidden events.</p>`;
    document.getElementById("hidden-restore-all-btn").style.display = "none";
    return;
  }
  document.getElementById("hidden-restore-all-btn").style.display = "";

  // Merge meta from hiddenMeta + allEvents (in case page was refreshed)
  const rows = [...hiddenIds].map(id => {
    const meta = hiddenMeta[id];
    if (meta) return { id, title: meta.title, site: meta.site };
    // Try to find in allEvents
    const ev = allEvents.find(e => e.id === id);
    if (ev) return { id, title: ev.title, site: ev.extendedProps?.source_site || "" };
    return { id, title: `(Unknown event — ${id.slice(0,8)}…)`, site: "" };
  });

  list.innerHTML = rows.map(r => `
    <div class="hidden-event-row">
      <div class="hidden-event-info">
        <span class="hidden-event-title">${esc(r.title)}</span>
        ${r.site ? `<span class="hidden-event-site">${esc(r.site)}</span>` : ""}
      </div>
      <button class="btn-restore" onclick="restoreEvent('${esc(r.id)}')">↩ Restore</button>
    </div>`).join("");
}

/* ===== Init ===== */
document.addEventListener("DOMContentLoaded", async () => {
  initCalendar();
  await loadEvents();
});

/* ===== Calendar ===== */
function initCalendar() {
  calendar = new FullCalendar.Calendar(document.getElementById("calendar"), {
    initialView: "dayGridMonth",
    locale: "en",
    headerToolbar: {
      left:   "prev,next today",
      center: "title",
      right:  "dayGridMonth,timeGridWeek,listMonth",
    },
    buttonText: { today: "Today", month: "Month", week: "Week", list: "List" },
    height: "100%",
    eventClick(info) { showDetail(info.event); },
    eventDidMount(info) {
      const ep = info.event.extendedProps;
      info.el.title = [
        info.event.title,
        ep.organizer  ? `Organiser: ${ep.organizer}`   : "",
        ep.venue      ? `Venue: ${ep.venue}`            : ep.location ? `Location: ${ep.location}` : "",
        ep.source_site ? `Source: ${ep.source_site}`   : "",
      ].filter(Boolean).join("\n");
    },
  });
  calendar.render();
}

/* ===== Load events.json ===== */
async function loadEvents() {
  try {
    const res  = await fetch("events.json?" + Date.now());
    const data = await res.json();

    allEvents   = data.events || [];
    lastUpdated = data.meta?.generated_at || "";

    // Populate source filter
    const sites = [...new Set(allEvents.map(e => e.extendedProps?.source_site).filter(Boolean))].sort();
    const sel   = document.getElementById("filter-site");
    sites.forEach(s => {
      const opt = document.createElement("option");
      opt.value = s;
      opt.textContent = s;
      sel.appendChild(opt);
    });

    applyFilters();
    _updateHiddenBadge();

    if (lastUpdated) {
      const dt = new Date(lastUpdated).toLocaleString("en-MY", {
        year: "numeric", month: "short", day: "numeric",
        hour: "2-digit", minute: "2-digit",
      });
      document.getElementById("updated-badge").textContent = `Updated: ${dt}`;
      document.getElementById("status-text").textContent   = `Data updated: ${dt} UTC`;
    }
  } catch (e) {
    document.getElementById("status-text").textContent = "Failed to load events.json";
    console.error(e);
  }
}

/* ===== Filters (all client-side) ===== */
function applyFilters() {
  filters.site     = document.getElementById("filter-site").value;
  filters.category = document.getElementById("filter-category").value;
  filters.tier     = document.getElementById("filter-tier").value;
  filters.search   = document.getElementById("filter-search").value.toLowerCase();

  const filtered = allEvents.filter(e => {
    if (hiddenIds.has(e.id)) return false;   // hidden by user
    const ep   = e.extendedProps || {};
    const site = ep.source_site || "";
    const tier = SITE_TIER[site] || "";

    if (filters.site     && site              !== filters.site)     return false;
    if (filters.category && ep.category       !== filters.category) return false;
    if (filters.tier     && tier              !== filters.tier)     return false;
    if (filters.search) {
      const hay = [e.title, ep.organizer, ep.description].join(" ").toLowerCase();
      if (!hay.includes(filters.search)) return false;
    }
    return true;
  });

  calendar.removeAllEvents();
  calendar.addEventSource(filtered);
  document.getElementById("stats-label").textContent = `Events: ${filtered.length}`;
  _updateHiddenBadge();
}

function debounceSearch() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(applyFilters, 300);
}

/* ===== Event Detail ===== */
function showDetail(event) {
  const ep    = event.extendedProps;
  const panel = document.getElementById("side-panel");
  const color = event.backgroundColor || "#546e7a";
  const tier  = SITE_TIER[ep.source_site] || "";

  const start   = formatDate(event.startStr);
  const endRaw  = event.endStr ? addDays(event.endStr, -1) : event.startStr;
  const end     = formatDate(endRaw);
  const dateStr = start === end ? start : `${start} – ${end}`;
  const tagsHtml = (ep.tags || []).map(t => `<span class="tag">${esc(t)}</span>`).join("");

  panel.innerHTML = `
    <div class="event-detail">
      <div class="event-detail-header">
        <span class="event-site-badge" style="background:${color}">${esc(ep.source_site)}</span>
        ${tier ? `<span class="event-tier-badge">${esc(tier)}</span>` : ""}
        <div class="event-detail-title">${esc(event.title)}</div>
        <div class="event-detail-category">${esc(ep.category)}</div>
      </div>
      <div class="event-meta">
        <div class="event-meta-row"><span class="label">📅 Period</span><span class="value">${esc(dateStr)}</span></div>
        <div class="event-meta-row"><span class="label">🏢 Organiser</span><span class="value">${esc(ep.organizer || "—")}</span></div>
        <div class="event-meta-row"><span class="label">📍 Location</span><span class="value">${esc(ep.location || "—")}${ep.venue ? `<br><small style="color:var(--muted)">${esc(ep.venue)}</small>` : ""}</span></div>
      </div>
      ${ep.summary ? `<div class="event-summary"><strong>Summary</strong>${esc(ep.summary)}</div>` : ""}
      ${ep.description ? `<div class="event-description">${esc(ep.description)}</div>` : ""}
      ${tagsHtml ? `<div class="event-tags">${tagsHtml}</div>` : ""}
      ${ep.source_url ? `<div class="event-source-link">🔗 <a href="${esc(ep.source_url)}" target="_blank" rel="noopener">View source page</a></div>` : ""}
      <div class="event-scraped-at">Updated: ${formatDateTime(ep.updated_at)}</div>
      <button class="btn-hide" onclick="hideEvent('${esc(event.id)}', '${esc(event.title).replace(/'/g,"&#39;")}', '${esc(ep.source_site || "").replace(/'/g,"&#39;")}')">🗑 Hide this event</button>
    </div>`;
}

/* ===== CSV Export (client-side) ===== */
function exportCSV() {
  const filtered = allEvents.filter(e => {
    const ep   = e.extendedProps || {};
    const site = ep.source_site || "";
    if (filters.site     && site        !== filters.site)     return false;
    if (filters.category && ep.category !== filters.category) return false;
    return true;
  });

  const header = ["title","organiser","location","venue","start","end","category","summary","source_site","source_url","tags"];
  const rows   = filtered.map(e => {
    const ep = e.extendedProps || {};
    return [
      e.title, ep.organizer, ep.location, ep.venue,
      e.start, addDays(e.end, -1),
      ep.category, ep.summary, ep.source_site, ep.source_url,
      (ep.tags || []).join("; "),
    ].map(v => `"${String(v || "").replace(/"/g, '""')}"`).join(",");
  });

  const csv  = [header.join(","), ...rows].join("\r\n");
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = `kl_promo_${new Date().toISOString().slice(0,10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

/* ===== Utilities ===== */
function esc(str) {
  return String(str || "")
    .replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}
function formatDate(iso) {
  if (!iso) return "—";
  try { return new Date(iso + "T00:00:00").toLocaleDateString("en-MY", { year:"numeric", month:"short", day:"numeric" }); }
  catch { return iso; }
}
function formatDateTime(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString("en-MY", { year:"numeric", month:"short", day:"numeric", hour:"2-digit", minute:"2-digit" }); }
  catch { return iso; }
}
function addDays(iso, days) {
  if (!iso) return iso;
  try { const d = new Date(iso); d.setDate(d.getDate() + days); return d.toISOString().slice(0,10); }
  catch { return iso; }
}
function showToast(msg, ms = 3000) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), ms);
}
