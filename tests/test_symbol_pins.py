"""symbol-pins r1 (OD O3141, PM-B landing throughput item 6): pins that judge the
live tree anchor on symbols plus content, never on line numbers, so a train that
only shifts lines needs no re-pin commit -- and a real change is still RED.

Measured denominator (git grep of tests/*.py for quoted "<pkg>/<file>.py:<N>"):
14 literals in 7 files; the 4 that judge the live tree were row 703's
STAR_KWARGS_CONSTRUCTIONS, now keyed file::qualname::AST-hash. The row 706
resolver-adjacent census pins COUNTS (sites/nodeids; a line is only part of a
site's printed identity) and tests/source_window_hashes.json keys windows by
test::function::expression::occurrence with a content hash -- both already
line-free; the tests below prove it rather than assume it.
"""
import importlib.util
import json
import re
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

REPO = Path(__file__).resolve().parents[1]
PACKAGE = "bulk_downloader"
PINNED_FILES = ("runner_telemetry.py", "runner_transport.py")
SHIFT = "\n" * 40


def _load(name):
    path = REPO / "tests" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_symbol_pins_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def row703():
    return _load("test_row703_a_proxy_shadows_the_guarded_transport")


@pytest.fixture(scope="module")
def row706():
    return _load("test_rows706_714_test_hygiene")


def _copy_pinned(root, edit=None):
    """The two files holding every **kwargs construction, copied under root;
    edit(name, text) -> text may change one before it is written."""
    (root / PACKAGE).mkdir(parents=True)
    for name in PINNED_FILES:
        text = (REPO / PACKAGE / name).read_text(encoding="utf-8")
        (root / PACKAGE / name).write_text(edit(name, text) if edit else text, encoding="utf-8")
    return root


def _shift_above(marker):
    def edit(name, text):
        if name != "runner_transport.py":
            return text
        assert text.count(marker) == 1, f"COULD NOT LOOK: {marker!r} not unique in {name}"
        return text.replace(marker, SHIFT + marker)
    return edit


# -- row 703: the star-kwargs registry ---------------------------------------
def test_row703_forty_blank_lines_above_a_pinned_function_need_no_repin(tmp_path, row703):
    plain = row703._scan_tree(_copy_pinned(tmp_path / "plain"))
    shifted = row703._scan_tree(_copy_pinned(tmp_path / "shifted", _shift_above("    def _probe_size(")))
    moved = {r["where"] for r in shifted} - {r["where"] for r in plain}
    assert moved, "control: the shift moved no construction -- this probe cannot say YES"
    row703.test_every_star_kwargs_construction_is_declared(shifted)


def test_row703_moving_a_pinned_call_to_another_function_is_red(tmp_path, row703):
    edit = _shift_above("    def _probe_size(")
    renamed = row703._scan_tree(_copy_pinned(
        tmp_path / "moved",
        lambda n, t: edit(n, t).replace("    def _probe_size(", "    def _probe_size_elsewhere(")))
    with pytest.raises(AssertionError, match=r"new:.*_probe_size_elsewhere"):
        row703.test_every_star_kwargs_construction_is_declared(renamed)


def test_row703_a_new_star_kwargs_construction_is_still_named(tmp_path, row703):
    rows = row703._scan_tree(_copy_pinned(tmp_path / "plain"))
    rows.append({**rows[0], "anchor": f"{PACKAGE}/added.py::go::0123456789ab#0", "star_kwargs": True})
    with pytest.raises(AssertionError, match=r"new:.*added\.py::go"):
        row703.test_every_star_kwargs_construction_is_declared(rows)


_FIXTURE = (
    "import httpx\n"
    "from bulk_downloader.ssrf_transport import guarded_transport, PINNED\n"
    "def a(kw):\n"
    "    return httpx.Client(transport=guarded_transport(PINNED), **kw)\n"
    "def b(kw):\n"
    "    return None\n")


def _fixture_anchors(row703, root, source):
    (root / PACKAGE).mkdir(parents=True)
    (root / PACKAGE / "m.py").write_text(source, encoding="utf-8")
    return [r["anchor"] for r in row703._scan_tree(root) if r["star_kwargs"]]


def test_row703_anchor_is_line_free_and_names_the_function(tmp_path, row703):
    base = _fixture_anchors(row703, tmp_path / "base", _FIXTURE)
    assert len(base) == 1 and "::a::" in base[0]
    shifted = _fixture_anchors(row703, tmp_path / "shift", _FIXTURE.replace("def a(", SHIFT + "def a("))
    assert shifted == base
    call = "    return httpx.Client(transport=guarded_transport(PINNED), **kw)\n"
    moved_src = _FIXTURE.replace(call, "    return None\n", 1).replace("def b(kw):\n    return None\n", "def b(kw):\n" + call)
    moved = _fixture_anchors(row703, tmp_path / "moved", moved_src)
    assert len(moved) == 1 and "::b::" in moved[0] and moved != base
    changed = _fixture_anchors(row703, tmp_path / "changed", _FIXTURE.replace("**kw)", "**kw, timeout=1)"))
    assert changed != base, "a content change at the call must change its key"


# -- row 706: the resolver-adjacent census pins counts, not lines ------------
_ADJACENT = (
    "def test_client_refuses(client):\n"
    "    client.get('http://a.invalid/x')\n"
    "    client.get('http://b.invalid/y')\n"
    "def test_unrelated():\n"
    "    assert 1\n")


def _census(row706, monkeypatch, source):
    monkeypatch.setattr(row706, "_tracked_test_sources", lambda repo: {"tests/test_fx.py": source})
    sites = row706._dns_adjacent_sites(REPO)
    return len(sites), sorted({nodeid for _, nodeid in sites})


def test_row706_census_counts_do_not_move_with_lines(row706, monkeypatch):
    plain = _census(row706, monkeypatch, _ADJACENT)
    assert plain == (2, ["tests/test_fx.py::test_client_refuses"])
    assert _census(row706, monkeypatch, SHIFT + _ADJACENT) == plain
    moved = _ADJACENT.replace("    client.get('http://b.invalid/y')\n", "").replace(
        "    assert 1\n", "    assert 'http://b.invalid/y'\n")
    assert _census(row706, monkeypatch, moved) == (1, plain[1]), (
        "control: moving a site out of a resolver-adjacent function must change the count")


# -- source windows: keyed by symbol + content hash ---------------------------
_WINDOW_KEY = re.compile(r"^tests/[\w/]+\.py::[\w.]+(::[\w.]+)?::.+::\d+$")
_LINE_KEY = re.compile(r"\.py:\d")


def test_source_window_keys_carry_no_line_numbers():
    data = json.loads((REPO / "tests" / "source_window_hashes.json").read_text(encoding="utf-8"))
    keys = [w["key"] for w in data["windows"]]
    assert len(keys) == data["resolved"] > 0, "COULD NOT LOOK: empty source-window denominator"
    assert all(len(w["sha256"]) == 64 for w in data["windows"])
    shaped = [k for k in keys if not _WINDOW_KEY.match(k)]
    assert not shaped, f"source-window key(s) not test::function::expression::n: {shaped}"
    lined = [k for k in keys if _LINE_KEY.search(k)]
    assert not lined, f"source-window key(s) carry a line number: {lined}"
    assert _LINE_KEY.search("tests/test_x.py:12::w") and not _WINDOW_KEY.match("tests/test_x.py:12"), (
        "control: the probe must be able to say YES to a line-keyed entry")
