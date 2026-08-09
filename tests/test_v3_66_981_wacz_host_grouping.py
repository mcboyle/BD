"""bd-wacz-corpus --hosts: group a WACZ corpus by HOST, and say how each group
was derived.

@981, item 44. `--group` reads the `t_<hex>_<name>` export convention, which the
box's real corpus does not use: there, `123.wacz` and `1232.wacz` carry no site
signal at all, and no filename heuristic will ever group them. Three tiers,
filename-first with the archive as the authority:

  1. the `{host}_{siteid}_{YYYYMMDD}` capture naming, parsed by the SAME
     function the capture picker uses -- no archive is opened.  method=filename
  2. `pages/pages.jsonl`, the WACZ page record `wacz_export._pages_jsonl`
     writes. The authority, and the only thing that groups `123` with `1232`.
     method=archive
  3. the stem, grouped under ITSELF and never guessed.  method=unknown

EVERY GROUP CARRIES THE METHOD THAT PRODUCED IT, which is the whole point: a
guessed grouping and a measured one must never be indistinguishable in the
output. CLAUDE.md section 0 -- a check that cannot answer must say so, and
unknown is a third state.

Exit codes are the tool's existing contract: 0 ran-and-clean, 1
ran-with-findings, 2 UNKNOWN. An archive that could not be OPENED is a finding.
A archive that opened and simply carries no URL is a property of the corpus,
not a defect, and does not flip the exit -- a screen that cries wolf on 66
orphans gets switched off, which section 0 names as a soundness bug of its own.
"""

import importlib.machinery
import importlib.util
import json
import pathlib
import subprocess
import sys
import zipfile

REPO = pathlib.Path(__file__).resolve().parent.parent
TOOL = REPO / "toolchain" / "bin" / "bd-wacz-corpus"

# A real capture name off the box: {host}_{siteid}_{YYYYMMDD}_{HHMMSS}_{rand}.
_BD_NAME = "auth.example.com_0b60f1ec_20260629_145050_52e5"


def _pages(url, *, header=True):
    """A WACZ pages.jsonl: the format header, then one page record."""
    lines = []
    if header:
        lines.append(json.dumps({"format": "json-pages-1.0", "id": "pages",
                                 "title": "Capture pages"}))
    lines.append(json.dumps({"id": "page-0", "url": url, "ts": "2026-06-29",
                             "title": "t"}))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _wacz(path, members):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return path


def _cap(path, url=None, *, pages=True):
    """A well-formed capture; `pages=False` omits pages.jsonl entirely."""
    members = {"archive/data.warc": b"WARC/1.1\r\n\r\nhello\r\n",
               "datapackage.json": b"{}"}
    if pages:
        members["pages/pages.jsonl"] = _pages(url or "https://unset.example/x")
    return _wacz(path, members)


def _run(*args):
    return subprocess.run([sys.executable, str(TOOL), *args],
                          capture_output=True, text=True, timeout=300)


def _hosts(root):
    r = _run("--root", str(root), "--hosts", "--json")
    assert r.stdout.strip(), (
        "no stdout to parse: rc=%d %s" % (r.returncode, r.stderr[-500:]))
    out = json.loads(r.stdout)
    assert "hosts" in out.get("modes", {}), (
        "--hosts produced no `hosts` mode: %r" % sorted(out.get("modes", {})))
    return r.returncode, out["modes"]["hosts"]


def _by_host(mode):
    return {g["host"]: g for g in mode["groups"]}


def _load_tool_module():
    """Load the extensionless tool as a module so its internals can be driven
    directly. Nothing else can express 'the filename parser was unavailable'."""
    loader = importlib.machinery.SourceFileLoader("bd_wacz_corpus_under_test",
                                                  str(TOOL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- tier 1

def test_HOSTS_resolves_a_BD_capture_from_the_FILENAME_alone(tmp_path):
    """Tier 1. BD's own naming carries the host, so the archive stays shut.

    `archives_opened` is the assertion that matters: the box corpus is 4.04 GB,
    and a grouping that opens every archive to learn what the filename already
    said is a different cost and a different failure mode.
    """
    _cap(tmp_path / (_BD_NAME + ".wacz"), "https://auth.example.com/scene/2")
    rc, h = _hosts(tmp_path)
    groups = _by_host(h)
    assert "auth.example.com" in groups, (
        "the host in the capture filename was not used: %r" % sorted(groups))
    assert groups["auth.example.com"]["methods"] == ["filename"], groups
    assert h["archives_opened"] == 0, (
        "the filename already carried the host and the tool opened the archive "
        "anyway (%d opened)" % h["archives_opened"])


def test_HOSTS_resolves_a_DERIVATIVE_filename_WITHOUT_opening_the_archive(tmp_path):
    """MEASURED, and it is the defect @978 fixed wearing a different hat.

    `_parse_capture_host` anchors on a date-ish TAIL, so it returns None for
    `<name>.redacted (2)` -- the marker and the copy suffix sit where the tail
    has to be. Measured on this tree: two of six real-shaped names resolve from
    `_base()` and NEITHER resolves from the raw stem. A tool that forgets to
    strip first falls through to the archive for 601 redacted files.

    The discriminator is deliberate: this archive is TRUNCATED. Resolve from the
    filename and it is never opened; fall through and the run reports an
    unreadable archive and a finding.
    """
    (tmp_path / (_BD_NAME + ".redacted (2).wacz")).write_bytes(
        b"PK\x03\x04 not really a zip")
    rc, h = _hosts(tmp_path)
    groups = _by_host(h)
    assert "auth.example.com" in groups, (
        "a derivative filename did not resolve to its host -- the copy suffix "
        "and derivative marker were not stripped before parsing: %r"
        % sorted(groups))
    assert h["archives_opened"] == 0, (
        "the archive was opened for a name that already carried the host")
    assert h["unreadable"] == 0 and rc == 0, (
        "a truncated archive was read that never needed reading: rc=%d %r"
        % (rc, h))


# ---------------------------------------------------------------- tier 2

def test_HOSTS_reads_the_ARCHIVE_when_the_filename_carries_no_host(tmp_path):
    """Tier 2, and the case item 44 exists for.

    `123.wacz` and `1232.wacz` are real names off the box. No filename rule will
    ever group them; `pages/pages.jsonl` is the only thing that can.
    """
    _cap(tmp_path / "123.wacz", "https://vod.example.org/watch/1")
    _cap(tmp_path / "1232.wacz", "https://vod.example.org/watch/2")
    rc, h = _hosts(tmp_path)
    groups = _by_host(h)
    assert "vod.example.org" in groups, (
        "the archive's own page record did not group two site-signal-free "
        "filenames: %r" % sorted(groups))
    g = groups["vod.example.org"]
    assert g["captures"] == 2 and g["sources"] == 2, g
    assert g["methods"] == ["archive"], g
    assert g["merge_candidate"] is True, g
    assert h["archives_opened"] == 2, h


def test_HOSTS_group_label_carries_no_CREDENTIALS_and_no_PORT(tmp_path):
    """The design said `urlsplit().netloc`; this ships `hostname` instead.

    netloc keeps `user:pass@` and `:port`, so the same site behind a port would
    form a SECOND group -- and the label, which is printed, would become a place
    a credential lives (CLAUDE.md section 7). Both fixtures below must land in
    one bare-host group.

    The userinfo here is a zero-entropy repeat on purpose, exactly as the @973
    suite's secret fixture is: gitleaks scans this file, and a realistic-looking
    value would make the test the leak it is asserting about.
    """
    _cap(tmp_path / "a.wacz", "https://aaaa:bbbb@Vod.Example.Org:8443/watch/1")
    _cap(tmp_path / "b.wacz", "https://vod.example.org/watch/2")
    rc, h = _hosts(tmp_path)
    groups = _by_host(h)
    assert "vod.example.org" in groups, sorted(groups)
    assert groups["vod.example.org"]["captures"] == 2, (
        "a port or userinfo split one host into two groups: %r" % sorted(groups))
    blob = json.dumps(h)
    assert "bbbb@" not in blob and ":8443" not in blob, (
        "userinfo or a port reached the report: %r" % sorted(groups))


# ---------------------------------------------------------------- tier 3

def test_HOSTS_never_GUESSES_and_says_WHY_it_could_not_answer(tmp_path):
    """Tier 3. No host in the name, none in the archive -- grouped under itself,
    labelled `unknown`, with the reason stated. Silence here is the failure:
    'grouped somewhere' and 'could not be grouped' must not look alike."""
    _cap(tmp_path / "plain.wacz", pages=False)
    rc, h = _hosts(tmp_path)
    groups = _by_host(h)
    assert "plain" in groups, (
        "an unresolvable capture did not group under its own stem: %r"
        % sorted(groups))
    g = groups["plain"]
    assert g["methods"] == ["unknown"], g
    assert g["files"][0].get("reason") == "no_pages_jsonl", (
        "the tool could not answer and did not say why: %r" % g["files"])
    assert h["unknown_reasons"].get("no_pages_jsonl") == 1, h


def test_HOSTS_an_UNKNOWN_never_joins_a_RESOLVED_group(tmp_path):
    """A stem that happens to LOOK like a host must not be laundered into a
    measured group. `host.example.com.wacz` carries no capture tail, so tier 1
    declines it; if its unknown bucket keyed on the bare label it would merge
    with the group a real filename produced and a guess would read as a
    measurement."""
    _cap(tmp_path / ("auth.example.com_0b60f1ec_20260629_145050_52e5.wacz"),
         "https://auth.example.com/x")
    _cap(tmp_path / "auth.example.com.wacz", pages=False)
    rc, h = _hosts(tmp_path)
    resolved = [g for g in h["groups"] if g["methods"] != ["unknown"]]
    unknown = [g for g in h["groups"] if g["methods"] == ["unknown"]]
    assert len(resolved) == 1 and len(unknown) == 1, (
        "a guessed bucket and a measured host collapsed into one group: %r"
        % h["groups"])
    assert resolved[0]["captures"] == 1, resolved
    assert unknown[0]["captures"] == 1, unknown


# ------------------------------------------------- counting, and the exit code

def test_HOSTS_counts_SOURCES_not_FILES_for_merge_candidacy(tmp_path):
    """A raw capture and its `.redacted` twin are ONE capture. Counting files
    would report a merge candidate for a site with a single capture, and a
    template merge needs two DIFFERENT captures to have anything to merge."""
    _cap(tmp_path / (_BD_NAME + ".wacz"), "https://auth.example.com/x")
    _cap(tmp_path / (_BD_NAME + ".redacted.wacz"), "https://auth.example.com/x")
    rc, h = _hosts(tmp_path)
    g = _by_host(h)["auth.example.com"]
    assert g["captures"] == 2, g
    assert g["sources"] == 1, (
        "a raw capture and its derivative counted as two sources: %r" % g)
    assert g["merge_candidate"] is False, (
        "one capture in two forms was reported as a merge candidate: %r" % g)
    assert h["merge_candidates"] == 0, h


def test_HOSTS_reports_an_UNREADABLE_archive_as_a_FINDING(tmp_path):
    """Three states, not two. 'could not open it' is not 'opened it and found no
    URL' -- the first is a defect and exits 1, the second is a property of the
    corpus and does not."""
    (tmp_path / "broken.wacz").write_bytes(b"PK\x03\x04 not really a zip")
    rc, h = _hosts(tmp_path)
    assert h["unreadable"] == 1, (
        "an archive that could not be opened was folded into the ordinary "
        "unknowns: %r" % h)
    assert rc == 1, "an unreadable archive did not flip the exit code: %d" % rc

    # And the other direction, in the same test: an archive that opens cleanly
    # and simply carries no URL must NOT flip it, or the mode cries wolf on
    # every site-signal-free name in the corpus and gets switched off.
    (tmp_path / "broken.wacz").unlink()
    _cap(tmp_path / "quiet.wacz", pages=False)
    rc2, h2 = _hosts(tmp_path)
    assert h2["unreadable"] == 0 and rc2 == 0, (
        "an openable archive with no page URL was reported as a finding: "
        "rc=%d %r" % (rc2, h2))


def test_HOSTS_by_method_accounts_for_EVERY_file_examined(tmp_path):
    """The non-empty-denominator assertion, written before the verdict. Every
    file lands in exactly one method bucket, so a file that silently fell out of
    the walk cannot be hidden by a plausible-looking group list."""
    _cap(tmp_path / (_BD_NAME + ".wacz"), "https://auth.example.com/x")
    _cap(tmp_path / "123.wacz", "https://vod.example.org/watch/1")
    _cap(tmp_path / "plain.wacz", pages=False)
    rc, h = _hosts(tmp_path)
    assert h["examined"] == 3, h
    assert h["by_method"] == {"filename": 1, "archive": 1, "unknown": 1}, h
    assert sum(h["by_method"].values()) == h["examined"], h
    assert sum(g["captures"] for g in h["groups"]) == h["examined"], (
        "the groups do not account for every file examined: %r" % h)
    assert h["denominator"], "the mode states no denominator"
    assert h["hosts"] == 2, (
        "`hosts` must count RESOLVED hosts only -- an unresolved stem is not a "
        "host: %r" % h)


def test_HOSTS_is_included_in_ALL_and_states_its_parser(tmp_path):
    _cap(tmp_path / (_BD_NAME + ".wacz"), "https://auth.example.com/x")
    r = _run("--root", str(tmp_path), "--all", "--json")
    out = json.loads(r.stdout)
    assert "hosts" in out["modes"], sorted(out["modes"])
    assert "_parse_capture_host" in out["modes"]["hosts"]["filename_parser"], (
        "the mode does not name the filename parser it used: %r"
        % out["modes"]["hosts"].get("filename_parser"))


def test_HOSTS_says_so_when_the_FILENAME_PARSER_is_unavailable(tmp_path):
    """The silent-degradation case, proven in BOTH directions.

    The parser is imported from `bulk_downloader.dom_analyzer` rather than
    copied, because two copies of a rule is how they drift. The cost is that a
    rename or a relocated tool makes tier 1 vanish -- at which point every
    BD-named capture falls through to an archive read that a REDACTED capture
    cannot answer, and the corpus reads as `unknown` with nothing saying why.
    """
    mod = _load_tool_module()
    p = _cap(tmp_path / (_BD_NAME + ".wacz"), "https://auth.example.com/x")

    live = mod.mode_hosts([p])
    assert "_parse_capture_host" in live["filename_parser"], live
    assert live["by_method"].get("filename") == 1, (
        "the real parser did not resolve a BD capture name, so the negative "
        "half of this test would pass for the wrong reason: %r" % live)

    mod._PARSER_CACHE = (None, "UNAVAILABLE: forced by test")
    dead = mod.mode_hosts([p])
    assert dead["filename_parser"].startswith("UNAVAILABLE"), (
        "tier 1 was gone and the report did not say so: %r"
        % dead["filename_parser"])
    assert dead["by_method"].get("filename", 0) == 0, (
        "a method the tool could not run was still reported: %r" % dead)


def test_HOSTS_never_writes_to_the_corpus(tmp_path):
    """Read-only, asserted rather than claimed -- and tier 2 is the mode that
    opens archives, so it is the one with something to prove."""
    import hashlib
    p = _cap(tmp_path / "123.wacz", "https://vod.example.org/watch/1")
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    listing = sorted(x.name for x in tmp_path.iterdir())
    r = _run("--root", str(tmp_path), "--hosts")
    # Assert the run HAPPENED first: a tool that refused the flag also writes
    # nothing, so without this the two assertions below pass over a run that
    # never examined anything -- section 0, in a read-only guard.
    assert r.returncode == 0, "the run did not happen: rc=%d %s" % (
        r.returncode, r.stderr[-300:])
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before, (
        "the tool rewrote a capture while grouping it")
    assert sorted(x.name for x in tmp_path.iterdir()) == listing
