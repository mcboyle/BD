""""history.file_size" is a stat taken before the file's last write.

THE DEFECT. Every completion path captures the download's byte count, then calls
`_embed_metadata_if_mp4(...)`, which rewrites the file in place through
mutagen.mp4, and only THEN hands the stale pre-mutation number to
`_update_job(file_size=...)` and `db_log(..., file_size, ...)`. The file on disk
is larger than the number history records. MEASURED here, with mutagen 1.48.1
and the 1442-byte MP4 embedded below, using the same MetadataContext
`test_a_real_tag_write_moves_the_number` builds: 1442 -> 2675, delta +1233 for
tags alone (re-tagging the same file again is idempotent, delta 0). That figure
is reproducible from the fixture sitting in this file; figures measured against
samples that are not in this repository are deliberately not quoted, because a
number a reader cannot re-derive is inherited as authority and is wrong exactly
as often as nobody checks it.

IT IS WRONG BY THE PROJECT'S OWN DEFINITION OF THE COLUMN, not by opinion.
db.db_log's docstring and migrations.py migration 8 both state that `file_size`
IS an on-disk stat and that `bytes_fetched` is the column carrying the transfer
count. So the pre-tag number is the transfer count wearing the on-disk column's
name -- the exact conflation migration 8 was cut to end.

WHY IT IS WORTH FIXING WHEN NOTHING VISIBLY BREAKS TODAY. The under-report is a
small fraction of a realistic download, which moves no displayed figure, and the
one exact-equality consumer -- `library_final.list_size_drift`, surfaced at
POST /api/library/audit -- is blind for an UNRELATED reason: it resolves
`history.filename` (a BASENAME in production) with a bare `Path(fn).exists()`
and never joins the download_dir it accepts, so it skips every production row.
That blindness is a separate item and is NOT fixed here. The hazard this cut
removes is the COUPLING: the moment someone repairs that join, every tagged MP4
becomes a false "truncated or altered download" at tolerance_bytes=0 -- a mass
false positive, which is CLAUDE.md section 0's "gate that cries wolf". Fixing
the producer first means the repair can land without arming an alarm. The same
trap arms if provenance.sha256 is ever backfilled, since runner.py records
provenance from this same `file_size`.

WHAT THIS CUT MUST NOT DO. It must not reassign the variable passed to
`bytes_fetched=`. Several of the paired sites pass the SAME name to both
columns, so a fix that rebinds it in place would fold the tag bytes into the
transfer count and restore the defect migration 8 closed. That is why the
structural gate below has two halves and the second one is GREEN TODAY: it is a
regression guard, not a RED.

AND A REBINDING IS NOT A RE-DERIVATION. An earlier draft of the gate below
accepted any name rebound between the tag write and the record. A clone that
wrote `file_size_on_disk = downloaded_size` at all seven callsites -- leaving
the defect entirely unfixed, every row still the pre-tag number -- passed that
gate. The denominator (an assignment exists in the interval) did not contain the
subject (the recorded value is derived from the file AFTER the write). The gate
now requires the intervening binding's value to contain a Call: something that
goes and looks. `test_the_gate_tells_an_alias_from_a_re_derivation` pins both
directions of that predicate so it cannot silently loosen again.

KNOWN CONSEQUENCE, PINNED RATHER THAN ASSERTED AWAY. Recording the post-tag size
means two copies of the SAME video tagged for two different sites no longer
share a byte size, so `batch_ops.bulk_dedup_scan` pass 1 -- which groups on
EXACT `history.file_size` -- stops grouping them. That is a missed group, a
silent false NEGATIVE: `recoverable_gb` under-reports and nobody is told. That
consumer had zero tests before this file, so nothing in the repository could
have observed the loss. `test_tagged_copies_no_longer_group_in_dedup_pass_1`
below makes it observable. Moving pass 1 onto a content key is a separate item.

RED-first: the structural gate and every helper assertion below fail on
pristine source. The denominator-sanity tests, the gate's own predicate
self-check, the bytes_fetched regression guard and the dedup pin pass today, by
design, and prove nothing about the defect on their own.
"""
from __future__ import annotations

import ast
import base64
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TINY_MP4_B64 = (
    "AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAAIZnJlZQAAAm1tZGF0AAACUwYF"
    "//9P3EXpvebZSLeWLNgg2SPu73gyNjQgLSBjb3JlIDE2NCByMzEwOCAzMWUxOWY5IC0gSC4y"
    "NjQvTVBFRy00IEFWQyBjb2RlYyAtIENvcHlsZWZ0IDIwMDMtMjAyMyAtIGh0dHA6Ly93d3cu"
    "dmlkZW9sYW4ub3JnL3gyNjQuaHRtbCAtIG9wdGlvbnM6IGNhYmFjPTAgcmVmPTEgZGVibG9j"
    "az0wOjA6MCBhbmFseXNlPTA6MCBtZT1kaWEgc3VibWU9MCBwc3k9MSBwc3lfcmQ9MS4wMDow"
    "LjAwIG1peGVkX3JlZj0wIG1lX3JhbmdlPTE2IGNocm9tYV9tZT0xIHRyZWxsaXM9MCA4eDhk"
    "Y3Q9MCBjcW09MCBkZWFkem9uZT0yMSwxMSBmYXN0X3Bza2lwPTEgY2hyb21hX3FwX29mZnNl"
    "dD0wIHRocmVhZHM9MSBsb29rYWhlYWRfdGhyZWFkcz0xIHNsaWNlZF90aHJlYWRzPTAgbnI9"
    "MCBkZWNpbWF0ZT0xIGludGVybGFjZWQ9MCBibHVyYXlfY29tcGF0PTAgY29uc3RyYWluZWRf"
    "aW50cmE9MCBiZnJhbWVzPTAgd2VpZ2h0cD0wIGtleWludD0yNTAga2V5aW50X21pbj0xIHNj"
    "ZW5lY3V0PTAgaW50cmFfcmVmcmVzaD0wIHJjPWNyZiBtYnRyZWU9MCBjcmY9MjMuMCBxY29t"
    "cD0wLjYwIHFwbWluPTAgcXBtYXg9NjkgcXBzdGVwPTQgaXBfcmF0aW89MS40MCBhcT0wAIAA"
    "AAAKZYiEOiYoAAkC4AAAAw1tb292AAAAbG12aGQAAAAAAAAAAAAAAAAAAAPoAAAD6AABAAAB"
    "AAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAACAAACN3RyYWsAAABcdGtoZAAAAAMAAAAAAAAAAAAAAAEA"
    "AAAAAAAD6AAAAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAA"
    "AEAAAAAAEAAAABAAAAAAACRlZHRzAAAAHGVsc3QAAAAAAAAAAQAAA+gAAAAAAAEAAAAAAa9t"
    "ZGlhAAAAIG1kaGQAAAAAAAAAAAAAAAAAAEAAAABAAFXEAAAAAAAtaGRscgAAAAAAAAAAdmlk"
    "ZQAAAAAAAAAAAAAAAFZpZGVvSGFuZGxlcgAAAAFabWluZgAAABR2bWhkAAAAAQAAAAAAAAAA"
    "AAAAJGRpbmYAAAAcZHJlZgAAAAAAAAABAAAADHVybCAAAAABAAABGnN0YmwAAAC2c3RzZAAA"
    "AAAAAAABAAAApmF2YzEAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAAAEAAQAEgAAABIAAAAAAAA"
    "AAEVTGF2YzYwLjMxLjEwMiBsaWJ4MjY0AAAAAAAAAAAAAAAY//8AAAAsYXZjQwFCwAr/4QAV"
    "Z0LACtp7ARAAAAMAEAAAAwAg8SJqAQAEaM4PyAAAABBwYXNwAAAAAQAAAAEAAAAUYnRydAAA"
    "AAAAABMoAAATKAAAABhzdHRzAAAAAAAAAAEAAAABAABAAAAAABxzdHNjAAAAAAAAAAEAAAAB"
    "AAAAAQAAAAEAAAAUc3RzegAAAAAAAAJlAAAAAQAAABRzdGNvAAAAAAAAAAEAAAAwAAAAYnVk"
    "dGEAAABabWV0YQAAAAAAAAAhaGRscgAAAAAAAAAAbWRpcmFwcGwAAAAAAAAAAAAAAAAtaWxz"
    "dAAAACWpdG9vAAAAHWRhdGEAAAABAAAAAExhdmY2MC4xNi4xMDA="
)


def _tiny_mp4(dest: Path) -> Path:
    """A real, taggable MP4 -- 1442 bytes, embedded, no ffmpeg at test time.

    Generated once with `ffmpeg -f lavfi -i color=c=black:s=16x16:d=0.1:r=1
    -c:v libx264 -preset ultrafast -pix_fmt yuv420p`. It is embedded rather
    than generated so this test does not acquire a system-package dependency:
    a test that skips when ffmpeg is missing would report N/A on the one box
    that matters, and N/A is not PASS.
    """
    dest.write_bytes(base64.b64decode(_TINY_MP4_B64))
    return dest


# -- the denominator ---------------------------------------------------------

def _tracked_sources() -> list[str]:
    """git ls-files, not rglob: ephemeral agent worktrees live under the
    repository root and would multiply every count below."""
    return [f for f in subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z", "bulk_downloader/*.py"],
        capture_output=True, text=True).stdout.split("\0") if f]


def _owner_map(tree: ast.AST) -> dict:
    """(lineno, col) -> the FunctionDef that owns that node.

    Deepest definition first so a closure claims its own nodes before the
    enclosing function can; ast has no parent pointers and a line-range
    comparison gets nested definitions wrong.
    """
    o: dict = {}
    defs = [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for fn in sorted(defs, key=lambda f: -f.lineno):
        for ch in ast.walk(fn):
            if hasattr(ch, "lineno"):
                o.setdefault((ch.lineno, ch.col_offset), fn)
    return o


def _bindings(fn: ast.AST) -> list[tuple]:
    """(name, lineno, value_node) for every rebinding of a local name in `fn`.

    The VALUE is carried, not just the line. Without it the only question a
    caller can ask is "was this name assigned here", and that question cannot
    tell `x = downloaded_size` from `x = stat(path)`. Parameters bind with a
    value of None -- they are bound, and they derive nothing.
    """
    b: list[tuple] = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    b.append((t.id, n.lineno, n.value))
        elif isinstance(n, (ast.AugAssign, ast.AnnAssign)) and isinstance(n.target, ast.Name):
            b.append((n.target.id, n.lineno, getattr(n, "value", None)))
    for a in fn.args.args + fn.args.kwonlyargs:
        b.append((a.arg, fn.lineno, None))
    return b


def _rebound_between(name, lo, hi, bindings) -> bool:
    """Was `name` assigned at all between the tag write and the record?

    This is the predicate the bytes_fetched guard wants: ANY rebinding of the
    name the transfer count is read from is the thing being forbidden there, so
    it must NOT be tightened the way the gate below is.
    """
    return any(n == name and lo < ln < hi for n, ln, _ in bindings)


def _rederived_between(name, lo, hi, bindings) -> bool:
    """Was `name` bound between the tag write and the record to something that
    actually goes and LOOKS at the file?

    A rebinding is not a re-derivation. `x = downloaded_size` is an alias of a
    number that still predates the write, and a gate that accepts it certifies
    nothing -- MEASURED: a clone doing exactly that at all seven callsites, with
    the defect fully intact, passed the untightened gate. Requiring a Call in
    the bound value is the weakest predicate that excludes the alias while still
    admitting any hand-rolled implementation (`os.path.getsize(p)`,
    `p.stat().st_size`, a helper call under any name).
    """
    hits = [v for n, ln, v in bindings if n == name and lo < ln < hi]
    return any(v is not None
               and any(isinstance(x, ast.Call) for x in ast.walk(v))
               for v in hits)


def _root_name(e: ast.AST):
    """The leftmost Name of an attribute chain: dl_result.bytes_written ->
    'dl_result'. Returns None for a literal, so `bytes_fetched=None` and
    `bytes_fetched=0` are correctly outside the rebinding guard's subject."""
    while isinstance(e, ast.Attribute):
        e = e.value
    return e.id if isinstance(e, ast.Name) else None


def _paired_sites() -> list[dict]:
    """Every (tag write, done-record) pair, with the expressions each records.

    PAIRING RULE: a done-record D is paired with the LAST tag write that
    precedes it inside the same function. Pairing D with *every* preceding
    embed was tried first and is wrong in both directions -- in
    `_try_library_extractor` it pairs the HLS branch's embed with the
    direct-URL branch's db_log, two arms that cannot both run, which both
    masks a real violation (one arm reports clean for the other) and invents
    one across a `return`. Nearest-preceding is exact for every site here.

    INSTRUMENT: ast.parse + ast.walk. PREDICATES: Call with func.attr ==
    '_embed_metadata_if_mp4' for the tag write; db_log with args[3] == 'done'
    and _update_job with args[1] == 'done' for the record. Not grep: 'done'
    and 'file_size' are both far too common as text, and the property being
    asserted is an ORDERING between two calls, which text cannot express.
    """
    sites: list[dict] = []
    for rel in _tracked_sources():
        try:
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        own = _owner_map(tree)
        embeds = sorted(
            [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "_embed_metadata_if_mp4"],
            key=lambda n: n.lineno)
        if not embeds:
            continue
        fns = {id(own[(e.lineno, e.col_offset)]): own[(e.lineno, e.col_offset)]
               for e in embeds if (e.lineno, e.col_offset) in own}
        for fn in fns.values():
            fe = sorted([e for e in embeds
                         if own.get((e.lineno, e.col_offset)) is fn],
                        key=lambda n: n.lineno)
            bl = _bindings(fn)
            for D in sorted([n for n in ast.walk(fn) if isinstance(n, ast.Call)],
                            key=lambda n: n.lineno):
                nm = D.func.attr if isinstance(D.func, ast.Attribute) \
                    else getattr(D.func, "id", None)
                if nm == "db_log" and len(D.args) >= 6 \
                        and isinstance(D.args[3], ast.Constant) and D.args[3].value == "done":
                    fs = D.args[5]
                    bf = {k.arg: k.value for k in D.keywords}.get("bytes_fetched")
                elif nm == "_update_job":
                    kw = {k.arg: k.value for k in D.keywords}
                    if "file_size" not in kw:
                        continue
                    if not (len(D.args) > 1 and isinstance(D.args[1], ast.Constant)
                            and D.args[1].value == "done"):
                        continue
                    fs, bf = kw["file_size"], None
                else:
                    continue
                prior = [e for e in fe if e.lineno < D.lineno]
                if not prior:
                    continue
                C = prior[-1]
                sites.append({
                    "rel": rel, "call": nm, "record_line": D.lineno,
                    "embed_line": C.lineno,
                    "file_size": fs, "bytes_fetched": bf,
                    "bindings": bl,
                })
    return sites


def test_the_scan_finds_the_tag_writes():
    """A collapsed denominator makes every assertion below vacuous.

    Measured 7 at the time of writing (6 in runner_extractors.py, 1 in
    runner_transport.py). A floor, not the exact number: a new tag-write path
    must fail the GATE by not re-stating, not fail this test by moving a count.
    """
    n = sum(len([x for x in ast.walk(ast.parse(
        (ROOT / rel).read_text(encoding="utf-8", errors="replace")))
        if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)
        and x.func.attr == "_embed_metadata_if_mp4"])
        for rel in _tracked_sources())
    assert n >= 5, (
        f"the AST scan found only {n} _embed_metadata_if_mp4 call sites in "
        f"tracked application source. It cannot see its subject, so the gate "
        f"below would report clean truthfully and uselessly."
    )


def test_the_scan_pairs_every_tag_write_with_a_done_record():
    """The gate asserts over PAIRS, so the pair count is its real denominator.

    Measured 14: 7 tag writes, each followed by one
    _update_job(status='done', file_size=...) and one db_log(..., 'done', ...).
    """
    sites = _paired_sites()
    assert len(sites) >= 10, (
        f"only {len(sites)} (tag write -> done record) pairs found; the gate's "
        f"denominator has collapsed and it can no longer see the defect."
    )


_ALIAS_SRC = '''
def f(self, downloaded_size, output_path):
    self._embed_metadata_if_mp4(output_path)
    recorded = downloaded_size
    self._update_job(url, "done", "m", filename=fn, file_size=recorded)
'''

_REDERIVE_SRC = '''
def f(self, downloaded_size, output_path):
    self._embed_metadata_if_mp4(output_path)
    recorded = os.path.getsize(output_path)
    self._update_job(url, "done", "m", filename=fn, file_size=recorded)
'''


def _predicate_on(source: str) -> tuple[bool, bool]:
    """(rebound?, rederived?) for `recorded` between the embed and the record."""
    fn = ast.parse(source).body[0]
    b = _bindings(fn)
    lo = next(n.lineno for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr == "_embed_metadata_if_mp4")
    hi = next(n.lineno for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr == "_update_job")
    return (_rebound_between("recorded", lo, hi, b),
            _rederived_between("recorded", lo, hi, b))


def test_the_gate_tells_an_alias_from_a_re_derivation():
    """SELF-CHECK on the gate's predicate. Green today; it tests this module.

    It exists because the shipped gate was once blind in exactly this way, and
    the blindness was invisible from the gate's own output: a clone that
    renamed the stale value at all seven callsites passed every test in this
    file. Both directions are pinned here, so loosening the predicate back to
    "was it assigned" fails immediately and locally instead of silently
    certifying a no-op fix.
    """
    alias_rebound, alias_rederived = _predicate_on(_ALIAS_SRC)
    assert alias_rebound, "the loose predicate must still see a plain rebinding"
    assert not alias_rederived, (
        "`recorded = downloaded_size` is an ALIAS of a number captured before "
        "the tag write. The gate must not accept it as a re-stat -- accepting "
        "it is what let a no-op fix pass all ten tests."
    )
    real_rebound, real_rederived = _predicate_on(_REDERIVE_SRC)
    assert real_rebound and real_rederived, (
        "`recorded = os.path.getsize(output_path)` IS a re-derivation. A gate "
        "that rejected it would cry wolf on a correct hand-rolled fix and get "
        "switched off."
    )


def test_every_done_record_after_a_tag_write_restats_the_file():
    """THE GATE. RED on pristine: 14 of 14 pairs violate.

    The property is DERIVED, not name-matched: the expression handed to
    file_size must be a local name bound, between the tag write and the record,
    to something that CALLS out to look at the file. It says nothing about what
    the helper is called or how it is spelled, so renaming or inlining the
    re-stat keeps this green -- a gate pinned to a helper's name would cry wolf
    on the next refactor and get switched off, which is section 0's inverse
    rule. It says nothing about the helper's shape either, only that the value
    is derived rather than aliased.
    """
    bad = []
    for s in _paired_sites():
        fs = s["file_size"]
        ok = isinstance(fs, ast.Name) and _rederived_between(
            fs.id, s["embed_line"], s["record_line"], s["bindings"])
        if not ok:
            bad.append(f"{s['rel']}:{s['record_line']} {s['call']}("
                       f"file_size={ast.unparse(fs)}) -- tag write at line "
                       f"{s['embed_line']} rewrote the file after that value "
                       f"was computed")
    assert not bad, (
        "these records write a file_size that predates the tag write that "
        "changed the file:\n  " + "\n  ".join(bad) +
        "\nRe-stat the file after embedding and record THAT number. "
        "db.db_log's docstring and migrations.py migration 8 both define "
        "file_size as an on-disk stat; bytes_fetched is the transfer count. "
        "Binding a second name to the same stale value is not a fix."
    )


def test_the_restat_does_not_swallow_the_transfer_count():
    """REGRESSION GUARD -- GREEN TODAY, and therefore not a RED.

    Several paired sites pass the SAME name to file_size and bytes_fetched. The
    cheapest possible fix is to rebind that name after the embed, which turns
    every one of those rows into a claim that the tag bytes crossed the wire
    and undoes migration 8. Measured: 6 name-rooted bytes_fetched sites, 0
    violations before this cut; the rebind-in-place fix produces 4.

    NOTE the predicate here is the LOOSE one on purpose. Any rebinding of the
    name bytes_fetched reads is forbidden, whether it aliases or re-derives --
    tightening this one to "derived from a Call" would let the exact wrong fix
    it exists to catch straight through.
    """
    bad = []
    for s in _paired_sites():
        bf = s["bytes_fetched"]
        if bf is None:
            continue
        root = _root_name(bf)
        if root is None:
            continue
        if _rebound_between(root, s["embed_line"], s["record_line"], s["bindings"]):
            bad.append(f"{s['rel']}:{s['record_line']} bytes_fetched="
                       f"{ast.unparse(bf)} -- '{root}' is rebound after the "
                       f"tag write at line {s['embed_line']}")
    assert not bad, (
        "these sites fold the metadata atoms into the TRANSFER count:\n  "
        + "\n  ".join(bad) +
        "\nbytes_fetched is what crossed the wire (#63, migration 8). Bind the "
        "re-stat to a NEW name; do not reassign the one bytes_fetched reads."
    )


# -- the producer's own contract ---------------------------------------------

def _runner():
    from bulk_downloader.runner import SiteRunner
    r = SiteRunner.__new__(SiteRunner)
    r.config = {"embed_metadata": True, "name": "test"}
    return r


def test_the_runner_can_restat_a_file_it_just_tagged():
    """RED: SiteRunner has no such method on pristine source."""
    from bulk_downloader.runner import SiteRunner
    assert hasattr(SiteRunner, "_size_on_disk_after_tagging"), (
        "SiteRunner cannot re-derive a file's size after tagging it, so every "
        "completion path has nothing to record but the stale pre-tag number."
    )


def test_it_reports_the_size_the_file_actually_has(tmp_path):
    """RED. The ordering property, stated without mutagen: a number captured
    before a write must not survive the write."""
    p = tmp_path / "v.mp4"
    p.write_bytes(b"x" * 4321)
    captured_before_a_later_write = os.path.getsize(p)
    p.write_bytes(b"x" * 4321 + b"y" * 1233)   # what tag_mp4 does, in miniature
    got = _runner()._size_on_disk_after_tagging(str(p), captured_before_a_later_write)
    assert got == 5554, f"recorded {got}, but the file is 5554 bytes"
    assert got != captured_before_a_later_write


def test_a_real_tag_write_moves_the_number(tmp_path):
    """RED, and the one assertion that uses the real tagger.

    mutagen is a declared runtime requirement (requirements.txt), so its
    absence is an environment fault to surface, not a reason to go quiet.
    """
    from bulk_downloader import mp4_metadata as M
    assert M.is_available(), (
        "mutagen is not importable, so this test cannot observe the write it "
        "exists to measure. requirements.txt pins mutagen>=1.47,<2.0 -- fix "
        "the environment; an unobservable check must not report PASS."
    )
    p = _tiny_mp4(tmp_path / "v.mp4")
    pre_tag = os.path.getsize(p)
    assert M.tag_mp4(str(p), M.MetadataContext(
        title="Scene Title 1080p", artist="A Performer", album="SiteX",
        date="2026-01-02", comment="https://example.test/scene/1",
        encoder="BulkDownloader v3.43.64")) is True
    on_disk = os.path.getsize(p)
    assert on_disk > pre_tag, (
        f"tag_mp4 did not change the file size ({pre_tag} -> {on_disk}); the "
        f"fixture or the tagger changed and this test no longer observes the "
        f"defect it was written for."
    )
    got = _runner()._size_on_disk_after_tagging(str(p), pre_tag)
    assert got == on_disk, (
        f"recorded {got}; the tagged file is {on_disk} bytes "
        f"(pre-tag was {pre_tag}, delta +{on_disk - pre_tag})"
    )


def test_a_vanished_file_keeps_the_recorded_size_rather_than_zero(tmp_path):
    """RED, and the CRY-WOLF guard on the producer.

    If the file was quarantined or moved between the tag and the record, the
    honest answer is the number we already had -- not 0. Recording 0 hands
    library_final.list_size_drift a fabricated "truncated download" for a job
    that was fine, and a gate that cries wolf gets switched off.
    """
    got = _runner()._size_on_disk_after_tagging(str(tmp_path / "gone.mp4"), 4321)
    assert got == 4321, f"recorded {got} for a file that is not there"
    assert _runner()._size_on_disk_after_tagging(None, 4321) == 4321


def test_a_zero_byte_stat_does_not_overwrite_a_real_size(tmp_path):
    """RED, cry-wolf guard. A 0-byte stat on a job that transferred bytes is a
    stat we should not believe over the count we already have."""
    p = tmp_path / "v.mp4"
    p.write_bytes(b"")
    assert _runner()._size_on_disk_after_tagging(str(p), 4321) == 4321
    # and a genuinely empty job still records 0, not a fabricated number
    assert _runner()._size_on_disk_after_tagging(str(p), 0) == 0


def test_a_shrunken_file_reports_its_real_size(tmp_path):
    """RED. Blocks the tempting `max(actual, fallback)` shortcut.

    Sizes do not only grow: re-tagging can REMOVE atoms. Clamping to the
    larger of the two would make this producer structurally unable to report a
    genuine truncation -- a gate that cannot see its subject.
    """
    p = tmp_path / "v.mp4"
    p.write_bytes(b"x" * 100)
    got = _runner()._size_on_disk_after_tagging(str(p), 4321)
    assert got == 100, f"recorded {got}; the file really is 100 bytes"


# -- the consumer this cut knowingly degrades --------------------------------

def _dedup_collisions(tmp_root: Path, rows) -> dict:
    """Run batch_ops.bulk_dedup_scan against an isolated history DB.

    `rows` is [(site_id, filename, file_size)]. min_file_size_mb=0 because the
    50 MB production default would exclude a test fixture -- a denominator that
    cannot contain its subject reports zero groups truthfully and uselessly.
    """
    import bulk_downloader.db as db
    from bulk_downloader import batch_ops
    saved = db.DB_PATH
    dbf = tempfile.mkdtemp(dir=str(tmp_root)) + "/queue.db"
    db.DB_PATH = dbf
    try:
        db.db_init()
        for sid, fn, sz in rows:
            db.db_log(site_id=sid, site_name=sid.upper(),
                      url=f"http://example.test/{sid}", status="done",
                      filename=fn, file_size=sz)
        return batch_ops.bulk_dedup_scan(min_file_size_mb=0)
    finally:
        db.DB_PATH = saved


def test_tagged_copies_no_longer_group_in_dedup_pass_1(tmp_path):
    """PINS A KNOWN, ACCEPTED CONSEQUENCE of recording the post-tag size.

    GREEN TODAY and green after the cut -- it is not a RED. It is here because
    `batch_ops.bulk_dedup_scan` had ZERO tests before this file (measured:
    `grep -rl bulk_dedup_scan tests/` returned nothing), so the regression this
    cut causes in it would otherwise be unobservable by anything in the
    repository.

    THE REGRESSION, stated plainly rather than argued away: pass 1 groups
    history rows on EXACT `file_size`. Two copies of the same video downloaded
    from two sites transferred the same number of bytes, so before this cut
    they grouped. After it they carry their own post-tag sizes, which differ by
    the difference between the two sites' metadata -- a delta that scales with
    the tags and the cover, NOT with the file, so it does not wash out on a
    large file. The result is a missed group: a silent false NEGATIVE in which
    `recoverable_gb` under-reports and the operator is never told. Pass 2's
    hash confirmation is unaffected. Moving pass 1 onto a content key is a
    separate item; when it lands, this test is what will have to change, and
    that is the point of writing it down.
    """
    from bulk_downloader import mp4_metadata as M
    assert M.is_available(), (
        "mutagen is not importable, so this test cannot produce the two "
        "differently-tagged copies it exists to compare. An unobservable "
        "check must not report PASS."
    )
    d = tmp_path / "dl"
    d.mkdir()
    a, b = _tiny_mp4(d / "a.mp4"), _tiny_mp4(d / "b.mp4")
    pre_tag = os.path.getsize(a)
    assert os.path.getsize(b) == pre_tag
    assert M.tag_mp4(str(a), M.MetadataContext(
        title="Scene One", artist="A Performer", album="SiteA",
        date="2026-01-02", comment="https://a.example.test/scene/1",
        encoder="BulkDownloader")) is True
    assert M.tag_mp4(str(b), M.MetadataContext(
        title="Scene One, the very same footage under a longer name",
        artist="A Performer", album="SiteBBBBBB", date="2026-01-02",
        comment="https://b.example.test/videos/2026/01/scene-one-1080p",
        encoder="BulkDownloader")) is True
    sa, sb = os.path.getsize(a), os.path.getsize(b)
    assert sa != sb, (
        f"both copies tagged to {sa} bytes, so this test can no longer "
        f"observe the divergence it pins; the tagger or the fixture changed."
    )

    # POSITIVE CONTROL -- the pre-cut recording. Identical numbers DO group,
    # so a zero below is a real behaviour change and not a broken harness.
    before = _dedup_collisions(tmp_path, [("a", str(a), pre_tag),
                                          ("b", str(b), pre_tag)])
    assert before.get("candidates_with_size_collisions") == 1, (
        f"pass 1 did not group two rows recorded at the same size, so this "
        f"test cannot see grouping at all: {before}"
    )

    # THE CONSEQUENCE -- what this cut now writes.
    after = _dedup_collisions(tmp_path, [("a", str(a), sa), ("b", str(b), sb)])
    assert after.get("total_files_scanned") == 2, (
        f"both rows must reach pass 1 for this to mean anything: {after}"
    )
    assert after.get("candidates_with_size_collisions") == 0, (
        f"two copies of one video, tagged for different sites, are {sa} and "
        f"{sb} bytes on disk. If pass 1 grouped them anyway, its key is no "
        f"longer the exact file_size this test was written against: {after}"
    )


BD_GATE_SCOPE = "repo-wide"
