"""Tests for password_import (v3.43.14)."""
import json

from bulk_downloader.password_import import (
    import_passwords, FormatNotRecognized,
)


# ─── Bitwarden ─────────────────────────────────────────────────────

def test_bitwarden_json_unencrypted():
    payload = json.dumps({
        "encrypted": False,
        "items": [
            {"type": 1, "name": "WowGirls", "notes": "premium",
             "login": {"username": "matt@x.com", "password": "p4ss",
                       "uris": [{"uri": "https://wowgirls.com"}]}},
            {"type": 1, "name": "UltraFilms",
             "login": {"username": "matt", "password": "sec2",
                       "uris": [{"uri": "https://ultrafilms.com/members"}]}},
            {"type": 2, "name": "secure note skipped"},  # type 2 = note
        ]
    })
    fmt, recs = import_passwords(payload)
    assert fmt == "bitwarden_json"
    assert len(recs) == 2  # Type-2 note skipped
    wow = recs[0]
    assert wow["name"] == "WowGirls"
    assert wow["url"] == "https://wowgirls.com"
    assert wow["username"] == "matt@x.com"
    assert wow["password"] == "p4ss"
    assert wow["notes"] == "premium"


def test_bitwarden_encrypted_rejected():
    payload = json.dumps({"encrypted": True, "items": []})
    try:
        import_passwords(payload)
        assert False, "should have rejected encrypted bitwarden"
    except FormatNotRecognized as e:
        assert "encrypted" in str(e).lower()


def test_bitwarden_missing_uris():
    """Login items without uris should still import with empty url."""
    payload = json.dumps({
        "encrypted": False,
        "items": [{"type": 1, "name": "X", "login": {"username": "u", "password": "p"}}],
    })
    fmt, recs = import_passwords(payload)
    assert fmt == "bitwarden_json"
    assert recs[0]["url"] == ""


# ─── LastPass ─────────────────────────────────────────────────────

def test_lastpass_csv():
    payload = (
        "url,username,password,extra,name,grouping,fav\n"
        "https://wowgirls.com,matt,p1,premium account,Wow,Adult,0\n"
        "https://ultrafilms.com,m,p2,,Ultra,,0\n"
    )
    fmt, recs = import_passwords(payload)
    assert fmt == "lastpass_csv"
    assert len(recs) == 2
    assert recs[0]["name"] == "Wow"
    assert recs[0]["notes"] == "premium account"


def test_lastpass_without_lp_columns_falls_through():
    """A CSV with only the 4 common columns is Chrome, not LastPass."""
    payload = "name,url,username,password\nWow,https://x.com,matt,p\n"
    fmt, recs = import_passwords(payload)
    assert fmt == "chrome_csv"


# ─── Chrome / Edge ────────────────────────────────────────────────

def test_chrome_csv_4col():
    payload = (
        "name,url,username,password\n"
        "Wow,https://wowgirls.com,matt,p1\n"
    )
    fmt, recs = import_passwords(payload)
    assert fmt == "chrome_csv"
    assert recs[0]["name"] == "Wow"
    assert recs[0]["password"] == "p1"


def test_chrome_csv_5col_with_note():
    payload = (
        "name,url,username,password,note\n"
        "Wow,https://wowgirls.com,matt,p1,my note\n"
    )
    fmt, recs = import_passwords(payload)
    assert fmt == "chrome_csv"
    assert recs[0]["notes"] == "my note"


# ─── 1Password CSV ────────────────────────────────────────────────

def test_1password_csv():
    payload = (
        "title,website,username,password,notes\n"
        '"My Wow",https://wowgirls.com,matt,p1,premium\n'
        '"My Ultra",https://ultrafilms.com,m,p2,\n'
    )
    fmt, recs = import_passwords(payload)
    assert fmt == "1password_csv"
    assert len(recs) == 2
    assert recs[0]["name"] == "My Wow"


# ─── 1PIF ──────────────────────────────────────────────────────────

def test_1pif():
    rec1 = {
        "uuid": "abc",
        "typeName": "webforms.WebForm",
        "title": "WowGirls",
        "location": "https://wowgirls.com",
        "secureContents": {
            "fields": [
                {"designation": "username", "value": "matt"},
                {"designation": "password", "value": "secret"},
            ],
            "notesPlain": "premium account",
        }
    }
    payload = json.dumps(rec1) + "\n***5fe848ad***\n"
    fmt, recs = import_passwords(payload)
    assert fmt == "1pif"
    assert len(recs) == 1
    assert recs[0]["name"] == "WowGirls"
    assert recs[0]["username"] == "matt"
    assert recs[0]["password"] == "secret"
    assert recs[0]["notes"] == "premium account"


# ─── Unrecognized ─────────────────────────────────────────────────

def test_random_text_rejected():
    try:
        import_passwords("this is not a password file")
        assert False, "should have rejected"
    except FormatNotRecognized:
        pass


def test_empty_input_rejected():
    try:
        import_passwords("")
        assert False, "should have rejected empty input"
    except FormatNotRecognized:
        pass


def test_bytes_input_utf8():
    """Importer accepts bytes and decodes as UTF-8."""
    payload = "name,url,username,password\nW,https://x.com,m,p\n".encode("utf-8")
    fmt, recs = import_passwords(payload)
    assert fmt == "chrome_csv"


def test_bytes_input_utf8_bom():
    """UTF-8 with BOM (Windows Notepad's default) should also work."""
    payload = "\ufeffname,url,username,password\nW,https://x.com,m,p\n".encode("utf-8")
    fmt, recs = import_passwords(payload)
    assert fmt == "chrome_csv"


# ─── Generic CSV fallback ─────────────────────────────────────────

def test_generic_csv_minimum_fields():
    """Username+password with non-standard column names still imports."""
    payload = "site,login,secret\nWow,matt,p1\n"
    fmt, recs = import_passwords(payload)
    assert fmt == "generic_csv"
    assert recs[0]["password"] == "p1"
    assert recs[0]["username"] == "matt"
    assert recs[0]["name"] == "Wow"


def test_generic_csv_password_only_rejected():
    """A CSV without a password column is rejected by all parsers."""
    payload = "name,url\nWow,https://x.com\n"
    try:
        import_passwords(payload)
        assert False, "should have rejected (no password column)"
    except FormatNotRecognized:
        pass
