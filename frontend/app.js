/* ===== Config ===== */
const API = "";  // same origin; change to "http://localhost:8000" if serving separately

/* ===== State ===== */
let calendar  = null;
let siteMeta  = [];   // [{ name, tier }]
let filters   = { site: "", category: "", tier: "", search: "" };
let searchTimer = null;

const SITE_COLORS = {
  "HOMEDEC":           "#4CAF50",
  "HomeLove MY":       "#8BC34A",
  "PIKOM":             "#CDDC39",
  "Senheng":           "#e53935",
  "Harvey Norman MY":  "#ff6f00",
  "Courts MY":         "#1565c0",
  "Best Denki MY":     "#2e7d32",
  "MITEC":             "#6a1b9a",
  "KLCC Convention":   "#ad1457",
  "PWTC":              "#00695c",
  "Mid Valley":        "#4527a0",
  "Sunway Pyramid":    "#00838f",
  "Suria KLCC":        "#d84315",
  "Starling Mall":     "#0277bd",
  "Lowyat.net":        "#546e7a",
  "SoyaCincau":        "#5d4037",
  "Samsung MY":        "#1976d2",
  "LG MY":             "#b71c1c",
  "Panasonic MY":      "#0288d1",
  "Lazada MY":         "#ff6900",
  "Shopee MY":         "#ee4d2d",
};

const TIER_LABELS = {
  "Tier1-Exhibition": "Exhibition / Event Organiser",
  "Tier2-Retail":     "Retailer",
  "Tier3-Venue":      "Venue Calendar",
  "Tier4-Media":      "Tech Media",
  "Tier5-Brand":      "Brand",
  "Tier6-Online":     "Online Campaign",
};

/* ===== Init ===== */
document.addEventListener("DOMContentLoaded", async () => {
  initCalendar();
  await Promise.all([loadSites(), refreshStatus()]);
  await loadEvents();
  setInterval(refreshStatus, 60_000);
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
        ep.organizer ? `Organiser: ${ep.organizer}` : "",
        ep.venue     ? `Venue: ${ep.venue}`         : ep.location ? `Location: ${ep.location}` : "",
        ep.source_site ? `Source: ${ep.source_site}` : "",
      ].filter(Boolean).join("\n");
    },
    datesSet(info) {
      loadEvents(info.startStr.slice(0, 10), info.endStr.slice(0, 10));
    },
  });
  calendar.render();
}

/* ===== Data Loading ===== */
async function loadEvents(start, end) {
  const params = new URLSearchParams();
  if (start)           params.set("start",    start);
  if (end)             params.set("end",      end);
  if (filters.site)    params.set("site",     filters.site);
  if (filters.category) params.set("category", filters.category);
  if (filters.search)  params.set("search",   filters.search);

  try {
    const res   = await fetch(`${API}/api/events?${params}`);
    let   data  = await res.json();

    // Client-side tier filter (not passed to API)
    if (filters.tier) {
      const sitesInTier = siteMeta
        .filter(s => s.tier === filters.tier)
        .map(s => s.name);
      data = data.filter(e => sitesInTier.includes(e.extendedProps?.source_site));
    }

    calendar.removeAllEvents();
    calendar.addEventSource(data);
    document.getElementById("stats-label").textContent = `Events: ${data.length}`;
  } catch (e) {
    console.error("loadEvents:", e);
  }
}

async function loadSites() {
  try {
    const res  = await fetch(`${API}/api/scrape/sites`);
    const data = await res.json();
    siteMeta   = data.sites || [];

    const sel = document.getElementById("filter-site");
    siteMeta.forEach(s => {
      const opt = document.createElement("option");
      opt.value = s.name;
      opt.textContent = s.name;
      sel.appendChild(opt);
    });
  } catch (e) { /* ignore */ }
}

async function refreshStatus() {
  try {
    const res  = await fetch(`${API}/api/scrape/status`);
    const data = await res.json();
    document.getElementById("status-text").textContent =
      `Auto-scrape: every ${data.interval_hours}h`;
    document.getElementById("next-run-text").textContent = data.next_run
      ? `Next run: ${new Date(data.next_run).toLocaleString("en-MY")}`
      : "";
  } catch (e) { /* ignore */ }
}

/* ===== Filters ===== */
function applyFilters() {
  filters.site     = document.getElementById("filter-site").value;
  filters.category = document.getElementById("filter-category").value;
  filters.tier     = document.getElementById("filter-tier").value;
  loadEvents();
}

function debounceSearch() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    filters.search = document.getElementById("filter-search").value;
    loadEvents();
  }, 350);
}

/* ===== Event Detail ===== */
function showDetail(event) {
  const ep      = event.extendedProps;
  const panel   = document.getElementById("side-panel");
  const color   = SITE_COLORS[ep.source_site] || "#546e7a";
  const tierKey = siteMeta.find(s => s.name === ep.source_site)?.tier || "";
  const tierLabel = TIER_LABELS[tierKey] || tierKey;

  const start   = formatDate(event.startStr);
  const endRaw  = event.endStr ? addDays(event.endStr, -1) : event.startStr;
  const end     = formatDate(endRaw);
  const dateStr = start === end ? start : `${start} – ${end}`;

  const tagsHtml = (ep.tags || [])
    .map(t => `<span class="tag">${esc(t)}</span>`)
    .join("");

  panel.innerHTML = `
    <div class="event-detail">
      <div class="event-detail-header">
        <span class="event-site-badge" style="background:${color}">
          ${esc(ep.source_site)}
        </span>
        ${tierLabel ? `<span class="event-tier-badge">${esc(tierLabel)}</span>` : ""}
        <div class="event-detail-title">${esc(event.title)}</div>
        <div class="event-detail-category">${esc(ep.category)}</div>
      </div>

      <div class="event-meta">
        <div class="event-meta-row">
          <span class="label">📅 Period</span>
          <span class="value">${esc(dateStr)}</span>
        </div>
        <div class="event-meta-row">
          <span class="label">🏢 Organiser</span>
          <span class="value">${esc(ep.organizer || "—")}</span>
        </div>
        <div class="event-meta-row">
          <span class="label">📍 Location</span>
          <span class="value">${esc(ep.location || "—")}${ep.venue ? `<br><small style="color:var(--muted)">${esc(ep.venue)}</small>` : ""}</span>
        </div>
      </div>

      ${ep.summary ? `
      <div class="event-summary">
        <strong>Summary</strong>${esc(ep.summary)}
      </div>` : ""}

      ${ep.description ? `
      <div class="event-description">${esc(ep.description)}</div>` : ""}

      ${tagsHtml ? `<div class="event-tags">${tagsHtml}</div>` : ""}

      ${ep.source_url ? `
      <div class="event-source-link">
        🔗 <a href="${esc(ep.source_url)}" target="_blank" rel="noopener">View source page</a>
      </div>` : ""}

      <div class="event-scraped-at">
        Updated: ${formatDateTime(ep.updated_at)}
      </div>
    </div>
  `;
}

/* ===== Scrape ===== */
async function triggerScrape() {
  const btn = document.getElementById("btn-scrape");
  btn.disabled = true;
  btn.classList.add("scraping");
  btn.textContent = "⟳ Scraping…";

  try {
    await fetch(`${API}/api/scrape`, { method: "POST" });
    showToast("Scrape job started. Calendar will refresh automatically.");
    setTimeout(() => loadEvents(), 20_000);
    setTimeout(() => loadEvents(), 60_000);
  } catch {
    showToast("Failed to start scrape job.");
  } finally {
    setTimeout(() => {
      btn.disabled = false;
      btn.classList.remove("scraping");
      btn.textContent = "⟳ Scrape Now";
    }, 5_000);
  }
}

/* ===== CSV Export ===== */
function exportCSV() {
  const params = new URLSearchParams();
  if (filters.site) params.set("site", filters.site);
  window.open(`${API}/api/events/export/csv?${params}`, "_blank");
}

/* ===== Report Modal ===== */
async function openDigest() {
  const modal = document.getElementById("digest-modal");
  const body  = document.getElementById("digest-content");
  modal.classList.add("active");
  body.innerHTML = '<div class="loading">Generating report with Claude AI…</div>';

  try {
    const res  = await fetch(`${API}/api/digest`);
    const data = await res.json();
    body.innerHTML = renderMarkdown(data.report || "No data available.");
  } catch {
    body.textContent = "Failed to generate report.";
  }
}

function closeDigest(e) {
  if (e && e.target !== document.getElementById("digest-modal")) return;
  document.getElementById("digest-modal").classList.remove("active");
}

async function copyDigest() {
  const text = document.getElementById("digest-content").innerText;
  try {
    await navigator.clipboard.writeText(text);
    showToast("Copied to clipboard.");
  } catch {
    showToast("Copy failed.");
  }
}

/* ===== Tier Legend ===== */
function openLegend() {
  const modal = document.getElementById("legend-modal");
  const body  = document.getElementById("legend-content");
  modal.classList.add("active");

  const rows = Object.entries(TIER_LABELS).map(([key, label]) => {
    const sites = siteMeta.filter(s => s.tier === key);
    const siteList = sites
      .map(s => `<span class="tier-dot" style="background:${SITE_COLORS[s.name]||'#546e7a'}"></span>${esc(s.name)}`)
      .join("&nbsp; ");
    return `<tr>
      <td><strong>${esc(label)}</strong><br><small style="color:var(--muted)">${esc(key)}</small></td>
      <td style="font-size:.75rem;line-height:1.9">${siteList || "—"}</td>
    </tr>`;
  }).join("");

  body.innerHTML = `
    <table class="tier-table">
      <thead><tr><th>Tier</th><th>Sources</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function closeLegend(e) {
  if (e && e.target !== document.getElementById("legend-modal")) return;
  document.getElementById("legend-modal").classList.remove("active");
}

/* ===== Utilities ===== */
function esc(str) {
  return String(str || "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function formatDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso + "T00:00:00").toLocaleDateString("en-MY", {
      year: "numeric", month: "short", day: "numeric",
    });
  } catch { return iso; }
}

function formatDateTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("en-MY", {
      year: "numeric", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return iso; }
}

function addDays(iso, days) {
  if (!iso) return iso;
  try {
    const d = new Date(iso);
    d.setDate(d.getDate() + days);
    return d.toISOString().slice(0, 10);
  } catch { return iso; }
}

function showToast(msg, duration = 3500) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), duration);
}

function renderMarkdown(text) {
  return text
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/^#{3}\s+(.+)$/gm, "<h3>$1</h3>")
    .replace(/^#{2}\s+(.+)$/gm, "<h2>$1</h2>")
    .replace(/^#{1}\s+(.+)$/gm, "<h1>$1</h1>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g,     "<em>$1</em>")
    .replace(/^---$/gm,        "<hr>")
    .replace(/^[-•]\s+(.+)$/gm, "<li>$1</li>")
    .replace(/(<li>[\s\S]*?<\/li>)/g, "<ul>$1</ul>")
    .replace(/\n{2,}/g, "<br><br>")
    .replace(/\n/g,     "<br>");
}
