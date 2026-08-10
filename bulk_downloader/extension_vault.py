"""v3.43.14 (extension bridge): vault-scoped tokens + origin matching.

Lets the browser extension fetch saved passwords for autofill in any
website, separate from the queue-pairing flow. Three pieces:

  ─ Token issuance ─
    Pair the extension with the vault via a one-time QR code, distinct
    from the URL-queue pairing token. The vault token has narrower scope
    (only the /api/secrets/extension/* endpoints) so a leaked vault token
    can't be used to queue downloads or modify the app, and vice versa.

  ─ Origin matching ─
    When the extension asks "what entries match this page?", we filter
    by the page's URL pattern. Each stored entry has a `patterns` list
    (regex or substring); we match against the page's full URL. A future
    eTLD+1 helper is here too for cases where the entry stored only a
    bare hostname.

  ─ Rate limiting ─
    - Per-entry: max 1 fetch every 5 seconds. Stops a hijacked vault
      token from harvesting every password in one burst.
    - Per-token: max 30 fetches per minute. Catches sustained abuse.
    Both reset by deleting the token (re-pair).

  ─ Audit log ─
    Every successful fetch writes one line to vault_access.log so the
    user can see what got served and when.
"""
from __future__ import annotations

import json
import re
import secrets as _stdlib_secrets
import sys
import threading
import time
from pathlib import Path


VAULT_TOKENS_FILE = Path("vault_tokens.json")
VAULT_AUDIT_LOG = Path("vault_access.log")

# Tokens never used for vault access also get GC'd after this many days
TOKEN_IDLE_EXPIRY_DAYS = 30

# B10 (v3.66.43): minimum gap between last_used_at disk writes. Coalesces
# the per-request rewrite of vault_tokens.json on busy autofill sessions.
LAST_USED_COALESCE_SECONDS = 60.0

# Per-entry minimum interval between successful fetch_one calls
PER_ENTRY_COOLDOWN_SECONDS = 5.0
# Per-token max successful fetch_one calls in a 60-second sliding window
PER_TOKEN_RATE_LIMIT = 30
PER_TOKEN_WINDOW_SECONDS = 60.0

# Pairing tokens (pre-redemption) expire faster — they're meant to be
# scanned within seconds, not days
PAIRING_TOKEN_EXPIRY_SECONDS = 600


_lock = threading.RLock()


def _now() -> float:
    return time.time()


# ─── Token storage ───────────────────────────────────────────────

def _load_tokens() -> dict:
    """Read vault_tokens.json. Missing/malformed -> empty dict."""
    if not VAULT_TOKENS_FILE.exists():
        return {"pairing": {}, "redeemed": {}}
    try:
        data = json.loads(VAULT_TOKENS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict): raise ValueError("not a dict")
        data.setdefault("pairing", {})
        data.setdefault("redeemed", {})
        return data
    except Exception as e:
        # AF4 (v3.66.41): don't silently wipe an unparseable token store —
        # that would drop every paired extension and let the next save
        # overwrite recoverable data. Move it aside, then reinit fresh.
        try:
            bak = VAULT_TOKENS_FILE.with_name(
                VAULT_TOKENS_FILE.name + f".corrupt-{int(_now())}")
            VAULT_TOKENS_FILE.replace(bak)
            sys.stderr.write(
                f"  vault_tokens.json malformed: {e}; backed up to {bak}; reinit\n")
        except Exception as e2:
            sys.stderr.write(
                f"  vault_tokens.json malformed: {e}; backup failed: {e2}; reinit\n")
        return {"pairing": {}, "redeemed": {}}


def _save_tokens(data: dict) -> bool:
    """Atomic write via tmpfile + rename. Returns True on success, False
    on failure (AF3, v3.66.41) so security-relevant callers — revocation —
    can tell whether the change actually persisted rather than reporting a
    phantom success that a leaked token would survive."""
    tmp = VAULT_TOKENS_FILE.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        # AF5 (v3.66.42): vault_tokens.json holds LIVE bearer tokens —
        # publish it owner-only (0600), never world-readable. Best-effort.
        try: tmp.chmod(0o600)
        except Exception: pass
        tmp.replace(VAULT_TOKENS_FILE)
        return True
    except Exception as e:
        sys.stderr.write(f"  vault_tokens save failed: {e}\n")
        try:
            if tmp.exists(): tmp.unlink()
        except Exception:
            pass
        return False


# ─── Pairing flow ─────────────────────────────────────────────────

def issue_pairing_token() -> str:
    """Generate a short-lived pairing token. Returned to the user as a
    QR code or copyable string. Until redeemed (by the extension calling
    redeem_pairing_token with the same value), this token grants nothing.

    After redemption, the token is consumed and replaced with a long-
    lived vault token that the extension stores in chrome.storage.local."""
    with _lock:
        data = _load_tokens()
        # Clean up expired pairing tokens while we're here. NEW-12
        # (v3.66.43): use the shared _pairing_expired predicate so the
        # cleanup and redeem checks can't diverge at the boundary tick.
        data["pairing"] = {
            tok: ts for tok, ts in data["pairing"].items()
            if not _pairing_expired(ts)
        }
        token = _stdlib_secrets.token_urlsafe(24)
        data["pairing"][token] = _now()
        _save_tokens(data)
        return token


def _pairing_expired(ts: float) -> bool:
    """NEW-12 (v3.66.43): single source of truth for pairing-token
    expiry, shared by cleanup (issue) and rejection (redeem)."""
    return _now() - ts > PAIRING_TOKEN_EXPIRY_SECONDS


_LABEL_BAD_CHARS = re.compile(r"[^\w .\-]", re.UNICODE)


def _sanitize_label(label: str) -> str:
    """NEW-10 (v3.66.43): sanitize the attacker-controllable extension
    label at intake. Strips control chars and HTML/JS metacharacters so
    a malicious label can't carry an XSS payload into the operator's
    settings page (belt-and-suspenders alongside a frontend audit)."""
    if not isinstance(label, str):
        return "extension"
    cleaned = _LABEL_BAD_CHARS.sub("_", label)[:60].strip()
    return cleaned or "extension"


def redeem_pairing_token(pairing_token: str, extension_label: str = "") -> str | None:
    """Consume a pairing token and issue a long-lived vault token.
    Returns the new vault token on success, or None if the pairing
    token is unknown, already used, or expired.

    The vault token is what the extension stores and sends as the
    Authorization header on subsequent vault API calls."""
    with _lock:
        data = _load_tokens()
        pairing = data.get("pairing") or {}
        ts = pairing.get(pairing_token)
        if ts is None: return None
        if _pairing_expired(ts):
            del pairing[pairing_token]
            _save_tokens(data)
            return None
        # Consume the pairing token
        del pairing[pairing_token]
        # Issue the long-lived vault token
        vault_token = _stdlib_secrets.token_urlsafe(32)
        data.setdefault("redeemed", {})[vault_token] = {
            "issued_at": _now(),
            "last_used_at": _now(),
            "label": _sanitize_label(extension_label),
            # Sliding-window fetch timestamps (kept up to PER_TOKEN_WINDOW_SECONDS old)
            "recent_fetches": [],
            # Per-entry last-fetch timestamps
            "entry_cooldowns": {},
        }
        _save_tokens(data)
        return vault_token


def revoke_vault_token(vault_token: str) -> bool:
    """Permanently invalidate a vault token. Returns True if it existed
    and was removed."""
    with _lock:
        data = _load_tokens()
        if vault_token in (data.get("redeemed") or {}):
            del data["redeemed"][vault_token]
            if not _save_tokens(data):
                return False   # AF3: revocation did not persist
            return True
        return False


def list_vault_tokens() -> list[dict]:
    """Return all redeemed vault tokens with metadata (labels,
    timestamps). Used by the settings UI to show 'You have 2 extensions
    paired' with revoke buttons. Does NOT return the raw token values —
    just shortened prefixes for identification."""
    data = _load_tokens()
    out = []
    for tok, meta in (data.get("redeemed") or {}).items():
        out.append({
            "id": tok[:8],  # short prefix for the UI
            "label": meta.get("label", "extension"),
            "issued_at": meta.get("issued_at", 0),
            "last_used_at": meta.get("last_used_at", 0),
        })
    return sorted(out, key=lambda x: -x["last_used_at"])


def revoke_by_prefix(prefix: str) -> bool:
    """Revoke a vault token by its short ID prefix. UI-friendly version
    of revoke_vault_token."""
    # B9 (v3.66.43): str.startswith("") is True for every token, so an
    # empty/very-short prefix would revoke an arbitrary token. Require
    # >=4 chars — any 4-char slice of a token_urlsafe(32) prefix is
    # overwhelmingly unique. Defense in depth; the route also guards.
    if not prefix or len(prefix) < 4:
        return False
    with _lock:
        data = _load_tokens()
        redeemed = data.get("redeemed") or {}
        for tok in list(redeemed.keys()):
            if tok.startswith(prefix):
                del redeemed[tok]
                if not _save_tokens(data):
                    return False   # AF3: revocation did not persist
                return True
    return False


# ─── Validation ───────────────────────────────────────────────────

def validate_vault_token(vault_token: str) -> dict | None:
    """Check a token presented in a request. Returns the token's metadata
    dict if valid, None if not. Updates last_used_at as a side effect."""
    if not vault_token: return None
    with _lock:
        data = _load_tokens()
        redeemed = data.get("redeemed") or {}
        meta = redeemed.get(vault_token)
        if meta is None: return None
        # Lazy GC of long-idle tokens
        last_used = meta.get("last_used_at", 0)
        now = _now()
        if last_used and now - last_used > TOKEN_IDLE_EXPIRY_DAYS * 86400:
            # NEW-4 (v3.66.43): honor the _save_tokens bool (AF3 only
            # covered the explicit-revoke callers). If the GC write
            # fails, restore the in-memory delete so memory matches
            # disk — otherwise the token "GCs" in RAM but survives on
            # disk and reappears on the next _load_tokens().
            snapshot = redeemed[vault_token]
            del redeemed[vault_token]
            if not _save_tokens(data):
                redeemed[vault_token] = snapshot
                sys.stderr.write(
                    f"[extension_vault] lazy GC failed to persist; "
                    f"token {vault_token[:8]} left intact\n")
            return None
        # B10 (v3.66.43): coalesce the last_used_at write. Every
        # autofill request previously rewrote the whole file; the
        # 30-day idle-GC threshold swamps a 60s coalesce window, so a
        # busy session no longer thrashes vault_tokens.json.
        meta["last_used_at"] = now
        if not last_used or now - last_used >= LAST_USED_COALESCE_SECONDS:
            _save_tokens(data)
        return meta


# ─── Origin matching ──────────────────────────────────────────────

def get_registrable_domain(url_or_host: str) -> str:
    """Lightweight eTLD+1 derivation. Strips schemes, paths, ports;
    returns the last two dot-segments of the hostname.

    Limitation: doesn't know about multi-part TLDs like .co.uk or
    .com.au — those would return 'co.uk' instead of 'example.co.uk'.
    A full publicsuffix list would fix that but adds 200KB+ of data;
    not worth it for a self-hosted single-user tool. Edge cases can
    be handled by storing more-specific regex patterns on entries."""
    if not url_or_host: return ""
    s = url_or_host.strip().lower()
    # Strip scheme if present
    if "://" in s: s = s.split("://", 1)[1]
    # Strip everything after the path. KEPT: this function accepts a
    # SCHEME-LESS "host/path", which registrable_domain's normalizer does not
    # strip (it only urlparses when a scheme is present). Dropping this would
    # silently hand it "example.com/foo" as a hostname.
    s = s.split("/", 1)[0]
    from .registrable_domain import registrable_domain
    return registrable_domain(s)


def get_hostname(url_or_host: str) -> str:
    """Extract the full hostname from a URL or host string.

    Strips scheme, credentials, path, and port. Preserves the full
    dotted name (so `https://example.co.uk:8443/x` returns `example.co.uk`,
    not `co.uk` like get_registrable_domain does). Safer for use as
    an autofill match anchor — won't over-match across siblings on a
    shared public-suffix domain."""
    if not url_or_host: return ""
    s = url_or_host.strip().lower()
    if "://" in s: s = s.split("://", 1)[1]
    s = s.split("/", 1)[0]
    s = s.rsplit("@", 1)[-1]  # creds before port
    s = s.split(":", 1)[0]
    # B20 (v3.66.40): normalize IDN/Unicode hosts to their ASCII punycode
    # form so a homograph host (e.g. a Cyrillic-"a" "exаmple.com") can't be
    # confused with or impersonate an ASCII host during autofill matching.
    # All-ASCII hosts are left byte-identical (the idna codec is skipped).
    # Fall back to the raw host on any encoding error (empty/oversized
    # labels, etc.) — normalization must never make matching throw.
    if s and not s.isascii():
        try:
            s = s.encode("idna").decode("ascii")
        except Exception:
            pass
    return s


def _host_suffixes(host: str) -> list[str]:
    """Dot-bounded suffixes of a hostname, longest first.

    ``a.b.example.com`` -> ``[a.b.example.com, b.example.com, example.com, com]``.

    Used so an autofill pattern can match the request host or any of its
    parent domains, but NEVER an unrelated host that merely contains the
    saved name as a substring (that was the B1 phishing vuln)."""
    if not host:
        return []
    parts = host.split(".")
    return [".".join(parts[i:]) for i in range(len(parts))]


def hostname_pattern(url_or_host: str) -> str:
    """Build a regex pattern that matches the literal hostname.

    Used as a safer default than the bare eTLD+1 when auto-suggesting
    autofill entries. The result is `re.escape()`'d so dots in the
    hostname don't accidentally act as wildcards."""
    h = get_hostname(url_or_host)
    return re.escape(h) if h else ""


def entries_matching_origin(
    url: str,
    all_entries: list[dict],
) -> list[dict]:
    """Return entries whose `patterns` list matches the URL. Each
    pattern is tried as regex first, falling back to substring match
    if regex compile fails.

    Entries without patterns (the user saved without an auto-suggest
    pattern) don't match anything automatically — they only show up via
    the manual override "show all entries" button in the popup.

    Returns the entries unchanged from the input (does NOT include
    the password)."""
    if not url or not all_entries: return []
    # B1 (v3.66.38): match each pattern against the dot-bounded suffixes of
    # the request HOSTNAME, never as a substring of the full URL. The old
    # ``re.search(pat, url)`` let an attacker page whose URL merely
    # *contained* a saved host as a substring
    # (``https://attacker.com/?next=login.example.com``) harvest the saved
    # password. fullmatch (not match) against each suffix also rejects
    # ``example.com.attacker.com``.
    host = get_hostname(url)
    if not host: return []
    suffixes = _host_suffixes(host)
    target_etld1 = get_registrable_domain(url)
    out = []
    for entry in all_entries:
        patterns = entry.get("patterns") or []
        if not patterns: continue
        for pat in patterns:
            if not isinstance(pat, str): continue
            p = pat.lower().strip()
            if not p: continue
            try:
                rx = re.compile(p)
                if any(rx.fullmatch(suf) for suf in suffixes):
                    out.append(entry); break
            except re.error:
                # Pattern isn't valid regex — exact suffix equality only,
                # never substring (substring is the bug we're closing).
                if p in suffixes:
                    out.append(entry); break
                # Last-ditch: bare-domain pattern vs eTLD+1.
                if p.strip(r"\.^$()") == target_etld1:
                    out.append(entry); break
    return out


# ─── Rate limiting ────────────────────────────────────────────────

def check_and_record_fetch(vault_token: str, entry_key: str) -> tuple[bool, str]:
    """Atomically check whether a fetch is allowed and, if so, record it.

    Returns (True, "") on allow, (False, reason_string) on deny.

    Two limits enforced:
      - Per-entry cooldown: same entry can't be fetched twice within
        PER_ENTRY_COOLDOWN_SECONDS (default 5). Caps the rate at which
        a compromised token can extract any single password.
      - Per-token sliding window: max PER_TOKEN_RATE_LIMIT fetches in
        the last PER_TOKEN_WINDOW_SECONDS. Catches sustained abuse.
    """
    now = _now()
    with _lock:
        data = _load_tokens()
        meta = (data.get("redeemed") or {}).get(vault_token)
        if meta is None: return False, "invalid token"

        # Per-entry cooldown
        cooldowns = meta.setdefault("entry_cooldowns", {})
        last = cooldowns.get(entry_key, 0)
        if last and (now - last) < PER_ENTRY_COOLDOWN_SECONDS:
            wait = PER_ENTRY_COOLDOWN_SECONDS - (now - last)
            return False, f"entry on cooldown, retry in {wait:.1f}s"

        # Per-token sliding window
        recent = meta.setdefault("recent_fetches", [])
        cutoff = now - PER_TOKEN_WINDOW_SECONDS
        recent = [t for t in recent if t > cutoff]
        if len(recent) >= PER_TOKEN_RATE_LIMIT:
            return False, f"too many fetches ({PER_TOKEN_RATE_LIMIT}/min limit)"

        # Allow + record
        recent.append(now)
        meta["recent_fetches"] = recent
        cooldowns[entry_key] = now
        meta["last_used_at"] = now
        _save_tokens(data)
        return True, ""


# ─── Audit log ────────────────────────────────────────────────────

_AUDIT_CTRL_CHARS = "".join(chr(c) for c in range(32)) + "\x7f"
_AUDIT_TRANSLATION = str.maketrans({c: " " for c in _AUDIT_CTRL_CHARS})


def _audit_sanitize(s: str, *, max_len: int = 512) -> str:
    """NEW-3 (v3.66.43): strip control chars (incl. newline and tab) from
    caller-controlled audit fields so a vault-token holder can't forge
    log lines or shift field meaning via embedded \\n / \\t."""
    if not isinstance(s, str):
        return ""
    return s.translate(_AUDIT_TRANSLATION)[:max_len]


def audit_fetch(token_meta: dict, entry_key: str, origin: str,
                success: bool, reason: str = "") -> None:
    """Append one line to vault_access.log. Best-effort; failures don't
    block the fetch."""
    try:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        label = _audit_sanitize((token_meta or {}).get("label", "?"),
                                max_len=120)
        entry_key = _audit_sanitize(entry_key, max_len=200)
        origin = _audit_sanitize(origin, max_len=512)
        if success:
            status = "ok"
        else:
            status = "deny:" + _audit_sanitize(reason, max_len=80)
        line = f"{ts}\t{label}\t{entry_key}\t{origin}\t{status}\n"
        with open(VAULT_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
