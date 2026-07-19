"""learn_impl.classify -- verbatim from learn.py (DECOMP-LEAF cut 5)."""

from __future__ import annotations
import re

from ._assets import RECORDER_JS, TEACH_OVERLAY_JS
from .selectors import (
    _is_submit_shaped,
    _synthesize_download_row_selector,
    _which_url_attr,
    synthesize_selectors,
)


def install_recorder(page):
    """Install the recorder. Safe to call multiple times — the JS guards
    against double-install with a `__pwrec_installed` flag."""
    try:
        # add_init_script applies on future navigations; evaluate handles
        # the current page right now.
        page.add_init_script(RECORDER_JS)
        page.evaluate(RECORDER_JS)
    except Exception:
        pass


def install_teach_overlay(page, sid, base_url="", fingerprint=None):
    """Phase 10: install the Teach Mode overlay on a manual-takeover page.
    `sid` is the site ID for endpoint URL construction. `base_url` defaults
    to '' meaning same-origin — the takeover browser hits the local Flask
    server. The two are injected as window globals so the JS can build
    endpoint URLs without hardcoding.

    v3.43.44: `fingerprint` (optional dict from per-site config) is
    injected so the scoreCandidates() function can use it for URL-pattern
    fingerprinting (Engine D). Shape:
      {"known_hosts": ["cdn.site.com", ...],
       "known_path_prefixes": ["/v/123/", ...]}
    """
    try:
        # Inject site id + base + fingerprint BEFORE the overlay JS
        # runs so endpoints work and scoreCandidates picks up the
        # URL fingerprint on first scan.
        fp = fingerprint or {}
        hosts = fp.get("known_hosts") or []
        prefixes = fp.get("known_path_prefixes") or []
        # Defensive: only emit strings, only the first 20 of each
        hosts = [h for h in hosts if isinstance(h, str)][:20]
        prefixes = [p for p in prefixes if isinstance(p, str)][:20]
        import json as _json
        bootstrap_js = (
            f"window.__pw_teach_sid={sid!r}; "
            f"window.__pw_teach_base={base_url!r}; "
            f"window.__pw_teach_fp_hosts={_json.dumps(hosts)}; "
            f"window.__pw_teach_fp_prefixes={_json.dumps(prefixes)};"
        )
        page.add_init_script(bootstrap_js)
        page.add_init_script(TEACH_OVERLAY_JS)
        page.evaluate(bootstrap_js)
        page.evaluate(TEACH_OVERLAY_JS)
    except Exception as e:
        import sys
        sys.stderr.write(f"  teach overlay install failed: {e}\n")


def harvest_recordings(ctx):
    """Return {'clicks': [...], 'inputs': [...]} from every page in the
    context, sorted by timestamp. Empty lists if nothing was recorded
    (e.g. user cancelled before doing anything)."""
    clicks, inputs = [], []
    try: pages = list(ctx.pages)
    except Exception: pages = []
    for page in pages:
        try:
            c = page.evaluate("window.__pwrec_clicks || []")
            i = page.evaluate("window.__pwrec_inputs || []")
            if c: clicks.extend(c)
            if i: inputs.extend(i)
        except Exception:
            continue
        # v3.66.288: capture done -> forget. Clear the cross-navigation
        # persistence store so its (structure-only) records can't leak into
        # a later capture that reuses this tab. Best-effort; the in-memory
        # arrays above were already read.
        try:
            page.evaluate(
                "try{sessionStorage.removeItem('__pwrec_store_v1:clicks');"
                "sessionStorage.removeItem('__pwrec_store_v1:inputs');}catch(e){}")
        except Exception:
            pass
    clicks.sort(key=lambda x: x.get("ts", 0))
    inputs.sort(key=lambda x: x.get("ts", 0))
    return {"clicks": clicks, "inputs": inputs}


_NON_TEXT_INPUT_TYPES = frozenset({
    "password", "checkbox", "radio", "submit", "button", "reset",
    "image", "file", "hidden", "range", "color",
})


def classify_login(harvest, login_url=""):
    """Returns dict with keys 'user_field', 'pass_field', 'submit_btn',
    each a list of selectors (possibly empty)."""
    inputs = harvest.get("inputs", []) or []
    clicks = harvest.get("clicks", []) or []

    # Password: latest input event with type=password (handles cases where
    # user clears+retypes — we want the final state).
    pass_rec = None
    for inp in inputs:
        if (inp.get("type") or "").lower() == "password":
            pass_rec = inp

    # Username: latest input event that's NOT a password and is a text-style
    # input. We pick the LAST one (user might tab through fields, cleared
    # and retyped, etc.) — the final touch reflects the actual field used.
    #
    # v3.66.287: detect by EXCLUSION rather than a narrow positive type
    # whitelist. The password detector above is a bare presence check
    # (type == "password"); the username detector previously required
    # type in {text, email, tel, ""}, so a same-page username/email field
    # rendered as search / number / url / etc. (or a blank/custom type the
    # browser coerces to text) was silently dropped — user_field came back
    # empty while the password still classified ("password fills but not
    # the username, even though it was recorded"). Now: accept any recorded
    # input/textarea that is NOT the password, NOT a non-text control, and
    # NOT flagged secret (e.g. autocomplete=current-password on a field
    # whose literal type isn't "password").
    user_rec = None
    for inp in inputs:
        if inp.get("secret"):
            continue
        if (inp.get("tag", "") or "").lower() not in ("input", "textarea"):
            continue
        if (inp.get("type") or "").strip().lower() in _NON_TEXT_INPUT_TYPES:
            continue
        user_rec = inp

    # Submit button: last click that happened on the login URL (or any URL
    # containing it, to handle login.example.com/auth → example.com/members).
    # If login_url is empty (caller didn't pass one), fall back to "the last
    # click before any URL change" — i.e. find the click whose URL doesn't
    # match the next click's URL.
    # Submit button: prefer the LAST submit-SHAPED click on the login URL.
    # SAUCE fix: the previous `login_url in cu` substring test alone let a
    # post-navigation record whose URL merely has login_url as a prefix
    # (saucedemo.com/ -> saucedemo.com/inventory.html) overwrite sub_rec with
    # a non-submit element (a bare <div>), yielding an EMPTY submit_btn and a
    # clobbered <input type=submit>. Restrict the winner to submit-shaped
    # clicks, falling back to the old "last on-login click" only when none is
    # submit-shaped (preserves the login.example.com/auth -> members tolerance).
    sub_rec = None
    if login_url:
        on_login = [c for c in clicks
                    if login_url in (c.get("url", "") or "")
                    or (c.get("url", "") or "").startswith(login_url)]
        shaped = [c for c in on_login if _is_submit_shaped(c)]
        if shaped:
            sub_rec = shaped[-1]
        elif on_login:
            sub_rec = on_login[-1]
    if sub_rec is None:
        # Last-click-before-URL-change heuristic (empty login_url, or no
        # on-login click matched).
        # v3.65.2: previously broke on the FIRST nav-triggering click,
        # which during a wizard run is typically a "Forgot password" /
        # nav link clicked before the user got to the real submit
        # button. The docstring promises "last click before URL change"
        # — keep the LATEST qualifying click so it wins.
        # SAUCE: among the URL-changing clicks, still prefer a submit-shaped
        # one over an incidental nav click.
        changers = [c for i, c in enumerate(clicks)
                    if i + 1 < len(clicks)
                    and clicks[i + 1].get("url") != c.get("url")]
        shaped = [c for c in changers if _is_submit_shaped(c)]
        if shaped:
            sub_rec = shaped[-1]
        elif changers:
            sub_rec = changers[-1]
        elif clicks:
            sub_rec = clicks[-1]  # last resort: just take the last click

    # v3.43.50: recover the login URL and success URL from the harvest
    # so the caller can populate cfg["login_url"] / cfg["success_url"].
    # Sibling fix to v3.43.49's credential capture.
    #
    # login_url: the URL where the user entered credentials. The
    #   password input record's `url` is the most reliable signal
    #   (the user typed there). Fall back to user record's URL.
    # success_url: the URL the page went to AFTER successful submit.
    #   That's the URL of any record (click or input) whose timestamp
    #   is later than sub_rec AND whose URL differs from sub_rec.url.
    #   Empty if no post-submit activity was captured (user clicked
    #   I'm Done immediately after).
    captured_login_url = ""
    if pass_rec and pass_rec.get("url"):
        captured_login_url = pass_rec["url"]
    elif user_rec and user_rec.get("url"):
        captured_login_url = user_rec["url"]
    elif sub_rec and sub_rec.get("url"):
        captured_login_url = sub_rec["url"]

    captured_success_url = ""
    if sub_rec:
        sub_ts = sub_rec.get("ts", 0)
        sub_url = sub_rec.get("url", "")
        # Walk all clicks AND inputs ordered by ts; first record after
        # sub_ts with a different URL wins.
        post = []
        for c in clicks:
            if c.get("ts", 0) > sub_ts and c.get("url", "") != sub_url:
                post.append(c)
        for i in inputs:
            if i.get("ts", 0) > sub_ts and i.get("url", "") != sub_url:
                post.append(i)
        post.sort(key=lambda r: r.get("ts", 0))
        if post:
            captured_success_url = post[0].get("url", "")
        # Strip session-specific path components from success_url. Many
        # sites land you on a per-user path like /user/123/dashboard,
        # which won't match on a fresh login. Hostname + first path
        # segment is the useful signal — caller can always edit later.
        #
        # v3.65.2: the previous implementation claimed to "strip the
        # path down to scheme://host" but actually passed `p.path` to
        # urlunparse, KEEPING the entire path. Combined with the
        # _success_url_matches helper's path-prefix semantics, that
        # broke fresh-login verification on any site with per-user
        # paths. Now: split on '/', keep only the first non-empty
        # segment (so "/members" stays, "/user/123/dashboard" becomes
        # "/user"). For pages with no path at all, drop to scheme://host.
        if captured_success_url:
            try:
                from urllib.parse import urlparse, urlunparse
                p = urlparse(captured_success_url)
                if p.scheme and p.netloc:
                    segs = [s for s in (p.path or "").split("/") if s]
                    keep_path = "/" + segs[0] if segs else ""
                    captured_success_url = urlunparse(
                        (p.scheme, p.netloc, keep_path, "", "", ""))
            except Exception:
                pass

    return {
        "user_field": synthesize_selectors(user_rec),
        "pass_field": synthesize_selectors(pass_rec),
        "submit_btn": synthesize_selectors(sub_rec),
        # v3.43.49 bug fix: also surface the actual typed values so the
        # caller can populate cfg["username"] / cfg["password"]. Without
        # this, the teach wizard captured the field SELECTORS but the
        # user still had to retype credentials into the site config bar
        # — a real footgun.
        #
        # We pull values from `_input_value` (the separate raw-value
        # channel added in v3.43.49) rather than from `text` because
        # the text field is redacted for password inputs (renders as
        # "[pw]" in the teach panel's click log). The _input_value
        # channel is never rendered — it exists exclusively for this
        # classifier path.
        "username_value": (user_rec.get("_input_value", "") if user_rec else ""),
        "password_value": (pass_rec.get("_input_value", "") if pass_rec else ""),
        # v3.43.50: also surface login_url + success_url. Same bug
        # class as the credential capture — wizard had the data,
        # threw it away. Now plumbed.
        "login_url_value": captured_login_url,
        "success_url_value": captured_success_url,
    }


def classify_download(harvest):
    """From recorded clicks during a manual download takeover, identify:
      - trigger_selectors: how to open the resolution modal (if needed).
        This is the click on the page (not in the modal) that has
        download-flavored text or an icon.
      - row_selectors: how to find resolution rows in the modal. Looks
        for clicks with download-URL-like attributes.
      - url_attribute: name of the HTML attribute on the row that carries
        the file URL. Lets the worker httpx-fetch directly without
        triggering Playwright's download event (faster + dodges signed-URL
        races).
      - tier_label: text label of the resolution the user picked
        (e.g. "4K", "Full HD"). Captured here for Phase 5.6 quality
        ladder learning; not yet acted upon.

    Returns a dict with whichever of these could be inferred. Empty
    values mean the harvest didn't contain enough signal — caller falls
    back to the existing 14-element-type wide scan."""
    clicks = harvest.get("clicks", []) or []
    if not clicks: return {}

    # Find the row click: the one whose attributes point at a video URL.
    # If multiple, prefer the LAST (user might browse modal, hover, etc.;
    # the actual download click is typically last).
    #
    # v3.42.1 bug fix: a click on the inner <span> of an <a href="..."> only
    # registers the span (no href on the span itself). The recorder now
    # captures ancestor URL info under ancestor* keys; if the URL came
    # from there, build a synthetic row_rec that represents the
    # ancestor anchor — so selector synthesis targets the <a>, not the
    # <span>, and uses the ancestor's text (e.g. "3840 x 2160 4K") for
    # the tier label. row_idx tracks the position in the ORIGINAL clicks
    # list (synthetic records aren't in it, so .index() would fail).
    row_rec = None
    row_idx = -1
    for i, c in enumerate(clicks):
        attr, val = _which_url_attr(c)
        if attr and val:
            target_val = c.get({
                "href": "href", "data-href": "dataHref",
                "data-url": "dataUrl", "data-src": "dataSrc",
                "data-download": "dataDownload",
            }.get(attr, "")) or ""
            if target_val == val:
                row_rec = c
            else:
                # URL came from ancestor — synthesize record so selector
                # synthesis targets the right element.
                synth = dict(c)
                synth["tag"] = c.get("ancestorTag") or "a"
                synth["text"] = c.get("ancestorText") or c.get("text") or ""
                key = {
                    "href": "href", "data-href": "dataHref",
                    "data-url": "dataUrl", "data-src": "dataSrc",
                    "data-download": "dataDownload",
                }.get(attr)
                if key:
                    synth[key] = val
                synth["id"] = ""
                synth["cls"] = ""
                synth["role"] = ""
                synth["testid"] = ""
                row_rec = synth
            row_idx = i

    # Find the trigger click: a click BEFORE the row click whose text
    # contains download-flavored words. Often it's just "Download".
    trigger_rec = None
    if row_rec and row_idx > 0:
        for c in clicks[:row_idx]:
            text = (c.get("text") or "").lower()
            if any(kw in text for kw in ("download", "get it", "save")):
                trigger_rec = c  # keep updating; want the last one before row
    # If no text-based trigger but there's a click before the row, use that
    if not trigger_rec and row_rec and row_idx > 0:
        trigger_rec = clicks[row_idx - 1]

    out = {}
    if trigger_rec:
        out["trigger_selectors"] = synthesize_selectors(trigger_rec)
    if row_rec:
        out["row_selectors"] = _synthesize_download_row_selector(row_rec)
        attr, _ = _which_url_attr(row_rec)
        if attr: out["url_attribute"] = attr
        # Capture the resolution label text for ladder learning (5.6)
        text = (row_rec.get("text") or "").strip()
        if text:
            # Take just the resolution portion if possible — e.g. "4K (HEVC)"
            # → "4K", "Full HD 1080p" → "Full HD" or "1080p"
            m = re.search(r"\b(\d{3,4}p|\d{3,4}\s*[x×]\s*\d{3,4}|[24568]k|web\s*hd|full\s*hd|hd|sd|ultra|standard|low|small|tiny|medium|high)\b", text, re.I)
            if m: out["tier_label"] = m.group(0)
            else: out["tier_label"] = text[:30]
    return out


def merge_learned(config, new_selectors, kind="login"):
    """Update `config['learned'][kind]` with newly-recorded selectors.

    Strategy: prepend the new selectors at the front (most recent wins),
    keep up to MAX entries per role, dedupe across the merged list. The
    in-place mutation is intentional — caller is expected to persist
    config via _save_sites_config() afterward.

    Schema differs slightly per kind:
      login:    each role is a list of selectors (user_field, pass_field, submit_btn)
      download: trigger_selectors and row_selectors are lists; url_attribute
                and tier_label are scalars (single-value); tier_labels is a
                dict accumulated over many sessions for ladder learning.

    v3.43.10: when row_selectors change AND url_attribute is in parallel-
    list form (v3.42.4 multi-variant templates), we have to keep the two
    aligned. Adding a new row_selector to the front means a new slot
    must also appear at the front of url_attribute, otherwise the
    resolver in runner.py reads the WRONG attribute for matched
    selectors and falls through to click-and-capture. Symptom: file
    downloads via Chrome's download bar to the default folder instead
    of via httpx to the configured download_dir.

    Special-case interactions with the existing url_attribute handler:
      - If the incoming dict has BOTH a url_attribute string AND new
        row_selectors, the url_attribute string already prepends a slot
        via the dedicated handler. We don't double-prepend.
      - If only new row_selectors arrive (no url_attribute in the
        commit), we prepend empty slots to url_attribute so the new
        selectors fall through to click-and-capture safely instead of
        misaligning into another selector's attribute name."""
    MAX_PER_ROLE = 5
    learned = config.setdefault("learned", {})
    block = learned.setdefault(kind, {})

    # Detect parallel-list mode up-front
    incoming_url_attr = new_selectors.get("url_attribute")
    existing_url_attr = block.get("url_attribute")
    url_attr_list_mode = (
        isinstance(existing_url_attr, list) and
        not isinstance(incoming_url_attr, list)  # incoming list overwrites entirely
    )
    # If the incoming commit also has a url_attribute string AND new
    # row_selectors, the url_attribute handler below will prepend ONE
    # slot. So we should NOT additionally prepend from the row_selectors
    # branch.
    incoming_url_attr_handles_prepend = (
        url_attr_list_mode and isinstance(incoming_url_attr, str)
    )

    for role, val in new_selectors.items():
        if not val and val != 0:
            continue
        # url_attribute handling — see top-of-function comment for the matrix
        if role == "url_attribute":
            existing = block.get(role)
            if isinstance(val, str) and isinstance(existing, list):
                # Teach is adding ONE new row_selector with this url_attr;
                # the list path elsewhere prepends the row_selector, so we
                # prepend the corresponding url_attribute slot too.
                merged_list = [val] + list(existing)
                block[role] = merged_list[:MAX_PER_ROLE]
            elif isinstance(val, dict) and isinstance(existing, dict):
                merged = dict(existing); merged.update(val)
                block[role] = merged
            else:
                block[role] = val
            continue
        if isinstance(val, list):
            existing = block.get(role, []) or []
            new_only = [s for s in val if s not in existing]
            merged = list(val) + [s for s in existing if s not in val]
            block[role] = merged[:MAX_PER_ROLE]
            # v3.43.10: row_selectors prepend → patch url_attribute list
            # only when the commit didn't also include a url_attribute
            # string (which already prepends its own slot above).
            if (role == "row_selectors"
                    and url_attr_list_mode
                    and new_only
                    and not incoming_url_attr_handles_prepend):
                prepend_count = len(new_only)
                old_url_attrs = list(block.get("url_attribute") or [])
                # No incoming url_attribute => empty slot (click-and-capture)
                # is the safest default for the new row_selectors.
                new_url_attrs = [""] * prepend_count + old_url_attrs
                block["url_attribute"] = new_url_attrs[:len(block[role])]
        elif role == "tier_label":
            ladder = block.setdefault("tier_labels_seen", [])
            if val not in ladder:
                ladder.append(val)
                ladder[:] = ladder[-20:]  # keep last 20
        else:
            block[role] = val

    # Final invariant check — if url_attribute is a list AND row_selectors
    # is also a list (both must be), keep their lengths equal. Skip when
    # row_selectors isn't set yet (allowed during template-only configs).
    # Direction matters: merge_learned PREPENDS row_selectors, so when
    # url_attribute is short it's missing slots at the FRONT (where the
    # new row_selectors went). Front-pad / front-keep to preserve the
    # alignment of the original tail entries.
    #
    # v3.65.2: the symmetric case (url_attribute LONGER than row_selectors)
    # was trimmed from the wrong end. Since new entries are prepended, the
    # *extras* always live at the TAIL — those are the entries left over
    # from previously-evicted row_selectors. Tail-trim (ua[:len(rows)])
    # preserves the newest, correctly-aligned front entries. The original
    # ua[-len(rows):] kept the oldest entries and dropped the ones that
    # matched the current row_selectors, producing the exact "resolver
    # reads the wrong attribute" symptom this block was supposed to
    # prevent.
    if isinstance(block.get("url_attribute"), list) and \
            isinstance(block.get("row_selectors"), list) and block["row_selectors"]:
        rows = block["row_selectors"]
        ua = block["url_attribute"]
        if len(ua) < len(rows):
            ua = [""] * (len(rows) - len(ua)) + ua
        elif len(ua) > len(rows):
            ua = ua[:len(rows)]
        block["url_attribute"] = ua

    # Track stats
    stats = learned.setdefault("stats", {})
    stats[f"manual_{kind}"] = (stats.get(f"manual_{kind}", 0) or 0) + 1
    stats["last_learned"] = __import__("datetime").datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    return config
