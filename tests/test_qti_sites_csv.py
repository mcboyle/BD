"""qti-sites-csv -- POST /api/sites/bulk_csv (deep-queue-templates-io F7, F9, F10).

F7  the password column was handed to _create_site and written PLAINTEXT to
    sites_config.json; POST /api/sites has routed it to the secrets vault
    since v3.66.326.
F9  a UTF-8-BOM CSV (Excel "CSV UTF-8") was rejected: the header read
    "\\ufefflogin_url".
F10 parse_csv split lines before csv.reader, so a quoted cell holding a
    line break became two sites.

Each finding has its own RED row, a control that passes on both sides, and a
row proving the tightened parser loses no line.
"""

# Its subject is two modules, not the tree: not a repo-wide CI gate.
BD_GATE_SCOPE = "module"

import csv
import io
import json
import os
import random
import time

import pytest

HEADER = "name,login_url,username,password\n"
# Excel puts the BOM on the FIRST header cell; the finding has login_url there.
URL_FIRST = "login_url,name\n"


@pytest.fixture(autouse=True)
def _sites_file_pin_is_returned_as_found():
    """Booting the app pins app.SITES_FILE to this test's tmp dir; give it back."""
    from bulk_downloader import app as a
    saved = a.SITES_FILE
    saved_latch = a._SITES_FILE_LAST_AUTO_OBJECT
    try:
        yield
    finally:
        a.SITES_FILE = saved
        a._SITES_FILE_LAST_AUTO_OBJECT = saved_latch


def _boot_empty():
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    from bulk_downloader import app as a
    from bulk_downloader import db
    db.db_init()
    assert a.SITES_FILE.parent.is_dir(), f"dead SITES_FILE pin: {a.SITES_FILE}"
    a.SITES_FILE.write_text(json.dumps({}), encoding="utf-8")
    a._load_sites_config()
    return a, a.app.test_client()


class _Vault:
    """Stand-in encrypted backend: records what it is asked to keep."""
    name = "stub-encrypted"

    def __init__(self, unlocked=True):
        self._unlocked = unlocked
        self.kept = {}

    def is_unlocked(self):
        return self._unlocked

    def set(self, key, value):
        self.kept[key] = value

    def get(self, key):
        return self.kept.get(key)


def _with_backend(backend, fn):
    from bulk_downloader import secrets_store as ss
    orig = ss.get_backend
    ss.get_backend = lambda: backend
    try:
        return fn()
    finally:
        ss.get_backend = orig


def _post_csv(c, text):
    r = c.post("/api/sites/bulk_csv", json={"csv": text})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def _disk(a):
    raw = a.SITES_FILE.read_text(encoding="utf-8")
    return raw, json.loads(raw)


# ── F7: the password column never lands on disk as plaintext ───────────────

def test_f7_bulk_csv_password_is_not_written_plaintext():
    a, c = _boot_empty()

    class _Plain:
        name = "plaintext"

    body = _with_backend(_Plain(), lambda: _post_csv(
        c, HEADER + "PwSite,https://pw.test/login,u1,QTI-F7-PLAINTEXT-PW\n"))
    assert body["created"] == 1, body
    raw, disk = _disk(a)
    site = next(s for s in disk.values() if s.get("name") == "PwSite")
    assert "QTI-F7-PLAINTEXT-PW" not in raw, (
        "F7: bulk_csv wrote the password column plaintext into sites_config.json")
    assert site.get("username") == "u1"            # the row itself was imported
    entry = body["results"][0]
    # A plaintext backend can vault nothing, and the row says so instead of
    # reporting a bare "created".
    assert entry.get("cred_stored") is False, entry
    assert entry.get("secrets_plaintext") is True, entry


def test_f7_bulk_csv_password_goes_to_an_unlocked_vault_as_a_reference():
    from bulk_downloader import secrets_store as ss
    a, c = _boot_empty()
    vault = _Vault(unlocked=True)
    body = _with_backend(vault, lambda: _post_csv(
        c, HEADER + "VaultRow,https://vault.test/login,u2,QTI-F7-VAULTED-PW\n"))
    entry = body["results"][0]
    assert entry.get("cred_stored") is True, entry
    raw, disk = _disk(a)
    assert "QTI-F7-VAULTED-PW" not in raw
    sid = entry["id"]
    assert disk[sid]["password"] == ss.make_password_reference(sid)
    assert vault.kept == {ss.site_password_key(sid): "QTI-F7-VAULTED-PW"}
    assert a.s_meta[sid].get("has_password") is True


def test_f7_bulk_csv_locked_vault_creates_the_site_and_flags_the_row():
    a, c = _boot_empty()
    vault = _Vault(unlocked=False)
    body = _with_backend(vault, lambda: _post_csv(
        c, HEADER + "LockedRow,https://locked.test/login,u3,QTI-F7-LOCKED-PW\n"))
    entry = body["results"][0]
    assert body["created"] == 1 and entry["status"] == "created"
    assert entry.get("cred_stored") is False and entry.get("secrets_locked") is True
    raw, _ = _disk(a)
    assert "QTI-F7-LOCKED-PW" not in raw and vault.kept == {}


def test_f7_control_row_without_a_password_carries_no_credential_flags():
    a, c = _boot_empty()
    body = _post_csv(c, HEADER + "NoPw,https://nopw.test/login,u4,\n")
    entry = body["results"][0]
    assert entry["status"] == "created"
    assert "cred_stored" not in entry and "secrets_plaintext" not in entry
    _, disk = _disk(a)
    assert disk[entry["id"]].get("username") == "u4"


# ── F9: a UTF-8 BOM in front of the header ─────────────────────────────────

def test_f9_parse_csv_accepts_a_bom_before_the_header():
    from bulk_downloader import csv_bulk
    rows, errors = csv_bulk.parse_csv("\ufeff" + URL_FIRST + "https://bom.test/login,BomSite\n")
    assert errors == [], f"F9: BOM CSV rejected: {errors}"
    assert [(r["name"], r["login_url"], r["line"]) for r in rows] == [
        ("BomSite", "https://bom.test/login", 2)]
    assert csv_bulk.parse_csv("\ufeff") == ([], [(0, "empty file")])


def test_f9_bulk_csv_upload_with_bom_bytes_creates_the_site():
    a, c = _boot_empty()
    data = ("\ufeff" + URL_FIRST + "https://bomup.test/login,BomUpload\n").encode("utf-8")
    assert data.startswith(b"\xef\xbb\xbf")
    r = c.post("/api/sites/bulk_csv", data={"file": (io.BytesIO(data), "sites.csv")},
               content_type="multipart/form-data")
    body = r.get_json()
    assert body["created"] == 1 and body["errors"] == 0, f"F9: {body}"
    # the raw-body form carries the same bytes
    r = c.post("/api/sites/bulk_csv", data=data.replace(b"BomUpload", b"BomRaw"),
               content_type="text/csv")
    assert r.get_json()["created"] == 1, r.get_json()


def test_f9_control_a_bom_is_only_dropped_at_the_very_start():
    from bulk_downloader import csv_bulk
    rows, errors = csv_bulk.parse_csv(HEADER + "\ufeffMid,https://mid.test/login,,\n")
    assert errors == [] and rows[0]["name"] == "\ufeffMid"
    rows, errors = csv_bulk.parse_csv("name,url\nA,https://a.test\n")
    assert rows == [] and "login_url" in errors[0][1]      # the refusal still exists


# ── F10: a quoted cell may hold a line break ───────────────────────────────

def test_f10_quoted_cell_with_a_line_break_is_one_row():
    from bulk_downloader import csv_bulk
    text = HEADER + '"Two\nLines",https://two.test/login,u,p\nNext,https://next.test/login,,\n'
    rows, errors = csv_bulk.parse_csv(text)
    assert errors == [], errors
    assert [(r["line"], r["name"], r["login_url"]) for r in rows] == [
        (2, "Two\nLines", "https://two.test/login"),
        (4, "Next", "https://next.test/login"),
    ], "F10: a quoted line break split one site into two"
    assert rows[0]["username"] == "u" and rows[0]["password"] == "p"


def test_f10_bulk_csv_creates_one_site_for_a_two_line_cell():
    a, c = _boot_empty()
    body = _post_csv(c, HEADER + '"Two\nLines",https://two.test/login,,\n')
    assert body["created"] == 1 and body["errors"] == 0, f"F10: {body}"
    _, disk = _disk(a)
    assert len(disk) == 1


def test_f10_blank_and_hash_lines_inside_a_quoted_cell_stay_in_the_cell():
    from bulk_downloader import csv_bulk
    rows, errors = csv_bulk.parse_csv(
        HEADER + '"a\n\n# not a comment\nb",https://cell.test/login,,\n')
    assert errors == [] and len(rows) == 1
    assert rows[0]["name"] == "a\n\n# not a comment\nb" and rows[0]["line"] == 2


def test_f10_stray_quotes_lose_no_line():
    """Quotes that do not form one record are read one line per row, as before."""
    from bulk_downloader import csv_bulk
    text = (HEADER + '5" Tape,https://a.test/login,,\n# comment\n\n'
            'B,https://b.test/login,,\nC "x,https://c.test/login,,\nD,https://d.test/login,,\n')
    rows, errors = csv_bulk.parse_csv(text)
    assert errors == []
    assert [(r["line"], r["name"]) for r in rows] == [
        (2, '5" Tape'), (5, "B"), (6, 'C "x'), (7, "D")]
    # a quote that is never closed does not swallow the rows after it
    rows, errors = csv_bulk.parse_csv(
        HEADER + '"open,https://open.test/login\nAfter,https://after.test/login,,\n')
    assert [(r["line"], r["name"]) for r in rows] == [(3, "After")]
    assert errors == [(2, "missing login_url")]


def test_f10_control_the_shipped_template_still_parses():
    from bulk_downloader import csv_bulk
    text = csv_bulk.build_csv_template([{"id": "video_js", "name": 'Video "JS"'}])
    rows, errors = csv_bulk.parse_csv(text)
    assert errors == [] and [r["name"] for r in rows] == ["Example Site"]


# ── r3 (lens R41-B): linear cost, and stray leading quotes merge nothing ───

def test_f10_r3_an_open_quote_does_not_make_the_parse_quadratic():
    """R41-B FINDING 1: one stray quote on row 1, then N clean rows.

    r2 re-counted every pending line on every line: 32 000 rows took 82 s
    (BASE 0.15 s). The bound is 30x above the linear cost and 8x below the
    r2 cost, so host load cannot flip it either way.
    """
    from bulk_downloader import csv_bulk
    n = 32000
    text = (HEADER + 'A 5" pipe,https://a.test/login,u,p\n'
            + "".join(f"S{i},https://s{i}.test/login,u,p\n" for i in range(n)))
    t0 = time.perf_counter()
    rows, errors = csv_bulk.parse_csv(text)
    took = time.perf_counter() - t0
    assert errors == [] and len(rows) == n + 1          # the same rows as BASE
    assert rows[0]["name"] == 'A 5" pipe' and rows[-1]["line"] == n + 2
    assert took < 10.0, f"F10 r3: QUADRATIC PARSE: {n} rows took {took:.1f}s"


def test_f10_r3_two_lines_that_start_a_cell_with_a_stray_quote_merge_nothing():
    """R41-B FINDING 2: '"A,..' / 'B,..' / '"C,..' is not one well-formed record."""
    from bulk_downloader import csv_bulk
    text = (HEADER + '"A,https://a.test/l,u,p\nB,https://b.test/l,u,p\n'
            '"C,https://c.test/l,u,p\n')
    rows, errors = csv_bulk.parse_csv(text)
    assert [(r["line"], r["name"], r["login_url"]) for r in rows] == [
        (3, "B", "https://b.test/l")], "F10 r3: STRAY-QUOTE LINES MERGED INTO ONE SITE"
    assert errors == [(2, "missing login_url"), (4, "missing login_url")]


def _reference_parse(text):
    """The rule, written the slow obvious way: (first line, cells) per record."""
    if text.startswith("\ufeff"):
        text = text[1:]
    out, pending = [], []

    def one_per_line(lines):
        for i, raw in lines:
            if raw.strip() and not raw.strip().startswith("#"):
                out.append((i, next(csv.reader([raw]), [])))

    for i, raw in enumerate(text.splitlines(), 1):
        if not pending and (not raw.strip() or raw.strip().startswith("#")):
            continue
        pending.append((i, raw))
        if "".join(line for _, line in pending).count('"') % 2:
            continue
        found = []
        if len(pending) > 1:
            try:
                found = list(csv.reader(
                    io.StringIO("\n".join(line for _, line in pending), newline=""),
                    strict=True))
            except csv.Error:
                found = []
        if len(found) == 1:
            out.append((pending[0][0], found[0]))
        else:
            one_per_line(pending)
        pending = []
    one_per_line(pending)
    return out


def test_f10_r3_no_line_is_lost_or_merged_on_4000_random_inputs():
    """Every surviving physical line is either its own record, exactly as
    BASE read it, or part of ONE well-formed multi-line record."""
    from bulk_downloader import csv_bulk
    rng = random.Random(20261013)
    seen_multi = seen_fallback = 0
    for _ in range(4000):
        lines = ["".join(rng.choice('ab,,"" #') for _ in range(rng.randint(0, 7)))
                 for _ in range(rng.randint(1, 7))]
        text = "login_url,name\n" + "\n".join(lines) + "\n"
        ref = _reference_parse(text)
        rows, errors = csv_bulk.parse_csv(text)
        got = sorted([r["line"] for r in rows] + [ln for ln, _ in errors])
        want = sorted(ln for ln, cells in ref[1:] if any(c.strip() for c in cells))
        assert got == want, (text, got, want)
        by_line = {ln: cells for ln, cells in ref}
        for r in rows:
            assert r["login_url"] == by_line[r["line"]][0].strip(), (text, r)
        starts = [ln for ln, _ in ref] + [len(lines) + 2]
        spans = [b - a for a, b in zip(starts, starts[1:])]
        seen_multi += any(n > 1 for n in spans)
        seen_fallback += text.count('"') % 2
    # the generator really reaches both branches
    assert seen_multi > 100 and seen_fallback > 100, (seen_multi, seen_fallback)
