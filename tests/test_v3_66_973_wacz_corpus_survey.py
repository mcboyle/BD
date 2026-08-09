"""bd-wacz-corpus: a read-only survey of a local WACZ corpus.

@973. Four analyses over one denominator, each independently selectable so their
answers can be compared rather than trusted:

  --group   site/player grouping, so merge inputs are DERIVED not eyeballed
  --pairs   raw <-> .redacted twins and the size ratio between them
  --health  does each archive parse, pass CRC, and carry a WARC payload
  --scrub   does each still carry findings under the canonical floor

THE DENOMINATOR IS THE WHOLE POINT. CLAUDE.md section 0's opening rule is that a
gate which cannot see its subject reports OK -- truthfully and uselessly -- and
this repo has the scar: bd-wacz-scrub, bd-scrub-proof and bd-share-safe each
carried a TEXT_EXT allowlist with no `.warc` in it, and all three printed
"verified clean" over 228 contaminated files. So every count here states what it
counted, an empty corpus is UNKNOWN rather than "0 problems", and a member that
could not be read is its own third state rather than a member with nothing in it.

Exit codes: 0 ran-and-clean, 1 ran-with-findings, 2 UNKNOWN (cannot answer).
2 is NOT a softer 1.
"""

import json
import pathlib
import subprocess
import sys
import zipfile

REPO = pathlib.Path(__file__).resolve().parent.parent
TOOL = REPO / "toolchain" / "bin" / "bd-wacz-corpus"

# Zero-entropy on purpose (CLAUDE.md section 7): gitleaks scans this file, and a
# realistic-looking value would make the test a place a secret lives. The
# scrubber matches this structurally on the KEY, not on entropy, so a repeated
# character is still detected. Do not "improve" it into something realistic.
_KV_SECRET = "api_key=" + ("a" * 32)


def _wacz(path, members):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return path


def _good(path, warc=b"WARC/1.1\r\nWARC-Type: response\r\n\r\nhello\r\n"):
    return _wacz(path, {
        "archive/data.warc": warc,
        "indexes/index.cdxj": b"com,example)/ 20200101 {}\n",
        "datapackage.json": json.dumps({"profile": "data-package"}).encode(),
    })


def _run(*args):
    return subprocess.run([sys.executable, str(TOOL), *args],
                          capture_output=True, text=True, timeout=300)


def _json(*args):
    r = _run(*args, "--json")
    assert r.stdout.strip(), "no stdout to parse: rc=%d %s" % (r.returncode, r.stderr[-400:])
    return r.returncode, json.loads(r.stdout)


def test_the_tool_exists_and_is_python():
    assert TOOL.is_file(), "%s is absent" % TOOL
    assert TOOL.read_text(encoding="utf-8").startswith("#!"), "no shebang"


def test_an_EMPTY_corpus_is_UNKNOWN_not_clean(tmp_path):
    """THE LOAD-BEARING ASSERTION. A survey over nothing must not read as a pass.

    This is the exact shape section 0 opens with, and the shape check_requirements
    shipped with: 'every entry resolves', over nothing, exit 0, silent stdout.
    """
    rc, out = _json("--root", str(tmp_path), "--all")
    assert rc == 2, (
        "an empty corpus exited %d; a survey that found NOTHING must report "
        "UNKNOWN, because 'no findings over zero files' is indistinguishable "
        "from 'no findings' and the second is what a reader will take from it"
        % rc)
    assert out.get("status") == "unknown"
    assert out["denominator"]["wacz_files"] == 0


def test_a_MISSING_root_is_UNKNOWN_not_empty(tmp_path):
    """A root that does not exist is a different failure from a root with no files,
    and neither is 'clean'. Collapsing them hides a mistyped path."""
    rc, out = _json("--root", str(tmp_path / "nope"), "--all")
    assert rc == 2 and out.get("status") == "unknown"
    assert "root" in json.dumps(out).lower()


def test_every_count_STATES_its_denominator(tmp_path):
    """CLAUDE.md section 1: say which denominator a count is over, in the same
    sentence as the count. A bare number in a report is how 'All three importers'
    ended up beside a 2108-file instrument."""
    _good(tmp_path / "t_aaaa_site1.wacz")
    _good(tmp_path / "t_bbbb_site2.wacz")
    rc, out = _json("--root", str(tmp_path), "--all")
    assert rc in (0, 1)
    assert out["denominator"]["wacz_files"] == 2, out["denominator"]
    for mode in ("group", "pairs", "health", "scrub"):
        assert mode in out["modes"], "mode %r missing from output" % mode
        assert "examined" in out["modes"][mode], (
            "mode %r reports no denominator of its own -- a reader cannot tell "
            "how many files its answer is over: %r" % (mode, out["modes"][mode]))


def test_PAIRS_finds_the_twin_and_reports_the_ratio(tmp_path):
    """The question I could not settle from Drive metadata alone."""
    _wacz(tmp_path / "t_cccc_x.wacz", {"archive/data.warc": b"A" * 40000,
                                       "datapackage.json": b"{}"})
    _wacz(tmp_path / "t_cccc_x.redacted.wacz", {"archive/data.warc": b"A" * 4000,
                                                "datapackage.json": b"{}"})
    rc, out = _json("--root", str(tmp_path), "--pairs")
    p = out["modes"]["pairs"]
    assert p["paired"] == 1, p
    assert p["unpaired_raw"] == 0, p
    entry = p["entries"][0]
    assert "ratio" in entry and 0 < entry["ratio"] < 1, entry
    assert entry["raw_bytes"] > entry["redacted_bytes"]


def test_PAIRS_does_not_count_a_redacted_file_as_its_own_raw(tmp_path):
    """Over-sensitivity guard: `x.redacted.wacz` must not also enumerate as a raw
    named `x.redacted`, which would invent a phantom unpaired capture."""
    _wacz(tmp_path / "t_dddd_y.redacted.wacz", {"archive/data.warc": b"A" * 100,
                                                "datapackage.json": b"{}"})
    rc, out = _json("--root", str(tmp_path), "--pairs")
    p = out["modes"]["pairs"]
    assert p["unpaired_raw"] == 0, (
        "a .redacted file was counted as an unpaired RAW capture: %r" % p)
    assert p["unpaired_redacted"] == 1, p


def test_HEALTH_separates_BROKEN_from_UNREADABLE_from_FINE(tmp_path):
    """Three states, because 'could not open' is not 'opened and found nothing'."""
    _good(tmp_path / "t_eeee_ok.wacz")
    (tmp_path / "t_ffff_trunc.wacz").write_bytes(b"PK\x03\x04 not really a zip")
    _wacz(tmp_path / "t_gggg_nowarc.wacz", {"datapackage.json": b"{}"})
    rc, out = _json("--root", str(tmp_path), "--health")
    h = out["modes"]["health"]
    assert h["examined"] == 3, h
    assert h["ok"] == 1, h
    assert h["unreadable"] == 1, (
        "the truncated archive was not reported as UNREADABLE: %r" % h)
    assert h["no_payload"] == 1, (
        "an archive with no WARC member was not flagged -- it would silently "
        "contribute nothing to a merge: %r" % h)
    assert rc == 1, "findings present but exit was %d" % rc


def test_SCRUB_never_reports_clean_over_a_member_it_could_not_read(tmp_path):
    """The @859/@971 lesson, mechanised.

    A binary member cannot be regexed and must be passed over -- that part is
    correct. Reporting the FILE clean on that basis is not: the scan's
    denominator excluded the only member that mattered.
    """
    _wacz(tmp_path / "t_hhhh_bin.wacz", {
        "archive/data.warc": b"\x00\x01\x02" + _KV_SECRET.encode() + b"\x00",
        "datapackage.json": b"{}"})
    rc, out = _json("--root", str(tmp_path), "--scrub")
    s = out["modes"]["scrub"]
    assert s["examined"] == 1, s
    assert s.get("unscannable_members", 0) >= 1, (
        "a member that could not be scanned was not counted, so 'clean' here "
        "would be a verdict over a denominator missing its subject: %r" % s)
    assert s["clean"] == 0, (
        "the file was declared clean while a member went unread: %r" % s)


def test_SCRUB_does_find_a_finding_in_readable_text(tmp_path):
    """The other direction: a tool that called everything unscannable would pass
    the test above and be worthless."""
    _wacz(tmp_path / "t_iiii_txt.wacz", {
        "archive/data.warc": ("WARC/1.1\r\n\r\n" + _KV_SECRET).encode(),
        "datapackage.json": b"{}"})
    rc, out = _json("--root", str(tmp_path), "--scrub")
    s = out["modes"]["scrub"]
    assert s["with_findings"] == 1, (
        "a plain-text member carrying a key=value secret was not flagged: %r" % s)
    assert rc == 1


def test_GROUP_derives_site_candidates_from_the_naming(tmp_path):
    """t_<hash>_<name>.wacz -- the name is the only site signal available."""
    for n in ("t_1111aaaabbbbcccc_reptyle.wacz", "t_2222aaaabbbbcccc_reptyle-2.wacz",
              "t_3333aaaabbbbcccc_reptyle259.wacz", "t_4444aaaabbbbcccc_bitmovin.wacz"):
        _good(tmp_path / n)
    rc, out = _json("--root", str(tmp_path), "--group")
    g = out["modes"]["group"]
    assert g["examined"] == 4, g
    groups = {x["site"]: x["captures"] for x in g["groups"]}
    assert groups.get("reptyle") == 3, (
        "the three reptyle captures did not group: %r" % groups)
    cands = [x["site"] for x in g["groups"] if x.get("merge_candidate")]
    assert "reptyle" in cands and "bitmovin" not in cands, (
        "merge candidacy should need >1 capture: %r" % g["groups"])


def test_it_NEVER_writes_to_the_corpus(tmp_path):
    """Read-only, asserted rather than claimed. A survey that mutates its subject
    changes the thing the next run measures."""
    import hashlib
    p = _good(tmp_path / "t_jjjj_ro.wacz")
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    listing_before = sorted(x.name for x in tmp_path.iterdir())
    _run("--root", str(tmp_path), "--all")
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before, "the tool rewrote a capture"
    assert sorted(x.name for x in tmp_path.iterdir()) == listing_before, (
        "the tool created or removed files in the corpus directory")
