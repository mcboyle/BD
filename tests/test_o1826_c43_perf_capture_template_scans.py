"""O1826 C43: capture/template lookups must not re-read the store per lookup.

M178 template_registry.load_templates re-read and re-parsed every template JSON on
every lookup (twice per lookup when html was given). M054
dom_analyzer._resolve_capture_any walked the capture roots twice per bare-basename
resolve. M056 drift_repair opened every capture once PER stale site in a sweep.
Counters below pin the I/O; the result assertions pin behavior unchanged.
"""

import json
import os
from pathlib import Path

from bulk_downloader import dom_analyzer as da
from bulk_downloader import drift_repair as dr
from bulk_downloader import selector_drift as sd
from bulk_downloader import template_registry as tr

BD_GATE_SCOPE = "module"

_OLD = 1_500_000_000  # a stamp well outside the racy window


def _write_template(d: Path, name: str, host: str, status="enabled", **extra):
    data = {"host": host, "status": status, "selectors": {"title": "h1.%s" % name}}
    data.update(extra)
    fp = d / ("%s.template.json" % name)
    fp.write_text(json.dumps(data), encoding="utf-8")
    os.utime(fp, (_OLD, _OLD))
    return fp


def _template_dir(tmp_path):
    d = tmp_path / "tpl"
    d.mkdir()
    _write_template(d, "a", "a.example")
    _write_template(d, "a2", "a.example", selectors={"title": "h2.alt"})
    _write_template(d, "b", "b.example")
    _write_template(d, "off", "c.example", status="disabled")
    (d / "bad.template.json").write_text("{not json", encoding="utf-8")
    return d


def _count_template_reads(monkeypatch):
    reads = []
    real = Path.read_text

    def counting(self, *a, **k):
        if self.name.endswith(".template.json"):
            reads.append(self.name)
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", counting)
    return reads


def _trust_stamps(monkeypatch):
    # Files written by the test are stamped "now"; the racy window would (rightly)
    # re-read them. Widen trust so the counter measures the cache, not the window.
    monkeypatch.setattr(tr, "_RACY_NS", -(10**15), raising=False)


def test_template_lookups_parse_each_template_once(tmp_path, monkeypatch):
    d = _template_dir(tmp_path)
    _trust_stamps(monkeypatch)
    reads = _count_template_reads(monkeypatch)
    for _ in range(5):
        t = tr.find_template_for_url("https://a.example/x", [d])
        assert t["host"] == "a.example"
        assert tr.find_template_for_url(
            "https://a.example/x", [d], html="<h2 class='alt'>x</h2>"
        )["selectors"] == {"title": "h2.alt"}
    assert sorted(reads) == sorted(
        [
            "a.template.json",
            "a2.template.json",
            "b.template.json",
            "off.template.json",
            "bad.template.json",
        ]
    ), "C43-M178 template re-read per lookup: %d reads" % len(reads)


def test_html_lookup_lists_template_dir_once(tmp_path, monkeypatch):
    d = _template_dir(tmp_path)
    globs = []
    real = Path.glob

    def counting(self, pattern, *a, **k):
        if pattern == "*.template.json":
            globs.append(str(self))
        return real(self, pattern, *a, **k)

    monkeypatch.setattr(Path, "glob", counting)
    tr.find_template_for_url("https://a.example/x", [d], html="<h1 class='a'>x</h1>")
    assert len(globs) == 1, "C43-M178 html lookup listed dir %d times" % len(globs)


def test_template_results_unchanged_and_isolated(tmp_path, monkeypatch):
    d = _template_dir(tmp_path)
    _trust_stamps(monkeypatch)
    loaded = tr.load_templates([d])
    assert [t["host"] for t in loaded] == ["a.example", "a.example", "b.example"]
    assert loaded[0]["_template_file"] == str((d / "a.template.json").resolve())
    assert [
        t["selectors"]["title"]
        for t in tr.find_template_variants_for_url("https://a.example/", [d])
    ] == ["h1.a", "h2.alt"]
    assert tr.select_best_variant("https://a.example/", "<h2 class='alt'></h2>", [d])[
        "selectors"
    ] == {"title": "h2.alt"}
    assert tr.find_template_for_url("https://c.example/", [d]) is None
    assert tr.find_template_for_url("https://zzz.example/", [d]) is None

    # A caller mutating its result must not poison the next lookup.
    got = tr.find_template_for_url("https://b.example/", [d])
    got["selectors"]["title"] = "MUTATED"
    got["host"] = "mutated"
    loaded[1]["selectors"]["title"] = "MUTATED"
    again = tr.find_template_for_url("https://b.example/", [d])
    assert again["host"] == "b.example"
    assert again["selectors"] == {"title": "h1.b"}
    assert tr.load_templates([d])[1]["selectors"] == {"title": "h2.alt"}


def test_template_edits_are_seen(tmp_path):
    d = _template_dir(tmp_path)
    assert tr.find_template_for_url("https://b.example/", [d]) is not None
    # Same-size in-place rewrite inside the racy window -> must re-read.
    fp = d / "b.template.json"
    fp.write_text(
        fp.read_text(encoding="utf-8").replace("h1.b", "h3.b"), encoding="utf-8"
    )
    os.utime(fp, (_OLD, _OLD))
    assert tr.find_template_for_url("https://b.example/", [d])["selectors"] == {
        "title": "h3.b"
    }
    _write_template(d, "b", "b.example", status="disabled")
    assert tr.find_template_for_url("https://b.example/", [d]) is None
    _write_template(d, "e", "e.example")
    assert tr.find_template_for_url("https://e.example/", [d])["host"] == "e.example"
    (d / "e.template.json").unlink()
    assert tr.find_template_for_url("https://e.example/", [d]) is None


def _capture_root(tmp_path):
    root = tmp_path / "store"
    nested = root / "captures" / "template_onboarding" / "n.example_1_20260101"
    nested.mkdir(parents=True)
    (nested / "capture_deep.json").write_text("{}", encoding="utf-8")
    (root / "captures" / "capture_flat.json").write_text("{}", encoding="utf-8")
    return root


def test_bare_basename_resolve_walks_capture_roots_once(tmp_path, monkeypatch):
    root = _capture_root(tmp_path)
    walks = []
    real = da.scan_captures

    def counting(*a, **k):
        walks.append(1)
        return real(*a, **k)

    monkeypatch.setattr(da, "scan_captures", counting)
    p = da._resolve_capture_any("capture_deep.json", root=root)
    assert p is not None and p.name == "capture_deep.json"
    assert len(walks) == 1, "C43-M054 resolve walked capture roots %d times" % len(
        walks
    )
    assert da._resolve_capture_any("capture_flat.json", root=root).name == (
        "capture_flat.json"
    )
    assert da._resolve_capture_any("capture_none.json", root=root) is None
    rel = "captures/template_onboarding/n.example_1_20260101/capture_deep.json"
    assert da.resolve_capture_token(rel, root=root) == root.resolve() / rel
    assert da.resolve_capture_token("captures/../x.json", root=root) is None


def _drift_store(tmp_path, monkeypatch):
    root = tmp_path / "drift"
    cap = root / "captures"
    cap.mkdir(parents=True)
    for name, host, mt in [
        ("capture_a1.json", "a.example", 100),
        ("capture_a2.json", "a.example", 300),
        ("capture_b1.json", "b.example", 200),
        ("capture_b2.json", "b.example", 200),
        ("capture_c1.json", "c.example", 50),
        ("capture_x.json", "", 400),
    ]:
        fp = cap / name
        fp.write_text(
            json.dumps({"url": "https://%s/p" % host if host else "", "tag": name}),
            encoding="utf-8",
        )
        os.utime(fp, (mt, mt))
    monkeypatch.setattr(da, "_capture_store_root", lambda: root)
    monkeypatch.setattr(da, "_project_root", lambda: root)
    return root


def test_drift_sweep_opens_each_capture_once(tmp_path, monkeypatch):
    _drift_store(tmp_path, monkeypatch)
    sites = {
        "s1": "a.example",
        "s2": "b.example",
        "s3": "a.example",
        "s4": "nohost.example",
        "s5": "c.example",
    }
    monkeypatch.setattr(
        sd,
        "status_all",
        lambda: [{"site_id": sid, "flagged_stale": True} for sid in sites],
    )
    monkeypatch.setattr(
        dr,
        "_site_cfg_for",
        lambda sid: {"start_url": "https://%s/" % sites[sid], "trigger_selector": "a"},
    )
    opened = []
    real_load = da.load_capture

    def counting_load(p):
        opened.append(Path(p).name)
        return real_load(p)

    picked = {}

    def recording_redacted_dom(cap):
        picked.setdefault(cap["url"], []).append(cap["tag"])
        return {"ok": False}

    monkeypatch.setattr(da, "load_capture", counting_load)
    monkeypatch.setattr(da, "redacted_dom", recording_redacted_dom)
    res = dr.scheduled_drift_repair(
        force=True,
        drafts_dir=tmp_path / "drafts",
        reviewed_dir=tmp_path / "rev",
        state_file=tmp_path / "last.json",
    )
    assert res == {"ran": True, "considered": 5, "repaired": 0, "skipped": 5}
    # Newest per host; mtime tie keeps the first capture listed.
    assert picked == {
        "https://a.example/p": ["capture_a2.json", "capture_a2.json"],
        "https://b.example/p": ["capture_b1.json"],
        "https://c.example/p": ["capture_c1.json"],
    }
    # 6 captures indexed once + 4 selected captures loaded for their site.
    assert len(opened) <= 6 + 4, (
        "C43-M056 sweep opened captures %d times for 5 sites" % len(opened)
    )


def test_latest_capture_for_host_direct_call_unchanged(tmp_path, monkeypatch):
    root = _drift_store(tmp_path, monkeypatch)
    assert dr._latest_capture_for_host("a.example", root=root)["tag"] == (
        "capture_a2.json"
    )
    assert dr._latest_capture_for_host("b.example")["tag"] == "capture_b1.json"
    assert dr._latest_capture_for_host("nohost.example", root=root) is None
    assert dr._latest_capture_for_host("", root=root) is None
