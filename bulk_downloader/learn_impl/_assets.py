"""learn_impl._assets -- RECORDER_JS + TEACH_OVERLAY_JS injected-JS blobs (verbatim,
byte-identical; ~53% of the original learn.py). Sink: no imports."""

RECORDER_JS = r"""
(() => {
  if (window.__pwrec_installed) return;
  window.__pwrec_installed = true;
  // v3.66.288: survive a same-ORIGIN full-page navigation. The harvest
  // lives in page-global state, which a document swap destroys — so a
  // two-step login (username -> Next/navigate -> password) previously lost
  // the page-1 username harvest, leaving only the page-2 password
  // ("password fills but not the username"). We rehydrate from
  // sessionStorage on (re)install and persist each event back, so
  // same-origin same-tab navigations accumulate.
  //
  // SECRET POSTURE: only STRUCTURE is written to storage. The stored copy
  // blanks the raw value channels (_input_value and text), so no typed
  // credential is ever persisted to sessionStorage. The in-memory arrays
  // keep full records for same-page finalize; cross-navigation rehydrated
  // records are value-less (selectors are recovered for replay; the
  // operator re-enters the username once). Every storage op is wrapped, so
  // a blocked/absent sessionStorage degrades to the prior in-memory-only
  // behaviour. Cross-ORIGIN navigations don't share sessionStorage and are
  // not covered. The store is cleared by harvest_recordings at finalize.
  const _PWREC_STORE = '__pwrec_store_v1:';
  const _pwrecRedact = (rec) => {
    const c = {}; for (const k in rec) { c[k] = rec[k]; }
    if ('_input_value' in c) c._input_value = '';
    if ('text' in c) c.text = '';
    return c;
  };
  const _pwrecLoad = (k) => {
    try { return JSON.parse(sessionStorage.getItem(_PWREC_STORE + k) || '[]') || []; }
    catch (e) { return []; }
  };
  const _pwrecPersist = (k, rec) => {
    try {
      const arr = _pwrecLoad(k);
      arr.push(_pwrecRedact(rec));
      sessionStorage.setItem(_PWREC_STORE + k, JSON.stringify(arr));
    } catch (e) {}
  };
  window.__pwrec_clicks = _pwrecLoad('clicks');
  window.__pwrec_inputs = _pwrecLoad('inputs');
  const _attr = (el, n) => { try { return el.getAttribute(n) || ''; } catch(e) { return ''; } };
  // Bug fix (v3.42.1): the user often clicks the inner <span> of an
  // <a href="..."> — the click target's tagName is SPAN and its href
  // is empty, so the URL-attribute detection misses entirely. Walk up
  // the DOM up to 6 levels looking for the nearest ancestor that
  // carries any of our URL-flavored attributes. Record those attributes
  // on the click event under `ancestor_*` keys so the Python classifier
  // can find them. Also record the ancestor tag (usually 'a').
  const _findUrlAncestor = (el) => {
    const URL_ATTRS = ['href', 'data-href', 'data-url', 'data-src', 'data-download'];
    let cur = el;
    for (let depth = 0; depth < 6 && cur; depth++) {
      for (const a of URL_ATTRS) {
        const v = _attr(cur, a);
        if (v) return { el: cur, attr: a, value: v, depth };
      }
      cur = cur.parentElement;
    }
    return null;
  };
  const _info = (el) => {
    const anc = _findUrlAncestor(el);
    // v3.43.49: redact password fields. el.value on a type=password
    // input is the actual typed password. Previously the full value
    // flowed into the click harvest as `text` (rendered via innerHTML
    // in the teach panel's click log) AND into the input harvest. The
    // input harvest still needs the raw value so classify_login can
    // populate cfg["password"] — that's the _input_value channel
    // below, which never gets rendered. The text channel uses a
    // sentinel for password fields so the UI shows "[pw]" instead.
    const isPw = (el.tagName === 'INPUT' && el.type === 'password');
    // FOUND-5 (NEW-1 companion): set an explicit `secret` flag on
    // password-field records so the server-side scrub catches id-only
    // selectors (where the synthesized selector may omit type=password).
    // Mirrors secrets-side _is_secret_action: type=password OR an
    // autocomplete that names a password field.
    const _ac = (_attr(el, 'autocomplete') || '').toLowerCase();
    const isSecret = isPw || /(?:^|[\s,])(?:current-|new-)?password\b/.test(_ac);
    return {
      tag: (el.tagName || '').toLowerCase(),
      id:  el.id || '',
      name: el.name || '',
      type: el.type || '',
      secret: isSecret,
      cls:  (typeof el.className === 'string' ? el.className : '') || '',
      text: (isPw
              ? (el.value ? '[pw]' : '')
              : (el.innerText || el.textContent || el.value || '').slice(0, 100).trim()),
      // v3.43.49: separate channel for the raw input value. Captured
      // even for password fields — needed by classify_login to
      // populate cfg["password"]. Never rendered in the UI; only
      // consumed by the server-side classifier.
      _input_value: ((el.tagName === 'INPUT' || el.tagName === 'TEXTAREA')
                       ? (el.value || '')
                       : ''),
      href: _attr(el, 'href'),
      role: _attr(el, 'role'),
      autocomplete: _attr(el, 'autocomplete'),
      placeholder: _attr(el, 'placeholder'),
      ariaLabel: _attr(el, 'aria-label'),
      testid: _attr(el, 'data-testid') || _attr(el, 'data-test') || _attr(el, 'data-cy'),
      // Phase 5.7: capture data-* attributes that commonly hold direct
      // download URLs.
      dataHref: _attr(el, 'data-href'),
      dataUrl: _attr(el, 'data-url'),
      dataSrc: _attr(el, 'data-src'),
      dataDownload: _attr(el, 'data-download'),
      // v3.42.1 bug fix: ancestor URL info (when the click target itself
      // had no URL attribute but an ancestor anchor did). The classifier
      // checks these as a fallback.
      ancestorTag: anc ? (anc.el.tagName || '').toLowerCase() : '',
      ancestorAttr: anc ? anc.attr : '',
      ancestorUrl: anc ? anc.value : '',
      ancestorDepth: anc ? anc.depth : -1,
      ancestorText: anc ? (anc.el.innerText || anc.el.textContent || '').slice(0, 100).trim() : '',
      url:  String(window.location.href || ''),
      ts:   Date.now(),
    };
  };
  // v3.43.0: expose the info-extractor so the teach panel's hover-pick
  // mode can use the same ancestor-walking logic when capturing a
  // synthetic click from a hover-click rather than a real click event.
  // Without this the pick mode would fall back to a minimal record that
  // missed ancestor URLs — exactly the v3.42.1 bug the recorder change
  // fixed in the first place.
  window.__pwrec_info_for_pick = _info;
  document.addEventListener('click', (e) => {
    try { if (e.target) { const _r = _info(e.target); window.__pwrec_clicks.push(_r); _pwrecPersist('clicks', _r); } } catch(err) {}
  }, true);
  document.addEventListener('input', (e) => {
    try {
      const t = e.target;
      if (!t || (t.tagName !== 'INPUT' && t.tagName !== 'TEXTAREA')) return;
      const _r = _info(t);
      window.__pwrec_inputs.push(_r);
      _pwrecPersist('inputs', _r);
    } catch(err) {}
  }, true);
})();
"""


TEACH_OVERLAY_JS = r"""
(() => {
  // Phase 19.fix: idempotency check is on the DOM, not the window. The
  // OLD code set `window.__pw_teach_installed = true` and bailed on
  // re-entry — but the panel is appended to document.documentElement,
  // which gets replaced when the page navigates. After a redirect to
  // the post-login page, the panel was gone but the window flag was
  // still true, so re-injection on navigation did nothing. Symptom:
  // "overlay flashes and then vanishes."
  // Now: only bail if the actual panel element is still in the DOM.
  // The state object lives on window so its contents (recorded events,
  // chosen selectors) survive page transitions even though the DOM
  // panel is rebuilt fresh each time.
  if (document.getElementById('__pw_teach_panel')) return;

  // ── State ───────────────────────────────────────────────────────────
  // Reuse existing state if a prior install already populated it; this
  // way recorded events from the login page survive the navigation to
  // the post-login page.
  const state = window.__pw_teach_state = window.__pw_teach_state || {
    teach_only: true,           // block clicks by default
    pinned_set: new Set(),      // set of event_idx values currently expanded
                                // (multi-select: each entry shows its
                                // candidate-selector picker independently)
    chosen_selectors: {},       // {event_idx: [picked_selector_strings]}
    events: [],                 // mirror of __pwrec_clicks for our UI
    // v3.43.44: URL fingerprint from prior successful downloads.
    // Set by install_teach_overlay's bootstrap script before the
    // panel JS runs. Empty arrays on first run for a new site.
    fp_hosts: window.__pw_teach_fp_hosts || [],
    fp_prefixes: window.__pw_teach_fp_prefixes || [],
  };
  // Migration: an older state from a previous session may have used the
  // single-event pinned_event_idx scheme. Convert it.
  if (state.pinned_event_idx !== undefined && !state.pinned_set) {
    state.pinned_set = new Set(state.pinned_event_idx >= 0 ? [state.pinned_event_idx] : []);
    delete state.pinned_event_idx;
  }
  if (!(state.pinned_set instanceof Set)) state.pinned_set = new Set();

  // ── Selector synthesis (mirror of Python synthesize_selectors) ──────
  // We re-implement the ranking here so the panel can show candidates
  // immediately without round-tripping to the server. The Python side
  // remains authoritative on commit (it re-synthesizes from the raw
  // record), so any divergence resolves in favor of Python.
  const STABLE_KW = ['login','signin','sign-in','submit','logon','log-in','log_in',
    'btn-primary','loginbutton','submitbutton','loginbtn','auth-submit',
    'auth-button','btn-login','primary-btn','download','clickable','video','row'];
  const HASHED_RE = /^(sc-[a-z0-9]{6,}-?\d*|css-[a-z0-9]{5,}|jsx-\d{8,}|_[A-Za-z]{1,3}_[a-z0-9]{5,}|[A-Za-z]{4,}_[a-z0-9]{5,}|[A-Z][A-Za-z]+-[a-z]+-\d+|[A-Z][A-Za-z]{1,3}\d{6,}|jss\d+)$/;
  function looksHashed(s) {
    if (!s) return true;
    if (HASHED_RE.test(s)) return true;
    if (s.length >= 6 && s.length <= 14 && /^[A-Za-z0-9]+$/.test(s) && /[A-Z]/.test(s) && /[a-z]/.test(s)) {
      const v = (s.match(/[aeiou]/gi) || []).length / s.length;
      if (v < 0.15 || v > 0.6) return true;
    }
    return false;
  }
  function synth(rec) {
    if (!rec) return [];
    const out = [];
    const tag = (rec.tag || '').toLowerCase();
    if (rec.id && !looksHashed(rec.id)) out.push('#' + rec.id);
    if (rec.testid) out.push("[data-testid='" + rec.testid + "']");
    if (rec.name && tag) out.push(tag + "[name='" + rec.name + "']");
    if (tag === 'input' && ['email','password','submit'].includes(rec.type)) out.push("input[type='" + rec.type + "']");
    if (['username','email','current-password','new-password'].includes(rec.autocomplete)) {
      out.push("input[autocomplete='" + rec.autocomplete + "']");
    }
    if (rec.ariaLabel && rec.ariaLabel.length < 60) {
      out.push("[aria-label='" + rec.ariaLabel.replace(/'/g, "\\'") + "']");
    }
    // Class hint
    if (rec.cls) {
      for (const c of rec.cls.split(/\s+/)) {
        if (looksHashed(c)) continue;
        if (STABLE_KW.some(kw => c.toLowerCase().includes(kw))) {
          out.push((tag || '*') + '.' + c);
          break;
        }
      }
    }
    // data-href / data-url for download rows
    for (const a of ['dataHref','dataUrl','dataSrc','dataDownload']) {
      if (rec[a]) {
        const attr = a.replace(/([A-Z])/g, '-$1').toLowerCase();
        out.push((tag || '*') + '[' + attr + ']');
        break;
      }
    }
    if (rec.text && rec.text.length < 40 && ['button','a','div','span','li'].includes(tag)) {
      out.push(tag + ":has-text('" + rec.text.replace(/'/g, "\\'") + "')");
    }
    return [...new Set(out)];
  }

  // ── Hover overlay ────────────────────────────────────────────────────
  // Single absolutely-positioned element that follows the mouse target.
  // pointer-events:none so it never intercepts user clicks itself.
  const hover = document.createElement('div');
  hover.id = '__pw_teach_hover';
  hover.style.cssText = `
    position:fixed; pointer-events:none; z-index:2147483646;
    border:2px dashed #ff4d8f; border-radius:3px;
    background:rgba(255,77,143,0.06); display:none;
    transition:all .05s ease-out;`;
  document.documentElement.appendChild(hover);
  const hoverLabel = document.createElement('div');
  hoverLabel.style.cssText = `
    position:fixed; pointer-events:none; z-index:2147483647;
    background:#ff4d8f; color:#fff; font:11px/1.4 system-ui,sans-serif;
    padding:3px 6px; border-radius:3px; max-width:380px;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
    display:none;`;
  document.documentElement.appendChild(hoverLabel);

  function rectFor(el) {
    try { return el.getBoundingClientRect(); } catch (e) { return null; }
  }
  function elementToRecord(el) {
    const _attr = (n) => { try { return el.getAttribute(n) || ''; } catch (e) { return ''; } };
    return {
      tag: (el.tagName || '').toLowerCase(),
      id: el.id || '',
      name: el.name || '',
      type: el.type || '',
      cls: (typeof el.className === 'string' ? el.className : '') || '',
      text: (el.innerText || el.textContent || el.value || '').slice(0, 100).trim(),
      href: _attr('href'), role: _attr('role'),
      autocomplete: _attr('autocomplete'),
      placeholder: _attr('placeholder'),
      ariaLabel: _attr('aria-label'),
      testid: _attr('data-testid') || _attr('data-test') || _attr('data-cy'),
      dataHref: _attr('data-href'), dataUrl: _attr('data-url'),
      dataSrc: _attr('data-src'), dataDownload: _attr('data-download'),
    };
  }
  document.addEventListener('mousemove', (e) => {
    if (panel && panel.contains(e.target)) { hover.style.display = 'none'; hoverLabel.style.display='none'; return; }
    const t = e.target;
    if (!t || t === hover || t === hoverLabel) return;
    const r = rectFor(t);
    if (!r || r.width < 1 || r.height < 1) return;
    hover.style.cssText += `display:block; left:${r.left}px; top:${r.top}px; width:${r.width}px; height:${r.height}px;`;
    const sels = synth(elementToRecord(t));
    hoverLabel.textContent = sels[0] || ('<' + (t.tagName||'?').toLowerCase() + '>');
    hoverLabel.style.cssText += `display:block; left:${r.left}px; top:${Math.max(0, r.top - 22)}px;`;
  }, true);
  document.addEventListener('mouseout', () => {
    hover.style.display = 'none'; hoverLabel.style.display = 'none';
  }, true);

  // ── Click interception ──────────────────────────────────────────────
  // Capture phase so we run BEFORE the site's own listeners. If teach_only
  // is on, swallow the event entirely. Either way, push to events so the
  // log fills.
  //
  // v3.43.22: shift-click is a per-click pass-through. Recording happens
  // normally (so the selector is captured), but preventDefault is NOT
  // called so the page's own click handler runs. Use case: two-step
  // download flows where the first button opens a modal containing the
  // resolution choices (brazzers, bangbros, filthykings). Without
  // shift-click the modal never opens because the trigger click is
  // swallowed, leaving the user unable to capture the second step.
  // Visual feedback: pink = recorded only (current), green = live mode
  // (current), cyan = shift-through (new).
  document.addEventListener('click', (e) => {
    if (panel && panel.contains(e.target)) return;  // panel clicks are normal
    // Record into our local list AND the recorder's __pwrec_clicks so the
    // server-side harvest at commit time gets everything.
    const rec = elementToRecord(e.target);
    rec.url = String(window.location.href || '');
    rec.ts = Date.now();
    // v3.43.22: mark the record with the modifier state so consumers can
    // tell at commit time which clicks were intended as triggers (shift)
    // vs end-of-flow downloads (no shift). Doesn't change the selector
    // shape — pure metadata.
    rec.shift = !!e.shiftKey;
    state.events.push(rec);
    // Mirror to recorder buffer if it exists
    if (window.__pwrec_clicks) window.__pwrec_clicks.push(rec);
    if (state.teach_only && !e.shiftKey) {
      // Default teach-only: block the click entirely.
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      flash(e.target, '#ff4d8f');  // pink: recorded only
    } else if (state.teach_only && e.shiftKey) {
      // v3.43.22: shift-click in teach-only mode = pass-through for
      // this single click. Lets the user open modals / trigger
      // dropdowns mid-teach without leaving teach-only mode. The
      // synthesized selector is recorded the same way as a normal
      // teach-only click.
      flash(e.target, '#06b6d4');  // cyan: shift-through
    } else {
      // Live mode: clicks pass through; record only.
      flash(e.target, '#4ade80');  // green for live-click
    }
    renderEvents();
  }, true);
  function flash(el, color) {
    try {
      const old = el.style.outline;
      el.style.outline = `3px solid ${color}`;
      el.style.outlineOffset = '2px';
      setTimeout(() => { el.style.outline = old; }, 350);
    } catch (e) {}
  }

  // ── Floating panel ──────────────────────────────────────────────────
  let panel;
  function buildPanel() {
    // Phase 24.6: inject a tiny stylesheet once for mobile-only overrides.
    // The teach panel is fixed-positioned with inline style, so media
    // queries need !important specificity to take effect.
    if (!document.getElementById('__pw_teach_mobile_css')) {
      const st = document.createElement('style');
      st.id = '__pw_teach_mobile_css';
      st.textContent = `
        @media (max-width: 640px), (pointer: coarse) {
          #__pw_teach_panel {
            width: calc(100vw - 16px) !important;
            right: 8px !important; left: 8px !important;
            top: 8px !important; max-height: 70vh !important;
            font-size: 14px !important;
          }
          #__pw_teach_panel button {
            min-height: 44px !important; font-size: 13px !important;
            padding: 10px 14px !important;
          }
          #__pw_teach_panel #pw_teach_autoprop,
          #__pw_teach_panel #pw_teach_whatif {
            min-height: 38px !important; font-size: 12px !important;
          }
          /* Bigger checkbox for "Teach-only" toggle */
          #__pw_teach_panel input[type="checkbox"] {
            width: 20px !important; height: 20px !important;
          }
          /* Event log entries — bigger pin checkboxes */
          #__pw_teach_panel .pw_teach_pin {
            width: 22px !important; height: 22px !important;
          }
        }`;
      document.head.appendChild(st);
    }
    panel = document.createElement('div');
    panel.id = '__pw_teach_panel';
    panel.style.cssText = `
      position:fixed; top:12px; right:12px; z-index:2147483647;
      width:380px; max-height:80vh; overflow:hidden;
      background:#1a1a1f; color:#e6e6e9; border:1px solid #3a3a44;
      border-radius:8px; box-shadow:0 8px 32px rgba(0,0,0,0.6);
      font:13px/1.4 system-ui,-apple-system,sans-serif;
      display:flex; flex-direction:column;`;
    panel.innerHTML = `
      <div id="pw_teach_hdr" style="
        padding:10px 12px; background:#252530; border-bottom:1px solid #3a3a44;
        display:flex; align-items:center; gap:8px; cursor:move; user-select:none">
        <span style="font-weight:600; flex:1">🧠 Teach Mode</span>
        <span id="pw_teach_count" style="font-size:11px; color:#888"></span>
      </div>
      <div style="padding:10px 12px; border-bottom:1px solid #2a2a34">
        <label style="display:flex; align-items:center; gap:6px; cursor:pointer; font-size:12px">
          <input type="checkbox" id="pw_teach_only" checked>
          <span>Teach-only (block real clicks)</span>
        </label>
        <div style="font-size:10px; color:#888; margin-top:4px; padding-left:18px; line-height:1.5">
          Pink = recorded only · Green = clicked through<br>
          <b style="color:#06b6d4">Cyan = shift-click pass-through</b> (records AND opens modals)
        </div>
      </div>
      <div id="pw_teach_log" style="
        flex:1; overflow-y:auto; padding:8px; max-height:50vh;
        font:11px/1.4 ui-monospace,Menlo,Consolas,monospace"></div>
      <div style="padding:8px 12px; background:#1f1f28; border-top:1px solid #3a3a44; display:flex; gap:6px; align-items:center">
        <button id="pw_teach_autoprop" style="background:#7c3aed; color:#fff; border:0; padding:6px 10px; border-radius:4px; cursor:pointer; font-size:11px; font-weight:600" title="Scan the page for likely download candidates and pre-record them. Pure heuristic — no AI. Review the proposed picks before committing.">🔍 Auto-propose</button>
        <button id="pw_teach_whatif" style="background:#0ea5e9; color:#fff; border:0; padding:6px 10px; border-radius:4px; cursor:pointer; font-size:11px; font-weight:600" title="Highlight all elements that the currently-picked selectors would match on this page.">👁 What-if</button>
        <button id="pw_teach_aisuggest" style="background:#ec4899; color:#fff; border:0; padding:6px 10px; border-radius:4px; cursor:pointer; font-size:11px; font-weight:600" title="Send the current DOM + a screenshot to the self-hosted AI to get selector suggestions. Self-hosted only — never leaves your network.">🪄 AI suggest</button>
        <button id="pw_teach_diffrepair" style="background:#f97316; color:#fff; border:0; padding:6px 10px; border-radius:4px; cursor:pointer; font-size:11px; font-weight:600" title="When the site redesigns and your learned selectors stop matching, ask the AI to find structurally-equivalent replacements in the current DOM.">🔧 Diff Repair</button>
        <span id="pw_teach_autoprop_status" style="flex:1; color:#888; font-size:10px"></span>
      </div>
      <div style="padding:10px 12px; background:#252530; border-top:1px solid #3a3a44; display:flex; gap:6px; flex-wrap:wrap">
        <button id="pw_teach_pick" style="flex:1; min-width:80px; background:#a855f7; color:#fff; border:0; padding:8px; border-radius:4px; cursor:pointer; font-weight:600" title="Hover-pick: point at any element on the page and click to capture its selector. ESC cancels.">🎯 Pick</button>
        <button id="pw_teach_verify" style="flex:1; min-width:80px; background:#4a9eff; color:#fff; border:0; padding:8px; border-radius:4px; cursor:pointer; font-weight:600" title="Test the picked selectors against the live page. Shows which element matched and the URL that would be downloaded.">Verify</button>
        <button id="pw_teach_test" style="flex:1; min-width:80px; background:#f59e0b; color:#fff; border:0; padding:8px; border-radius:4px; cursor:pointer; font-weight:600" title="Verify, then actually fetch 2 MB of the URL to confirm it's real video bytes. Runs automatically before Commit.">🧪 Test</button>
        <button id="pw_teach_commit" style="flex:1; min-width:80px; background:#22c55e; color:#fff; border:0; padding:8px; border-radius:4px; cursor:pointer; font-weight:600" title="Commit selectors. Runs a Test Download first; on success, saves and closes. Hold Shift while clicking to skip the test (not recommended).">Commit</button>
        <button id="pw_teach_save_template" style="flex:1; min-width:80px; background:#0ea5e9; color:#fff; border:0; padding:8px; border-radius:4px; cursor:pointer; font-weight:600" title="Save the current selectors as a reusable template that auto-suggests on future sites matching the URL pattern. Available anytime; a banner also offers it automatically after a successful commit.">💾 Template</button>
        <button id="pw_teach_cancel" style="flex:1; min-width:80px; background:#3a3a44; color:#e6e6e9; border:0; padding:8px; border-radius:4px; cursor:pointer">Cancel</button>
      </div>
      <div id="pw_teach_status" style="display:none; padding:8px 12px; font-size:11px; border-top:1px solid #3a3a44"></div>`;
    document.documentElement.appendChild(panel);

    // Toggle
    panel.querySelector('#pw_teach_only').addEventListener('change', (e) => {
      state.teach_only = e.target.checked;
      showStatus(state.teach_only ? 'Teach-only ON — clicks blocked' : 'Live mode — clicks pass through', '#888');
    });

    // Buttons
    panel.querySelector('#pw_teach_pick').addEventListener('click', () => togglePickMode());
    panel.querySelector('#pw_teach_verify').addEventListener('click', () => verify());
    panel.querySelector('#pw_teach_test').addEventListener('click', () => testDownload());
    panel.querySelector('#pw_teach_commit').addEventListener('click', (ev) => commit(ev.shiftKey));
    panel.querySelector('#pw_teach_save_template').addEventListener('click', () => openSaveTemplateDialog());
    panel.querySelector('#pw_teach_cancel').addEventListener('click', () => cancel());
    panel.querySelector('#pw_teach_autoprop').addEventListener('click', () => autoPropose());
    panel.querySelector('#pw_teach_whatif').addEventListener('click', () => whatIf());
    panel.querySelector('#pw_teach_aisuggest').addEventListener('click', () => aiSuggest());
    panel.querySelector('#pw_teach_diffrepair').addEventListener('click', () => diffRepair());

    // Drag the header
    let drag = null;
    panel.querySelector('#pw_teach_hdr').addEventListener('mousedown', (e) => {
      const r = panel.getBoundingClientRect();
      drag = {dx: e.clientX - r.left, dy: e.clientY - r.top};
      e.preventDefault();
    });
    document.addEventListener('mousemove', (e) => {
      if (!drag) return;
      panel.style.left = (e.clientX - drag.dx) + 'px';
      panel.style.top = (e.clientY - drag.dy) + 'px';
      panel.style.right = 'auto';
    });
    document.addEventListener('mouseup', () => { drag = null; });
  }

  function renderEvents() {
    if (!panel) return;
    const log = panel.querySelector('#pw_teach_log');
    const count = panel.querySelector('#pw_teach_count');
    // Header shows event count AND total picks across all events, so the
    // user has feedback when teaching multiple resolutions in one session.
    const totalPicks = Object.values(state.chosen_selectors)
      .reduce((n, list) => n + (list && list.length ? 1 : 0), 0);
    count.textContent = state.events.length + ' event' +
      (state.events.length === 1 ? '' : 's') +
      (totalPicks ? ' · ' + totalPicks + ' picked' : '');
    if (state.events.length === 0) {
      log.innerHTML = '<div style="color:#666; font-style:italic; padding:12px; text-align:center">' +
        'Click anything on the page to record it.<br><br>' +
        'For multiple resolutions (e.g. 8K, 6K, 4K), click each in turn — they\'re saved in the order you click. ' +
        'Higher-quality first.</div>';
      return;
    }
    log.innerHTML = '';
    state.events.slice(-20).reverse().forEach((rec, displayIdx) => {
      const realIdx = state.events.length - 1 - displayIdx;
      const sels = synth(rec);
      const chosen = state.chosen_selectors[realIdx] || (sels.length ? [sels[0]] : []);
      state.chosen_selectors[realIdx] = chosen;
      // Phase 19.fix: pinned_set replaces pinned_event_idx so multiple
      // events can be expanded at once. Workflow for teaching a
      // resolution ladder:
      //   1. Click the 8K download button (teach-only blocks the click)
      //   2. Expand the recorded event in the panel, check the row_selector
      //   3. Click the 6K download button
      //   4. Expand that event, check its row_selector
      //   5. Repeat for 4K, 1080p, etc.
      //   6. Hit Commit — all selectors saved in priority order
      const pinnedSet = state.pinned_set || (state.pinned_set = new Set());
      const expanded = pinnedSet.has(realIdx);
      const row = document.createElement('div');
      row.style.cssText = 'border-bottom:1px solid #2a2a34; padding:6px; cursor:pointer';
      const tag = rec.tag || '?';
      const text = (rec.text || '').slice(0, 40);
      // Priority badge: events with chosen selectors get a numbered badge
      // showing the order they'll be tried by the worker. Order is by
      // realIdx ascending = the order the user clicked them.
      const orderedPicked = Object.keys(state.chosen_selectors)
        .map(Number)
        .filter((k) => (state.chosen_selectors[k] || []).length)
        .sort((a, b) => a - b);
      const priority = orderedPicked.indexOf(realIdx);
      const badge = priority >= 0
        ? `<span style="background:#22c55e;color:#fff;padding:1px 6px;border-radius:8px;font-size:9px;font-weight:600">#${priority+1}</span>`
        : '';
      const headerHtml = `
        <div style="display:flex; gap:6px; align-items:start">
          <span style="color:#888">${expanded ? '▾' : '▸'}</span>
          <div style="flex:1; overflow:hidden">
            <div style="color:#4a9eff">${badge} &lt;${tag}&gt;${text ? ' "' + text + '"' : ''}</div>
            <div style="color:#22c55e; font-size:10px">${chosen[0] || sels[0] || '<no selector>'}</div>
          </div>
        </div>`;
      let bodyHtml = '';
      if (expanded && sels.length) {
        bodyHtml = '<div style="margin:6px 0 0 16px; padding:6px; background:#0e0e14; border-radius:4px">';
        sels.forEach((s) => {
          const on = chosen.includes(s);
          bodyHtml += `<label style="display:flex; gap:4px; padding:2px; cursor:pointer; font-size:10px">
            <input type="checkbox" data-sel="${s.replace(/"/g,'&quot;')}" data-idx="${realIdx}" ${on?'checked':''}>
            <span style="color:${on?'#22c55e':'#888'}">${s}</span>
          </label>`;
        });
        bodyHtml += '</div>';
      }
      row.innerHTML = headerHtml + bodyHtml;
      row.addEventListener('click', (e) => {
        if (e.target.tagName === 'INPUT') return;
        // Toggle just THIS event's expanded state — independent of others.
        if (pinnedSet.has(realIdx)) pinnedSet.delete(realIdx);
        else pinnedSet.add(realIdx);
        renderEvents();
      });
      // Wire up the checkboxes after render
      log.appendChild(row);
    });
    // Bind checkbox changes
    log.querySelectorAll('input[type="checkbox"][data-sel]').forEach((cb) => {
      cb.addEventListener('change', (e) => {
        const idx = parseInt(e.target.dataset.idx);
        const sel = e.target.dataset.sel;
        const list = state.chosen_selectors[idx] || [];
        if (e.target.checked) { if (!list.includes(sel)) list.push(sel); }
        else { const i = list.indexOf(sel); if (i >= 0) list.splice(i, 1); }
        state.chosen_selectors[idx] = list;
        renderEvents();
      });
    });
  }

  function showStatus(msg, color) {
    const s = panel.querySelector('#pw_teach_status');
    s.style.display = 'block';
    s.style.color = color || '#e6e6e9';
    s.textContent = msg;
    setTimeout(() => { s.style.display = 'none'; }, 4000);
  }

  // ── Server round-trips ──────────────────────────────────────────────
  // The site_id and base URL are injected by Python at install time
  // because we don't have window.location-equivalents that survive
  // navigation reliably.
  function endpoint(name) {
    const base = window.__pw_teach_base || '';
    const sid = window.__pw_teach_sid || '';
    return `${base}/api/sites/${sid}/teach_${name}`;
  }
  async function postJson(url, body) {
    try {
      const r = await fetch(url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body || {}),
      });
      return await r.json();
    } catch (e) { return {ok: false, error: String(e)}; }
  }
  async function verify() {
    const picks = collectPicks();
    const total = picks.row_selectors.length + picks.trigger_selectors.length;
    if (total === 0) {
      showStatus('Nothing picked yet — use 🎯 Pick to hover-select, or click events in the log', '#fbbf24'); return;
    }
    showStatus(`Verifying ${total} selector${total===1?'':'s'}…`, '#4a9eff');
    const r = await postJson(endpoint('verify'), {selectors: picks});
    if (r.ok) {
      const chainNote = picks.row_selectors.length > 1
        ? ` · chain ${picks.row_selectors.length}`
        : '';
      // v3.43.0: show extracted URL when present so user can sanity-check
      // visually before testing.
      let extra = '';
      if (r.extracted_url) {
        const short = r.extracted_url.length > 100
          ? r.extracted_url.slice(0, 80) + '…' + r.extracted_url.slice(-15)
          : r.extracted_url;
        extra = ` · URL via ${r.url_via || '?'}: ${short}`;
      } else if (r.via_learned) {
        extra = ' · no URL (click-and-capture path)';
      }
      showStatus(`✓ Matched [${r.match_text || '?'}] score=${r.score}${chainNote}${extra}`, '#22c55e');
    } else {
      showStatus('✗ ' + (r.error || 'no match found with picked selectors'), '#ef4444');
    }
  }
  // v3.43.0: Test Download — runs verify AND fetches 2 MB of the resulting
  // URL to confirm video magic bytes. Used as a gate before commit.
  // v3.43.9: also stashes the result in state.lastTestResult so the
  // save-as-template flow can confirm the selectors actually worked.
  async function testDownload() {
    const picks = collectPicks();
    const total = picks.row_selectors.length + picks.trigger_selectors.length;
    if (total === 0) {
      showStatus('Nothing to test — pick selectors first', '#fbbf24'); return false;
    }
    showStatus('Testing download — fetching 2 MB from extracted URL…', '#f59e0b');
    const r = await postJson(endpoint('test_download'), {selectors: picks});
    state.lastTestResult = r;
    if (!r.ok) {
      showStatus('✗ Test failed: ' + (r.error || 'unknown'), '#ef4444');
      return false;
    }
    if (r.kind === 'click_and_capture') {
      showStatus(`⚠ ${r.note}`, '#fbbf24');
      return 'click_capture';
    }
    const status = r.http_status || 0;
    const ctype = r.content_type || '?';
    const sizeMB = r.content_length
      ? (r.content_length / (1024*1024)).toFixed(1) + ' MB'
      : '?';
    if (r.http_error) {
      showStatus(`✗ Fetch error: ${r.http_error}`, '#ef4444');
      return false;
    }
    if (status < 200 || status >= 300) {
      showStatus(`✗ HTTP ${status} from ${r.extracted_url.slice(0,60)}…`, '#ef4444');
      return false;
    }
    if (!r.magic_ok) {
      showStatus(`✗ HTTP 200 but bytes don't look like video (${r.magic_kind}). Got Content-Type: ${ctype}. URL may be returning HTML/JSON instead of the file — check that you picked the right element.`, '#ef4444');
      return false;
    }
    showStatus(`✓ Test PASSED — ${r.magic_kind.toUpperCase()} container, ${sizeMB}, ${ctype}`, '#22c55e');
    return true;
  }
  async function commit(skipTest) {
    const picks = collectPicks();
    const total = picks.row_selectors.length + picks.trigger_selectors.length;
    if (total === 0) {
      showStatus('Nothing to commit — pick selectors first', '#fbbf24'); return;
    }
    // v3.43.0: gate commit on a successful Test Download. Shift-click bypasses
    // for power users (e.g. when the test URL is rate-limited but selectors
    // are known good).
    let testOutcome = null;
    if (!skipTest) {
      testOutcome = await testDownload();
      if (testOutcome === false) {
        return;
      }
    }
    showStatus(skipTest ? 'Committing (test skipped — Shift override)…' : 'Test passed. Committing…', '#4a9eff');
    const r = await postJson(endpoint('commit'), {selectors: picks, events: state.events});
    if (!r.ok) {
      showStatus('✗ Commit failed: ' + (r.error || 'unknown'), '#ef4444');
      return;
    }
    // v3.43.9: instead of immediately closing, offer the save-as-template
    // banner. The user can save, dismiss, or do nothing — closing the
    // browser still happens after either choice (or a 30s timeout).
    if (testOutcome === true) {
      // Real test pass — high confidence the selectors work
      showSaveTemplateBanner(true);
    } else if (testOutcome === 'click_capture') {
      // Click-capture path; couldn't pre-verify
      showSaveTemplateBanner(false);
    } else {
      // Shift-override commit — no test happened
      showStatus('✓ Saved. Closing browser…', '#22c55e');
      setTimeout(() => { try { window.close(); } catch (e) {} }, 1200);
    }
  }
  // v3.43.9: offer a one-click banner to save the current selectors as
  // a reusable user template. Auto-shown after successful commit, also
  // openable from the 💾 Template button at any time.
  function showSaveTemplateBanner(testPassed) {
    const status = document.getElementById('pw_teach_status');
    if (!status) return;
    const verified = testPassed
      ? '<span style="color:#22c55e">✓ Selectors verified via Test Download.</span>'
      : '<span style="color:#fbbf24">⚠ Click-and-capture path — couldn\'t pre-verify bytes.</span>';
    status.style.display = 'block';
    status.style.background = '#1e3a5f';
    status.style.color = '#e6e6e9';
    status.innerHTML = `
      <div style="font-size:12px; line-height:1.5">
        <div style="font-weight:600; margin-bottom:4px">💾 Save as template?</div>
        <div style="color:#9ca3af; font-size:11px; margin-bottom:6px">${verified}</div>
        <div style="color:#9ca3af; font-size:11px; margin-bottom:8px">
          Saved templates auto-suggest on future sites matching the URL pattern. Skip if this is a one-off teach.
        </div>
        <div style="display:flex; gap:6px">
          <button id="pw_save_tpl_yes" style="flex:1; background:#0ea5e9; color:#fff; border:0; padding:6px; border-radius:3px; cursor:pointer; font-weight:600">Save template…</button>
          <button id="pw_save_tpl_no" style="flex:1; background:#3a3a44; color:#e6e6e9; border:0; padding:6px; border-radius:3px; cursor:pointer">Skip, just close</button>
        </div>
      </div>
    `;
    status.querySelector('#pw_save_tpl_yes').addEventListener('click', () => openSaveTemplateDialog());
    status.querySelector('#pw_save_tpl_no').addEventListener('click', () => {
      showStatus('✓ Saved. Closing browser…', '#22c55e');
      setTimeout(() => { try { window.close(); } catch (e) {} }, 800);
    });
    // Don't auto-close — wait for the user to choose. After 60s of no
    // interaction, fall through to closing.
    setTimeout(() => {
      if (status.querySelector('#pw_save_tpl_yes')) {
        showStatus('✓ Saved. Closing browser…', '#22c55e');
        setTimeout(() => { try { window.close(); } catch (e) {} }, 800);
      }
    }, 60000);
  }
  // v3.43.9: modal for naming + describing the new template. Lives in
  // the teach panel rather than the main UI because the user is inside
  // the takeover browser; the main UI isn't visible.
  function openSaveTemplateDialog() {
    const picks = collectPicks();
    if (picks.row_selectors.length + picks.trigger_selectors.length === 0) {
      showStatus('Nothing to save — pick selectors first', '#fbbf24');
      return;
    }
    // Generate sensible defaults from the current page URL
    const pageUrl = location.href;
    let suggestedPattern = '';
    let suggestedName = 'My template';
    try {
      const u = new URL(pageUrl);
      // Pattern: escape dots in the domain, drop subdomain prefix
      // (so venus.wowgirls.com -> wowgirls\.com)
      const parts = u.hostname.split('.');
      const domain = parts.length >= 2 ? parts.slice(-2).join('.') : u.hostname;
      suggestedPattern = domain.replace(/\./g, '\\\\.');
      // Name: titlecased domain
      const baseName = (parts.length >= 2 ? parts[parts.length - 2] : u.hostname);
      suggestedName = baseName.charAt(0).toUpperCase() + baseName.slice(1) + ' template';
    } catch (e) { /* ignore */ }
    // Build a modal overlay inside the takeover page. We can't use the
    // main UI's modal system (different document); just inject one.
    const existing = document.getElementById('__pw_save_tpl_modal');
    if (existing) existing.remove();
    const m = document.createElement('div');
    m.id = '__pw_save_tpl_modal';
    m.style.cssText = `
      position:fixed; inset:0; background:rgba(0,0,0,0.6); z-index:2147483647;
      display:flex; align-items:center; justify-content:center;
      font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif`;
    m.innerHTML = `
      <div style="background:#1e1e26; color:#e6e6e9; border:1px solid #3a3a44; border-radius:8px; padding:18px; width:480px; max-width:90vw; box-shadow:0 12px 48px rgba(0,0,0,0.6)">
        <div style="font-size:14px; font-weight:600; margin-bottom:4px">💾 Save as template</div>
        <div style="font-size:11px; color:#9ca3af; margin-bottom:14px">Saves the current selectors as a reusable template. Auto-suggests on future sites matching the URL pattern below.</div>

        <div style="margin-bottom:12px">
          <label style="display:block; font-size:11px; color:#9ca3af; margin-bottom:4px">Template name</label>
    // AUDIT v3.43.47: route both through esc() before interpolation —
    // u.hostname is permissive enough that pathological URLs could
    // carry attribute-breaking chars. esc() is defined later in this
    // file (line ~827); it handles all standard HTML escapes.
          <input id="pw_tpl_name" type="text" value="${esc(suggestedName)}" style="width:100%; padding:6px 8px; background:#252530; border:1px solid #3a3a44; border-radius:3px; color:#e6e6e9; font-size:13px; box-sizing:border-box">
        </div>

        <div style="margin-bottom:12px">
          <label style="display:block; font-size:11px; color:#9ca3af; margin-bottom:4px">Description (shown in the template picker)</label>
          <input id="pw_tpl_desc" type="text" value="Saved from a successful teach session" style="width:100%; padding:6px 8px; background:#252530; border:1px solid #3a3a44; border-radius:3px; color:#e6e6e9; font-size:13px; box-sizing:border-box">
        </div>

        <div style="margin-bottom:14px">
          <label style="display:block; font-size:11px; color:#9ca3af; margin-bottom:4px">URL pattern (regex, matched against URLs to auto-suggest this template)</label>
          <input id="pw_tpl_pattern" type="text" value="${esc(suggestedPattern)}" placeholder="e.g. mysite\\\\.com  (leave blank to disable auto-suggest)" style="width:100%; padding:6px 8px; background:#252530; border:1px solid #3a3a44; border-radius:3px; color:#e6e6e9; font-family:ui-monospace,Menlo,Consolas,monospace; font-size:12px; box-sizing:border-box">
          <div style="font-size:10px; color:#6b7280; margin-top:4px; line-height:1.4">Regex. Escape dots with <code>\\\\.</code>. Auto-generated from the page domain; edit to match more or fewer URLs. Leave empty if you only want this template available via the picker.</div>
        </div>

        <details style="margin-bottom:14px; font-size:11px; color:#9ca3af">
          <summary style="cursor:pointer; user-select:none">Show selectors being saved</summary>
          <pre style="background:#252530; padding:8px; border-radius:3px; margin-top:6px; overflow-x:auto; font-size:10px; color:#cbd5e0; white-space:pre-wrap; word-break:break-all">${esc(JSON.stringify(picks, null, 2))}</pre>
        </details>

        <div id="pw_tpl_error" style="display:none; color:#ef4444; font-size:11px; margin-bottom:8px"></div>

        <div style="display:flex; gap:8px; justify-content:flex-end">
          <button id="pw_tpl_cancel" style="background:#3a3a44; color:#e6e6e9; border:0; padding:8px 14px; border-radius:3px; cursor:pointer">Cancel</button>
          <button id="pw_tpl_save" style="background:#0ea5e9; color:#fff; border:0; padding:8px 14px; border-radius:3px; cursor:pointer; font-weight:600">Save template</button>
        </div>
      </div>
    `;
    document.documentElement.appendChild(m);
    m.querySelector('#pw_tpl_cancel').addEventListener('click', () => m.remove());
    m.querySelector('#pw_tpl_save').addEventListener('click', () => doSaveTemplate(m));
    // Esc dismisses
    function onEsc(e) {
      if (e.key === 'Escape') {
        m.remove();
        document.removeEventListener('keydown', onEsc, true);
      }
    }
    document.addEventListener('keydown', onEsc, true);
  }
  // Escape HTML for safe injection. Tiny helper, defined here because the
  // takeover panel is a separate document from the main UI.
  function esc(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
    }[c]));
  }
  async function doSaveTemplate(modal) {
    const name = (modal.querySelector('#pw_tpl_name').value || '').trim();
    const description = (modal.querySelector('#pw_tpl_desc').value || '').trim();
    const patternRaw = (modal.querySelector('#pw_tpl_pattern').value || '').trim();
    const errEl = modal.querySelector('#pw_tpl_error');
    errEl.style.display = 'none';
    if (!name) {
      errEl.textContent = 'Name is required.';
      errEl.style.display = 'block';
      return;
    }
    const picks = collectPicks();
    const learned = {download: {
      row_selectors: picks.row_selectors,
      trigger_selectors: picks.trigger_selectors,
    }};
    // Pass url_attribute through (may be string OR list)
    if (picks.url_attribute !== undefined && picks.url_attribute !== null) {
      learned.download.url_attribute = picks.url_attribute;
    }
    const payload = {
      name: name,
      description: description || 'Saved from a successful teach session',
      patterns: patternRaw ? [patternRaw] : [],
      learned: learned,
      source: 'user_teach',
    };
    // The save endpoint is teach-scoped (no CSRF / no auth) because the
    // takeover browser can't carry the main UI's session cookie.
    try {
      const r = await postJson(endpoint('save_template'), payload);
      if (!r.ok) {
        errEl.textContent = 'Save failed: ' + (r.error || 'unknown');
        errEl.style.display = 'block';
        return;
      }
      modal.remove();
      showStatus(`✓ Template "${name}" saved. Closing browser…`, '#22c55e');
      setTimeout(() => { try { window.close(); } catch (e) {} }, 1500);
    } catch (e) {
      errEl.textContent = 'Network error: ' + e;
      errEl.style.display = 'block';
    }
  }
  async function cancel() {
    if (!confirm('Cancel teaching session? All unconfirmed selectors will be discarded.')) return;
    await postJson(endpoint('cancel'), {});
    try { window.close(); } catch (e) {}
  }

  // ── v3.43.0: Hover-pick mode ──────────────────────────────────────
  // Click 🎯 Pick to enter; mouse hover draws an outline; click captures.
  // ESC or clicking 🎯 Pick again exits. The captured element joins
  // state.events as a synthetic recorded click — same downstream flow
  // as a real click, so the existing selector synth and merge logic
  // handles it without special-casing.
  const pickMode = { active: false, overlay: null, lastEl: null };
  function togglePickMode() {
    if (pickMode.active) { exitPickMode(); return; }
    enterPickMode();
  }
  function enterPickMode() {
    pickMode.active = true;
    panel.querySelector('#pw_teach_pick').style.background = '#dc2626';
    panel.querySelector('#pw_teach_pick').textContent = '✕ Exit Pick';
    // Outline overlay — a single absolutely-positioned div we move
    // around to hug the hovered element. Cheaper than per-element CSS.
    const ov = document.createElement('div');
    ov.id = '__pw_pick_outline';
    ov.style.cssText = `
      position:fixed; pointer-events:none; z-index:2147483646;
      border:2px solid #a855f7; background:rgba(168,85,247,0.12);
      box-shadow:0 0 0 1px rgba(0,0,0,0.4) inset;
      transition:all 30ms ease-out;
      font:11px ui-monospace,Menlo,Consolas,monospace;
      color:#fff; display:none`;
    const lbl = document.createElement('div');
    lbl.id = '__pw_pick_label';
    lbl.style.cssText = `
      position:absolute; top:-22px; left:0; background:#a855f7;
      color:#fff; padding:1px 6px; border-radius:3px;
      font:11px/1.4 ui-monospace,Menlo,Consolas,monospace;
      white-space:nowrap; max-width:80vw; overflow:hidden;
      text-overflow:ellipsis`;
    ov.appendChild(lbl);
    document.documentElement.appendChild(ov);
    pickMode.overlay = ov;
    document.addEventListener('mousemove', pickMove, true);
    document.addEventListener('click', pickClick, true);
    document.addEventListener('keydown', pickKey, true);
    showStatus('🎯 Pick mode: hover an element and click to capture · ESC to cancel', '#a855f7');
  }
  function exitPickMode() {
    pickMode.active = false;
    panel.querySelector('#pw_teach_pick').style.background = '#a855f7';
    panel.querySelector('#pw_teach_pick').textContent = '🎯 Pick';
    if (pickMode.overlay) { pickMode.overlay.remove(); pickMode.overlay = null; }
    pickMode.lastEl = null;
    document.removeEventListener('mousemove', pickMove, true);
    document.removeEventListener('click', pickClick, true);
    document.removeEventListener('keydown', pickKey, true);
  }
  function pickKey(e) {
    if (e.key === 'Escape') {
      exitPickMode();
      showStatus('Pick cancelled', '#888');
      e.preventDefault(); e.stopPropagation();
    }
  }
  function pickMove(e) {
    // Walk through points to find an element BEHIND our overlay/panel
    // — the overlay has pointer-events:none, but the teach panel itself
    // captures the mouse and we don't want to "pick" the panel.
    if (e.target && (e.target.closest && e.target.closest('#__pw_teach_panel'))) {
      pickMode.overlay.style.display = 'none';
      return;
    }
    const el = e.target;
    if (!el || el === pickMode.lastEl) return;
    pickMode.lastEl = el;
    const r = el.getBoundingClientRect();
    const ov = pickMode.overlay;
    ov.style.display = 'block';
    ov.style.left = r.left + 'px';
    ov.style.top = r.top + 'px';
    ov.style.width = r.width + 'px';
    ov.style.height = r.height + 'px';
    // Label = tag + class hint, like DevTools picker
    const tag = (el.tagName || '').toLowerCase();
    const cls = (typeof el.className === 'string' && el.className)
      ? '.' + el.className.split(/\s+/).filter(Boolean).slice(0,2).join('.')
      : '';
    const id = el.id ? '#' + el.id : '';
    ov.querySelector('#__pw_pick_label').textContent =
      `${tag}${id}${cls}` + (el.innerText ? ' · "' + el.innerText.slice(0,40).trim() + '"' : '');
  }
  function pickClick(e) {
    if (!pickMode.active) return;
    // Don't capture clicks on our own panel
    if (e.target && (e.target.closest && e.target.closest('#__pw_teach_panel'))) return;
    e.preventDefault(); e.stopPropagation();
    const el = e.target;
    if (!el) { exitPickMode(); return; }
    // Build a synthetic click record using the same shape the recorder
    // produces — that way classify_download / synth / merge all work
    // unchanged. _info() captures ancestor URLs too, so a span pick
    // gets credit for its parent <a href>.
    const rec = window.__pwrec_info_for_pick
      ? window.__pwrec_info_for_pick(el)
      : null;
    let synthRec;
    if (rec) {
      synthRec = rec;
    } else {
      // Fallback: minimal record. Should never hit this if the recorder
      // installed correctly, but defensive.
      synthRec = {
        tag: (el.tagName || '').toLowerCase(),
        id: el.id || '', cls: (typeof el.className === 'string' ? el.className : ''),
        text: (el.innerText || '').slice(0,100).trim(),
        href: el.getAttribute && el.getAttribute('href') || '',
        url: location.href, ts: Date.now(),
      };
    }
    // Append to the recorder's clicks buffer so it merges with real clicks
    // in the events panel. Mark it as pick-originated for visual distinction.
    synthRec._via_pick = true;
    if (window.__pwrec_clicks) window.__pwrec_clicks.push(synthRec);
    state.events.push(synthRec);
    // Pre-select the synthesized selectors so commit picks them up.
    const sels = synth(synthRec);
    if (sels.length) {
      const idx = state.events.length - 1;
      state.pinned_set.add(idx);
      state.chosen_selectors[idx] = [sels[0]];
    }
    renderEvents();
    showStatus(`✓ Picked: ${synthRec.tag}${synthRec.id ? '#'+synthRec.id : ''} — selector ready`, '#22c55e');
    // Stay in pick mode so user can grab multiple elements in one pass
  }

  // ── Phase 23.5: Heuristic auto-propose ────────────────────────────
  // Scan the live DOM for elements that look like download candidates.
  // Rule-based, no AI. Each candidate gets a score; high-scoring ones
  // synthesize fake recorded events that the user can then verify or
  // commit just like real clicks. Saves the user from clicking every
  // resolution by hand on sites with consistent patterns.
  function autoPropose() {
    const statusEl = panel.querySelector('#pw_teach_autoprop_status');
    statusEl.textContent = 'Scanning…';
    statusEl.style.color = '#a78bfa';
    let candidates;
    try {
      candidates = scoreCandidates();
    } catch (e) {
      statusEl.textContent = '✗ ' + e.message;
      statusEl.style.color = '#ef4444';
      return;
    }
    if (!candidates.length) {
      statusEl.textContent = 'No likely download candidates found on this page.';
      statusEl.style.color = '#888';
      return;
    }
    // Take the top 5 — anything beyond is noise on most sites
    const top = candidates.slice(0, 5);
    // Synthesize "events" that look like real recorder captures
    let added = 0;
    for (const c of top) {
      // Don't duplicate elements already in the event log
      const exists = state.events.some(e =>
        e.text === c.text && e.tag === c.tag);
      if (exists) continue;
      const rec = {
        tag: c.tag, text: c.text, href: c.href,
        dataHref: c.dataHref, dataUrl: c.dataUrl,
        dataSrc: c.dataSrc, dataDownload: c.dataDownload,
        id: c.id, className: c.className,
        synthetic: true,    // marker so UI can tag these visibly
      };
      state.events.push(rec);
      // Pre-pin and pre-pick the best selector
      const realIdx = state.events.length - 1;
      const sels = synth(rec);
      if (sels.length) {
        state.chosen_selectors[realIdx] = [sels[0]];
        if (state.pinned_set) state.pinned_set.add(realIdx);
      }
      added++;
    }
    if (added > 0) {
      renderEvents();
      statusEl.textContent = `✓ Proposed ${added} candidate${added===1?'':'s'} (review picks above, then Verify or Commit)`;
      statusEl.style.color = '#22c55e';
    } else {
      statusEl.textContent = 'All likely candidates were already in the log.';
      statusEl.style.color = '#888';
    }
  }

  function scoreCandidates() {
    // v3.43.44: tiered scoring + ancestor context + filesize signal
    // + URL-pattern fingerprinting. Mirrors the Python
    // bulk_downloader/heuristic_scoring.py module — keep both
    // aligned. The JS version operates on live DOM; Python on
    // pasted HTML.
    const out = [];
    const seen = new Set();

    // Tiered resolution scoring (A)
    const RESOLUTION_TIERS = [
      [/\b(8\s*[kK]|7680\s*[x×]\s*4320|4320p?)\b/, 80, '8K'],
      [/\b(6\s*[kK]|5760\s*[x×]\s*3240)\b/, 70, '6K'],
      [/\b(4\s*[kK]|3840\s*[x×]\s*2160|2160p?)\b/, 60, '4K'],
      [/\b(2\s*[kK]|2560\s*[x×]\s*1440|1440p?|QHD)\b/, 50, '2K'],
      [/\b(1920\s*[x×]\s*1080|1080p?|Full\s*HD|FHD)\b/, 40, '1080p'],
      [/\b(1280\s*[x×]\s*720|720p?|HD)\b/, 25, '720p'],
      [/\b(854\s*[x×]\s*480|480p?|SD)\b/, 10, '480p'],
    ];

    const DOWNLOAD_RE = /\b(download|save|mp4|video|stream|full\s*movie|get|grab|export|direct\s*link)\b/i;
    const DOWNLOAD_RES_PAIR_RE = /(?:(download|save|get).{0,20}(8k|6k|4k|2k|1440p?|1080p?|720p?|2160p?)|(8k|6k|4k|2k|1440p?|1080p?|720p?|2160p?).{0,20}(download|save|get))/i;
    // Expanded weighted negatives (G)
    const NEGATIVES = [
      [/\b(report|flag|abuse|dmca)\b/i, -60, 'report'],
      [/\b(next\s*episode|prev|previous|related|recommended|up\s*next)\b/i, -50, 'nav'],
      [/\b(embed|copy\s*link|short\s*url|share\s*url)\b/i, -45, 'share'],
      [/\b(trailer|preview|sample|teaser|clip)\b/i, -40, 'preview'],
      [/\b(screenshot|thumbnail|poster|cover|still)\b/i, -30, 'thumbnail'],
      [/\b(advertisement|sponsor|ad-banner)\b/i, -50, 'ad'],
      [/\b(share|tweet|facebook|whatsapp|telegram)\b/i, -25, 'social'],
      [/\b(comment|reply|like|favourite|favorite)\b/i, -30, 'comment'],
      [/\b(sign\s*up|register|create\s*account)\b/i, -40, 'signup'],
      [/\b(login|sign\s*in|log\s*in)\b/i, -40, 'login'],
      [/\b(subscribe|membership|premium\s*only|join\s*now)\b/i, -35, 'subscribe'],
    ];
    // Ancestor context (C)
    const ANCESTOR_POSITIVE = /\b(download|mirror|source|quality|version|versions|get|resolutions|file-list|dl-list|stream-list|player-controls|video-actions|media-actions)\b/i;
    const ANCESTOR_NEGATIVE = /\b(related|recommended|sidebar|comments?|nav|navigation|footer|header|menu|search|breadcrumb|advert|promo|social|share-bar|widget|popup|modal-ad)\b/i;
    // Filesize parser (B)
    const SIZE_RE = /\b(\d+(?:[.,]\d+)?)\s*(B|KB|KiB|MB|MiB|GB|GiB|TB|TiB)\b/i;
    const SIZE_MULTIPLIERS = {B:1, KB:1024, KIB:1024, MB:1048576, MIB:1048576, GB:1073741824, GIB:1073741824, TB:1099511627776, TIB:1099511627776};
    function parseFilesize(text) {
      if (!text) return 0;
      const m = SIZE_RE.exec(text);
      if (!m) return 0;
      const num = parseFloat(m[1].replace(',', '.'));
      if (!isFinite(num)) return 0;
      const mult = SIZE_MULTIPLIERS[m[2].toUpperCase()] || 0;
      return Math.floor(num * mult);
    }
    function scoreFilesize(bytes) {
      if (bytes <= 0) return 0;
      if (bytes < 5*1024*1024) return -20;            // <5MB tiny
      if (bytes < 50*1024*1024) return -5;            // <50MB small
      if (bytes < 500*1024*1024) return 10;           // <500MB medium
      if (bytes < 2*1024*1024*1024) return 20;        // <2GB plausible
      return 30;                                        // GB-scale
    }
    // Build the fingerprint set from prior successes — stored
    // in window-scoped state populated by the runner. Empty when
    // none recorded.
    const fpHosts = (state && state.fp_hosts) || [];
    const fpPrefixes = (state && state.fp_prefixes) || [];
    function fingerprintBonus(url) {
      if (!url || (!fpHosts.length && !fpPrefixes.length)) return 0;
      try {
        // Browsers resolve relative URLs via URL constructor
        const u = new URL(url, window.location.origin);
        if (fpHosts.includes(u.hostname.toLowerCase())) return 30;
        for (const p of fpPrefixes) {
          if (p && u.pathname.startsWith(p)) return 30;
        }
      } catch (e) {}
      return 0;
    }

    const candidates = document.querySelectorAll(
      'a, button, video, source, [data-href], [data-url], [data-src*=".mp4"], [data-download]'
    );
    for (const el of candidates) {
      if (panel && panel.contains(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) continue;
      const text = (el.innerText || el.textContent || '').trim().slice(0, 200);
      const href = el.getAttribute('href') || '';
      const dataHref = el.getAttribute('data-href') || '';
      const dataUrl = el.getAttribute('data-url') || '';
      const dataSrc = el.getAttribute('data-src') || '';
      const dataDl = el.getAttribute('data-download') || '';
      const tag = el.tagName.toLowerCase();
      const dataAttrsConcat = dataHref + ' ' + dataUrl + ' ' + dataSrc + ' ' + dataDl;
      const allText = (text + ' ' + href + ' ' + dataAttrsConcat).slice(0, 400);

      const key = tag + ':' + text.slice(0, 50) + ':' + (href || dataHref).slice(0, 60);
      if (seen.has(key)) continue;
      seen.add(key);

      let score = 0;
      const reasons = [];

      // Tiered resolution
      let resTier = 0;
      let resLabel = '';
      for (const [pat, tier, label] of RESOLUTION_TIERS) {
        if (pat.test(allText) && tier > resTier) {
          resTier = tier;
          resLabel = label;
        }
      }
      if (resTier > 0) {
        score += resTier;
        reasons.push([resTier, 'res ' + resLabel]);
      }
      // Download keyword
      if (DOWNLOAD_RE.test(allText)) {
        score += 25;
        reasons.push([25, 'download kw']);
      }
      // Combinatorial download+resolution
      if (DOWNLOAD_RES_PAIR_RE.test(allText)) {
        score += 20;
        reasons.push([20, 'dl+res pair']);
      }
      // Video file extension in href / data
      if (/\.(mp4|mkv|webm|mov|m4v)(\?|#|$)/i.test(href + dataAttrsConcat)) {
        score += 30;
        reasons.push([30, 'video ext']);
      }
      // <video>/<source> tags
      if (tag === 'video' || tag === 'source') {
        score += 20;
        reasons.push([20, 'video element']);
      }
      // data-* attribute presence
      if (dataAttrsConcat.trim()) {
        score += 15;
        reasons.push([15, 'data-*']);
      }
      // Negatives
      for (const [pat, weight, label] of NEGATIVES) {
        if (pat.test(allText)) {
          // 4K+ trailer/sample is still good content
          if (label === 'preview' && resTier >= 60) continue;
          score += weight;
          reasons.push([weight, label]);
        }
      }
      // Long text → paragraph
      if (text.length > 80) {
        score -= 10;
        reasons.push([-10, 'long text']);
      }
      // Filesize from candidate + ±80 chars of surrounding context
      let surround = '';
      try {
        const parent = el.parentElement;
        if (parent) {
          surround = (parent.innerText || '').slice(0, 400);
        }
      } catch (e) {}
      const fsize = parseFilesize(text + ' ' + surround);
      const fsizeDelta = scoreFilesize(fsize);
      if (fsizeDelta !== 0) {
        score += fsizeDelta;
        reasons.push([fsizeDelta, 'size ' + Math.round(fsize/1048576) + 'MB']);
      }
      // Ancestor context — walk up to 4 parents
      let ancestorText = '';
      let p = el.parentElement;
      for (let depth = 0; depth < 4 && p; depth++) {
        ancestorText += ' ' + (p.className || '') + ' ' + (p.id || '');
        p = p.parentElement;
      }
      if (ANCESTOR_POSITIVE.test(ancestorText)) {
        score += 25;
        reasons.push([25, 'pos ancestor']);
      }
      if (ANCESTOR_NEGATIVE.test(ancestorText)) {
        score -= 40;
        reasons.push([-40, 'neg ancestor']);
      }
      // URL fingerprint (D)
      const fpBonus = fingerprintBonus(href || dataHref || dataUrl || dataSrc);
      if (fpBonus > 0) {
        score += fpBonus;
        reasons.push([fpBonus, 'known CDN']);
      }
      // Position + shape (E)
      if (r.top >= 0 && r.top < window.innerHeight) {
        score += 10;
        reasons.push([10, 'above-fold']);
      }
      if (r.width >= 80 && r.height >= 30) {
        score += 15;
        reasons.push([15, 'button-sized']);
      }
      if (r.top > window.innerHeight * 5) {
        score -= 10;
        reasons.push([-10, 'deep-page']);
      }

      if (!text && !href && !dataHref && !dataUrl && !dataSrc) continue;
      if (score < 25) continue;

      out.push({
        tag, text, href, dataHref, dataUrl, dataSrc, dataDownload: dataDl,
        id: el.id || '', className: el.className || '',
        score, score_reasons: reasons, resolution_tier: resTier,
        estimated_size_bytes: fsize,
      });
    }
    // Sort: score DESC, then resolution_tier DESC, then size DESC
    out.sort((a, b) => {
      if (b.score !== a.score) return b.score - a.score;
      if (b.resolution_tier !== a.resolution_tier) return b.resolution_tier - a.resolution_tier;
      return b.estimated_size_bytes - a.estimated_size_bytes;
    });
    return out;
  }

  // ── Phase 27: AI-assisted suggestion ──────────────────────────────
  // Sends the current DOM + a screenshot of the visible viewport to
  // the self-hosted AI. Returned suggestions are synthesized as
  // synthetic events (same path as 23.5 auto-propose), pre-pinned for
  // the user to verify/commit. Never auto-commits — the human stays
  // in the loop.
  async function aiSuggest() {
    const statusEl = panel.querySelector('#pw_teach_autoprop_status');
    statusEl.textContent = 'Capturing DOM + screenshot…';
    statusEl.style.color = '#ec4899';
    // Capture DOM excerpt — focus on the area visible/interactable
    // since vision models do better with focused HTML than the whole
    // page including <script> blocks and nav cruft.
    let domExcerpt = '';
    try {
      // Prefer the <main> or largest visible region; fall back to body.
      const main = document.querySelector('main, article, [role="main"], #content, .content');
      const root = main || document.body;
      // Strip <script>/<style>/<noscript> from the clone before serializing
      const clone = root.cloneNode(true);
      clone.querySelectorAll('script, style, noscript, iframe').forEach(e => e.remove());
      // Strip our own panel from the snapshot too
      clone.querySelectorAll('#__pw_teach_panel').forEach(e => e.remove());
      domExcerpt = clone.outerHTML.slice(0, 16000);
    } catch (e) {
      domExcerpt = document.body.innerHTML.slice(0, 16000);
    }
    // Capture a screenshot via the runner-side endpoint. We don't have
    // chrome.tabs in this context; we send DOM-only and let the server
    // decide whether to grab a screenshot via Playwright on its end.
    // (Vision-quality DOM-only suggestions are still useful — vision
    // is great for ambiguous cases but text is enough for most.)
    statusEl.textContent = 'Asking the local AI…';
    let resp;
    try {
      const r = await fetch('/api/ai/suggest_selectors', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          dom_excerpt: domExcerpt,
          page_url: window.location.href,
          context_hint: 'This page is part of a video site; the user wants the highest-resolution download link.',
        }),
      });
      resp = await r.json();
    } catch (e) {
      statusEl.textContent = '✗ AI request failed: ' + e.message;
      statusEl.style.color = '#ef4444';
      return;
    }
    if (!resp.ok) {
      statusEl.textContent = '✗ ' + (resp.error || 'AI returned an error');
      statusEl.style.color = '#ef4444';
      return;
    }
    const sugg = resp.suggestions || [];
    if (!sugg.length) {
      statusEl.textContent = '⚠ AI returned no suggestions (try a different page region or model)';
      statusEl.style.color = '#fbbf24';
      return;
    }
    // Verify each suggestion actually matches something on the page
    // before adding it — drops hallucinated selectors silently.
    let added = 0, skipped = 0;
    for (const s of sugg) {
      let matchEl = null;
      try { matchEl = document.querySelector(s.selector); }
      catch (e) { skipped++; continue; }
      if (!matchEl) { skipped++; continue; }
      const text = (matchEl.innerText || matchEl.textContent || '').trim().slice(0, 200);
      const href = matchEl.getAttribute('href') || matchEl.getAttribute('data-href') || '';
      const exists = state.events.some(e => e.text === text && e.tag === matchEl.tagName.toLowerCase());
      if (exists) { skipped++; continue; }
      const rec = {
        tag: matchEl.tagName.toLowerCase(),
        text, href,
        id: matchEl.id || '', className: matchEl.className || '',
        synthetic: true, ai: true,
        ai_confidence: s.confidence,
        ai_reasoning: s.reasoning,
      };
      state.events.push(rec);
      const realIdx = state.events.length - 1;
      // Use the AI's selector directly rather than re-synthesizing
      state.chosen_selectors[realIdx] = [s.selector];
      if (state.pinned_set) state.pinned_set.add(realIdx);
      added++;
    }
    renderEvents();
    statusEl.style.color = added > 0 ? '#22c55e' : '#fbbf24';
    statusEl.textContent = added > 0
      ? `✓ AI: ${added} suggestion${added===1?'':'s'} added · ${skipped} skipped (no match or duplicate) · ${resp.latency_ms||'?'}ms`
      : `⚠ All ${skipped} AI suggestions failed to match the page — model may be confused`;
  }

  // ── Phase 29: Differential teach ──────────────────────────────────
  // For when a site redesigns and your previously-learned selectors no
  // longer match. We test each learned selector against the live DOM,
  // collect the broken ones, send them + the current DOM to the AI,
  // and pre-pin the proposed replacements so the user can verify and
  // commit through the normal flow.
  async function diffRepair() {
    const statusEl = panel.querySelector('#pw_teach_autoprop_status');
    statusEl.textContent = 'Fetching learned selectors…';
    statusEl.style.color = '#f97316';
    // Fetch this site's learned selectors via the main API
    let learned;
    try {
      const base = window.__pw_teach_base || '';
      const sid = window.__pw_teach_sid || '';
      const r = await fetch(`${base}/api/status?light=1`);
      const all = await r.json();
      learned = (all[sid]?.config?.learned?.download) || {};
    } catch (e) {
      statusEl.textContent = '✗ Failed to fetch learned selectors: ' + e.message;
      statusEl.style.color = '#ef4444';
      return;
    }
    const allSelectors = [
      ...(learned.row_selectors || []).map(s => ({sel: s, role: 'row_selectors'})),
      ...(learned.trigger_selectors || []).map(s => ({sel: s, role: 'trigger_selectors'})),
    ];
    if (!allSelectors.length) {
      statusEl.textContent = '⚠ No learned download selectors to repair — teach from scratch instead';
      statusEl.style.color = '#fbbf24';
      return;
    }
    // Test each against the live DOM
    const broken = [], working = [];
    for (const {sel, role} of allSelectors) {
      let matches = false;
      try {
        const els = document.querySelectorAll(sel);
        // We consider it "broken" if it matches NOTHING OR matches
        // something inside our own teach panel only (no real targets).
        if (els.length === 0) { matches = false; }
        else {
          // At least one real (non-panel) match?
          for (const el of els) {
            if (!panel || !panel.contains(el)) { matches = true; break; }
          }
        }
      } catch (e) { /* invalid selector counts as broken */ }
      if (matches) working.push({sel, role});
      else broken.push({sel, role});
    }
    if (!broken.length) {
      statusEl.textContent = `✓ All ${allSelectors.length} learned selectors still match — nothing to repair`;
      statusEl.style.color = '#22c55e';
      return;
    }
    statusEl.textContent = `Found ${broken.length} broken selector${broken.length===1?'':'s'} · asking AI for replacements…`;
    // Build DOM excerpt (same as aiSuggest)
    let domExcerpt = '';
    try {
      const main = document.querySelector('main, article, [role="main"], #content, .content');
      const root = main || document.body;
      const clone = root.cloneNode(true);
      clone.querySelectorAll('script, style, noscript, iframe').forEach(e => e.remove());
      clone.querySelectorAll('#__pw_teach_panel').forEach(e => e.remove());
      domExcerpt = clone.outerHTML.slice(0, 16000);
    } catch (e) {
      domExcerpt = document.body.innerHTML.slice(0, 16000);
    }
    // Call diff_repair
    let resp;
    try {
      const base = window.__pw_teach_base || '';
      const r = await fetch(`${base}/api/ai/diff_repair`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({
          broken_selectors: broken.map(b => b.sel),
          working_selectors: working.map(w => w.sel),
          dom_excerpt: domExcerpt,
          page_url: window.location.href,
        })});
      resp = await r.json();
    } catch (e) {
      statusEl.textContent = '✗ Diff repair request failed: ' + e.message;
      statusEl.style.color = '#ef4444';
      return;
    }
    if (!resp.ok) {
      statusEl.textContent = '✗ ' + (resp.error || 'AI returned an error');
      statusEl.style.color = '#ef4444';
      return;
    }
    const repairs = resp.repairs || [];
    // Verify each proposed replacement actually matches before adding
    let added = 0, skipped = 0;
    for (const r of repairs) {
      let matchEl = null;
      try { matchEl = document.querySelector(r.new_selector); }
      catch (e) { skipped++; continue; }
      if (!matchEl || (panel && panel.contains(matchEl))) { skipped++; continue; }
      const text = (matchEl.innerText || matchEl.textContent || '').trim().slice(0, 200);
      const href = matchEl.getAttribute('href') || matchEl.getAttribute('data-href') || '';
      const rec = {
        tag: matchEl.tagName.toLowerCase(),
        text, href,
        id: matchEl.id || '', className: matchEl.className || '',
        synthetic: true, ai: true, repair: true,
        ai_confidence: r.confidence,
        ai_reasoning: r.reasoning,
        replaces: r.old_selector,
      };
      state.events.push(rec);
      const realIdx = state.events.length - 1;
      state.chosen_selectors[realIdx] = [r.new_selector];
      if (state.pinned_set) state.pinned_set.add(realIdx);
      added++;
    }
    renderEvents();
    const removed = (resp.removed || []).length;
    const parts = [];
    if (added) parts.push(`${added} repair${added===1?'':'s'} added`);
    if (skipped) parts.push(`${skipped} skipped (no DOM match)`);
    if (removed) parts.push(`${removed} marked as removed (no equivalent)`);
    statusEl.style.color = added > 0 ? '#22c55e' : '#fbbf24';
    statusEl.textContent = (added > 0 ? '✓ ' : '⚠ ') + parts.join(' · ')
      + ` · ${resp.latency_ms||'?'}ms`;
  }

  // ── Phase 23.2: What-if preview ────────────────────────────────────
  // Highlight all elements on the live page that the currently-picked
  // selectors would match. Different colors per role.
  function whatIf() {
    const statusEl = panel.querySelector('#pw_teach_autoprop_status');
    const picks = collectPicks();
    // Clear any prior highlights
    document.querySelectorAll('[data-pw-whatif]').forEach(el => {
      el.style.outline = el.dataset.pwOldOutline || '';
      el.style.outlineOffset = '';
      delete el.dataset.pwWhatif;
      delete el.dataset.pwOldOutline;
    });
    let rowCount = 0, trigCount = 0;
    function mark(selectors, color) {
      let n = 0;
      for (const sel of selectors) {
        try {
          const els = document.querySelectorAll(sel);
          els.forEach(el => {
            if (panel && panel.contains(el)) return;
            el.dataset.pwOldOutline = el.style.outline || '';
            el.style.outline = '3px solid ' + color;
            el.style.outlineOffset = '2px';
            el.dataset.pwWhatif = '1';
            n++;
          });
        } catch (e) { /* invalid selector — skip */ }
      }
      return n;
    }
    rowCount = mark(picks.row_selectors || [], '#22c55e');
    trigCount = mark(picks.trigger_selectors || [], '#fbbf24');
    if (rowCount === 0 && trigCount === 0) {
      statusEl.textContent = 'No elements matched. Pick some selectors above and try again.';
      statusEl.style.color = '#fbbf24';
      return;
    }
    statusEl.textContent = `${rowCount} row match${rowCount===1?'':'es'} (green), ${trigCount} trigger match${trigCount===1?'':'es'} (yellow). Auto-clears in 5s.`;
    statusEl.style.color = '#22c55e';
    setTimeout(() => {
      document.querySelectorAll('[data-pw-whatif]').forEach(el => {
        el.style.outline = el.dataset.pwOldOutline || '';
        el.style.outlineOffset = '';
        delete el.dataset.pwWhatif;
        delete el.dataset.pwOldOutline;
      });
    }, 5000);
  }
  function collectPicks() {
    // Aggregate every chosen_selector across all events into the right
    // role buckets. Heuristic for routing: events with download-flavored
    // text or a data-href attribute go to row_selectors, others to
    // trigger_selectors. The Python side will re-classify on commit
    // anyway, so this is a hint, not authoritative.
    //
    // Phase 19.fix: sort by event_idx ascending so the order in the
    // output list matches the order the user clicked. This is the order
    // the worker's detect.py will iterate when running the fallback
    // chain — first selector tried first. Recommendation to the user
    // is "click highest-quality first" (e.g. 8K, then 6K, then 4K) so
    // the resulting chain prefers high quality but falls back gracefully.
    const out = {row_selectors: [], trigger_selectors: [], url_attribute: ''};
    const sortedIdx = Object.keys(state.chosen_selectors)
      .map(Number)
      .sort((a, b) => a - b);
    sortedIdx.forEach((idx) => {
      const rec = state.events[idx];
      if (!rec) return;
      const sels = state.chosen_selectors[idx];
      if (!sels || !sels.length) return;
      const isRow = !!(rec.dataHref || rec.dataUrl || rec.dataSrc || rec.dataDownload)
                    || /\.(mp4|mkv|webm|mov)/.test(rec.href || '');
      if (isRow) {
        out.row_selectors.push(...sels);
        if (rec.dataHref) out.url_attribute = 'data-href';
        else if (rec.dataUrl) out.url_attribute = 'data-url';
        else if (rec.dataSrc) out.url_attribute = 'data-src';
        else if (rec.dataDownload) out.url_attribute = 'data-download';
      } else {
        out.trigger_selectors.push(...sels);
      }
    });
    out.row_selectors = [...new Set(out.row_selectors)];
    out.trigger_selectors = [...new Set(out.trigger_selectors)];
    return out;
  }

  // Build it.
  buildPanel();
  renderEvents();

  // Phase 19.fix: SPA route changes can replace document.body wholesale,
  // taking our panel with it. Without a MutationObserver, the panel
  // disappears after the first navigation and never comes back (since
  // add_init_script runs once per navigation, and the IIFE's DOM check
  // at the top would normally see no panel and rebuild — but only if
  // the script is re-evaluated, which doesn't happen for in-page SPA
  // route changes).
  try {
    const watchdog = new MutationObserver(() => {
      if (!document.getElementById('__pw_teach_panel')) {
        buildPanel();
        renderEvents();
      }
    });
    watchdog.observe(document.documentElement,
                     { childList: true, subtree: true });
  } catch (e) { /* MutationObserver missing — best-effort */ }

  // Also re-check on URL changes (some SPAs render in-place rather than
  // tearing down the body, so MutationObserver doesn't fire — but we
  // still want a fresh panel in case event handlers got rebound).
  let lastUrl = location.href;
  setInterval(() => {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      if (!document.getElementById('__pw_teach_panel')) {
        buildPanel();
        renderEvents();
      }
    }
  }, 800);
})();
"""
