"""O1826 BRIEF-44 perf-algorithms (O1778 TECH_DEBT_REPORT M015 M121 M177 M043 M137 M168).

Each hot path is driven on a fixture with an operation counter: hamming_distance calls
(dedup groups), Levenshtein DP lines executed (alias fuzzy lookup), bs4 Tag.__eq__ calls
(candidate walk), MEDIA_JSON_KEYS .lower() calls (JSON media walk), link queries and URL
resolutions inside _ANCHOR_JS, and WalFrame page copies per CDC poll.  At base every counter
grows with the square (or product) of the input; the bound asserted here is far below that.
Every case also pins the output to the base algorithm (a verbatim reference copy, or
expectations hand-derived from base) so the speedup cannot change a result.
"""
from __future__ import annotations

import json
import random
import sqlite3
import sys

import pytest

BD_GATE_SCOPE = "module"


# ── M015 app_dedup: /api/dedup/groups pairwise Hamming grouping ─────────────


def _dedup_rows():
    rnd = random.Random(1826)
    rows = []

    def add(h):
        rows.append((f"/lib/v{len(rows):04d}.mp4", h, 1000 + len(rows), float(len(rows))))

    for _ in range(600):
        add(f"{rnd.getrandbits(64):016x}")
    for _ in range(30):  # near-duplicate clusters, 1..4 bits apart, some exact
        base = rnd.getrandbits(64)
        add(f"{base:016x}")
        add(f"{base:016x}")
        for flips in (1, 2, 3, 4, 5):
            v = base
            for bit in rnd.sample(range(64), flips):
                v ^= 1 << bit
            add(f"{v:016x}")
    for _ in range(20):  # short (16-bit) hashes: distance >= bits at d=32
        add(f"{rnd.getrandbits(16):04x}")
    hx = f"{rnd.getrandbits(64):016x}"
    add(hx.upper())  # case-folded twin of a plain hash
    add(hx)
    # Not plain hex, yet int(.., 16) accepts some of them: kept as loose candidates.
    for odd in (None, "", "zzzzzzzzzzzzzzzz", "0x" + hx[2:], " " + hx[1:], "-" + hx[1:], "1_" + hx[2:]):
        add(odd)
    return rows


def _reference_groups(rows, distance, hamming):
    """The base grouping loop, verbatim semantics (rows[i+1:] scan)."""
    seen: set = set()
    groups: list = []
    for i, r in enumerate(rows):
        if r[0] in seen:
            continue
        group = [{"path": r[0], "hash_hex": r[1], "file_size_bytes": r[2] or 0,
                  "computed_at": r[3] or 0.0, "distance": 0}]
        for r2 in rows[i + 1:]:
            if r2[0] in seen:
                continue
            d = hamming(r[1], r2[1])
            if 0 <= d <= distance:
                group.append({"path": r2[0], "hash_hex": r2[1], "file_size_bytes": r2[2] or 0,
                              "computed_at": r2[3] or 0.0, "distance": d})
                seen.add(r2[0])
        if len(group) > 1:
            seen.add(r[0])
            groups.append({
                "members": group,
                "max_distance": max(m["distance"] for m in group),
                "size_diff_bytes": max(m["file_size_bytes"] for m in group)
                                   - min(m["file_size_bytes"] for m in group),
            })
    return groups


@pytest.fixture
def dedup_client(monkeypatch):
    flask = pytest.importorskip("flask")
    from bulk_downloader import app_dedup, dedup

    rows = _dedup_rows()
    calls = {"n": 0}

    class _CountingDedup:
        @staticmethod
        def hamming_distance(a, b):
            calls["n"] += 1
            return dedup.hamming_distance(a, b)

    class _Registry:
        def _conn(self):
            c = sqlite3.connect(":memory:")
            c.execute("CREATE TABLE video_hashes (path TEXT, hash_hex TEXT, "
                      "file_size_bytes INTEGER, computed_at REAL)")
            c.executemany("INSERT INTO video_hashes VALUES (?,?,?,?)", rows)
            return c

    monkeypatch.setattr(app_dedup, "_app__DEDUP_AVAILABLE", lambda: True)
    monkeypatch.setattr(app_dedup, "_app__dedup", lambda: _CountingDedup)
    monkeypatch.setattr(app_dedup, "_dedup_get_registry", lambda: _Registry())
    app = flask.Flask(__name__)
    app.register_blueprint(app_dedup.dedup_bp)
    return app.test_client(), rows, calls, dedup.hamming_distance


@pytest.mark.parametrize("distance", [0, 4, 9, 32])
def test_dedup_groups_identical_to_base_grouping(dedup_client, distance):
    client, rows, _calls, hamming = dedup_client
    doc = client.get(f"/api/dedup/groups?distance={distance}").get_json()
    assert doc["ok"] is True and doc["total_files"] == len(rows)
    expected = _reference_groups(rows, distance, hamming)
    assert expected, "positive control: the fixture must form groups"
    assert doc["groups"] == expected
    assert doc["group_count"] == len(expected)


def test_dedup_groups_compares_only_bucketed_pairs(dedup_client):
    client, rows, calls, _h = dedup_client
    doc = client.get("/api/dedup/groups?distance=4").get_json()
    assert doc["group_count"] >= 30
    all_pairs = len(rows) * (len(rows) - 1) // 2
    # base compares every unseen pair (~all_pairs); bucketing must cut that 20x
    assert calls["n"] * 20 < all_pairs, (
        f"M015: {calls['n']} hamming_distance calls for {len(rows)} rows "
        f"({all_pairs} pairs) -- grouping is still all-pairs")


# ── M121 metadata_normalizer: fuzzy alias lookup ────────────────────────────


def _base_levenshtein(a, b):
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[-1]


def _base_lookup(mn, table, name, max_d):
    folded = mn._fold(name)
    if folded in table:
        return (mn.to_filesystem_component(table[folded]), True, False, 0)
    if folded and max_d > 0:
        best, best_d = None, max_d + 1
        for key, canon in table.items():
            d = _base_levenshtein(folded, key)
            if d < best_d:
                best_d, best = d, canon
        if best is not None and best_d <= max_d:
            return (mn.to_filesystem_component(best), True, True, best_d)
    return (mn.to_filesystem_component(name), False, False, 0)


def _alias_fixture():
    rnd = random.Random(121)
    letters = "abcdefghijklmnopqrstuvwxyz  "
    aliases = {}
    for i in range(400):
        n = rnd.randint(3, 60)
        name = "".join(rnd.choice(letters) for _ in range(n)).strip() or f"x{i}"
        aliases[name] = f"Canon {i:03d}"
    aliases["Abcde"] = "Tie A"  # equal-distance ties: the first key in order must win
    aliases["Abcdx"] = "Tie B"
    aliases["!!"] = "Folds To Empty"
    queries = ["abcdy", "abcd", "abcdeqq", "Tie", "", "zz"]
    keys = list(aliases)
    for _ in range(60):
        k = list(rnd.choice(keys))
        for _e in range(rnd.randint(0, 3)):
            op = rnd.randrange(3)
            pos = rnd.randrange(len(k) + 1)
            if op == 0:
                k.insert(pos, rnd.choice(letters))
            elif op == 1 and k:
                del k[min(pos, len(k) - 1)]
            elif k:
                k[min(pos, len(k) - 1)] = rnd.choice(letters)
        queries.append("".join(k))
    for _ in range(20):
        queries.append("".join(rnd.choice(letters) for _ in range(rnd.randint(1, 50))))
    return aliases, queries


@pytest.mark.parametrize("max_d", [1, 2, 3])
def test_alias_lookup_identical_to_base(max_d):
    from bulk_downloader import metadata_normalizer as mn

    aliases, queries = _alias_fixture()
    table = mn.AliasTable(aliases, fuzzy_max_distance=max_d)
    folded_table = {mn._fold(v): c for v, c in aliases.items()}
    fuzzy_hits = 0
    for q in queries:
        r = table.lookup(q)
        got = (r.canonical, r.matched_alias, r.fuzzy, r.distance)
        assert got == _base_lookup(mn, folded_table, q, max_d), q
        fuzzy_hits += r.fuzzy
    assert fuzzy_hits >= 10, "positive control: the fixture must exercise the fuzzy path"


def test_alias_lookup_levenshtein_work_is_bounded():
    from bulk_downloader import metadata_normalizer as mn

    aliases, queries = _alias_fixture()
    table = mn.AliasTable(aliases)
    keys = [mn._fold(v) for v in aliases]
    target = mn.__file__
    lines = {"n": 0}

    def local(frame, event, arg):
        if event == "line":
            lines["n"] += 1
        return local

    def tracer(frame, event, arg):
        code = frame.f_code
        if code.co_filename == target and code.co_name.startswith("_levenshtein"):
            return local
        return None

    full_cells = 0
    for q in queries:
        f = mn._fold(q)
        if f and f not in table._exact:
            full_cells += sum(len(f) * len(k) for k in keys)
    old = sys.gettrace()
    sys.settrace(tracer)
    try:
        for q in queries:
            table.lookup(q)
    finally:
        sys.settrace(old)
    # base runs the full len(a)*len(b) table for every alias (>= 1 line per cell)
    assert lines["n"] * 10 < full_cells, (
        f"M121: {lines['n']} Levenshtein lines for {full_cells} full-table cells -- "
        "no length prefilter / early exit")


# ── M177 template_extractor candidates: element membership ─────────────────


def test_candidate_walk_does_no_deep_tag_equality(monkeypatch):
    bs4 = pytest.importorskip("bs4")
    from bulk_downloader.template_extractor_impl import candidates

    html = "".join(f'<p><a href="/a{i}">a{i}</a></p>' for i in range(200))
    html += "".join(f'<section><div data-url="/d{i}">d{i}</div></section>' for i in range(200))
    soup = bs4.BeautifulSoup(f"<html><body>{html}</body></html>", "html.parser")
    calls = {"n": 0}
    real_eq = bs4.element.Tag.__eq__

    def counting_eq(self, other):
        calls["n"] += 1
        return real_eq(self, other)

    monkeypatch.setattr(bs4.element.Tag, "__eq__", counting_eq)
    out = candidates._walk_for_candidates(soup)
    assert len(out) == 400
    # base: every data-* element is compared to every element already listed (~60k)
    assert calls["n"] <= 400, f"M177: {calls['n']} Tag.__eq__ calls for 400 elements"


def test_candidate_walk_identical_to_base_on_structural_twins():
    bs4 = pytest.importorskip("bs4")
    from bulk_downloader.template_extractor_impl import candidates

    html = ('<div class="one"><div data-url="/x">same</div></div>'
            '<section class="two"><div data-url="/x">same</div></section>'
            '<a href="/y">y</a><div data-src="/z">z</div><div data-href="">  </div>')
    soup = bs4.BeautifulSoup(html, "html.parser")
    twins = soup.find_all(attrs={"data-url": True})
    assert twins[0] == twins[1] and twins[0] is not twins[1]
    out = candidates._walk_for_candidates(soup)
    got = [(c["tag"], c["text"], c["href"], c["data_url"], c["data_src"], c["ancestor_text"])
           for c in out]
    # base: tag pass first, the twin collapsed into its first occurrence, blanks skipped
    assert got == [("a", "y", "/y", "", "", ""),
                   ("div", "same", "", "/x", "", "one"),
                   ("div", "z", "", "", "/z", "")]
    assert out[1]["_el"] is twins[0]


# ── M043 deep_detect providers: media-key check in the JSON walk ───────────


def test_json_walk_lowercases_media_keys_once(monkeypatch):
    from bulk_downloader.deep_detect import providers

    calls = {"n": 0}

    class _CountingKey(str):
        def lower(self):
            calls["n"] += 1
            return str.lower(self)

    keys = tuple(_CountingKey(k) for k in providers.MEDIA_JSON_KEYS)
    monkeypatch.setattr(providers, "MEDIA_JSON_KEYS", keys)
    doc = {"items": [{"id": i, "title": f"t{i}", "thumb": f"/t{i}.jpg", "VideoUrl": f"/v{i}.mp4"}
                     for i in range(300)]}
    out = providers._walk_json_for_media(doc, base_url="https://h.test/")
    assert len(out) == 300
    # base re-lowers the whole key tuple for every dict key walked (~1200 x 44)
    assert calls["n"] <= len(keys), f"M043: {calls['n']} .lower() calls on MEDIA_JSON_KEYS"


def test_json_walk_identical_to_base_and_follows_rebound_keys(monkeypatch):
    from bulk_downloader.deep_detect import providers

    doc = {"VideoUrl": "https://x.test/a.mp4",
           "meta": {"note": "see https://x.test/b.m3u8?t=1 here", "HLS": "  ", "Custom": "/c"},
           "list": [{"src": "/c.webm"}, {"title": "no url"}]}
    out = providers._walk_json_for_media(doc, base_url="https://h.test/")
    got = [(e["key"], e["path"], e["via"], e["url"]) for e in out]
    dec = providers.decode_url
    assert got == [
        ("VideoUrl", ["VideoUrl"], "media_key", dec("https://x.test/a.mp4", base_url="https://h.test/")),
        ("note", ["meta", "note"], "url_shaped_string",
         dec("https://x.test/b.m3u8?t=1", base_url="https://h.test/")),
        ("src", ["list", 0, "src"], "media_key", dec("/c.webm", base_url="https://h.test/")),
    ]
    # the key set is read at call time, as base did: a rebound tuple must take effect
    monkeypatch.setattr(providers, "MEDIA_JSON_KEYS", ("CUSTOM",))
    out = providers._walk_json_for_media(doc, base_url="https://h.test/")
    assert [(e["key"], e["via"]) for e in out] == [("VideoUrl", "url_shaped_string"),
                                                   ("note", "url_shaped_string"),
                                                   ("Custom", "media_key")]


# ── M137 scene_crawler: rival search in _ANCHOR_JS ─────────────────────────

_N_CARDS = 60
_N_SHADOW = 6
_SITE = "https://site.test"


def _listing_html():
    cards = []
    for i in range(_N_CARDS):
        img = '<img src="data:,">' if i % 2 == 0 else ""
        cards.append(f'<div class="row"><div class="cell">{img}<div class="box">'
                     f'<nuxtlink to="{_SITE}/members/video/slug-{i}">Title {i}</nuxtlink>'
                     f'</div></div></div>')
    shadow = "".join(
        f'<div class=\\"cell\\">{"<img src=data:,>" if i % 2 == 0 else ""}'
        f'<div class=\\"box\\"><nuxtlink to=\\"{_SITE}/members/video/shadow-{i}\\">S {i}</nuxtlink></div></div>'
        for i in range(_N_SHADOW))
    # absolute URLs: a set_content page's base is about:blank, where a relative
    # router path resolves to "" and every link would share one kind
    return (f'<html><body><a href="{_SITE}/about">About</a>'
            f'<a href="{_SITE}/members/video/slug-0">dup of 0</a>'
            f'<div class="listing">{"".join(cards)}</div>'
            '<div id="host"></div>'
            '<script>document.getElementById("host").attachShadow({mode: "open"})'
            f'.innerHTML = "<div class=\\"listing\\">{shadow}</div>";</script>'
            '</body></html>')


_COUNTED = r"""
(els) => {
  const c = {qsa: 0, attr: 0};
  const restore = [];
  for (const proto of [Element.prototype, Document.prototype, DocumentFragment.prototype]) {
    const orig = proto.querySelectorAll;
    proto.querySelectorAll = function (sel) {
      if (sel === __SEL__) c.qsa++;
      return orig.call(this, sel);
    };
    restore.push(() => { proto.querySelectorAll = orig; });
  }
  const ga = Element.prototype.getAttribute;
  Element.prototype.getAttribute = function (n) {
    if (n === "href" || n === "to") c.attr++;
    return ga.call(this, n);
  };
  restore.push(() => { Element.prototype.getAttribute = ga; });
  try {
    const rows = (__ANCHOR_JS__)(els);
    return {rows: rows, counts: c};
  } finally {
    restore.forEach((f) => f());
  }
}
"""


@pytest.fixture(scope="module")
def anchor_run():
    sync_api = pytest.importorskip("playwright.sync_api")
    from bulk_downloader import scene_crawler as sc

    js = (_COUNTED.replace("__SEL__", json.dumps(sc._LINK_SELECTOR))
          .replace("__ANCHOR_JS__", sc._ANCHOR_JS.strip()))
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(_listing_html())
            result = page.locator(sc._LINK_SELECTOR).evaluate_all(js)
        finally:
            browser.close()
    return result


def test_anchor_js_card_thumbnails_identical_to_base(anchor_run):
    rows = anchor_run["rows"]
    by_slug = {r["url"].rsplit("/", 1)[-1]: r for r in rows if "/members/video/" in r["url"]}
    for i in range(_N_CARDS):
        # base: an even card's <img> is a sibling inside the rival-free cell; an odd
        # card reaches the listing, which holds rivals, before any image
        assert by_slug[f"slug-{i}"]["has_img"] is (i % 2 == 0), i
    for i in range(_N_SHADOW):
        assert by_slug[f"shadow-{i}"]["has_img"] is (i % 2 == 0), i
    assert len(rows) == 2 + _N_CARDS + _N_SHADOW


def test_anchor_js_queries_and_resolves_each_link_once(anchor_run):
    counts = anchor_run["counts"]
    n_links = 2 + _N_CARDS + _N_SHADOW
    # base: one link query per ancestor per router link (~4 x 66) and two URL
    # resolutions per link in every queried subtree (~2 x 66 x 66)
    assert counts["qsa"] <= 2, f"M137: {counts['qsa']} link queries (one per tree expected)"
    assert counts["attr"] <= n_links, f"M137: {counts['attr']} URL resolutions for {n_links} links"


# ── M168 sqlite_cdc: per-poll WAL parse ─────────────────────────────────────


class _Sink:
    def ensure_table(self, *a):
        return True

    def apply_events(self, *a):
        return True


def _wal_bytes(tmp_path):
    db = tmp_path / "src.db"
    conn = sqlite3.connect(str(db), isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        for i in range(5):
            conn.execute("BEGIN")
            conn.executemany("INSERT INTO t (v) VALUES (?)", [(f"{i}-{j}" * 40,) for j in range(30)])
            conn.execute("COMMIT")
        conn.execute("BEGIN")
        conn.execute("UPDATE t SET v = 'x' WHERE id < 10")
        conn.execute("COMMIT")
        return (tmp_path / "src.db-wal").read_bytes()
    finally:
        conn.close()


def _wal_variants(data):
    page_size = int.from_bytes(data[8:12], "big")
    frame = 24 + page_size
    flipped = bytearray(data)
    flipped[-10] ^= 0xFF
    resalted = bytearray(data)
    resalted[32 + frame + 8] ^= 0x01  # second frame's salt1
    return {
        "full": data,
        "torn_tail": data[:-(page_size // 2)],
        "bad_last_checksum": bytes(flipped),
        "salt_break": bytes(resalted),
        "header_only": data[:32],
        "corrupt_header": b"\x00" * 4 + data[4:],
        "empty": b"",
        "absent": None,
    }


def test_cdc_poll_transaction_count_identical_to_base(tmp_path):
    from bulk_downloader import sqlite_cdc

    data = _wal_bytes(tmp_path)
    seen = {}
    for name, blob in _wal_variants(data).items():
        db = tmp_path / f"{name}.db"
        wal = str(db) + "-wal"
        if blob is not None:
            with open(wal, "wb") as fh:
                fh.write(blob)
        expected = len(list(sqlite_cdc.iter_wal_transactions(wal)))
        stream = sqlite_cdc.SqliteCDCStream(str(db), [], sink=_Sink(),
                                            state_path=str(tmp_path / f"{name}.json"))
        got = stream.poll()["transactions"]
        assert got == expected, name
        seen[name] = got
    # CREATE TABLE + 5 INSERT batches + 1 UPDATE
    assert seen["full"] == 7, "positive control: seven committed transactions"
    assert seen["torn_tail"] == seen["bad_last_checksum"] == 6
    assert seen["salt_break"] < seen["full"]
    assert seen["corrupt_header"] == seen["header_only"] == seen["empty"] == seen["absent"] == 0


def test_cdc_poll_copies_no_wal_pages(tmp_path, monkeypatch):
    from bulk_downloader import sqlite_cdc

    db = tmp_path / "p.db"
    data = _wal_bytes(tmp_path)
    (tmp_path / "p.db-wal").write_bytes(data)
    real = sqlite_cdc.WalFrame
    frames = {"n": 0}

    def counting_frame(*a, **kw):
        frames["n"] += 1
        return real(*a, **kw)

    expected = len(list(sqlite_cdc.iter_wal_transactions(str(tmp_path / "p.db-wal"))))
    monkeypatch.setattr(sqlite_cdc, "WalFrame", counting_frame)
    assert len(list(sqlite_cdc.iter_wal_frames(str(tmp_path / "p.db-wal")))) == frames["n"] > 0
    frames["n"] = 0
    stream = sqlite_cdc.SqliteCDCStream(str(db), [], sink=_Sink(),
                                        state_path=str(tmp_path / "p.json"))
    assert stream.poll()["transactions"] == expected == 7
    assert frames["n"] == 0, f"M168: poll materialised {frames['n']} WAL frames to count transactions"
