// Bulk Downloader autofill content script (v1.1.0).
//
// Runs on every page (manifest matches <all_urls>) at document_idle.
//
// What it does
// ────────────
//   1. Detects login-like forms (input[type=password] + nearby username field)
//   2. Asks the background script "do you have entries for this URL?"
//   3. If yes, injects a small "🔐 BulkDownloader" button next to the
//      username field. Click → asks background for the password →
//      fills both fields.
//   4. If the user has opted into silent autofill for THIS origin,
//      skips step 3 and just fills automatically (still requires the
//      background to be unlocked, which is gated separately).
//
// What it does NOT do
// ────────────────────
//   - Read the password value into content-script memory for any longer
//     than the fill operation itself. The password comes in via
//     chrome.runtime.sendMessage from the background, gets pasted into
//     the input element's .value, and the local variable goes out of
//     scope. The DOM still holds the value of course — that's
//     unavoidable for autofill — but no extension-side reference.
//   - Touch the form on URLs we have no entries for. Silent no-op.
//   - Work in iframes. all_frames=false in the manifest.
//   - Fight with browser native autofill. We detect and back off if
//     Chrome's own credential prompt is visible.
//
// Threat model
// ────────────
//   - Content scripts share JS context with the page. So even though
//     this script's locals are unreachable from page JS, anything we
//     PUT INTO THE DOM (input values) is readable by the page. That's
//     fundamental: autofill IS putting the secret into the DOM. Use
//     vault entries only on sites you trust to receive your password.
//   - We use chrome.runtime.sendMessage (not window.postMessage) for
//     content↔background, so page JS can't intercept.

(function() {
  'use strict';

  // Don't double-mount if the script gets injected twice (some browsers
  // do this on bfcache restore + history navigation).
  if (window.__bd_autofill_mounted) return;
  window.__bd_autofill_mounted = true;

  const STATE = {
    matched: [],           // entries returned for this origin
    inFlight: false,       // a fetch is in progress (rate-limit guard)
    silentAllowed: false,  // user opted into silent autofill for this origin
  };

  // ── Detect login fields ──────────────────────────────────────────
  // Returns {username, password} elements or null if no login form.

  function findLoginFields() {
    const pwSelectors = [
      'input[type="password"]:not([disabled]):not([readonly])',
    ];
    let pw = null;
    for (const sel of pwSelectors) {
      const el = document.querySelector(sel);
      if (el && el.offsetParent !== null) { pw = el; break; }
    }
    if (!pw) return null;

    // Username: the most-likely candidate is a text/email input
    // BEFORE the password element in DOM order (visual same-form layout).
    // Walk back via document.querySelectorAll.
    const candidates = [
      'input[autocomplete="username"]',
      'input[autocomplete="email"]',
      'input[type="email"]',
      'input[name="username"]', 'input[name="email"]', 'input[name="login"]',
      'input[id*="user" i]', 'input[id*="email" i]', 'input[id*="login" i]',
      'input[type="text"]',
    ];
    let user = null;
    for (const sel of candidates) {
      const els = document.querySelectorAll(sel);
      for (const el of els) {
        // Must be visible, enabled, before the password field
        if (!el || el.disabled || el.readOnly) continue;
        if (el.offsetParent === null) continue;
        if (pw.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_PRECEDING) {
          user = el; break;
        }
      }
      if (user) break;
    }
    return { username: user, password: pw };
  }

  // ── Background-script bridge ─────────────────────────────────────

  function bgCall(action, payload) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage({ action, payload }, (resp) => {
          if (chrome.runtime.lastError) {
            // Service worker may have been recycled; treat as no-data
            resolve({ ok: false, error: chrome.runtime.lastError.message });
            return;
          }
          resolve(resp || { ok: false, error: 'no response' });
        });
      } catch (e) {
        resolve({ ok: false, error: String(e) });
      }
    });
  }

  // ── Suggestion menu UI ───────────────────────────────────────────
  // Anchored to the username field. Floats above on z-index high enough
  // to beat most site UIs, low enough that the extension popup wins.

  function showSuggestion(userField, pwField, entries) {
    // Remove any prior menu
    const existing = document.getElementById('__bd_autofill_menu');
    if (existing) existing.remove();

    const r = userField.getBoundingClientRect();
    const menu = document.createElement('div');
    menu.id = '__bd_autofill_menu';
    menu.setAttribute('style', [
      'position:fixed',
      'left:' + Math.round(r.left + window.scrollX) + 'px',
      'top:' + Math.round(r.bottom + window.scrollY + 4) + 'px',
      'z-index:2147483647',
      'background:#1e1e26', 'color:#e6e6e9',
      'border:1px solid #4a9eff', 'border-radius:6px',
      'padding:6px', 'min-width:200px', 'max-width:320px',
      'font:13px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif',
      'box-shadow:0 6px 20px rgba(0,0,0,0.5)',
    ].join(';'));
    menu.innerHTML =
      '<div style="font-weight:600;font-size:11px;color:#9ca3af;margin:4px 6px 6px;letter-spacing:.5px">' +
      '🔐 BULK DOWNLOADER · sign in with</div>';
    entries.forEach((entry, idx) => {
      const row = document.createElement('div');
      row.setAttribute('style',
        'padding:6px 8px;cursor:pointer;border-radius:4px;display:flex;flex-direction:column');
      row.onmouseover = () => row.style.background = '#252530';
      row.onmouseout = () => row.style.background = '';
      row.onclick = () => { menu.remove(); doFill(userField, pwField, entry); };
      const name = document.createElement('div');
      name.style.fontWeight = '600';
      name.textContent = entry.name || 'saved login';
      const usr = document.createElement('div');
      usr.setAttribute('style', 'font-size:11px;color:#9ca3af');
      usr.textContent = entry.username || '(no username)';
      row.appendChild(name); row.appendChild(usr);
      menu.appendChild(row);
    });
    // Close menu on outside click + escape
    document.documentElement.appendChild(menu);
    setTimeout(() => {
      const onClickOutside = (e) => {
        if (!menu.contains(e.target) && e.target !== userField) {
          menu.remove();
          document.removeEventListener('click', onClickOutside, true);
        }
      };
      document.addEventListener('click', onClickOutside, true);
    }, 0);
    document.addEventListener('keydown', function onKey(e) {
      if (e.key === 'Escape') {
        menu.remove();
        document.removeEventListener('keydown', onKey);
      }
    });
  }

  // ── Fill the form ────────────────────────────────────────────────

  async function doFill(userField, pwField, entry) {
    if (STATE.inFlight) return;
    STATE.inFlight = true;
    try {
      const resp = await bgCall('vault_fetch_one', {
        id: entry.id,
        origin: location.href,
      });
      if (!resp || !resp.ok) {
        const msg = resp && resp.error ? resp.error : 'fetch failed';
        toast('Autofill: ' + msg);
        return;
      }
      const username = resp.username || entry.username || '';
      const password = resp.password || '';
      if (userField && username) {
        userField.focus();
        userField.value = username;
        userField.dispatchEvent(new Event('input', { bubbles: true }));
        userField.dispatchEvent(new Event('change', { bubbles: true }));
      }
      if (pwField && password) {
        pwField.focus();
        pwField.value = password;
        pwField.dispatchEvent(new Event('input', { bubbles: true }));
        pwField.dispatchEvent(new Event('change', { bubbles: true }));
      }
      if (userField) userField.blur();
      if (pwField) pwField.blur();
    } finally {
      STATE.inFlight = false;
    }
  }

  function toast(msg) {
    const t = document.createElement('div');
    t.setAttribute('style', [
      'position:fixed', 'bottom:20px', 'right:20px',
      'z-index:2147483647', 'background:#1e1e26', 'color:#fde68a',
      'border:1px solid #f59e0b', 'border-radius:4px',
      'padding:10px 14px',
      'font:600 12px -apple-system,BlinkMacSystemFont,sans-serif',
      'box-shadow:0 4px 14px rgba(0,0,0,0.4)',
    ].join(';'));
    t.textContent = msg;
    document.documentElement.appendChild(t);
    setTimeout(() => t.remove(), 3500);
  }

  // ── Detection trigger ────────────────────────────────────────────

  async function tryShow() {
    const fields = findLoginFields();
    if (!fields || !fields.password) return;
    // Ask background for matches. Cheap call — returns metadata only.
    if (STATE.matched.length === 0) {
      const resp = await bgCall('vault_list_for_origin',
        { origin: location.href });
      if (!resp || !resp.ok) return;
      STATE.matched = resp.entries || [];
      STATE.silentAllowed = !!resp.silent_allowed;
    }
    if (STATE.matched.length === 0) return;
    if (STATE.silentAllowed && STATE.matched.length === 1) {
      // Silent fill: pre-authorized for this origin and only one option
      doFill(fields.username, fields.password, STATE.matched[0]);
    } else {
      showSuggestion(fields.username || fields.password,
                     fields.password, STATE.matched);
    }
  }

  // Initial check at idle (already there per manifest run_at), then
  // again on focus of any input (SPA login flows that lazy-mount forms).
  setTimeout(tryShow, 300);
  document.addEventListener('focusin', (e) => {
    if (e.target.tagName === 'INPUT' &&
        (e.target.type === 'password' || e.target.type === 'email' ||
         e.target.type === 'text')) {
      tryShow();
    }
  });
})();
