"""plugins-exec-surface scrapers-fix r1+r2 -- F6, F8, F9 (FINDINGS S21/S22/S24/S35).

F6  community-scraper install wrote into any writable dir, the plugin dir included,
    where a plain reload imported the third-party companion .py.
F8  community remove unlinked every manifest path, not only files under stash_dir.
    r2 (lens R166-A): "dl/.." through a symlinked dl passed the lexical check, and a
    stash_dir swapped for a symlink between check and unlink redirected the unlink.
F9  a yml companion ref ("../../../x.py") went unnormalized into the raw-host URL, and
    the yt-dlp bridge `url` took any http(s) host (read-SSRF through --dump-json).

No network: GitHub fetches and DNS are monkeypatched; BD_HOME / INSTALL_DIR are tmp.
"""
import json
import os
import socket

import pytest

BD_GATE_SCOPE = "module"


@pytest.fixture
def cs(tmp_path, monkeypatch):
    from bulk_downloader import community_scrapers as mod
    from bulk_downloader import constants

    monkeypatch.setenv("BD_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(constants, "INSTALL_DIR", tmp_path / "install")
    (tmp_path / "install" / "plugins").mkdir(parents=True)
    files = {
        "scrapers/Evil.yml": b"name: Evil\nscript:\n  - Evil.py\n",
        "scrapers/Evil.py": b"# companion\n",
        "scrapers/Trav.yml": b"name: Trav\nscript:\n  - ../../../outside/trav.py\n",
        "scrapers/../../../outside/trav.py": b"# trav\n",
    }
    calls = []

    def fake_fetch(rel, timeout_s=30.0, github_token=""):
        calls.append(rel)
        return (files[rel], "") if rel in files else (b"", "http_404")

    monkeypatch.setattr(mod, "_fetch_raw_file", fake_fetch)
    ents = [mod.ScraperEntry(name="Evil", yml_path="scrapers/Evil.yml"),
            mod.ScraperEntry(name="Trav", yml_path="scrapers/Trav.yml")]
    monkeypatch.setattr(mod, "fetch_index", lambda **k: (ents, ""))
    monkeypatch.setattr(mod, "_test_calls", calls, raising=False)
    return mod


def _client():
    from bulk_downloader.app import app

    return app.test_client()


def _post(c, url, body):
    j = c.get("/api/csrf").get_json() or {}
    t = j.get("csrf_token") or j.get("token")
    return c.post(url, data=json.dumps(body),
                  headers={"X-CSRFToken": t, "X-CSRF-Token": t,
                           "Content-Type": "application/json"})


def test_f6_install_refuses_app_code_roots(cs, tmp_path, monkeypatch):
    plugins = tmp_path / "install" / "plugins"
    c = _client()
    r = _post(c, "/api/community_scrapers/install",
              {"scraper_name": "Evil", "stash_dir": str(plugins)})
    j = r.get_json()
    assert j["ok"] is False and j["error"] == "protected_dir:plugin_dir", j
    assert sorted(os.listdir(plugins)) == [], "companion written into the plugin dir"
    (plugins / "sub").mkdir()
    assert cs._validate_stash_dir(str(plugins / "sub")) == (False, "protected_dir:plugin_dir")
    pkg = os.path.dirname(os.path.abspath(cs.__file__))
    assert cs._validate_stash_dir(pkg) == (False, "protected_dir:app_package")
    onpath = tmp_path / "onpath"
    onpath.mkdir()
    monkeypatch.syspath_prepend(str(onpath))
    assert cs._validate_stash_dir(str(onpath)) == (False, "protected_dir:import_path")
    # r2: a symlinked stash dir is judged by where it points (lens mutant M3)
    for name, target in (("lnk_root", plugins), ("lnk_sub", plugins / "sub")):
        (tmp_path / name).symlink_to(target)
        assert cs._validate_stash_dir(str(tmp_path / name)) == (
            False, "protected_dir:plugin_dir"), name
    # ... and so is a plugin dir reached through a symlinked INSTALL_DIR
    from bulk_downloader import constants

    (tmp_path / "install_lnk").symlink_to(tmp_path / "install")
    monkeypatch.setattr(constants, "INSTALL_DIR", tmp_path / "install_lnk")
    assert cs._validate_stash_dir(str(plugins / "sub")) == (False, "protected_dir:plugin_dir")
    # "" on sys.path is the current directory
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    with monkeypatch.context() as mp:
        mp.chdir(cwd)
        mp.syspath_prepend("")
        assert cs._validate_stash_dir(str(cwd)) == (False, "protected_dir:import_path")
    # control: a plain Stash scrapers dir still installs, companion included
    stash = tmp_path / "stash"
    stash.mkdir()
    r = _post(c, "/api/community_scrapers/install",
              {"scraper_name": "Evil", "stash_dir": str(stash)})
    j = r.get_json()
    assert j["ok"] is True, j
    assert sorted(os.listdir(stash)) == ["Evil.py", "Evil.yml"]


def test_f8_remove_only_unlinks_entries_inside_stash_dir(cs, tmp_path):
    stash = tmp_path / "stash"
    outside = tmp_path / "outside"
    stash.mkdir()
    outside.mkdir()
    victim = outside / "victim.txt"
    victim.write_text("keep")
    link = outside / "lnk.py"
    (stash / "target.py").write_text("x")
    link.symlink_to(stash / "target.py")
    forged = {
        "Forged": [str(victim)],
        "Dotdot": [str(stash / ".." / "outside" / "victim.txt")],
        "Link": [str(link)],
        "Mixed": [str(stash / "target.py"), str(victim)],
    }
    m = cs.load_manifest()
    for name, files in forged.items():
        m.setdefault("installed", {})[name] = {"files": files}
    cs._save_manifest(m)
    for name in forged:
        res = cs.remove_one(name, stash_dir=str(stash))
        assert res.ok is False and res.error == "files_outside_stash_dir:1", (name, res)
        assert name in cs.load_manifest()["installed"], name
    assert victim.read_text() == "keep"
    assert os.path.islink(link) and (stash / "target.py").exists()
    # r2: "dl/.." through a symlinked dl climbs out of dl's target, not back to stash
    (outside / "sub").mkdir()
    (outside / "v6").write_text("keep")
    (stash / "dl").symlink_to(outside / "sub")
    m = cs.load_manifest()
    m["installed"]["Climb"] = {"files": [str(stash) + "/dl/../v6"]}
    cs._save_manifest(m)
    res = cs.remove_one("Climb", stash_dir=str(stash))
    assert res.ok is False and res.error == "files_outside_stash_dir:1", res
    assert (outside / "v6").read_text() == "keep", "unlink followed dl/.. outside stash_dir"
    os.remove(stash / "dl")
    # control: a real install removes cleanly through the route
    c = _client()
    assert _post(c, "/api/community_scrapers/install",
                 {"scraper_name": "Evil", "stash_dir": str(stash)}).get_json()["ok"] is True
    j = _post(c, "/api/community_scrapers/remove",
              {"scraper_name": "Evil", "stash_dir": str(stash)}).get_json()
    assert j["ok"] is True and sorted(os.path.basename(f) for f in j["files_removed"]) == [
        "Evil.py", "Evil.yml"], j
    assert sorted(os.listdir(stash)) == ["target.py"]


def test_f8_stash_dir_swapped_after_check_unlinks_nothing_outside(cs, tmp_path, monkeypatch):
    stash = tmp_path / "stash"
    outside = tmp_path / "outside"
    stash.mkdir()
    outside.mkdir()
    assert cs.install_one("Evil", stash_dir=str(stash)).ok is True
    for n in ("Evil.py", "Evil.yml"):
        (outside / n).write_text("keep")
    real_unlink, swapped = os.unlink, []

    def swap_then_unlink(path, *a, **k):
        if not swapped and os.path.basename(path).startswith("Evil."):
            # stash_dir becomes a symlink after the check, before the first unlink
            swapped.append(path)
            os.rename(stash, tmp_path / "stash_real")
            stash.symlink_to(outside)
        return real_unlink(path, *a, **k)

    with monkeypatch.context() as mp:
        mp.setattr(os, "supports_dir_fd", os.supports_dir_fd | {swap_then_unlink})
        mp.setattr(os, "unlink", swap_then_unlink)
        mp.setattr(os, "remove", swap_then_unlink)
        res = cs.remove_one("Evil", stash_dir=str(stash))
    assert swapped, "remove_one never unlinked"
    assert sorted(os.listdir(outside)) == ["Evil.py", "Evil.yml"], "unlinked through the swap"
    assert res.ok is True and sorted(os.listdir(tmp_path / "stash_real")) == [], res


def test_f9_companion_ref_never_leaves_scrapers_tree(cs, tmp_path):
    stash = tmp_path / "stash"
    stash.mkdir()
    res = cs.install_one("Trav", stash_dir=str(stash))
    assert res.ok is True and res.error == "companions_missed:1", res
    assert not any(".." in rel for rel in cs._test_calls), cs._test_calls
    assert cs.load_manifest()["installed"]["Trav"]["companions_missed"] == [
        "../../../outside/trav.py:refused_ref"]
    for bad in ("../x.py", "a/../b.py", "/etc/x.py", "a//b.py", "./x.py", ".hidden.py",
                "a\\b.py", "x.py?raw=1", "x.py#f", "%2e%2e/x.py", "https://h/x.py"):
        assert cs._safe_companion_rel(bad) is None, bad
    # control: the shapes the repo actually uses still resolve under scrapers/
    assert cs._safe_companion_rel("Foo Bar.py") == "scrapers/Foo Bar.py"
    assert cs._safe_companion_rel("scripts/foo.js") == "scrapers/scripts/foo.js"
    assert cs._safe_companion_rel("scrapers/Evil.py") == "scrapers/Evil.py"


@pytest.fixture
def tb(tmp_path, monkeypatch):
    from bulk_downloader import tool_bridge as mod

    echo = "/bin/echo"
    monkeypatch.setattr(mod, "_resolve", lambda b: echo if b == "yt-dlp" else None)
    monkeypatch.setattr(mod, "ALLOWLIST", mod._build_allowlist())
    home = tmp_path / "bdhome"
    home.mkdir()
    monkeypatch.setenv("BD_HOME", str(home))
    dns = {"internal.example": "10.1.2.3", "public.example": "93.184.216.34"}

    def fake_gai(host, *a, **k):
        if host not in dns:
            raise socket.gaierror("no such host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (dns[host], 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_gai)
    return mod


def test_f9_ytdlp_url_refuses_non_public_hosts(tb):
    for bad in ("http://127.0.0.1/x", "http://[::1]/x", "http://169.254.169.254/latest",
                "http://10.0.0.1/", "http://100.64.0.1/", "http://localhost:8000/",
                "http://a.localhost/"):
        with pytest.raises(tb.BridgeError, match="host is not public"):
            tb.build_argv("yt-dlp", {"--dump-json": True, "url": bad})
    # a name is classified at run(), after resolution, before anything executes
    for bad in ("http://internal.example/v", "http://unresolvable.example/v"):
        with pytest.raises(tb.BridgeError, match="host is not public"):
            tb.run("yt-dlp", {"url": bad})
    c = _client()
    r = _post(c, "/api/tools/run", {"tool": "yt-dlp", "flags": {"url": "http://127.0.0.1:8000/"}})
    assert r.status_code == 400 and "host is not public" in r.get_json()["error"]
    # control: a public host passes both checks and the (echo) tool runs
    out = tb.run("yt-dlp", {"url": "https://public.example/v"})
    assert out["returncode"] == 0 and "https://public.example/v" in out["stdout"], out
    r = _post(c, "/api/tools/run", {"tool": "yt-dlp", "flags": {"url": "https://93.184.216.34/v"}})
    assert r.status_code == 200 and "https://93.184.216.34/v" in r.get_json()["stdout"]
