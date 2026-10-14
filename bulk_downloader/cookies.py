"""Cookie file load/save and expiry tracking."""
import json, os, tempfile, time
from pathlib import Path

# ─── COOKIES ──────────────────────────────────────────────────────────────────
_SAME_SITE={"strict":"Strict","lax":"Lax","none":"None","no_restriction":"None"}

def normalize_stored_cookie(c):
    """One stored-form cookie dict -> BD's in-memory shape.

    Single source for the disk loader below and for the
    /api/sites/<sid>/load_cookies upload endpoint, which carried a
    byte-identical copy of this loop -- including the same defect.

    `expirationDate` becomes `expires` only when it is POSITIVE. Playwright
    marks a session cookie with `expires: -1` (login_impl/replay.py:476) and
    the browser-extension exporters emit the same for a cookie with no
    Expires/Max-Age. The old guard was `if c.get("expirationDate"):`, and -1
    is truthy, so the sentinel was copied through as though it were a real
    timestamp -- which cookies_expiry_info then graded EXPIRED, telling
    runner_auth to re-login a session that was live. pw_to_json has always
    guarded the same field with `> 0`; this keeps the two directions
    symmetrical, so a jar cannot change meaning by round-tripping through
    disk.

    `sameSite` follows what Chromium stores (fx-cookie-samesite-unspecified):
    extension exporters write "unspecified", "no_restriction" and lowercase
    values, and Chromium treats an unspecified SameSite as Lax. Anything that
    is not strict/none therefore loads as Lax. "None" is kept only on a Secure
    cookie: Chromium's add_cookies accepts SameSite=None without Secure and
    then silently drops it, which lost non-Secure session cookies (SID) from
    operator imports and landed workers on the login page.
    """
    secure=bool(c.get("secure"))
    ss=_SAME_SITE.get(str(c.get("sameSite") or "").strip().lower(),"Lax")
    if ss=="None" and not secure: ss="Lax"
    e={"name":c.get("name",""),"value":c.get("value",""),"domain":c.get("domain",""),
       "path":c.get("path","/"),"sameSite":ss,"secure":secure,"httpOnly":bool(c.get("httpOnly"))}
    exp=c.get("expirationDate")
    if exp and float(exp)>0: e["expires"]=int(exp)
    return e

def load_cookies_from_file(path):
    p = Path(path)
    if not p.exists():
        # Row 893: fallback to cluster vault if local cookie file does not exist
        try:
            from .vault_sync import get_vault_sync
            vs = get_vault_sync()
            if vs is not None:
                sid = p.stem
                if sid:
                    sess = vs.get_session(sid, "0")
                    if sess and "cookies" in sess and isinstance(sess["cookies"], list):
                        return [normalize_stored_cookie(c) for c in sess["cookies"]]
        except Exception:
            pass
        return []
    with open(path,"r",encoding="utf-8") as f: raw=json.load(f)
    items=[]
    for k,v in (raw.items() if isinstance(raw,dict) else [(None,raw)]):
        # aac-fix r2 (D170 R1): a Playwright storage_state's "origins" holds
        # localStorage per origin, not cookies; read as cookies it added a
        # nameless, domainless entry that fails the whole add_cookies batch.
        if isinstance(v,list) and k!="origins": items.extend(v)
    return [normalize_stored_cookie(c) for c in items]

def cookie_jar_state(path):
    """dl95-hqporner-1: "cookies", "empty" or "unreadable" for the jar that
    load_cookies_from_file(path) reads, including its vault fallback. Unlike
    the loader, an unreadable file or vault is not reported as empty."""
    p = Path(path)
    if p.exists():
        try:
            return "cookies" if load_cookies_from_file(p) else "empty"
        except Exception:
            return "unreadable"
    try:
        from .vault_sync import get_vault_sync
        vs = get_vault_sync()
        if vs is None or not p.stem:
            return "empty"
        status, sess = vs.lookup_session(p.stem, "0")
    except Exception:
        return "unreadable"
    if status == "unreadable":
        return "unreadable"
    cookies = (sess or {}).get("cookies")
    return "cookies" if isinstance(cookies, list) and cookies else "empty"


def pw_to_json(cookies):
    out=[]
    for c in cookies:
        e={k:c.get(k,"") for k in ("name","value","domain","path")}
        e["secure"]=bool(c.get("secure")); e["httpOnly"]=bool(c.get("httpOnly"))
        e["sameSite"]=c.get("sameSite","None")
        if c.get("expires",0)>0: e["expirationDate"]=c["expires"]
        out.append(e)
    return out

def is_cookie_jar_text(text):
    """True when `text` is a cookie jar in a shape load_cookies_from_file
    reads. The one jar test for /cookies/import, the export and every saver
    (aac-fix r2, D170 R1: import took jars that export and the login savers
    then refused, so a site's cookies stopped persisting):

      * a JSON list of cookie objects (each with "name" and "value");
      * an object holding at least one such list (a "cookies" key may hold an
        empty one), where every other list is a storage_state "origins" list
        of {"origin": ...} objects and anything else is metadata ("saved_at",
        a version, ...) -- a Playwright storage_state is one;
      * an empty jar, [] or {}.

    aac-fix F12: cookie_file is an operator-set path. Export reads it back and
    the savers replace it, so neither may treat an arbitrary file under the
    allowed roots (config, state, vault.key) as a jar."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return False

    def _is_cookie(c):
        return isinstance(c, dict) and "name" in c and "value" in c

    if isinstance(data, list):
        return all(_is_cookie(c) for c in data)
    if not isinstance(data, dict):
        return False
    if not data:
        return True
    cookie_lists = 0
    for key, v in data.items():
        if not isinstance(v, list):
            continue
        if (v and all(_is_cookie(c) for c in v)) or (key == "cookies" and not v):
            cookie_lists += 1
        elif key == "origins" and all(
                isinstance(o, dict) and "origin" in o for o in v):
            continue
        else:
            return False
    return cookie_lists > 0


class CookieFileNotAJarError(ValueError):
    """save_cookies_to_file refused to replace an existing non-empty file that
    is not a cookie jar (aac-fix F12). A ValueError, so the login savers'
    existing `except (OSError, ValueError)` log it instead of crashing."""


# The directory the process started in, resolved once: the BD_HOME fallback,
# so a later chdir never moves a jar (session-jar-integrity F14).
try:
    _START_DIR = Path.cwd().resolve()
except OSError:
    _START_DIR = None
_BD_HOMES = {}


def _bd_home():
    """BD_HOME as an absolute path, resolved once per value; unset -> the
    directory the process started in. Never the cwd of the moment."""
    raw = os.environ.get("BD_HOME") or ""
    home = _BD_HOMES.get(raw)
    if home is None:
        home = Path(raw).resolve() if raw else (_START_DIR or Path.cwd().resolve())
        _BD_HOMES[raw] = home
    return home


def cookie_jar_path(site_id, account_idx=0, site_config=None, root=None):
    """The jar a (site, account) session lives in (session-jar-integrity
    F2/F14) -- the one resolver for the keeper and the relogin callback.

    The site's configured cookie_file when set, else <BD_HOME>/cookies
    (`root` overrides that directory). Account 0 keeps the plain name
    (<sid>.json, or the cookie_file itself), so existing installs need no
    migration; account N>0 gets the __a<N> stem suffix. One shared path per
    site let account 1's keeper overwrite account 0's session, and a
    cwd-relative path made the keeper miss the jar the app had saved."""
    idx = int(account_idx or 0)
    cfg = site_config if isinstance(site_config, dict) else {}
    configured = str(cfg.get("cookie_file") or "").strip()
    if configured:
        p = Path(configured)
        return p if idx == 0 else p.with_name(f"{p.stem}__a{idx}{p.suffix}")
    name = f"{site_id}.json" if idx == 0 else f"{site_id}__a{idx}.json"
    return (Path(root) if root is not None else _bd_home() / "cookies") / name


def _jar_site_id(p, account_idx):
    """The vault site id for jar path `p`: its stem, less the __a<N>
    suffix cookie_jar_path adds for account N>0."""
    sid, tag = p.stem, f"__a{int(account_idx or 0)}"
    if account_idx and sid.endswith(tag):
        sid = sid[:-len(tag)]
    return sid


def _replace_with_text(dest, text):
    """Write `text` to a unique temp file beside `dest` (owner-only), then
    os.replace it over `dest`. A unique name per call: two writers on the
    same jar used to share <file>.tmp, and the loser's replace raised
    FileNotFoundError (session-jar-integrity S34)."""
    fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent),
                                    prefix=dest.name + ".", suffix=".tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_text(text, encoding="utf-8")
        # AF5 (v3.66.42): cookie jars are session credentials — publish
        # owner-only (0600) rather than at the umask default. Best-effort.
        try: tmp.chmod(0o600)
        except Exception: pass
        tmp.replace(dest)
    except BaseException:
        try: tmp.unlink()
        except OSError: pass
        raise


def save_cookies_to_file(path, cookies, *, validate: bool = True,
                         account_idx: int = 0):
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

    session-jar-integrity: the previous good jar is kept as <file>.bak before
    every replace, and the vault mirror goes to (site, account_idx) only --
    it used to land on account "0" whatever account saved.
    """
    p = Path(path)
    existing, parses = None, False
    # aac-fix F12: never overwrite a file that is not a cookie jar -- a JSON
    # document of another shape, or a non-JSON file not named *.json. A *.json
    # file that does not parse is a damaged jar and stays replaceable, so a
    # login / keepalive can still heal it.
    if p.is_file() and p.stat().st_size:
        try:
            existing = p.read_text(encoding="utf-8")
            json.loads(existing)
            parses = True
        except (OSError, UnicodeDecodeError, ValueError):
            existing, parses = None, False
        if (parses and not is_cookie_jar_text(existing)) or (
                not parses and p.suffix.lower() != ".json"):
            raise CookieFileNotAJarError(
                f"refusing to overwrite {p}: it exists and is not a cookie jar")
    p.parent.mkdir(parents=True, exist_ok=True)
    if parses:
        # a recovery copy of the jar this save replaces; a damaged jar
        # (parses False) never overwrites a good .bak
        _replace_with_text(p.with_name(p.name + ".bak"), existing)
    _replace_with_text(p, json.dumps(cookies, indent=2, ensure_ascii=False))
    if validate:
        _verify_cookie_roundtrip(p, expected_count=len(cookies))
    # Row 893: synchronize encrypted session state to cluster vault
    try:
        from .vault_sync import get_vault_sync
        vs = get_vault_sync()
        if vs is not None:
            sid = _jar_site_id(p, account_idx)
            if sid:
                vs.set_session(sid, str(int(account_idx or 0)),
                               {"cookies": cookies})
    except Exception:
        pass
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
        try: exp=float(exp or 0)
        except (TypeError, ValueError): exp=0.0
        # A NON-POSITIVE expiry is the session marker, not a timestamp in
        # 1969. The old test was `if not exp`, which is truthiness, so -1
        # fell through to `exp < now` and a live session cookie was counted
        # EXPIRED. runner_auth._check_cookies_or_relogin keys on exactly
        # this pair (`expired <= 0 or session != 0`), so the miscount forced
        # a re-login -- or, with no stored credentials, routed the URL to
        # _handle_failure on a working login.
        if exp<=0: session+=1
        elif exp<now: expired+=1
        elif exp<now+86400: expiring+=1; earliest=min(earliest,exp) if earliest else exp
        else: earliest=min(earliest,exp) if earliest else exp
    return {"session":session,"expired":expired,"expiring_soon":expiring,"earliest":earliest}

def jar_fingerprint(cookies):
    """Order-independent identity of a cookie jar's contents (domain, path,
    name, value). Two reads of the same jar compare equal; any login that
    replaces a cookie value changes it."""
    import hashlib
    rows = sorted(
        "\t".join(str(c.get(k) or "") for k in ("domain", "path", "name", "value"))
        for c in (cookies or []))
    return hashlib.sha1("\n".join(rows).encode("utf-8")).hexdigest()

def cookie_age_str(saved_at):
    if not saved_at: return ""
    s=time.time()-saved_at
    if s<3600: return f"{int(s//60)}m ago"
    if s<86400: return f"{int(s//3600)}h ago"
    return f"{int(s//86400)}d ago"
