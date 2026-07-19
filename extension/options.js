// Phase 77: options page script.
// v1.1.0: vault pairing + management.

// AUDIT v3.43.48: html-escape helper for any server-supplied string
// interpolated into innerHTML. The vault server is generally trusted
// (same operator), but defense-in-depth says escape every untrusted
// boundary. Used by refreshVaultStatus (silent_origins) and the
// pairing-result render.
function esc(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

(async () => {
  const cfg = await chrome.storage.sync.get(["bd_url", "bd_token"]);
  document.getElementById("bd_url").value = cfg.bd_url || "http://127.0.0.1:5555";
  document.getElementById("bd_token").value = cfg.bd_token || "";
  await refreshVaultStatus();
})();

document.getElementById("save").addEventListener("click", async () => {
  const bd_url = document.getElementById("bd_url").value.trim();
  const bd_token = document.getElementById("bd_token").value.trim();
  await chrome.storage.sync.set({ bd_url, bd_token });
  const saved = document.getElementById("saved");
  saved.style.display = "block";
  setTimeout(() => { saved.style.display = "none"; }, 2000);
});


// ── Vault management ───────────────────────────────────────────────

function bgCall(action, payload) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ action, payload: payload || {} }, (resp) => {
      resolve(resp || { ok: false, error: chrome.runtime.lastError?.message });
    });
  });
}

async function refreshVaultStatus() {
  const s = await bgCall("vault_status");
  const statusEl = document.getElementById("vault-status-text");
  const notPairedEl = document.getElementById("vault-not-paired");
  const pairedEl = document.getElementById("vault-paired");
  const silentWrap = document.getElementById("silent-origins-wrap");

  if (!s || !s.ok) {
    statusEl.textContent = "Status unavailable";
    return;
  }
  if (!s.paired) {
    statusEl.innerHTML = '<span style="color:#fbbf24">⚠ Not paired</span>';
    notPairedEl.style.display = "block";
    pairedEl.style.display = "none";
    return;
  }
  // Paired
  notPairedEl.style.display = "none";
  pairedEl.style.display = "block";
  if (s.locked) {
    statusEl.innerHTML = '<span style="color:#fbbf24">🔒 Locked (idle timeout) — re-pair to unlock</span>';
  } else {
    statusEl.innerHTML = '<span style="color:#86efac">✓ Paired and unlocked</span>';
  }
  // Set the idle lock dropdown to current value
  const lock = document.getElementById("idle_lock");
  if (lock) lock.value = String(s.idle_lock_minutes);
  // Silent origins list
  const list = document.getElementById("silent-origins-list");
  if (s.silent_origins && s.silent_origins.length > 0) {
    silentWrap.style.display = "block";
    // AUDIT v3.43.48: origin strings come from the server — escape
    // before innerHTML. Each row's remove-button data-origin attr
    // also needs escaping (attribute context).
    list.innerHTML = s.silent_origins.map(o =>
      `<div style="display:flex;justify-content:space-between;padding:3px 0">
         <span>${esc(o)}</span>
         <button data-origin="${esc(o)}" class="rm-silent" style="background:transparent;border:0;color:#dc2626;cursor:pointer;font-size:11px">remove</button>
       </div>`).join("");
    list.querySelectorAll(".rm-silent").forEach(btn => {
      btn.addEventListener("click", async () => {
        await bgCall("vault_set_silent_for_origin",
          { origin: "https://" + btn.dataset.origin, allow: false });
        await refreshVaultStatus();
      });
    });
  } else {
    silentWrap.style.display = "none";
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const pairBtn = document.getElementById("do-pair");
  if (pairBtn) {
    pairBtn.addEventListener("click", async () => {
      const token = document.getElementById("pair_token").value.trim();
      const label = document.getElementById("pair_label").value.trim();
      const result = document.getElementById("pair-result");
      if (!token) {
        result.innerHTML = '<span style="color:#fbbf24">Paste a pairing token first</span>';
        return;
      }
      result.textContent = "Pairing…";
      const r = await bgCall("vault_pair",
        { pairing_token: token, label: label || "extension" });
      if (r && r.ok) {
        // AUDIT v3.43.48: r.label is server-supplied — escape.
        result.innerHTML = '<span style="color:#86efac">✓ Paired as "' +
          esc(r.label || "extension") + '"</span>';
        document.getElementById("pair_token").value = "";
        await refreshVaultStatus();
      } else {
        // AUDIT v3.43.48: r.error is server-supplied — escape.
        result.innerHTML = '<span style="color:#dc2626">✗ ' +
          esc(r?.error || "pair failed") + '</span>';
      }
    });
  }
  const unpairBtn = document.getElementById("do-unpair");
  if (unpairBtn) {
    unpairBtn.addEventListener("click", async () => {
      if (!confirm("Unpair vault? Saved password autofill will stop working " +
                   "until you re-pair from the app.")) return;
      await bgCall("vault_unpair");
      await refreshVaultStatus();
    });
  }
  const lock = document.getElementById("idle_lock");
  if (lock) {
    lock.addEventListener("change", async () => {
      await bgCall("vault_set_idle_lock", { minutes: lock.value });
      await refreshVaultStatus();
    });
  }
});
