"""APP-FEATURE F026: /imports Bulk site import shows parsed-row preview before commit and cancels cleanly.

The preview property matrix (ImportsCenter.bulkPreview.test.tsx) regenerates the CSV inputs below and checks
them against the committed fixture, which records what backend csv_bulk.parse_csv makes of each one. Regenerate
with `./venv/bin/python -m tests.test_appfeat_f026_bulk_sites_preview --write` after a parse_csv change.
"""

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from bulk_downloader import csv_bulk
from tests.frontend_vitest import run_vitest

BD_GATE_SCOPE = "module"

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "frontend/src/routes/ImportsCenter.bulkPreview.cases.json"
# Shared with the vitest suite: one login_url rule and one committed-name table for preview and commit.
RULES = ROOT / "frontend/src/routes/ImportsCenter.bulkSiteRules.cases.json"
VITEST_TESTS = 32

# Mirrored verbatim by the generator in ImportsCenter.bulkPreview.test.tsx (the fixture digest proves it).
SENTINEL = "zq7sentinelq7z"
PUBLIC = ("name", "login_url", "username", "template", "login_template")
# (shape, header line, header usable by the backend). Usable: the secret sits in each non-public column.
# Unusable (no login_url, or quotes the backend cannot read as one record): the secret sits in every column.
HEADERS = (
    ("standard", "name,login_url,username,password,template,login_template", True),
    ("password-first", "password,login_url,name", True),
    ("unknown-secret-mid", "login_url,api_token,name,password", True),
    ("r4-cr", "name,login_url,password", True),
    ("trailing-secret", "name,login_url,username,client_secret", True),
    ("sentinel-header", f"name,login_url,{SENTINEL}_key", True),
    ("r1-no-login_url", "name,url,username,password", False),
    ("export-no-login_url", "title,username,password,notes", False),
    ("r3-unterminated-header", 'name,login_url,"password', False),
    ("malformed-header", 'name,login_url,"pass"word', False),
)
QUOTINGS = (
    ("none", "{s}"),
    ("closed", '"{s}"'),
    ("closed-comma", '"pre,{s}"'),
    ("escaped-quote", '"pre""{s}"'),
    ("multiline", '"pre\n{s}"'),
    ("multiline-blank", '"pre\n\n{s}"'),
    ("multiline-malformed", '"pre\n{s}"x'),
    ("multiline-unterminated", '"pre\n{s}'),
    ("unterminated", '"{s}'),
    ("comma-spill", "pre,{s}"),
    ("stray-quote", 'pre"{s}'),
    ("text-after-quote", '"pre"{s}'),
)
EOLS = ("lf", "crlf", "cr", "mixed")
MIXED = ("\n", "\r\n", "\r")
HEADER_ERROR = "header row must include a 'login_url' column"


def _cell(column: str, label: str) -> str:
    if column in ("name", "title"):
        return label
    if column in ("login_url", "url"):
        return "" if label == "Gamma" else f"https://{label.lower()}.example/login"
    return {"username": label.lower(), "template": "video_js", "login_template": "form_login"}.get(
        column, "otherpw"
    )


def _apply_eol(text: str, eol: str) -> str:
    if eol == "lf":
        return text
    if eol != "mixed":
        return text.replace("\n", {"crlf": "\r\n", "cr": "\r"}[eol])
    parts = text.split("\n")
    return parts[0] + "".join(MIXED[i % 3] + p for i, p in enumerate(parts[1:]))


def generate_cases():
    """Every (header shape x secret position x quoting x line ending) input, in fixture order."""
    for shape, header, usable in HEADERS:
        columns = header.split(",")
        positions = [i for i, c in enumerate(columns) if not usable or c not in PUBLIC]
        for pos in positions:
            for qi, (quoting, template) in enumerate(QUOTINGS):
                for eol in EOLS:
                    rows = []
                    for label in ("Alpha", "Beta", "Gamma"):
                        cells = [_cell(c, label) for c in columns]
                        if label == "Alpha":
                            cells[pos] = template.replace("{s}", SENTINEL)
                        rows.append(",".join(cells))
                    preamble = "﻿# exported sites\n\n" if (qi + pos) % 2 else ""
                    text = _apply_eol(preamble + header + "\n" + "\n".join(rows) + "\n", eol)
                    yield f"{shape}|{pos}|{quoting}|{eol}", text


def build_fixture() -> str:
    digest = hashlib.sha256()
    entries = []
    for case_id, text in generate_cases():
        digest.update(text.encode("utf-8") + b"\x00")
        rows, errors = csv_bulk.parse_csv(text)
        messages = {msg for _, msg in errors}
        assert messages <= {"missing login_url", "invalid login_url", HEADER_ERROR}, (case_id, errors)
        expect = [[r["line"] for r in rows], [ln for ln, _ in errors], int(HEADER_ERROR in messages)]
        entries.append(f"{json.dumps(case_id)}:{json.dumps(expect, separators=(',', ':'))}")
    head = json.dumps({"sentinel": SENTINEL, "count": len(entries), "inputs_sha256": digest.hexdigest()})
    return head[:-1] + ',"cases":{\n' + ",\n".join(entries) + "\n}}\n"


def test_appfeat_f026_parity_fixture_matches_backend_parse_csv():
    built = build_fixture()
    assert FIXTURE.read_text(encoding="utf-8") == built, (
        f"{FIXTURE.name} is stale against csv_bulk.parse_csv; regenerate with --write"
    )
    cases = json.loads(built)["cases"]
    # Positive controls: the matrix exercises valid rows, row errors and the header error.
    assert len(cases) == 1008, len(cases)
    assert any(v[0] for v in cases.values())
    assert any(v[1] and not v[2] for v in cases.values())
    assert sum(v[2] for v in cases.values()) == 4 * 12 * 4 * 2, "no-login_url shapes x positions"


def _rules():
    return json.loads(RULES.read_text(encoding="utf-8"))


def rules_csv(rules) -> str:
    """The table as one CSV (every cell quoted): login_url cases on lines 2.., then the name cases."""
    q = lambda v: '"' + v.replace('"', '""') + '"'
    cells = [[f"url{i}", u] for i, (u, _) in enumerate(rules["login_url"])] + [[n, u] for n, u, _ in rules["names"]]
    return "name,login_url\n" + "".join(f"{q(n)},{q(u)}\n" for n, u in cells)


def _expected_lines(rules):
    """(valid row line -> committed name, invalid row lines) for rules_csv()."""
    valid, invalid = {}, []
    for i, (u, ok) in enumerate(rules["login_url"]):
        if ok:
            valid[i + 2] = f"url{i}"
        else:
            invalid.append(i + 2)
    base = len(rules["login_url"]) + 2
    valid.update({base + i: want for i, (_, _, want) in enumerate(rules["names"])})
    return valid, invalid


def test_appfeat_f026_login_url_rule_and_names_match_shared_table():
    rules = _rules()
    got = [[u, csv_bulk.is_valid_http_login_url(u)] for u, _ in rules["login_url"]]
    assert got == rules["login_url"]
    # E10 (security verdict F026-r5): other schemes are invalid; positive control: plain https is valid.
    verdict = dict(got)
    for bad in ("javascript:alert(1)", "data:text/html,x", "ftp://example.com/", "file:///etc/passwd"):
        assert verdict[bad] is False, bad
    assert verdict["https://example.com"] is True
    # R1 (PM RULING-pm-B-ipv6): http(s) IPv6 literals stay valid, as at BASE.
    assert verdict["http://[::1]/login"] is True and verdict["https://[2001:db8::1]:8443/x"] is True
    # C2 domain: every name case is a rule-valid (committable) row.
    assert all(csv_bulk.is_valid_http_login_url(u) for _, u, _ in rules["names"])
    assert [[n, u, csv_bulk.clean_name(n, u)] for n, u, _ in rules["names"]] == rules["names"]
    # The title cases the preview's pyTitle must reproduce are Python's own str.title.
    assert [[s, s.title()] for s, _ in rules["title"]] == rules["title"]
    # Parse (shared by commit and preflight): rule-invalid rows are "invalid login_url" errors, the rest are rows.
    rows, errors = csv_bulk.parse_csv(rules_csv(rules))
    valid, invalid = _expected_lines(rules)
    assert errors == [(ln, "invalid login_url") for ln in invalid]
    assert {r["line"]: csv_bulk.clean_name(r["name"], r["login_url"]) for r in rows} == valid


@pytest.fixture
def _booted_app():
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    from bulk_downloader import app as a
    from bulk_downloader import db
    saved, saved_latch = a.SITES_FILE, a._SITES_FILE_LAST_AUTO_OBJECT
    try:
        db.db_init()
        assert a.SITES_FILE.parent.is_dir(), f"dead SITES_FILE pin: {a.SITES_FILE}"
        a.SITES_FILE.write_text(json.dumps({}), encoding="utf-8")
        a._load_sites_config()
        yield a.app.test_client()
    finally:
        a.SITES_FILE = saved
        a._SITES_FILE_LAST_AUTO_OBJECT = saved_latch


def test_appfeat_f026_commit_creates_exactly_the_rule_valid_rows_with_table_names(_booted_app):
    rules = _rules()
    r = _booted_app.post("/api/sites/bulk_csv", json={"csv": rules_csv(rules)})
    assert r.status_code == 200, r.get_data(as_text=True)
    results = r.get_json()["results"]
    valid, invalid = _expected_lines(rules)
    created = {x["line"]: x["name"] for x in results if x["status"] == "created"}
    errored = sorted(x["line"] for x in results if x["status"] == "error" and x["error"] == "invalid login_url")
    assert created == valid
    assert errored == invalid
    assert len(results) == len(valid) + len(invalid), results


def test_appfeat_f026_bulk_sites_csv_preview_and_cancel():
    spec = "src/routes/ImportsCenter.bulkPreview.test.tsx"
    receipt = run_vitest(spec, expected_tests=VITEST_TESTS)
    assert receipt["tests_passed"] == VITEST_TESTS, receipt


if __name__ == "__main__" and sys.argv[1:] == ["--write"]:
    FIXTURE.write_text(build_fixture(), encoding="utf-8")
