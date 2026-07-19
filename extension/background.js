// Phase 77 (v3.40.0): Bulk Downloader browser extension — background.
// v1.2.0 (v3.43.40): preview routing, per-site send, send-all-tabs,
//   keyboard commands, dynamic site submenu.
//
// Right-click menus:
//   - "Send link to Bulk Downloader"  (on links)
//   - "Send page URL to Bulk Downloader"
//   - "Send all video links on page"  (scrapes anchors with video extensions)
//   - "Send all open tabs"            (v1.2.0)
//   - "Send to specific site..."      (v1.2.0, dynamic submenu)
//
// Keyboard:
//   - Alt+Shift+B → send current page    (v1.2.0)
//   - Alt+Shift+T → send all tabs        (v1.2.0)
//
// Auth/config in options.html (sync-storage: bd_url, bd_token).

const DEFAULT_URL = "http://127.0.0.1:5555";

// v1.2.0: site index cache. Fetched on extension install / startup
// and refreshed every 5 min. Used to populate the per-site submenu
// without round-tripping to the server on every right-click.
let _sitesCache = { sites: [], lastFetched: 0 };
const SITES_CACHE_TTL_MS = 5 * 60 * 1000;

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "bd-send-link",
    title: "Send link to Bulk Downloader",
    contexts: ["link"],
  });
  chrome.contextMenus.create({
    id: "bd-send-page",
    title: "Send page URL to Bulk Downloader",
    contexts: ["page"],
  });
  chrome.contextMenus.create({
    id: "bd-scrape-page",
    title: "Send all video links on page to Bulk Downloader",
    contexts: ["page"],
  });
  // v1.2.0: new context menu entries
  chrome.contextMenus.create({
    id: "bd-send-all-tabs",
    title: "Send all open tabs to Bulk Downloader",
    contexts: ["page"],
  });
  chrome.contextMenus.create({
    id: "bd-send-to-specific",
    title: "Send to specific site...",
    contexts: ["link", "page"],
  });
  // Try to populate the per-site submenu right away
  refreshSitesCache().then(() => rebuildSiteSubmenu()).catch(() => {});
});

async function getConfig() {
  const cfg = await chrome.storage.sync.get(["bd_url", "bd_token"]);
  return {
    url: (cfg.bd_url || DEFAULT_URL).replace(/\/$/, ""),
    token: cfg.bd_token || "",
  };
}

async function sendUrl(targetUrl) {
  const { url, token } = await getConfig();
  const headers = { "Content-Type": "application/json" };
  if (token) headers["X-BD-Token"] = token;
  try {
    const r = await fetch(`${url}/api/quick_add`, {
      method: "POST",
      headers,
      body: JSON.stringify({ url: targetUrl }),
    });
    const data = await r.json().catch(() => ({}));
    return { ok: r.ok, status: r.status, data };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

// v1.3.0 (Phase 178): live status badge. When the user navigates to
// a page, query BD for what it knows about that URL and surface it
// via the toolbar action's badge text + tooltip.
//   '✓' badge: already downloaded
//   '⏳' badge: in queue
//   '⚠' badge: blocked by content_rights
//   '→' badge: would route to a configured site (not yet downloaded)
//   ''  : unknown / not handled by any configured site
async function lookupUrlStatus(targetUrl) {
  if (!targetUrl || !/^https?:\/\//.test(targetUrl)) return null;
  const { url, token } = await getConfig();
  const headers = {};
  if (token) headers["X-BD-Token"] = token;
  try {
    const r = await fetch(
      `${url}/api/extension/lookup_url?url=${encodeURIComponent(targetUrl)}`,
      { headers, signal: AbortSignal.timeout(5000) });
    if (!r.ok) return null;
    return await r.json();
  } catch (e) {
    return null;  // server unreachable; silent
  }
}

function updateBadge(tabId, info) {
  if (!info || !chrome.action) return;
  let text = "", color = "#666", tip = "Bulk Downloader";
  if (info.blocked) {
    text = "⚠";  color = "#ef4444";
    tip = `BLOCKED: ${info.block_reason || 'content rights'}`;
  } else if (info.in_queue) {
    text = "⏳";  color = "#f59e0b";
    tip = `In queue (${info.queue_status}) on site ${info.queue_site}`;
  } else if (info.known && info.history) {
    text = "✓";  color = "#10b981";
    tip = `Downloaded ${info.history_count}× — last: ${info.history.filename || info.history.ts}`;
  } else if (info.would_route_to) {
    text = "→";  color = "#3b82f6";
    tip = `Routes to ${info.would_route_to.site_name}`;
  } else {
    text = "";  // unknown URL — no badge
    tip = "Bulk Downloader (URL not recognized)";
  }
  try {
    chrome.action.setBadgeText({ tabId, text });
    if (text) chrome.action.setBadgeBackgroundColor({ tabId, color });
    chrome.action.setTitle({ tabId, title: tip });
  } catch (e) {
    // setBadge fails on chrome:// urls — silent
  }
}

// Hook navigation: refresh badge whenever a tab finishes loading
chrome.tabs?.onUpdated?.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status !== "complete" || !tab.url) return;
  lookupUrlStatus(tab.url).then(info => {
    if (info) updateBadge(tabId, info);
  });
});
// Also refresh on tab activate
chrome.tabs?.onActivated?.addListener(({ tabId }) => {
  chrome.tabs.get(tabId, (tab) => {
    if (chrome.runtime.lastError || !tab?.url) return;
    lookupUrlStatus(tab.url).then(info => {
      if (info) updateBadge(tabId, info);
    });
  });
});

async function sendUrlBatch(text) {
  const { url, token } = await getConfig();
  const headers = { "Content-Type": "application/json" };
  if (token) headers["X-BD-Token"] = token;
  try {
    const r = await fetch(`${url}/api/route_urls`, {
      method: "POST",
      headers,
      body: JSON.stringify({ text, ai_filter: true }),
    });
    const data = await r.json().catch(() => ({}));
    return { ok: r.ok, status: r.status, data };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

// ── v1.2.0: Site routing preview + override ─────────────────────────

async function fetchSitesList() {
  const { url, token } = await getConfig();
  const headers = {};
  if (token) headers["X-BD-Token"] = token;
  try {
    const r = await fetch(`${url}/api/sites_list`, { headers });
    if (!r.ok) return { ok: false, status: r.status };
    return await r.json();
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

async function refreshSitesCache(force) {
  if (!force && _sitesCache.sites.length
      && (Date.now() - _sitesCache.lastFetched) < SITES_CACHE_TTL_MS) {
    return _sitesCache.sites;
  }
  const d = await fetchSitesList();
  if (d && d.ok && Array.isArray(d.sites)) {
    _sitesCache = { sites: d.sites, lastFetched: Date.now() };
  }
  return _sitesCache.sites;
}

// Rebuild the per-site submenu under bd-send-to-specific. Called
// when the cache refreshes. Idempotent — clears old child entries
// then recreates them.
async function rebuildSiteSubmenu() {
  // Remove any previous per-site children. We use a stable prefix
  // "bd-site-" so we can recognize and reset them.
  try {
    // contextMenus has no "list" API in MV3 — we track our own ids
    for (const sid of (_lastSubmenuIds || [])) {
      try { await chrome.contextMenus.remove(sid); } catch (e) {}
    }
  } catch (e) {}
  _lastSubmenuIds = [];
  const sites = _sitesCache.sites || [];
  if (!sites.length) {
    // Empty placeholder so the user sees something even if cache empty
    try {
      chrome.contextMenus.create({
        id: "bd-site-empty",
        parentId: "bd-send-to-specific",
        title: "(no sites configured — open Options to set server URL)",
        contexts: ["link", "page"],
        enabled: false,
      });
      _lastSubmenuIds.push("bd-site-empty");
    } catch (e) {}
    return;
  }
  for (const site of sites) {
    const id = "bd-site-" + site.site_id;
    try {
      chrome.contextMenus.create({
        id,
        parentId: "bd-send-to-specific",
        title: site.name || site.site_id,
        contexts: ["link", "page"],
      });
      _lastSubmenuIds.push(id);
    } catch (e) {
      // Duplicate id from a previous create — chrome.contextMenus
      // doesn't allow re-creating the same id without remove() first.
      // We've already attempted remove() above; if create() fails
      // here, skip silently.
    }
  }
}

let _lastSubmenuIds = [];

async function previewRouting(targetUrl) {
  const { url, token } = await getConfig();
  const headers = { "Content-Type": "application/json" };
  if (token) headers["X-BD-Token"] = token;
  try {
    const r = await fetch(`${url}/api/route_preview`, {
      method: "POST",
      headers,
      body: JSON.stringify({ url: targetUrl }),
    });
    if (!r.ok) return { ok: false, status: r.status };
    return await r.json();
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

async function sendUrlToSite(targetUrl, siteId) {
  // Bypass routing — explicit site selection from the submenu.
  const { url, token } = await getConfig();
  const headers = { "Content-Type": "application/json" };
  if (token) headers["X-BD-Token"] = token;
  try {
    const r = await fetch(`${url}/api/sites/${siteId}/queue_url`, {
      method: "POST",
      headers,
      body: JSON.stringify({ url: targetUrl }),
    });
    const data = await r.json().catch(() => ({}));
    return { ok: r.ok, status: r.status, data };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

// ── v1.2.0: Send all open tabs ──────────────────────────────────────

async function sendAllTabs() {
  // chrome.tabs.query gives us every open tab in the current window.
  // We filter to http(s) — chrome:// and about: pages have no
  // download value.
  let tabs;
  try {
    tabs = await chrome.tabs.query({ currentWindow: true });
  } catch (e) {
    return { ok: false, error: "could not enumerate tabs: " + e };
  }
  const urls = (tabs || [])
    .map(t => t.url || "")
    .filter(u => u.startsWith("http"));
  if (!urls.length) {
    return { ok: false, error: "no http URLs in current window" };
  }
  return await sendUrlBatch(urls.join("\n"));
}

function notify(title, message) {
  // service-worker contexts can't always call chrome.notifications;
  // use it when available, fallback to console.
  if (chrome.notifications) {
    chrome.notifications.create({
      type: "basic",
      iconUrl: chrome.runtime.getURL("icon128.png"),
      title,
      message,
    });
  } else {
    console.log(`${title}: ${message}`);
  }
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId === "bd-send-link" && info.linkUrl) {
    const r = await sendUrl(info.linkUrl);
    notify("Bulk Downloader",
      r.ok ? `Sent: ${info.linkUrl}` : `Failed: ${r.error || r.status}`);
    return;
  }
  if (info.menuItemId === "bd-send-page" && tab?.url) {
    const r = await sendUrl(tab.url);
    notify("Bulk Downloader",
      r.ok ? `Sent: ${tab.url}` : `Failed: ${r.error || r.status}`);
    return;
  }
  // v1.2.0: send all open tabs
  if (info.menuItemId === "bd-send-all-tabs") {
    const r = await sendAllTabs();
    if (r.ok) {
      const total = Object.values(r.data?.summary || {})
        .reduce((s, x) => s + (x.added || 0), 0);
      notify("Bulk Downloader",
        `Sent ${total} URLs from ${Object.keys(r.data?.summary || {}).length} sites.`);
    } else {
      notify("Bulk Downloader", `Send all tabs failed: ${r.error || r.status}`);
    }
    return;
  }
  // v1.2.0: per-site submenu — id is "bd-site-<sid>"
  if (typeof info.menuItemId === "string"
      && info.menuItemId.startsWith("bd-site-")
      && info.menuItemId !== "bd-site-empty") {
    const siteId = info.menuItemId.slice("bd-site-".length);
    const targetUrl = info.linkUrl || tab?.url;
    if (!targetUrl) {
      notify("Bulk Downloader", "No URL to send.");
      return;
    }
    const r = await sendUrlToSite(targetUrl, siteId);
    if (r.ok) {
      const siteName = (r.data && r.data.site_name) || siteId;
      const added = (r.data && r.data.added) || 0;
      notify("Bulk Downloader",
        added ? `Sent to ${siteName}: ${targetUrl.slice(0, 60)}`
              : `Already queued in ${siteName}.`);
    } else {
      notify("Bulk Downloader",
        `Send-to-${siteId} failed: ${r.error || r.status}`);
    }
    return;
  }
  if (info.menuItemId === "bd-scrape-page" && tab?.id) {
    // Inject a content script to collect video-looking anchors
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
    if (!urls.length) {
      notify("Bulk Downloader", "No video links found on this page.");
      return;
    }
    const r = await sendUrlBatch(urls.join("\n"));
    if (r.ok) {
      const summary = r.data?.summary || {};
      const total = Object.values(summary).reduce((s, x) => s + (x.added || 0), 0);
      const skipped = r.data?.skipped_listings_count || 0;
      notify("Bulk Downloader",
        `Sent ${total} URLs across ${Object.keys(summary).length} sites` +
        (skipped ? ` (skipped ${skipped} listings)` : "") + ".");
    } else {
      notify("Bulk Downloader", `Batch send failed: ${r.error || r.status}`);
    }
  }
});

// Keyboard shortcut "send current page" via browser action
chrome.action.onClicked.addListener(async (tab) => {
  if (!tab?.url) return;
  const r = await sendUrl(tab.url);
  notify("Bulk Downloader",
    r.ok ? `Sent: ${tab.url}` : `Failed: ${r.error || r.status}`);
});

// v1.2.0: keyboard commands. Manifest "commands" block bindings.
// chrome.commands.onCommand fires when the user hits the configured
// hotkey (default Alt+Shift+B / Alt+Shift+T; rebindable in
// chrome://extensions/shortcuts).
if (chrome.commands && chrome.commands.onCommand) {
  chrome.commands.onCommand.addListener(async (command) => {
    if (command === "send-current-page") {
      const [tab] = await chrome.tabs.query({
        active: true, currentWindow: true,
      });
      if (!tab?.url) {
        notify("Bulk Downloader", "No active tab.");
        return;
      }
      const r = await sendUrl(tab.url);
      notify("Bulk Downloader",
        r.ok ? `Sent: ${tab.url.slice(0, 80)}`
              : `Failed: ${r.error || r.status}`);
      return;
    }
    if (command === "send-all-tabs") {
      const r = await sendAllTabs();
      if (r.ok) {
        const total = Object.values(r.data?.summary || {})
          .reduce((s, x) => s + (x.added || 0), 0);
        notify("Bulk Downloader",
          `Sent ${total} URLs from current window.`);
      } else {
        notify("Bulk Downloader",
          `Send all tabs failed: ${r.error || r.status}`);
      }
    }
  });
}

// v1.2.0: keep the site cache fresh + rebuild submenu when storage
// changes (user might point the extension at a different server in
// Options). Failure here is non-fatal — submenu just stays stale.
chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName === "sync" && changes.bd_url) {
    refreshSitesCache(true).then(() => rebuildSiteSubmenu()).catch(() => {});
  }
});


// ── v1.1.0: Vault autofill bridge ────────────────────────────────────
//
// The content script (autofill.js) calls into the background via
// chrome.runtime.sendMessage to ask "what entries match this origin?"
// and "fetch this entry's password". We never let the password value
// transit through the content script's locals beyond the fill itself.
//
// Storage:
//   bd_vault_token        : long-lived token paired with the app
//   bd_vault_silent_origins : { origin: true } allowlist of origins
//                            where silent autofill is OK
//   bd_vault_idle_lock_minutes : 0 = never, 15 = default. 0 means the
//                            token stays usable until manually revoked.
//   bd_vault_last_use_ts  : last time a fetch succeeded; used by idle
//                            lock to clear the token after inactivity.

async function vaultConfig() {
  const c = await chrome.storage.local.get([
    "bd_vault_token", "bd_vault_silent_origins",
    "bd_vault_idle_lock_minutes", "bd_vault_last_use_ts",
  ]);
  const cfg = await chrome.storage.sync.get(["bd_url"]);
  return {
    url: (cfg.bd_url || DEFAULT_URL).replace(/\/$/, ""),
    token: c.bd_vault_token || "",
    silentOrigins: c.bd_vault_silent_origins || {},
    idleLockMinutes: typeof c.bd_vault_idle_lock_minutes === "number"
                       ? c.bd_vault_idle_lock_minutes : 15,
    lastUseTs: c.bd_vault_last_use_ts || 0,
  };
}

async function vaultIsLocked() {
  const c = await vaultConfig();
  if (!c.token) return true;
  if (c.idleLockMinutes <= 0) return false;
  const idleMs = Date.now() - c.lastUseTs;
  return idleMs > c.idleLockMinutes * 60 * 1000;
}

async function vaultTouch() {
  await chrome.storage.local.set({ bd_vault_last_use_ts: Date.now() });
}

// Derive eTLD+1 of a URL for the silent-origins allowlist.
function registrableDomain(url) {
  try {
    const u = new URL(url);
    const parts = u.hostname.split(".").filter(Boolean);
    if (parts.length <= 2) return parts.join(".");
    return parts.slice(-2).join(".");
  } catch (e) {
    return "";
  }
}

// ── List entries matching an origin ──────────────────────────────────

async function vaultListForOrigin(origin) {
  const c = await vaultConfig();
  if (!c.token) return { ok: true, entries: [], silent_allowed: false };
  if (await vaultIsLocked()) {
    return { ok: false, error: "vault locked (idle timeout)" };
  }
  try {
    const r = await fetch(
      `${c.url}/api/secrets/extension/list_for_origin?origin=` +
        encodeURIComponent(origin),
      { headers: { "Authorization": "Bearer " + c.token } },
    );
    if (!r.ok) {
      return { ok: false, error: `HTTP ${r.status}` };
    }
    const data = await r.json();
    const etld1 = registrableDomain(origin);
    const silent_allowed = !!c.silentOrigins[etld1];
    return { ok: true, entries: data.entries || [], silent_allowed };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

async function vaultFetchOne(id, origin) {
  const c = await vaultConfig();
  if (!c.token) return { ok: false, error: "not paired" };
  if (await vaultIsLocked()) {
    return { ok: false, error: "vault locked (idle timeout)" };
  }
  try {
    const r = await fetch(`${c.url}/api/secrets/extension/fetch_one`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + c.token,
      },
      body: JSON.stringify({ id, origin: origin || "" }),
    });
    if (r.status === 429) {
      return { ok: false, error: "rate limited — wait a few seconds" };
    }
    if (!r.ok) {
      return { ok: false, error: `HTTP ${r.status}` };
    }
    const data = await r.json();
    if (data.ok) await vaultTouch();
    return data;
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

async function vaultPair(pairingToken, label) {
  const cfg = await chrome.storage.sync.get(["bd_url"]);
  const url = (cfg.bd_url || DEFAULT_URL).replace(/\/$/, "");
  try {
    const r = await fetch(`${url}/api/secrets/extension/pair`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pairing_token: pairingToken, label: label || "extension" }),
    });
    if (!r.ok) {
      const errData = await r.json().catch(() => ({}));
      return { ok: false, error: errData.error || `HTTP ${r.status}` };
    }
    const data = await r.json();
    if (data.ok && data.vault_token) {
      await chrome.storage.local.set({
        bd_vault_token: data.vault_token,
        bd_vault_last_use_ts: Date.now(),
      });
      return { ok: true, label: data.label };
    }
    return { ok: false, error: data.error || "pair failed" };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

async function vaultUnpair() {
  await chrome.storage.local.remove(["bd_vault_token", "bd_vault_last_use_ts"]);
  return { ok: true };
}

async function vaultStatus() {
  const c = await vaultConfig();
  const locked = await vaultIsLocked();
  return {
    ok: true,
    paired: !!c.token,
    locked,
    idle_lock_minutes: c.idleLockMinutes,
    silent_origins: Object.keys(c.silentOrigins),
  };
}

async function vaultSetSilentForOrigin(origin, allow) {
  const etld1 = registrableDomain(origin);
  if (!etld1) return { ok: false, error: "could not derive origin" };
  const c = await chrome.storage.local.get(["bd_vault_silent_origins"]);
  const so = c.bd_vault_silent_origins || {};
  if (allow) so[etld1] = true; else delete so[etld1];
  await chrome.storage.local.set({ bd_vault_silent_origins: so });
  return { ok: true, etld1, allowed: !!allow };
}

async function vaultSetIdleLock(minutes) {
  const m = Math.max(0, parseInt(minutes, 10) || 0);
  await chrome.storage.local.set({ bd_vault_idle_lock_minutes: m });
  return { ok: true, idle_lock_minutes: m };
}

// ── Message router ───────────────────────────────────────────────────
// Content script and popup use chrome.runtime.sendMessage to reach us.

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const action = msg && msg.action;
  const payload = (msg && msg.payload) || {};
  if (!action) { sendResponse({ ok: false, error: "no action" }); return; }
  // sendResponse must be called either sync or after returning true
  let p = null;
  switch (action) {
    case "vault_list_for_origin":
      p = vaultListForOrigin(payload.origin || sender.url || ""); break;
    case "vault_fetch_one":
      p = vaultFetchOne(payload.id, payload.origin || sender.url || ""); break;
    case "vault_pair":
      p = vaultPair(payload.pairing_token, payload.label); break;
    case "vault_unpair":
      p = vaultUnpair(); break;
    case "vault_status":
      p = vaultStatus(); break;
    case "vault_set_silent_for_origin":
      p = vaultSetSilentForOrigin(payload.origin, payload.allow); break;
    case "vault_set_idle_lock":
      p = vaultSetIdleLock(payload.minutes); break;
    // v1.2.0: routing preview / send-to-site / sites-list / send-all
    case "route_preview":
      p = previewRouting(payload.url); break;
    case "send_to_site":
      p = sendUrlToSite(payload.url, payload.site_id); break;
    case "sites_list":
      p = refreshSitesCache(payload.force).then(sites => ({
        ok: true, sites,
      })); break;
    case "send_url":
      p = sendUrl(payload.url); break;
    case "send_all_tabs":
      p = sendAllTabs(); break;
    default:
      sendResponse({ ok: false, error: "unknown action" });
      return;
  }
  p.then((resp) => sendResponse(resp))
   .catch((e) => sendResponse({ ok: false, error: String(e) }));
  return true;  // keep the message channel open for async response
});
