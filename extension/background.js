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

// Public-suffix data for registrableDomain() (O1671 AUDIT-12).
// BUNDLED, NEVER FETCHED: a security boundary must not depend on a network
// answer. Two sources, unioned:
//   - public_suffix_data.js: the Public Suffix List (ICANN + PRIVATE), loaded
//     below with importScripts(). Where importScripts does not exist (the
//     node-vm harnesses that run this worker standalone) or the load fails,
//     the rules are simply absent and registrableDomain() FAILS CLOSED to the
//     whole host -- it never guesses.
//   - BD_MULTI_PART_SUFFIXES: the curated set that must equal
//     _MULTI_PART_SUFFIXES in bulk_downloader/registrable_domain.py
//     (extension/test_registrable_domain.mjs asserts parity). It adds a few
//     user-content hosts the list does not carry and keeps the worst cases
//     separated even with no list loaded.
try {
  if (typeof importScripts === "function") importScripts("public_suffix_data.js");
} catch (e) {
  console.warn("[bd] public suffix list not loaded; silent-fill keys are per host", e);
}

const BD_MULTI_PART_SUFFIXES = new Set([
  // United Kingdom
  "co.uk", "org.uk", "me.uk", "ltd.uk", "plc.uk", "net.uk", "sch.uk",
  "ac.uk", "gov.uk", "nhs.uk", "police.uk", "mod.uk",
  // Australia / New Zealand
  "com.au", "net.au", "org.au", "edu.au", "gov.au", "asn.au", "id.au",
  "co.nz", "net.nz", "org.nz", "govt.nz", "ac.nz", "school.nz",
  // Japan / Korea / China / Taiwan / Hong Kong / Singapore / India
  "co.jp", "ne.jp", "or.jp", "ac.jp", "go.jp", "ad.jp", "ed.jp", "gr.jp",
  "co.kr", "or.kr", "ne.kr", "go.kr", "re.kr", "pe.kr",
  "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "ac.cn",
  "com.tw", "net.tw", "org.tw", "idv.tw", "gov.tw", "edu.tw",
  "com.hk", "net.hk", "org.hk", "edu.hk", "gov.hk", "idv.hk",
  "com.sg", "net.sg", "org.sg", "edu.sg", "gov.sg", "per.sg",
  "co.in", "net.in", "org.in", "firm.in", "gen.in", "ind.in", "gov.in",
  // Europe / Americas / Africa
  "co.za", "org.za", "net.za", "web.za", "gov.za", "ac.za",
  "com.br", "net.br", "org.br", "gov.br", "edu.br", "art.br", "blog.br",
  "com.mx", "org.mx", "net.mx", "edu.mx", "gob.mx",
  "com.ar", "net.ar", "org.ar", "gob.ar", "edu.ar",
  "co.il", "org.il", "net.il", "ac.il", "gov.il",
  "com.tr", "net.tr", "org.tr", "gov.tr", "edu.tr",
  "com.ru", "net.ru", "org.ru", "pp.ru",
  "com.pl", "net.pl", "org.pl", "gov.pl", "edu.pl",
  "com.ua", "net.ua", "org.ua", "in.ua", "kiev.ua",
  "co.id", "web.id", "or.id", "ac.id", "go.id",
  "com.my", "net.my", "org.my", "edu.my", "gov.my",
  "com.ph", "net.ph", "org.ph", "gov.ph",
  "com.vn", "net.vn", "org.vn", "edu.vn", "gov.vn",
  "co.th", "in.th", "ac.th", "go.th", "or.th",
  "com.es", "org.es", "nom.es", "gob.es", "edu.es",
  "com.pt", "org.pt", "edu.pt", "gov.pt",
  "com.gr", "net.gr", "org.gr", "edu.gr", "gov.gr",
  // User-content hosts. The security case is sharpest here: two projects on
  // github.io are unrelated strangers, and the old rule called them one site.
  "github.io", "gitlab.io", "githubusercontent.com",
  "pages.dev", "workers.dev", "netlify.app", "vercel.app",
  "herokuapp.com", "azurewebsites.net", "cloudfront.net",
  "s3.amazonaws.com", "blogspot.com", "wordpress.com", "tumblr.com",
  "myshopify.com", "web.app", "firebaseapp.com", "appspot.com",
  "r2.dev", "b-cdn.net", "sourceforge.io", "readthedocs.io",
]);

let bdSuffixIndexCache = null;

// exact / wildcard-parent / exception rule sets, built once.
function bdSuffixIndex() {
  if (bdSuffixIndexCache) return bdSuffixIndexCache;
  const exact = new Set(BD_MULTI_PART_SUFFIXES);
  const wild = new Set();
  const except = new Set();
  const text = (typeof BD_PSL === "object" && BD_PSL && typeof BD_PSL.rules === "string")
    ? BD_PSL.rules : "";
  for (const rule of text.split(/\s+/)) {
    if (!rule) continue;
    if (rule[0] === "!") except.add(rule.slice(1));
    else if (rule.startsWith("*.")) wild.add(rule.slice(2));
    else exact.add(rule);
  }
  bdSuffixIndexCache = { exact, wild, except };
  return bdSuffixIndexCache;
}

// Derive the silent-origins allowlist key of a URL: its registrable domain
// (public suffix + one label) when the suffix is KNOWN, else the whole host.
//
// PUBLIC-SUFFIX AWARE, FAIL CLOSED (O1671 AUDIT-12). The old last-two-labels
// slice keyed evil.co.uk and bank.co.uk to the same "co.uk", so one
// registrant's silent allow covered every other registrant under that suffix.
//   - Public Suffix List matching: an exception rule (!www.ck) wins, else the
//     longest exact (co.uk) or wildcard (*.ck) rule; key = suffix + one label.
//   - A host that IS a public suffix (co.uk, github.io) keys only itself.
//   - A single-label host (localhost) or an IP literal keys only itself.
//   - NO RULE MATCHED (a TLD or suffix the bundle does not know, or the list
//     did not load): the key is the WHOLE HOST. There is no last-two-labels
//     fallback -- that fallback is how an unlisted suffix (bank.co.ke ->
//     "co.ke") handed one site's consent to every neighbour. The cost of not
//     knowing is that consent stops at one host; it is never shared wider.
// DIVERGES from bulk_downloader/registrable_domain.py on purpose: that
// reference degrades an unknown suffix to last-two-labels and returns "0.1"
// for 127.0.0.1. Neither is acceptable for a consent key.
function registrableDomain(url) {
  let host;
  try {
    host = new URL(url).hostname;
  } catch (e) {
    return "";
  }
  host = String(host || "").toLowerCase().replace(/^\.+|\.+$/g, "");
  if (!host) return "";
  if (host.startsWith("[") || /^\d{1,3}(\.\d{1,3}){3}$/.test(host)) return host;
  const labels = host.split(".").filter(Boolean);
  if (labels.length < 2) return host;
  const idx = bdSuffixIndex();
  let suffixLen = 0;
  for (let n = labels.length; n >= 1; n--) {
    const cand = labels.slice(-n).join(".");
    if (idx.except.has(cand)) { suffixLen = n - 1; break; }
    if (idx.exact.has(cand)) { suffixLen = n; break; }
    if (n >= 2 && idx.wild.has(labels.slice(-(n - 1)).join("."))) { suffixLen = n; break; }
  }
  if (suffixLen < 1 || suffixLen >= labels.length) return host;
  return labels.slice(-(suffixLen + 1)).join(".");
}

// The key and current state of the silent toggle for one origin. The popup
// asks for it instead of deriving its own key, so there is one rule.
async function vaultSilentKey(origin) {
  const etld1 = registrableDomain(origin);
  if (!etld1) return { ok: false, error: "could not derive origin" };
  const c = await chrome.storage.local.get(["bd_vault_silent_origins"]);
  return { ok: true, etld1, allowed: !!(c.bd_vault_silent_origins || {})[etld1] };
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

// The origin a vault message may act for. The extension's own pages
// (popup/options act for the active tab) may name the origin in the payload;
// they are checked FIRST because options_page opens in a tab, so sender.tab
// is set for it too. A content script's sender.url is never under our
// chrome-extension:// root. Any other tab speaks only for the page it runs
// in -- sender.url, never a payload claim. Anyone else: null (refused).
function vaultSenderOrigin(sender, payload) {
  if (!sender) return null;
  const own = sender.id === chrome.runtime.id &&
    String(sender.url || "").startsWith(chrome.runtime.getURL(""));
  if (own) return payload.origin || sender.url || "";
  if (sender.tab) return sender.url || sender.origin || "";
  return null;
}

function vaultUntrustedSender() {
  return Promise.resolve({ ok: false, error: "untrusted sender" });
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const action = msg && msg.action;
  const payload = (msg && msg.payload) || {};
  if (!action) { sendResponse({ ok: false, error: "no action" }); return; }
  // sendResponse must be called either sync or after returning true
  let p = null;
  const vaultOrigin = vaultSenderOrigin(sender, payload);
  switch (action) {
    case "vault_list_for_origin":
      p = vaultOrigin === null ? vaultUntrustedSender()
        : vaultListForOrigin(vaultOrigin); break;
    case "vault_fetch_one":
      p = vaultOrigin === null ? vaultUntrustedSender()
        : vaultFetchOne(payload.id, vaultOrigin); break;
    case "vault_pair":
      p = vaultPair(payload.pairing_token, payload.label); break;
    case "vault_unpair":
      p = vaultUnpair(); break;
    case "vault_status":
      p = vaultStatus(); break;
    case "vault_set_silent_for_origin":
      p = vaultOrigin === null ? vaultUntrustedSender()
        : vaultSetSilentForOrigin(vaultOrigin, payload.allow); break;
    case "vault_silent_key":
      p = vaultOrigin === null ? vaultUntrustedSender()
        : vaultSilentKey(vaultOrigin); break;
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
