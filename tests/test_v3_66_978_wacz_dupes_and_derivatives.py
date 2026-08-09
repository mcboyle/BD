"""bd-wacz-corpus --dupes: hash dedup, and the no-op redaction it exposes.

@978. MEASURED against the real 1251-file corpus on the box (4.04 GB):

    size-collision groups   429, covering 1000 of 1251 files
    reclaimable if identical 1.73 GB  -- 43% of the corpus
    groups >1MB             420, accounting for 1.71 GB

Size collision is a CANDIDATE, not proof, so this hashes to settle it -- but
only within colliding groups. Hashing all 4.04 GB to answer a question that a
size histogram narrows to 1.71 GB is work nobody needs to do.

TWO CATEGORIES, AND CONFLATING THEM WOULD BE THE DEFECT. Identical copies of the
same artifact are a disk question. But a `.redacted` or `.scrubbed` file that is
byte-identical to its SOURCE is not a duplicate -- it means the scrubber
produced its input unchanged, and @971 established that path could pass binary
members through untouched. Reporting that as "reclaimable" would invite deleting
the evidence of a redaction that never happened.

The real corpus also carries shapes the first version of this tool could not
parse, and the tests use them rather than invented ones:
  * three derivative markers -- .redacted (601), .scrubbed (176),
    .redacted.scrubbed (46) -- not one;
  * copy suffixes in two forms, `(N)` and `(dupN)`, with and without a leading
    space, on 216 files.
"""

import hashlib
import json
import pathlib
import subprocess
import sys
import zipfile

REPO = pathlib.Path(__file__).resolve().parent.parent
TOOL = REPO / "toolchain" / "bin" / "bd-wacz-corpus"


def _w(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("archive/data.warc", payload)
        z.writestr("datapackage.json", b"{}")
    return path


def _json(root, *args):
    r = subprocess.run([sys.executable, str(TOOL), "--root", str(root), *args, "--json"],
                       capture_output=True, text=True, timeout=300)
    assert r.stdout.strip(), "no stdout: rc=%d %s" % (r.returncode, r.stderr[-400:])
    return r.returncode, json.loads(r.stdout)


def test_identical_copies_are_grouped_and_the_saving_stated(tmp_path):
    _w(tmp_path / "bang170.wacz", b"A" * 5000)
    _w(tmp_path / "bang170 (2).wacz", b"A" * 5000)          # real shape: " (2)"
    _w(tmp_path / "other.wacz", b"B" * 5000)                # same SIZE, different bytes
    rc, out = _json(tmp_path, "--dupes")
    d = out["modes"]["dupes"]
    assert d["duplicate_groups"] == 1, (
        "expected exactly one identical group; 'other' has the same size but "
        "different content and must not be folded in: %r" % d)
    assert d["reclaimable_bytes"] > 0
    assert d["hashed"] < d["examined"] or d["examined"] == d["hashed"], d
    grp = d["groups"][0]
    assert len(grp["files"]) == 2 and "of" not in str(grp.get("reclaimable", ""))


def test_same_size_different_bytes_is_NOT_a_duplicate(tmp_path):
    """The whole reason for stage two. Size collision is a candidate only."""
    _w(tmp_path / "a.wacz", b"A" * 9000)
    _w(tmp_path / "b.wacz", b"B" * 9000)
    rc, out = _json(tmp_path, "--dupes")
    d = out["modes"]["dupes"]
    assert d["duplicate_groups"] == 0, (
        "two same-size files with different content were called duplicates -- "
        "the hash stage is not running: %r" % d)
    assert d["hashed"] == 2, "the collision group was not hashed at all: %r" % d


def test_a_derivative_identical_to_its_SOURCE_is_a_FINDING_not_a_saving(tmp_path):
    """@971's defect class, mechanised.

    A .redacted twin byte-identical to its raw source means the scrubber
    returned its input. That is evidence, not disk to reclaim.
    """
    _w(tmp_path / "cap.wacz", b"SECRET" * 900)
    _w(tmp_path / "cap.redacted.wacz", b"SECRET" * 900)
    rc, out = _json(tmp_path, "--dupes")
    d = out["modes"]["dupes"]
    assert d["noop_derivatives"] == 1, (
        "a .redacted file identical to its source was not flagged as a no-op "
        "redaction: %r" % d)
    assert d["duplicate_groups"] == 0, (
        "the no-op redaction was ALSO counted as reclaimable disk -- deleting "
        "it would destroy the evidence that redaction did nothing: %r" % d)
    assert rc == 1, "a no-op redaction is a finding; exit was %d" % rc


def test_a_derivative_that_DIFFERS_is_silent(tmp_path):
    """Over-sensitivity guard: redaction that actually changed the bytes is the
    normal case and must not be reported."""
    _w(tmp_path / "cap.wacz", b"SECRET" * 900)
    _w(tmp_path / "cap.redacted.wacz", b"XXXXXX" * 900)
    rc, out = _json(tmp_path, "--dupes")
    assert out["modes"]["dupes"]["noop_derivatives"] == 0, out["modes"]["dupes"]


def test_ALL_THREE_derivative_markers_are_recognised(tmp_path):
    """.scrubbed and .redacted.scrubbed exist in the real corpus (176 and 46
    files). A classifier that knows only .redacted counts them as raw."""
    _w(tmp_path / "c.wacz", b"Z" * 4000)
    _w(tmp_path / "c.redacted.wacz", b"Z" * 4000)
    _w(tmp_path / "c.scrubbed.wacz", b"Z" * 4000)
    _w(tmp_path / "c.redacted.scrubbed.wacz", b"Z" * 4000)
    rc, out = _json(tmp_path, "--dupes")
    d = out["modes"]["dupes"]
    assert d["noop_derivatives"] == 3, (
        "expected all three derivatives to be flagged identical to their "
        "source; .scrubbed and .redacted.scrubbed are being read as raw "
        "captures: %r" % d)


def test_copy_suffixes_in_BOTH_real_forms_resolve_to_the_same_base(tmp_path):
    """216 real files carry `(N)` or `(dupN)`, with and without a space."""
    _w(tmp_path / "x.redacted.wacz", b"Q" * 3000)
    _w(tmp_path / "x.redacted (2).wacz", b"Q" * 3000)
    _w(tmp_path / "x.redacted(dup1).wacz", b"Q" * 3000)
    rc, out = _json(tmp_path, "--dupes")
    d = out["modes"]["dupes"]
    assert d["duplicate_groups"] == 1 and len(d["groups"][0]["files"]) == 3, (
        "the copy-suffixed twins did not resolve to one base: %r" % d)
    assert d["noop_derivatives"] == 0, (
        "there is no raw source here, so nothing can be a no-op derivative: %r" % d)


def test_it_only_hashes_files_in_a_COLLISION_group(tmp_path):
    """Stage one exists to avoid reading 4 GB. A tool that hashes everything
    would pass every test above and be unusable on the real corpus."""
    for i, n in enumerate(("u1.wacz", "u2.wacz", "u3.wacz")):
        _w(tmp_path / n, b"U" * (1000 + i * 100))     # all distinct sizes
    _w(tmp_path / "p1.wacz", b"P" * 7000)
    _w(tmp_path / "p2.wacz", b"P" * 7000)
    rc, out = _json(tmp_path, "--dupes")
    d = out["modes"]["dupes"]
    assert d["examined"] == 5, d
    assert d["hashed"] == 2, (
        "hashed %d of 5 files; only the 2 in a size-collision group should be "
        "read, or the tool reads the whole corpus to answer this" % d["hashed"])


def test_it_deletes_nothing(tmp_path):
    """Report-only, asserted. A dedup that mutates its subject on a report run
    is the one bug nobody recovers from."""
    a = _w(tmp_path / "k.wacz", b"K" * 2000)
    b = _w(tmp_path / "k (2).wacz", b"K" * 2000)
    before = sorted(p.name for p in tmp_path.iterdir())
    _json(tmp_path, "--dupes")
    assert sorted(p.name for p in tmp_path.iterdir()) == before, "files changed"
    assert a.exists() and b.exists()
