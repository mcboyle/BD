"""Cross-site selector reuse — P5-1b (v3.66.35).

When two sites have structurally similar login forms (e.g. both
Aylo-family — same input/button structure, different rotating CSS-module
class names and different hosts), a high-confidence selector proven on
Site A is, today, never consulted for Site B: each site's ``learned``
block is independent. This module closes that gap with a small cross-site
store keyed by a structural FORM SIGNATURE, consulted as a low-priority
tail on each site's selector chain.

What this is and isn't
----------------------

* It reuses ONLY selectors the operator already proved on their own
  sites (high ``_per_selector`` hit-count, zero recent misses). It does
  not scrape, fingerprint, or synthesize anything from a remote DOM. It
  is pure offline reuse of the operator's own learned blocks — the
  detect-side of the posture line.
* It never reorders or replaces a site's own learned/user-configured
  selectors. Cross-site entries are appended as a tail, after the site's
  own learned selectors and before (or interleaved with — caller's
  choice) the generic fallback list, and always deduped against what's
  already in the chain.

Opt-in
------

The integration points no-op unless ``BD_CROSS_SITE_SELECTORS=1``. Default
off → zero behaviour change, nothing read, nothing written. This mirrors
the honeypot scorer's ``BD_HONEYPOT_SCORE_THRESHOLD`` opt-in.

Coordination with capture-synthesis C-T1
----------------------------------------

OUTSTANDING_WORK §P5-1b flags that C-tier anti-unification (C-T1) and this
work both touch ``learn.py``'s store and are "adjacent, possibly the same
work." The store here is keyed by :func:`form_signature` and persisted as
a flat JSON document so that C-T1 synthesis output can later write entries
under the same signature key instead of standing up a parallel store.
The on-disk shape (``version`` + ``signatures`` map) is deliberately
generic for that reason.

Store shape (``$BD_HOME/cross_site_selectors.json``)::

    {
      "version": 1,
      "signatures": {
        "<sig>": {
          "user_field": [{"selector": "...", "source": "sitea", "hits": 12}],
          "pass_field": [...],
          "submit_btn": [...]
        }
      }
    }
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from typing import Optional, List, Dict
from urllib.parse import urlparse


# ── Tunables ────────────────────────────────────────────────────────
STORE_VERSION = 1
ROLES = ("user_field", "pass_field", "submit_btn")

# A learned selector must have at least this many recorded hits and zero
# recorded misses to be eligible for cross-site sharing. "Proven on
# Site A" means it actually worked there, repeatedly, without recent
# failure — not merely that it was once captured.
DEFAULT_MIN_HITS = 2

# Cap per (signature, role) so the store can't grow without bound and a
# chain tail stays short.
MAX_PER_ROLE = 8

# How many cross-site selectors to append to any one chain.
DEFAULT_AUGMENT_LIMIT = 4

_ENV_FLAG = "BD_CROSS_SITE_SELECTORS"


_STORE_KEY = "cross_site_selectors"


def enabled() -> bool:
    """True iff cross-site reuse is opted in.

    CLI->GUI parity 4.3a precedence (matches the @315 idiom): the global
    Settings store wins when set, the ``BD_CROSS_SITE_SELECTORS`` env var is the
    seed/override default, and the hard default is OFF. A GUI write to the store
    therefore takes effect live without a restart.
    """
    try:
        from . import global_config as _gc
        stored = _gc.get(_STORE_KEY, None)
    except Exception:
        stored = None
    if stored is not None:
        return bool(stored)
    return os.environ.get(_ENV_FLAG) == "1"


def store_path() -> str:
    """Path to the JSON store under BD_HOME (falls back to cwd if unset
    — matches how the rest of the app degrades when BD_HOME is missing)."""
    bd_home = os.environ.get("BD_HOME") or "."
    return os.path.join(bd_home, "cross_site_selectors.json")


# ── Selector shape normalization ────────────────────────────────────
# A "shape" is a structural fingerprint of a selector that survives the
# volatile parts sites rotate (CSS-module hashes, numeric id suffixes)
# while keeping the stable parts that make two forms "the same form"
# (tag, input type/name/autocomplete, button text).

# CSS-module / styled-components / emotion hash class prefixes.
_HASH_CLASS_PREFIX_RE = re.compile(r"^(sc-|css-|jsx-|emotion-|makeStyles-)")
# A bare token that mixes upper+lower case with no separator and is at
# least 5 chars (e.g. `joHetV`, `ljLTqn`) — the classic rotated hash.
_CAMEL_HASH_RE = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])[A-Za-z0-9]{5,}$")
# Mostly-hex / mostly-digit token (e.g. `a1b2c3`, `1f4e9d`, `48217`).
_HEXISH_RE = re.compile(r"^[0-9a-f]{5,}$", re.IGNORECASE)
# Numeric run inside an id (e.g. `user_12345`, `field-887`) — strip it.
_NUM_SUFFIX_RE = re.compile(r"[-_]?\d{2,}$")

# Attribute selectors we treat as STABLE form structure (keep verbatim).
_STABLE_ATTRS = frozenset({
    "type", "name", "autocomplete", "role", "inputmode", "enterkeyhint",
})


def _class_token_is_volatile(tok: str) -> bool:
    if not tok:
        return True
    if _HASH_CLASS_PREFIX_RE.match(tok):
        return True
    if _CAMEL_HASH_RE.match(tok):
        return True
    if _HEXISH_RE.match(tok):
        return True
    return False


def _normalize_classes(body: str) -> str:
    """Given the class portion of a selector (the bit after/with '.'),
    drop volatile class tokens, keep stable word-like ones (sorted for
    order-independence)."""
    toks = [t for t in body.split(".") if t]
    keep = sorted(t for t in toks if not _class_token_is_volatile(t))
    return "".join("." + t for t in keep)


def selector_shape(selector: str) -> str:
    """Normalize one selector string to a stable structural shape.

    Strips rotated CSS-module hash classes and numeric id suffixes; keeps
    tag, stable attribute selectors (type/name/autocomplete/…),
    ``:has-text(...)`` (button text is stable signal), and word-like
    classes. Returns '' for an empty/blank selector.
    """
    s = (selector or "").strip()
    if not s:
        return ""

    # Pull out and preserve :has-text("...") chunks verbatim (lowercased).
    has_texts: List[str] = []

    def _grab_has_text(m):
        has_texts.append(m.group(0).lower())
        return ""

    s = re.sub(r":has-text\([^)]*\)", _grab_has_text, s)

    out_parts: List[str] = []
    # Split on descendant combinator (whitespace) — process each simple
    # selector. We keep it deliberately simple: this is a fingerprint,
    # not a CSS parser.
    for simple in s.split():
        # Attribute selectors: [type="password"], [name='username'] …
        attrs = re.findall(r"\[([a-zA-Z_-]+)\s*([*^$~|]?=)?\s*([^\]]*)\]", simple)
        # Strip attr blocks out to find the leading tag/id/class body.
        body = re.sub(r"\[[^\]]*\]", "", simple)

        # Tag (leading run of letters before . # [ :)
        m = re.match(r"^([a-zA-Z][a-zA-Z0-9]*)", body)
        tag = (m.group(1).lower() if m else "")
        after_tag = body[len(tag):]

        # id  (#foo) — keep but strip numeric suffixes (volatile).
        ids = re.findall(r"#([A-Za-z0-9_-]+)", after_tag)
        id_part = ""
        for i in ids:
            stem = _NUM_SUFFIX_RE.sub("", i)
            if stem and not _class_token_is_volatile(stem):
                id_part += "#" + stem

        # classes (.foo.bar)
        class_body = "".join(re.findall(r"\.([A-Za-z0-9_-]+)", after_tag))
        class_part = _normalize_classes(class_body)

        attr_part = ""
        for name, op, val in attrs:
            name = name.lower()
            if name in _STABLE_ATTRS:
                v = val.strip().strip("'\"").lower()
                attr_part += f"[{name}={v}]" if v else f"[{name}]"
            # presence-only stable attrs (e.g. [required]) → keep name
            elif not op and not val.strip():
                attr_part += f"[{name}]"
            # volatile/value-bearing non-stable attrs (data-reactid, etc.)
            # are dropped.

        out_parts.append(tag + id_part + class_part + attr_part)

    shape = " ".join(p for p in out_parts if p)
    if has_texts:
        shape = (shape + " " + " ".join(sorted(has_texts))).strip()
    return shape


def _top_selector(block: dict, role: str) -> str:
    sels = block.get(role) or []
    for s in sels:
        if isinstance(s, str) and s.strip():
            return s.strip()
        # dict-form SelectorStep (P5-1): use its selector field
        if isinstance(s, dict) and (s.get("selector") or "").strip():
            return s["selector"].strip()
    return ""


def form_signature(login_block: dict) -> Optional[str]:
    """Compute a structural signature for a site's learned login block.

    The signature is a short hash of the SHAPE of the top (highest-
    confidence, front-of-list) selector in each role. Two sites whose
    forms are structurally identical (same input types/names, same submit
    shape) collide on this signature even with different hosts and
    different rotated class names.

    Returns None when there isn't enough structure to fingerprint — at
    minimum a user OR pass field must be present. (A site with only the
    generic fallback list and no learned selectors must NOT collide with
    every other empty site.)
    """
    if not isinstance(login_block, dict):
        return None
    shapes = {role: selector_shape(_top_selector(login_block, role))
              for role in ROLES}
    if not (shapes["user_field"] or shapes["pass_field"]):
        return None
    canon = "|".join(f"{role}={shapes[role]}" for role in ROLES)
    return hashlib.sha1(canon.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


# ── Confidence (which learned selectors are eligible to share) ──────
def high_confidence_selectors(login_block: dict, *,
                              min_hits: int = DEFAULT_MIN_HITS) -> Dict[str, List[str]]:
    """Return, per role, the learned selectors proven enough to share:
    present in the role list AND with ``hits >= min_hits`` and zero
    misses in ``_per_selector``. A selector with no counter entry is NOT
    eligible (we only share what's actually been exercised)."""
    out: Dict[str, List[str]] = {r: [] for r in ROLES}
    if not isinstance(login_block, dict):
        return out
    counters = login_block.get("_per_selector") or {}
    for role in ROLES:
        for raw in (login_block.get(role) or []):
            sel = raw if isinstance(raw, str) else (
                raw.get("selector") if isinstance(raw, dict) else None)
            sel = (sel or "").strip()
            if not sel:
                continue
            rec = counters.get(sel)
            if not isinstance(rec, dict):
                continue
            if (rec.get("hits") or 0) >= min_hits and (rec.get("misses") or 0) == 0:
                out[role].append(sel)
    return out


# ── Store I/O ───────────────────────────────────────────────────────
def _empty_store() -> dict:
    return {"version": STORE_VERSION, "signatures": {}}


def load_store(path: Optional[str] = None) -> dict:
    """Load the JSON store. Returns a fresh empty store if the file is
    missing or unreadable (never raises — a corrupt store must not break
    login)."""
    path = path or store_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("signatures"), dict):
            data.setdefault("version", STORE_VERSION)
            return data
    except Exception:
        pass
    return _empty_store()


def save_store(store: dict, path: Optional[str] = None) -> None:
    """Atomically persist the store (.tmp sibling + os.replace), utf-8.
    Swallows errors — a failed write must not break login."""
    path = path or store_path()
    try:
        d = os.path.dirname(path) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".cross_site_", suffix=".tmp", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(store, fh, ensure_ascii=False, indent=0)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
    except Exception:
        pass


# ── Record / lookup / augment ───────────────────────────────────────
def record_learned(store: dict, source: str, login_block: dict, *,
                   min_hits: int = DEFAULT_MIN_HITS) -> bool:
    """Fold a site's high-confidence learned login selectors into the
    store under its form signature. Returns True if the store changed.

    Dedups by selector within (signature, role); on a re-record keeps the
    higher hit count; caps each role list at ``MAX_PER_ROLE`` (highest
    hits win)."""
    sig = form_signature(login_block)
    if not sig:
        return False
    source = (source or "").strip().lower() or "unknown"
    hc = high_confidence_selectors(login_block, min_hits=min_hits)
    counters = (login_block.get("_per_selector") or {})
    changed = False
    sig_entry = store.setdefault("signatures", {}).setdefault(sig, {})
    for role in ROLES:
        role_list = sig_entry.setdefault(role, [])
        index = {e["selector"]: e for e in role_list if isinstance(e, dict) and "selector" in e}
        for sel in hc[role]:
            hits = int((counters.get(sel) or {}).get("hits") or min_hits)
            existing = index.get(sel)
            if existing is None:
                role_list.append({"selector": sel, "source": source, "hits": hits})
                changed = True
            else:
                # Same selector seen again — keep best hit count, and if it
                # now comes from this source, refresh the source label.
                if hits > (existing.get("hits") or 0):
                    existing["hits"] = hits
                    existing["source"] = source
                    changed = True
        # Cap (highest hits first, stable).
        if len(role_list) > MAX_PER_ROLE:
            role_list.sort(key=lambda e: e.get("hits", 0), reverse=True)
            del role_list[MAX_PER_ROLE:]
            sig_entry[role] = role_list
            changed = True
    return changed


def lookup(store: dict, signature: str, *,
           exclude_source: Optional[str] = None) -> Dict[str, List[str]]:
    """Return, per role, the cross-site selectors recorded under
    ``signature``, highest-hits first, optionally excluding entries whose
    source matches ``exclude_source`` (a site never reuses its own
    selectors via the cross-site path — it already has them as learned)."""
    out: Dict[str, List[str]] = {r: [] for r in ROLES}
    sig_entry = (store.get("signatures") or {}).get(signature or "")
    if not isinstance(sig_entry, dict):
        return out
    ex = (exclude_source or "").strip().lower()
    for role in ROLES:
        entries = [e for e in (sig_entry.get(role) or [])
                   if isinstance(e, dict) and e.get("selector")]
        if ex:
            entries = [e for e in entries if (e.get("source") or "").lower() != ex]
        entries.sort(key=lambda e: e.get("hits", 0), reverse=True)
        out[role] = [e["selector"] for e in entries]
    return out


def augment_chain(existing: List[str], extras: List[str], *,
                  limit: int = DEFAULT_AUGMENT_LIMIT) -> List[str]:
    """Append up to ``limit`` cross-site selectors to a role's chain as a
    low-priority tail, skipping any already present (dedup preserves the
    site's own ordering). Returns a new list; never mutates ``existing``."""
    out = list(existing or [])
    seen = set(out)
    added = 0
    for sel in (extras or []):
        if added >= limit:
            break
        if sel and sel not in seen:
            out.append(sel)
            seen.add(sel)
            added += 1
    return out


# ── Integration façade (the single call login.py makes) ─────────────
def _source_key(config: dict) -> str:
    """Stable per-site key for the 'source' label and self-exclusion.
    Prefers the login_url host, then base url host, then name/id."""
    if not isinstance(config, dict):
        return "unknown"
    for k in ("login_url", "url", "base_url"):
        u = config.get(k)
        if isinstance(u, str) and u.strip():
            try:
                host = (urlparse(u if "//" in u else "//" + u).hostname or "")
            except Exception:
                host = ""
            if host:
                return host.lower()
    for k in ("name", "id", "sid"):
        v = config.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
    return "unknown"


def sync_and_augment(config: dict, chains: Dict[str, List[str]], *,
                     limit: int = DEFAULT_AUGMENT_LIMIT,
                     verbose: bool = True) -> Dict[str, List[str]]:
    """The one entry point login.py calls.

    No-op (returns ``chains`` unchanged) when disabled or on ANY error.
    When enabled:
      1. records this site's high-confidence learned selectors into the
         store (so the store stays populated from normal operation), then
      2. augments each role chain with a deduped, source-excluded tail of
         cross-site selectors sharing the form signature.

    Wrapped so that a corrupt store, missing BD_HOME, or unexpected config
    shape can never break a login attempt.
    """
    if not enabled():
        return chains
    try:
        login_block = ((config.get("learned") or {}).get("login") or {}) \
            if isinstance(config.get("learned"), dict) else {}
        sig = form_signature(login_block)
        if not sig:
            return chains
        source = _source_key(config)
        store = load_store()
        if record_learned(store, source, login_block):
            save_store(store)
        cross = lookup(store, sig, exclude_source=source)
        out: Dict[str, List[str]] = {}
        total_added = 0
        for role in chains:
            extras = cross.get(role, [])
            augmented = augment_chain(chains[role], extras, limit=limit)
            total_added += len(augmented) - len(chains[role])
            out[role] = augmented
        # roles present in chains but not ROLES pass through untouched
        for role in ROLES:
            out.setdefault(role, chains.get(role, []))
        if verbose and total_added:
            sys.stderr.write(
                f"  login: cross-site reuse added {total_added} selector(s) "
                f"(sig={sig})\n")
        return out
    except Exception as e:  # pragma: no cover - defensive
        sys.stderr.write(f"  login: cross-site reuse skipped ({e})\n")
        return chains
