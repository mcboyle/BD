// Phase 77: popup script.

const DEFAULT_URL = "http://127.0.0.1:5555";

async function getConfig() {
  const cfg = await chrome.storage.sync.get(["bd_url", "bd_token"]);
  return {
    url: (cfg.bd_url || DEFAULT_URL).replace(/\/$/, ""),
    token: cfg.bd_token || "",
  };
}

function setStatus(text, ok) {
  const el = document.getElementById("status");
  el.style.display = "";
  el.textContent = text;
  el.style.color = ok ? "#86efac" : "#fca5a5";
}

async function getCurrentTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

document.getElementById("send-page").addEventListener("click", async () => {
  const tab = await getCurrentTab();
  if (!tab?.url) return setStatus("No active tab.", false);
  const { url, token } = await getConfig();
  const headers = { "Content-Type": "application/json" };
  if (token) headers["X-BD-Token"] = token;
  try {
    const r = await fetch(`${url}/api/quick_add`, {
      method: "POST",
      headers,
      body: JSON.stringify({ url: tab.url }),
    });
    setStatus(r.ok ? `Sent: ${tab.url.slice(0, 80)}` : `Error ${r.status}`, r.ok);
  } catch (e) {
    setStatus(`Network error: ${e.message}`, false);
  }
});

document.getElementById("scrape-page").addEventListener("click", async () => {
  const tab = await getCurrentTab();
  if (!tab?.id) return setStatus("No active tab.", false);
  setStatus("Scraping page...", true);
  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: () => {
      const VIDEO_EXT = /\.(mp4|mkv|webm|avi|mov|m3u8|mpd|ts|flv)(\?|#|$)/i;
      const VIDEO_PATTERNS = /\/(video|watch|v|play|movie|episode|stream)\//i;
      const out = new Set();
      for (const a of document.querySelectorAll("a[href]")) {
        const href = a.href;
        if (!href || !href.startsWith("http")) continue;
        if (VIDEO_EXT.test(href) || VIDEO_PATTERNS.test(href)) out.add(href);
      }
      return Array.from(out);
    },
  });
  const urls = (results && results[0]?.result) || [];
  if (!urls.length) return setStatus("No video links found.", false);
  const { url, token } = await getConfig();
  const headers = { "Content-Type": "application/json" };
  if (token) headers["X-BD-Token"] = token;
  try {
    const r = await fetch(`${url}/api/route_urls`, {
      method: "POST",
      headers,
      body: JSON.stringify({ text: urls.join("\n"), ai_filter: true }),
    });
    const d = await r.json().catch(() => ({}));
    if (r.ok) {
      const total = Object.values(d.summary || {}).reduce(
        (s, x) => s + (x.added || 0), 0);
      const skip = d.skipped_listings_count || 0;
      setStatus(`Sent ${total}` + (skip ? ` (skipped ${skip})` : "") + ".", true);
    } else {
      setStatus(`Error ${r.status}`, false);
    }
  } catch (e) {
    setStatus(`Network error: ${e.message}`, false);
  }
});

document.getElementById("open-options").addEventListener("click", (e) => {
  e.preventDefault();
  chrome.runtime.openOptionsPage();
});


// ── v1.2.0: Routing preview ─────────────────────────────────────────
// On popup open, fetch the routing decision for the current tab so
// the user can see where it would go before clicking Send. Hidden if
// the server is unreachable or no decision returned (don't show
// confusing empty state).

async function bgCallV12(action, payload) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(
      { action, payload: payload || {} },
      (resp) => resolve(resp || { ok: false }));
  });
}

async function refreshRoutePreview() {
  const tab = await getCurrentTab();
  const preview = document.getElementById("route-preview");
  const text = document.getElementById("route-preview-text");
  const detail = document.getElementById("route-preview-detail");
  if (!tab?.url || !tab.url.startsWith("http")) {
    preview.style.display = "none";
    return;
  }
  preview.style.display = "block";
  text.textContent = "Checking routing...";
  detail.style.display = "none";
  const r = await bgCallV12("route_preview", { url: tab.url });
  if (!r || !r.ok) {
    text.textContent = "Could not reach server.";
    text.style.color = "#fca5a5";
    return;
  }
  const decisions = r.decisions || [];
  const d = decisions[0];
  if (!d) {
    text.textContent = "No routing decision available.";
    return;
  }
  if (d.already_in_queue) {
    text.innerHTML = `Already in queue at <b>${esc(d.site_name || d.site_id || "?")}</b>`;
    text.style.color = "#fbbf24";
  } else if (d.site_id) {
    text.innerHTML = `Would route to <b>${esc(d.site_name)}</b>`;
    text.style.color = "#86efac";
    if (d.reason) {
      detail.textContent = d.reason;
      detail.style.display = "block";
    }
  } else {
    text.textContent = "No matching site — would be unrouted.";
    text.style.color = "#fbbf24";
    if (d.reason) {
      detail.textContent = d.reason;
      detail.style.display = "block";
    }
  }
}

function esc(s) {
  return String(s || "").replace(/[&<>"]/g,
    c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c]));
}

// ── v1.2.0: Send all tabs in current window ─────────────────────────

document.getElementById("send-all-tabs").addEventListener("click", async () => {
  setStatus("Sending all tabs...", true);
  const r = await bgCallV12("send_all_tabs", {});
  if (r.ok && r.data) {
    const summary = r.data.summary || {};
    const total = Object.values(summary).reduce(
      (s, x) => s + (x.added || 0), 0);
    const skip = r.data.skipped_listings_count || 0;
    const siteCount = Object.keys(summary).length;
    setStatus(
      `Sent ${total} URL${total === 1 ? "" : "s"} across ${siteCount} site${siteCount === 1 ? "" : "s"}` +
      (skip ? ` (skipped ${skip} listings)` : ""),
      true);
  } else {
    setStatus(`Error: ${r.error || r.status}`, false);
  }
});


// ── v1.2.0: Per-site override picker ────────────────────────────────

async function loadOverrideSites() {
  const sel = document.getElementById("override-site");
  const r = await bgCallV12("sites_list", { force: false });
  if (!r.ok || !r.sites || !r.sites.length) {
    sel.innerHTML = '<option value="">— no sites available —</option>';
    return;
  }
  sel.innerHTML = '<option value="">— select a site —</option>';
  for (const site of r.sites) {
    const opt = document.createElement("option");
    opt.value = site.site_id;
    opt.textContent = site.name || site.site_id;
    if (site.hostname) opt.textContent += ` (${site.hostname})`;
    sel.appendChild(opt);
  }
}

document.getElementById("override-send").addEventListener("click", async () => {
  const sel = document.getElementById("override-site");
  const siteId = sel.value;
  if (!siteId) return setStatus("Pick a site first.", false);
  const tab = await getCurrentTab();
  if (!tab?.url) return setStatus("No active tab.", false);
  const r = await bgCallV12("send_to_site",
    { url: tab.url, site_id: siteId });
  if (r.ok && r.data) {
    const siteName = r.data.site_name || siteId;
    if (r.data.added) {
      setStatus(`Sent to ${siteName}: ${tab.url.slice(0, 60)}`, true);
    } else {
      setStatus(`Already queued in ${siteName}.`, true);
    }
  } else {
    setStatus(`Send-to-site failed: ${r.error || r.status}`, false);
  }
});

// Lazy-load sites list when the user opens the override section
document.getElementById("override-details").addEventListener("toggle", (e) => {
  if (e.target.open) loadOverrideSites();
});


// ── v1.1.0: Vault status pill ──────────────────────────────────────
// Shows pair state + idle-lock countdown + per-site silent toggle.

function bgCall(action, payload) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ action, payload: payload || {} }, (resp) => {
      resolve(resp || { ok: false });
    });
  });
}

function registrableDomainFromUrl(url) {
  try {
    const u = new URL(url);
    const parts = u.hostname.split(".").filter(Boolean);
    if (parts.length <= 2) return parts.join(".");
    return parts.slice(-2).join(".");
  } catch { return ""; }
}

async function refreshVaultPill() {
  const pill = document.getElementById("vault-pill");
  const dot = document.getElementById("vault-dot");
  const label = document.getElementById("vault-label");
  const lockBtn = document.getElementById("vault-lock");
  const silentRow = document.getElementById("vault-silent-row");
  const silentBtn = document.getElementById("vault-silent-toggle");

  const s = await bgCall("vault_status");
  if (!s || !s.ok) return;
  // Always show the pill once we have status (so the user sees "not paired"
  // and knows the feature exists)
  pill.style.display = "block";

  if (!s.paired) {
    dot.style.background = "#64748b";
    label.textContent = "Vault: not paired (open Options)";
    lockBtn.style.display = "none";
    silentRow.style.display = "none";
    return;
  }

  if (s.locked) {
    dot.style.background = "#fbbf24";
    label.textContent = "Vault: locked (idle timeout) — re-pair";
    lockBtn.style.display = "none";
    silentRow.style.display = "none";
    return;
  }

  // Paired and unlocked
  dot.style.background = "#22c55e";
  label.textContent = "Vault: active";
  lockBtn.style.display = "inline-block";

  // Per-site silent toggle: derive current page's eTLD+1
  const tab = await getCurrentTab();
  if (tab && tab.url && tab.url.startsWith("http")) {
    const etld1 = registrableDomainFromUrl(tab.url);
    if (etld1) {
      silentRow.style.display = "flex";
      const isAllowed = (s.silent_origins || []).includes(etld1);
      silentBtn.textContent = isAllowed ? `on for ${etld1}` : `off for ${etld1}`;
      silentBtn.style.background = isAllowed ? "#0e3a23" : "#2a2e34";
      silentBtn.style.color = isAllowed ? "#86efac" : "#e8eaed";
      silentBtn.onclick = async () => {
        await bgCall("vault_set_silent_for_origin",
          { origin: tab.url, allow: !isAllowed });
        await refreshVaultPill();
      };
    } else {
      silentRow.style.display = "none";
    }
  } else {
    silentRow.style.display = "none";
  }
}

document.getElementById("vault-lock").addEventListener("click", async () => {
  // "Lock now" by removing the cached vault token. User must re-pair to use again.
  if (!confirm("Lock the vault? You'll need to re-pair from the app to autofill again.")) return;
  await bgCall("vault_unpair");
  await refreshVaultPill();
});

// Refresh on popup open
refreshVaultPill();
// v1.2.0: also show routing preview for the current tab
refreshRoutePreview();
