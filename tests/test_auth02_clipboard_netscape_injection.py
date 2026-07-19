"""RED-first repro for F-AUTH02-02.

``cookie_clipboard.to_netscape_text`` joins each cookie's fields with a TAB and
joins rows with a NEWLINE, escaping neither. A cookie value containing a newline
followed by a forged 7-field row therefore injects an ADDITIONAL, attacker-shaped
cookie line into the saved jar, which yt-dlp/httpx (or ``_parse_netscape``) then
re-parses as a genuine cookie. After the fix, a cookie carrying a TAB/CR/LF in
any field is dropped, so no extra row can be smuggled in.

Pristine-source RED: the forged row round-trips through ``_parse_netscape`` as a
second cookie, so the ``not in`` assertions fail until the fields are guarded.
"""
from bulk_downloader import cookie_clipboard as cc


def test_value_cannot_inject_a_cookie_row():
    forged = "legit\n.evil.example\tTRUE\t/\tTRUE\t9999999999\tinjected\tpwned"
    cookies = [{"domain": "good.example", "path": "/", "secure": True,
                "expires": 0, "name": "sid", "value": forged}]
    text = cc.to_netscape_text(cookies)
    parsed = cc._parse_netscape(text)
    names = {c.get("name") for c in parsed}
    domains = {c.get("domain") or "" for c in parsed}
    assert "injected" not in names, (names, text)
    assert not any("evil.example" in d for d in domains), (domains, text)


def test_clean_cookie_still_serializes():
    cookies = [{"domain": "good.example", "path": "/", "secure": True,
                "expires": 0, "name": "sid", "value": "abc123"}]
    text = cc.to_netscape_text(cookies)
    parsed = cc._parse_netscape(text)
    assert any(c.get("name") == "sid" and c.get("value") == "abc123"
               for c in parsed), text
