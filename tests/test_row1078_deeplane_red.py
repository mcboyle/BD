"""Row 1078 deep lane (O809, ORDERS-2233): RED contract for the third build.

Every node drives the shipped CLI path (bdctl.build_parser + cmd_netlog_reduce /
cmd_netlog_stats) against real files. The archive contract: no planted credential
reaches the archive, and reducing a log never destroys a file the operator owns.

(a) review-correctness-A REFUTATION / TO CLOSE: E1 stem collision, E2 userinfo in
    an error-embedded URL, A1 error prose after the first URL.
(b) N1-A R1: every synthetic secret absent from the archive (exact count 0).
(c) RULING-2222 B1 (default invocation) and B2 (token in error string).
(d) header redaction end-to-end, including URL-valued headers and header
    credentials quoted inside an error string.
"""
from __future__ import annotations

import json

BD_GATE_SCOPE = "module"

PRECIOUS = "PRECIOUS USER DATA -- not an archive\n"


def _events(n=2):
    return [{"url": f"https://cdn.test/{i}.ts", "response_status": 200,
             "elapsed_ms": 25.0 + i, "bytes": 500} for i in range(n)]


def _reduce(raw_file, events, out=None):
    import bdctl

    raw_file.write_text(json.dumps(events), encoding="utf-8")
    argv = ["netlog", "reduce", str(raw_file)] + (["--out", str(out)] if out else [])
    args = bdctl.build_parser().parse_args(argv)
    return bdctl.cmd_netlog_reduce(args)


def _archive_text(tmp_path, events):
    out = tmp_path / "archive_out.jsonl"
    assert _reduce(tmp_path / "raw.json", events, out) == 0
    return out.read_text(encoding="utf-8")


def _error_of(text, url_fragment):
    for line in text.splitlines()[1:]:
        rec = json.loads(line)
        if url_fragment in rec.get("url", ""):
            return rec.get("error")
    raise AssertionError(f"no archived event for {url_fragment}")


# ── (a) review-correctness-A: E1 stem collision ────────────────────────────

def test_a_e1_default_reduce_keeps_sibling_stem_jsonl(tmp_path):
    user_file = tmp_path / "recon.jsonl"
    user_file.write_text(PRECIOUS, encoding="utf-8")
    _reduce(tmp_path / "recon.json", _events())
    assert user_file.exists(), "E1: `bdctl netlog reduce recon.json` destroyed the operator's recon.jsonl"
    assert user_file.read_text(encoding="utf-8") == PRECIOUS, "E1: recon.jsonl overwritten by the archive"


def test_a_e1_out_reduce_keeps_out_dir_stem_jsonl(tmp_path):
    out_dir = tmp_path / "o"
    out_dir.mkdir()
    user_file = out_dir / "final.jsonl"
    user_file.write_text(PRECIOUS, encoding="utf-8")
    _reduce(tmp_path / "raw.json", _events(), out_dir / "final.reduced.jsonl")
    assert user_file.exists(), "E1: `--out o/final.reduced.jsonl` destroyed the operator's o/final.jsonl"
    assert user_file.read_text(encoding="utf-8") == PRECIOUS, "E1: o/final.jsonl overwritten by the archive"


def test_a_e1_control_no_collision_writes_archive_and_keeps_unrelated_file(tmp_path):
    """Positive control: the probe sees a surviving file and a written archive."""
    other = tmp_path / "notes.jsonl"
    other.write_text(PRECIOUS, encoding="utf-8")
    assert _reduce(tmp_path / "recon.json", _events()) == 0
    assert (tmp_path / "recon.reduced.jsonl").exists()
    assert other.read_text(encoding="utf-8") == PRECIOUS


# ── (a) review-correctness-A: E2 userinfo in error-embedded URL, A1 prose ───

def test_a_e2_userinfo_in_error_embedded_url_is_scrubbed(tmp_path):
    events = [{"url": "https://s.t/api", "response_status": 500, "elapsed_ms": 9.0, "bytes": 0,
               "error": "net::ERR fetching https://x:EUP_17@s.t/b?token=ETOK_17"}]
    text = _archive_text(tmp_path, events)
    leaked = [s for s in ("EUP_17", "ETOK_17") if s in text]
    assert leaked == [], f"E2: error-embedded URL credentials reached the archive: {leaked}"


def test_a_e2_control_same_password_in_url_field_is_scrubbed(tmp_path):
    """Positive control: the same userinfo in the url field is scrubbed and the host survives."""
    events = [{"url": "https://x:EUP_17@s.t/b?token=ETOK_17", "response_status": 500,
               "elapsed_ms": 9.0, "bytes": 0, "error": "failed"}]
    text = _archive_text(tmp_path, events)
    assert "s.t/b" in text
    assert text.count("EUP_17") + text.count("ETOK_17") == 0


def test_a_a1_error_prose_after_first_url_is_preserved(tmp_path):
    events = [{"url": "https://h.test/api", "response_status": 502, "elapsed_ms": 9.0, "bytes": 0,
               "error": "net::ERR_A at https://h.test/x?sig=S1X then retry https://y:PW2X@s.t/c?key=K2X"}]
    text = _archive_text(tmp_path, events)
    err = _error_of(text, "h.test/api")
    assert [s for s in ("S1X", "PW2X", "K2X") if s in text] == [], f"A1: credential kept: {err!r}"
    assert "then retry" in err and "s.t/c" in err, f"A1: error prose after the first URL lost: {err!r}"


def test_a_a1_control_plain_error_prose_is_kept_verbatim(tmp_path):
    """Positive control: _error_of reads the archived error; URL-free prose survives exactly."""
    events = [{"url": "https://h.test/api", "response_status": 500, "elapsed_ms": 9.0, "bytes": 0,
               "error": "Internal Error then retry later"}]
    assert _error_of(_archive_text(tmp_path, events), "h.test/api") == "Internal Error then retry later"


# ── (b) N1-A R1: no synthetic secret in the archive ────────────────────────

_R1_SECRETS = (
    "SECRET_TOKEN_AAA", "SECRET_KEY_DDD", "SECRET_SIG_FFF", "SECRET_ACCESS_GGG",
    "USERINFO_PASS_HHH", "COOKIE_SECRET_EEE", "AUTH_SECRET_III",
    "SETCOOKIE_SECRET_JJJ", "APIKEY_SECRET_KKK",
)


def _r1_events():
    events = [{"url": f"https://cdn.test/hls/seg_{i:04d}.ts?token=SECRET_TOKEN_AAA&access_token=SECRET_ACCESS_GGG",
               "response_status": 200, "elapsed_ms": 30.0, "bytes": 1000, "timestamp": 1700000000.0 + i}
              for i in range(5)]
    events.append({
        "url": "https://user:USERINFO_PASS_HHH@site.test/login?key=SECRET_KEY_DDD&next=/home",
        "method": "POST", "response_status": 403, "elapsed_ms": 80.0, "bytes": 10, "timestamp": 1700000010.0,
        "headers": {"Cookie": "sid=COOKIE_SECRET_EEE", "Authorization": "Bearer AUTH_SECRET_III",
                    "Set-Cookie": "sid=SETCOOKIE_SECRET_JJJ", "X-Api-Key": "APIKEY_SECRET_KKK",
                    "Accept": "text/html"}})
    events.append({"url": "https://cdn.test/video/big.mp4?sig=SECRET_SIG_FFF&quality=720",
                   "response_status": 200, "elapsed_ms": 5000.0, "bytes": 99, "timestamp": 1700000020.0})
    return events


def test_b_r1_archive_contains_no_planted_secret_exact_zero(tmp_path):
    events = _r1_events()
    planted = json.dumps(events)
    assert sum(1 for s in _R1_SECRETS if s in planted) == 9, "fixture must plant all 9 secrets"
    text = _archive_text(tmp_path, events)
    leaked = {s: text.count(s) for s in _R1_SECRETS if s in text}
    assert sum(text.count(s) for s in _R1_SECRETS) == 0, f"R1: archive leaks secrets: {leaked}"
    assert "big.mp4" in text and "quality=720" in text, "control: evidence must survive redaction"


# ── (c) RULING-2222 B1 / B2 ────────────────────────────────────────────────

def test_c_b1_default_invocation_writes_the_printed_path_only(tmp_path, capsys):
    import bdctl

    raw = tmp_path / "raw.json"
    assert _reduce(raw, _events()) == 0
    out = capsys.readouterr().out
    expected = raw.with_suffix(".reduced.jsonl")
    assert f"Archive written to: {expected}" in out
    assert expected.exists(), f"B1: printed archive {expected} does not exist"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["raw.json", "raw.reduced.jsonl"]
    args = bdctl.build_parser().parse_args(["netlog", "stats", str(expected)])
    assert bdctl.cmd_netlog_stats(args) == 0
    assert "Original Events: 2" in capsys.readouterr().out


def test_c_b2_token_in_error_string_is_scrubbed(tmp_path):
    events = [{"url": "https://site.test/api/fetch", "response_status": 500, "elapsed_ms": 120.0,
               "bytes": 0, "error": "net::ERR_FAILED fetching https://site.test/b?token=ERRURL_TOKEN_10"}]
    text = _archive_text(tmp_path, events)
    assert "ERRURL_TOKEN_10" not in text, "B2: archive leaked the error-string token"


# ── (d) header redaction end-to-end through bdctl ──────────────────────────

_CRED_HEADERS = {
    "Cookie": "sid=H_COOKIE_1", "cookie": "sid=H_COOKIE_2", "COOKIE": "sid=H_COOKIE_3",
    "Set-Cookie": "s=H_SETC_4", "set-cookie": "s=H_SETC_5",
    "Authorization": "Bearer H_AUTH_6", "authorization": "Basic H_AUTH_7",
    "Proxy-Authorization": "Basic H_PAUTH_9",
    "X-Api-Key": "H_APIKEY_10", "x-api-key": "H_APIKEY_11",
}


def _header_event(headers):
    return [{"url": "https://s.t/login", "method": "POST", "response_status": 403,
             "elapsed_ms": 5.0, "bytes": 1, "headers": headers}]


def test_d_credential_headers_scrubbed_all_case_variants(tmp_path):
    headers = dict(_CRED_HEADERS, Accept="text/html")
    text = _archive_text(tmp_path, _header_event(headers))
    secrets = [v.split("=")[-1].split()[-1] for v in _CRED_HEADERS.values()]
    assert len(secrets) == 10
    leaked = [s for s in secrets if s in text]
    assert leaked == [], f"header credential values reached the archive: {leaked}"
    rec = json.loads(text.splitlines()[1])
    assert set(rec["headers"]) == set(headers), "header NAMES must be kept"
    assert rec["headers"]["Accept"] == "text/html", "control: benign header value must survive"


def test_d_url_valued_header_userinfo_is_scrubbed(tmp_path):
    headers = {"Location": "https://u:H_LOCPW_16@s.t/next", "Referer": "https://r:H_REFPW_17@s.t/p?x=1"}
    text = _archive_text(tmp_path, _header_event(headers))
    leaked = [s for s in ("H_LOCPW_16", "H_REFPW_17") if s in text]
    assert leaked == [], f"URL-valued header userinfo reached the archive: {leaked}"


def test_d_control_url_valued_header_query_is_scrubbed(tmp_path):
    """Positive control: the header path is exercised -- a query token in Referer is scrubbed, host kept."""
    headers = {"Referer": "https://s.t/p?token=H_REFTOK_15"}
    text = _archive_text(tmp_path, _header_event(headers))
    assert "H_REFTOK_15" not in text and "s.t/p" in text


def test_d_header_credential_quoted_in_error_is_scrubbed(tmp_path):
    events = [{"url": "https://s.t/e", "response_status": 500, "elapsed_ms": 5.0, "bytes": 0,
               "error": "net::ERR at https://s.t/q Authorization: Bearer E_AUTH_18"}]
    text = _archive_text(tmp_path, events)
    assert "E_AUTH_18" not in text, "Authorization value quoted in the error string reached the archive"
