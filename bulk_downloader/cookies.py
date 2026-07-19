"""Cookie file load/save and expiry tracking."""
import json, time
from pathlib import Path

# ─── COOKIES ──────────────────────────────────────────────────────────────────
def load_cookies_from_file(path):
    with open(path,"r",encoding="utf-8") as f: raw=json.load(f)
    items=[]
    for v in (raw.values() if isinstance(raw,dict) else [raw]):
        if isinstance(v,list): items.extend(v)
    out=[]
    for c in items:
        ss=c.get("sameSite","None")
        if ss not in ("Strict","Lax","None"): ss="None"
        e={"name":c.get("name",""),"value":c.get("value",""),"domain":c.get("domain",""),
           "path":c.get("path","/"),"sameSite":ss,"secure":bool(c.get("secure")),"httpOnly":bool(c.get("httpOnly"))}
        if c.get("expirationDate"): e["expires"]=int(c["expirationDate"])
        out.append(e)
    return out

def pw_to_json(cookies):
    out=[]
    for c in cookies:
        e={k:c.get(k,"") for k in ("name","value","domain","path")}
        e["secure"]=bool(c.get("secure")); e["httpOnly"]=bool(c.get("httpOnly"))
        e["sameSite"]=c.get("sameSite","None")
        if c.get("expires",0)>0: e["expirationDate"]=c["expires"]
        out.append(e)
    return out

def save_cookies_to_file(path, cookies, *, validate: bool = True):
    """Persist cookies to JSON. Used after manual takeover login to save
    captured cookies to the configured Cookie File Path.

    v3.36.8: explicit UTF-8 encoding. On Windows, Path.write_text uses
    the system default (cp1252 on US Windows), which can't encode
    non-ASCII cookie values. Cookies with international characters
    (paywalled German/Russian/Japanese sites) would crash with
    UnicodeEncodeError.

    v3.43.19: atomic write via .tmp + replace. Without this, a crash
    during the write after manual login leaves a truncated file; the
    next runner startup reads malformed JSON and the user's just-
    captured manual login is lost — they have to do it again. The
    rename pattern guarantees the cookie file is always old-complete
    or new-complete.

    v3.43.32: round-trip validation after write. We read the file
    back, re-parse it, and verify the cookie count matches what we
    wrote. Catches:
      - Partial writes (disk filled mid-flush, replace() silently
        truncated the temp file)
      - Encoding corruption (filesystem with no UTF-8 support)
      - Format drift (some sneaky path bypasses this writer and we
        end up with malformed JSON)
      - Permission issues (write 'succeeded' but the file isn't
        readable by the same process)

    Raises CookieRoundTripError when validation fails; the caller
    should treat this as a save failure and retry. Pass
    validate=False to skip (useful for unit tests that mock
    filesystems).
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(cookies, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    # AF5 (v3.66.42): cookie jars are session credentials — publish
    # owner-only (0600) rather than at the umask default. Best-effort.
    try: tmp.chmod(0o600)
    except Exception: pass
    tmp.replace(p)
    if validate:
        _verify_cookie_roundtrip(p, expected_count=len(cookies))
    return p


class CookieRoundTripError(Exception):
    """Raised when save_cookies_to_file's post-write validation fails.
    Carries a `kind` string so callers can route the response:
      - 'unreadable': file can't be opened / parsed
      - 'count_mismatch': expected N cookies, got M
      - 'shape': top-level structure is wrong (not a list/dict)
    """
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _verify_cookie_roundtrip(path: Path, expected_count: int):
    """Read the just-written file back and verify it parses and has
    the expected number of cookies. Called by save_cookies_to_file
    after the atomic replace.

    Why expected_count instead of full deep-equality:
      - JSON serialization is lossy on some keys (e.g. ints might
        round-trip as floats in some libs); checking the count
        catches truncation without flagging legitimate format
        variation
      - Cheap: one parse, one len() check
      - In practice every corruption mode we've seen (disk full,
        encoding mismatch) drops or duplicates cookies, so count
        is the right signal
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        raise CookieRoundTripError(
            "unreadable",
            f"cookie file {path} unreadable after write: "
            f"{type(e).__name__}: {e}")
    # Accept both the canonical list shape AND the dict-of-domain
    # shape (Phase 9 / extension-vault legacy) — both are valid on
    # disk and parsable by load_cookies_from_file
    if isinstance(data, list):
        actual = len(data)
    elif isinstance(data, dict):
        # Sum across nested lists
        actual = sum(len(v) for v in data.values()
                      if isinstance(v, list))
    else:
        raise CookieRoundTripError(
            "shape",
            f"cookie file {path} has unexpected root type "
            f"{type(data).__name__} after write")
    if actual != expected_count:
        raise CookieRoundTripError(
            "count_mismatch",
            f"cookie file {path}: wrote {expected_count} cookies "
            f"but read back {actual}")

def cookies_expiry_info(cookies):
    now=time.time(); session=expired=expiring=0; earliest=None
    for c in cookies:
        exp=c.get("expires",0) or c.get("expirationDate",0)
        if not exp: session+=1
        elif exp<now: expired+=1
        elif exp<now+86400: expiring+=1; earliest=min(earliest,exp) if earliest else exp
        else: earliest=min(earliest,exp) if earliest else exp
    return {"session":session,"expired":expired,"expiring_soon":expiring,"earliest":earliest}

def cookie_age_str(saved_at):
    if not saved_at: return ""
    s=time.time()-saved_at
    if s<3600: return f"{int(s//60)}m ago"
    if s<86400: return f"{int(s//3600)}h ago"
    return f"{int(s//86400)}d ago"
