"""v3.43.14: Password file import.

Reads exports from common password managers and returns a normalized
list of {name, url, username, password, notes} entries. The caller
(app.py) presents these in a UI for the user to select which ones to
import, and then calls secrets_store.set() for each selected entry.

Supported formats (auto-detected from file content):

  Bitwarden JSON       - .json with {"items": [...]}, each item has
                         .name, .login.username, .login.password,
                         .login.uris[*].uri, .notes
  LastPass CSV         - .csv with columns: url, username, password,
                         extra, name, grouping, fav
  Chrome / Edge CSV    - .csv with columns: name, url, username,
                         password, note  (some exports omit note)
  1Password 1PIF       - JSONL where each line is a record with
                         secureContents.fields[*] and overview.url
  1Password CSV        - .csv with columns: title, website, username,
                         password, notes  (newer 1Password export)

If a file format can't be detected, raises FormatNotRecognized. The
caller surfaces this to the user with a "couldn't parse this file"
message.

No file we accept is treated as fully trusted: the caller still shows
a preview UI before committing anything to the backend.
"""
from __future__ import annotations

import csv
import io
import json


class FormatNotRecognized(Exception):
    """Raised when the file content doesn't match any known importer."""


# ─── Normalized record ───────────────────────────────────────────────

def _record(name: str, url: str = "", username: str = "",
            password: str = "", notes: str = "") -> dict:
    """Build a normalized password record. All fields are strings;
    missing fields become empty strings (never None)."""
    return {
        "name": (name or "").strip(),
        "url": (url or "").strip(),
        "username": (username or "").strip(),
        "password": password or "",  # don't strip — leading/trailing spaces matter
        "notes": (notes or "").strip(),
    }


# ─── Case-insensitive CSV header access (B7, v3.66.43) ───────────────
#
# csv.DictReader keys each row by the EXACT header text. The format-
# detection checks lowercase the fieldnames, so a Chrome/LastPass/
# 1Password export re-saved through Excel with Title-cased headers
# ("Name,URL,Username,Password") passes detection but every
# row.get("name") returns None and all values come out empty. Route
# row access through a {lowercased_header: original_header} map.

def _build_field_map(fieldnames) -> dict:
    if not fieldnames:
        return {}
    return {f.strip().lower(): f for f in fieldnames if isinstance(f, str)}


def _row_get(row: dict, field_map: dict, key: str) -> str:
    original = field_map.get(key)
    if original is None:
        return ""
    return row.get(original) or ""


# ─── Bitwarden JSON ──────────────────────────────────────────────────

def _parse_bitwarden(text: str) -> list[dict]:
    """Bitwarden export format. The top-level object has an 'items' list
    and an 'encrypted' boolean. We only accept unencrypted exports
    here — encrypted Bitwarden exports require the user's master
    password to decrypt and that's out of scope for this importer."""
    data = json.loads(text)
    if not isinstance(data, dict): raise FormatNotRecognized("not bitwarden")
    if data.get("encrypted") is True:
        raise FormatNotRecognized(
            "Bitwarden export is encrypted. Re-export with "
            "'Export as: .json (unencrypted)' and try again.")
    items = data.get("items")
    if not isinstance(items, list): raise FormatNotRecognized("no items array")
    out = []
    for item in items:
        if not isinstance(item, dict): continue
        if item.get("type") != 1: continue  # type 1 = login; 2 = note, etc.
        login = item.get("login") or {}
        # B8 (v3.66.43): prefer the first http(s) URI. Mobile-first
        # entries list androidapp:///iosapp:// before the web URL; taking
        # uris[0] blindly dropped the autofill-usable URL. Fall back to
        # the first URI of any scheme so the entry isn't lost.
        uris = login.get("uris") or []
        web_url = ""
        any_url = ""
        for u in uris:
            if not isinstance(u, dict):
                continue
            v = (u.get("uri") or "").strip()
            if not v:
                continue
            if not any_url:
                any_url = v
            lv = v.lower()
            if not web_url and (lv.startswith("http://")
                                or lv.startswith("https://")):
                web_url = v
                break  # first http(s) wins; stop scanning
        url = web_url or any_url
        out.append(_record(
            name=item.get("name") or "",
            url=url,
            username=login.get("username") or "",
            password=login.get("password") or "",
            notes=item.get("notes") or "",
        ))
    return out


# ─── LastPass CSV ────────────────────────────────────────────────────

def _parse_lastpass(text: str) -> list[dict]:
    """LastPass CSV. Expected header:
        url, username, password, totp, extra, name, grouping, fav
    (totp and grouping/fav may be missing in older exports.)

    Note: distinguishes from Chrome by requiring at least one of the
    LastPass-specific columns (extra, grouping, fav, totp) — Chrome
    has only 4-5 columns (name, url, username, password [, note])."""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise FormatNotRecognized("no header row")
    fm = _build_field_map(reader.fieldnames)
    fn = set(fm.keys())
    if not {"url", "username", "password", "name"}.issubset(fn):
        raise FormatNotRecognized("missing lastpass columns")
    # Must have at least one LP-specific column or it's likely Chrome
    if not (fn & {"extra", "grouping", "fav", "totp"}):
        raise FormatNotRecognized("no lastpass-distinguishing column")
    out = []
    for row in reader:
        out.append(_record(
            name=_row_get(row, fm, "name"),
            url=_row_get(row, fm, "url"),
            username=_row_get(row, fm, "username"),
            password=_row_get(row, fm, "password"),
            notes=_row_get(row, fm, "extra"),
        ))
    return out


# ─── Chrome / Edge CSV ───────────────────────────────────────────────

def _parse_chrome(text: str) -> list[dict]:
    """Chrome / Edge / Brave CSV. Header is:
        name, url, username, password [, note]
    Some browser versions add 'note' as a 5th column."""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise FormatNotRecognized("no header row")
    fm = _build_field_map(reader.fieldnames)
    fn = set(fm.keys())
    if not {"name", "url", "username", "password"}.issubset(fn):
        raise FormatNotRecognized("missing chrome columns")
    # Distinguish from LastPass (which also has these). Chrome has
    # exactly 4 or 5 columns; LastPass has 7+.
    if len(reader.fieldnames) > 5:
        raise FormatNotRecognized("too many columns for chrome")
    out = []
    for row in reader:
        out.append(_record(
            name=_row_get(row, fm, "name"),
            url=_row_get(row, fm, "url"),
            username=_row_get(row, fm, "username"),
            password=_row_get(row, fm, "password"),
            notes=_row_get(row, fm, "note"),
        ))
    return out


# ─── 1Password 1PIF ──────────────────────────────────────────────────

def _parse_1pif(text: str) -> list[dict]:
    """1Password Interchange Format. Older 1Password version exports
    as JSONL (one JSON object per line) with separator lines starting
    with `***`. Each record has:
        uuid, typeName ('webforms.WebForm' for logins), title,
        secureContents.fields = [{name,value,designation:'username'|'password'}],
        location (URL), notesPlain
    """
    out = []
    lines = text.splitlines()
    if not any("typeName" in line and "webforms" in line for line in lines):
        raise FormatNotRecognized("no 1pif login records found")
    for line in lines:
        line = line.strip()
        if not line or line.startswith("***"): continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict): continue
        if rec.get("typeName") != "webforms.WebForm": continue
        username = ""
        password = ""
        sc = rec.get("secureContents") or {}
        for field in sc.get("fields") or []:
            if not isinstance(field, dict): continue
            d = field.get("designation")
            v = field.get("value") or ""
            if d == "username" and not username: username = v
            elif d == "password" and not password: password = v
        out.append(_record(
            name=rec.get("title") or "",
            url=rec.get("location") or "",
            username=username,
            password=password,
            notes=sc.get("notesPlain") or "",
        ))
    return out


# ─── 1Password CSV ───────────────────────────────────────────────────

def _parse_1password_csv(text: str) -> list[dict]:
    """Newer 1Password CSV export. Header is:
        title, website, username, password, notes [, ...]
    (May have extra columns; we only consume the five core ones.)"""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise FormatNotRecognized("no header row")
    fm = _build_field_map(reader.fieldnames)
    fn = set(fm.keys())
    if not {"title", "website", "username", "password"}.issubset(fn):
        raise FormatNotRecognized("missing 1password csv columns")
    out = []
    for row in reader:
        out.append(_record(
            name=_row_get(row, fm, "title"),
            url=_row_get(row, fm, "website"),
            username=_row_get(row, fm, "username"),
            password=_row_get(row, fm, "password"),
            notes=_row_get(row, fm, "notes"),
        ))
    return out


# ─── Generic CSV (fallback) ──────────────────────────────────────────

def _parse_generic_csv(text: str) -> list[dict]:
    """Last-resort: any CSV with at least username + password columns.
    Tries common column name aliases."""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise FormatNotRecognized("no header row")
    fn = {f.strip().lower(): f for f in reader.fieldnames}
    pw_col = next((fn[k] for k in ("password","pw","pass","secret") if k in fn), None)
    if not pw_col: raise FormatNotRecognized("no password column")
    user_col = next((fn[k] for k in ("username","user","login","email","account") if k in fn), None)
    name_col = next((fn[k] for k in ("name","title","label","site") if k in fn), None)
    url_col = next((fn[k] for k in ("url","website","site","domain") if k in fn), None)
    notes_col = next((fn[k] for k in ("notes","note","comment","extra") if k in fn), None)
    out = []
    for row in reader:
        out.append(_record(
            name=row.get(name_col) if name_col else "",
            url=row.get(url_col) if url_col else "",
            username=row.get(user_col) if user_col else "",
            password=row.get(pw_col) or "",
            notes=row.get(notes_col) if notes_col else "",
        ))
    return out


# ─── Top-level entry point ───────────────────────────────────────────

# Order matters: more-specific parsers first. A LastPass CSV would
# accidentally match _parse_generic_csv, so we try the specific
# ones first and let _parse_generic_csv catch anything left over.
_PARSERS = [
    ("bitwarden_json", _parse_bitwarden),
    ("1pif",           _parse_1pif),
    ("lastpass_csv",   _parse_lastpass),
    ("1password_csv",  _parse_1password_csv),
    ("chrome_csv",     _parse_chrome),
    ("generic_csv",    _parse_generic_csv),
]


def import_passwords(content: bytes | str) -> tuple[str, list[dict]]:
    """Sniff format and parse. Returns (format_name, [records]).

    Caller should:
      1. Read the uploaded file's bytes
      2. Call import_passwords(content)
      3. Show the records in a UI for user selection
      4. For each selected record, call secrets_store backend.set() AND
         optionally create/update a site config entry

    Raises FormatNotRecognized if no parser matched."""
    if isinstance(content, bytes):
        # Try utf-8 first, fall back to utf-8-sig for BOM, then latin-1
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                text = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise FormatNotRecognized("not a text file (or unknown encoding)")
    else:
        text = content
    # Strip BOM if present
    if text.startswith("\ufeff"): text = text[1:]

    errors = []
    for name, parser in _PARSERS:
        try:
            records = parser(text)
            # Filter out empty rows (parsers may emit them for blank lines)
            records = [r for r in records if r.get("name") or r.get("url")
                       or r.get("username") or r.get("password")]
            if records:
                return name, records
            # Empty result is suspicious — try next parser
            errors.append(f"{name}: 0 records")
        except FormatNotRecognized as e:
            errors.append(f"{name}: {e}")
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}: {e}")

    raise FormatNotRecognized(
        f"no parser recognized this file. Tried: {'; '.join(errors)}"
    )
