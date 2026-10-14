"""dev-path-inputs (O1789 dev-console-exec F6/F7): caller-supplied dev-console
paths must resolve under an allowlisted root, be a regular file (or a directory
for tests_dir= / artifacts_dir=) and stay under a size cap.

F6: a FIFO ?path= hung the request thread forever (systemd_check, guard_status,
test_timing) and /dev/zero was read until MemoryError; an oversize regular file
stands in for /dev/zero here so the RED run cannot OOM the suite.
F7: systemd_check echoed any INI-shaped file and was an exists-oracle;
parametrize_fanout / flaky_tests listed foreign dirs; zip_manifest /
backup_check listed members of any archive.

fresh_app chdirs into tmp_path, so tmp_path is an allowed root; FOREIGN is a
sibling temp dir outside every root.
"""
import json
import os
import threading
import zipfile

import pytest

BD_GATE_SCOPE = "module"

REFUSED = "under an allowlisted root"
CANARY = "CANARY-dev-path-inputs-7f3a"
_UNIT = ("[Unit]\nDescription=bd\n[Service]\nWorkingDirectory=/opt/bd\n"
         "ExecStart=/usr/bin/python3 downloader_ui.py\nRestart=on-failure\n"
         "TimeoutStopSec=60\n[Install]\nWantedBy=multi-user.target\n")


@pytest.fixture
def foreign(tmp_path_factory, tmp_path):
    d = tmp_path_factory.mktemp("foreign")
    assert os.path.commonpath([str(d), str(tmp_path)]) != str(tmp_path)
    return d


@pytest.fixture
def client(fresh_app, monkeypatch):
    monkeypatch.delenv("BD_DEV_MODE_DISABLE", raising=False)
    monkeypatch.delenv("BD_HOME", raising=False)
    # warm the lazy dev_suite imports so the bounded waits below time the
    # path handling, not a cold first request
    fresh_app.get("/api/dev/systemd_check?path=/nonexistent/warmup")
    return fresh_app


def _get_bounded(client, url, secs=20):
    box = {}

    def run():
        box["resp"] = client.get(url)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(secs)
    assert not t.is_alive(), f"F6 FIFO: no response in {secs}s for {url}"
    return box["resp"]


@pytest.mark.parametrize("url", ["/api/dev/systemd_check?path={p}",
                                 "/api/dev/guard_status?path={p}",
                                 "/api/dev/test_timing?path={p}"])
def test_fifo_path_refused_without_hanging(client, tmp_path, url):
    fifo = tmp_path / "unit.fifo"
    os.mkfifo(fifo)
    body = _get_bounded(client, url.format(p=fifo)).get_json()
    assert body["ok"] is False and REFUSED in body["error"], body


def test_oversize_unit_refused(client, tmp_path):
    from bulk_downloader.dev_suite import release_lint as _rl
    big = tmp_path / "big.service"
    big.write_text(_UNIT + "#" * (8_000_001 - len(_UNIT)))
    assert big.stat().st_size > getattr(_rl, "DEV_INPUT_FILE_CAP", 8_000_000)
    body = client.get(f"/api/dev/systemd_check?path={big}").get_json()
    assert body["ok"] is False and "exceeds" in body.get("error", ""), \
        f"F6 size cap: oversize unit was read: {str(body)[:200]}"


def test_bounded_reader_refuses_fifo_and_overflow(tmp_path):
    from bulk_downloader.dev_suite.release_lint import read_dev_input_text
    fifo = tmp_path / "x.fifo"
    os.mkfifo(fifo)
    box = {}

    def run():
        try:
            read_dev_input_text(str(fifo))
        except OSError as e:
            box["err"] = str(e)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(20)
    assert not t.is_alive(), "F6 FIFO: bounded reader blocked on open (20s)"
    assert "not a regular file" in box.get("err", ""), box
    f = tmp_path / "x.txt"
    f.write_text("abcdef")
    with pytest.raises(OSError, match="exceeds 3 bytes"):
        read_dev_input_text(str(f), cap=3)
    assert read_dev_input_text(str(f), cap=6) == "abcdef"


def test_foreign_ini_not_echoed_and_no_oracle(client, foreign):
    ini = foreign / "secret.ini"
    ini.write_text(f"[Service]\nWorkingDirectory={CANARY}\n")
    present = client.get(f"/api/dev/systemd_check?path={ini}")
    missing = client.get(f"/api/dev/systemd_check?path={foreign}/nope.ini")
    assert CANARY not in present.get_data(as_text=True), \
        "F7 disclosure: foreign INI value echoed by systemd_check"
    assert present.get_json() == missing.get_json(), "F7 exists-oracle"
    assert REFUSED in present.get_json()["error"]


@pytest.mark.parametrize("url", ["/api/dev/parametrize_fanout?tests_dir={d}",
                                 "/api/dev/flaky_tests?artifacts_dir={d}"])
def test_foreign_dir_not_listed(client, foreign, url):
    (foreign / f"test_{CANARY}.py".replace("-", "_")).write_text(
        "import pytest\n\n\n@pytest.mark.parametrize('a', [1, 2])\n"
        "def test_x(a):\n    pass\n")
    for i in range(2):
        fails = [{"test": f"t_{CANARY}"}] if i else []
        (foreign / f"r{i}_{CANARY}.json").write_text(
            json.dumps({"failures": fails, "total": 1}))
    resp = client.get(url.format(d=foreign))
    text = resp.get_data(as_text=True)
    assert CANARY not in text and CANARY.replace("-", "_") not in text, \
        f"F7 disclosure: foreign dir listed by {url}"
    assert REFUSED in resp.get_json()["error"]


@pytest.mark.parametrize("route", ["zip_manifest", "backup_check"])
def test_foreign_archive_not_listed(client, foreign, route):
    zp = foreign / "a.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr(f"{CANARY}.txt", "x")
    resp = client.get(f"/api/dev/{route}?path={zp}")
    assert CANARY not in resp.get_data(as_text=True), \
        f"F7 disclosure: foreign archive members listed by {route}"
    assert REFUSED in resp.get_json()["error"]


def test_positive_control_inside_root_still_works(client, tmp_path):
    unit = tmp_path / "bd.service"
    unit.write_text(_UNIT)
    body = client.get(f"/api/dev/systemd_check?path={unit}").get_json()
    assert "error" not in body and body.get("checks"), body
    art = tmp_path / "test_results.json"
    art.write_text(json.dumps({"version": 2, "failures": [], "skips": []}))
    body = client.get(f"/api/dev/guard_status?path={art}").get_json()
    assert body["ok"] is True, body
    tdir = tmp_path / "tests"
    tdir.mkdir()
    (tdir / "test_a.py").write_text("def test_a(): pass\n")
    body = client.get(f"/api/dev/parametrize_fanout?tests_dir={tdir}").get_json()
    assert body["ok"] is True, body
    zp = tmp_path / "in.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("inside.txt", "x")
    body = client.get(f"/api/dev/backup_check?path={zp}").get_json()
    assert "inside.txt" in json.dumps(body), body
