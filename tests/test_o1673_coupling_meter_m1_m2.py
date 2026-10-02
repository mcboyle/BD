"""O1673 t154 option (c): bd-coupling-meter defects M1 and M2.

M1: the meter counted import STATEMENTS, so a second `from .log import ...` in
    one module added a second runner->core edge for one module pair.
M2: subsystems were chosen by raw substring, so spa_media_extract (a parser)
    landed in 'runner' because its name contains "extract".

The meter runs as a subprocess against a tmp bulk_downloader/ fixture only.
The fixture lives under a mkdtemp dir, not pytest's tmp_path: tmp_path holds
"/test_" and bdtools_sec.iter_py drops every file under a test marker.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

BD_GATE_SCOPE = "module"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METER = os.path.join(ROOT, "toolchain", "bin", "bd-coupling-meter")


@pytest.fixture
def make_pkg():
    made = []

    def _make(files):
        work = tempfile.mkdtemp(prefix="o1673meter-")
        made.append(work)
        pkg = os.path.join(work, "bulk_downloader")
        os.makedirs(pkg)
        for name, body in files.items():
            with open(os.path.join(pkg, name + ".py"), "w") as fh:
                fh.write(body)
        return work

    yield _make
    for work in made:
        shutil.rmtree(work, ignore_errors=True)


def _measure(work):
    r = subprocess.run([sys.executable, METER, "--work", work, "--json"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _pair_edges(m, a, b):
    for c in m["worst_cross"]:
        if sorted(c["pair"]) == sorted([a, b]):
            return c["edges"]
    return 0


def test_m1_two_statements_of_one_pair_are_one_edge(make_pkg):
    work = make_pkg({
        "log": "X = 1\n",
        "runner": "from .log import X\n\ndef f():\n    from .log import X\n    return X\n",
    })
    m = _measure(work)
    assert m["total_edges"] == 1, m
    assert m["across"] == 1, m
    assert _pair_edges(m, "runner", "core") == 1, m
    assert m["coupling_ratio"] == 1.0, m


def test_m2_substring_extract_without_runner_role_is_core(make_pkg):
    work = make_pkg({
        "streaming_manifest": "X = 1\n",
        "spa_media_extract": "from .streaming_manifest import X\n",
    })
    m = _measure(work)
    assert m["within"] == 1 and m["across"] == 0, m
    assert m["coupling_ratio"] == 0.0, m


def test_m2_substring_detect_download_inside_a_word_is_core(make_pkg):
    work = make_pkg({
        "constants": "X = 1\n",
        "drm_detect": "from .constants import X\n",
        "hydration_extractor": "from .constants import X\n",
        "breadcrumb_downloads": "from .constants import X\n",
    })
    m = _measure(work)
    assert m["within"] == 3 and m["across"] == 0, m


def test_control_real_runner_module_stays_runner(make_pkg):
    work = make_pkg({
        "constants": "X = 1\n",
        "runner_extractors": "from .constants import X\n",
        "runner_transport": "from .runner_extractors import X\n",
    })
    m = _measure(work)
    # runner_transport -> runner_extractors is within runner;
    # runner_extractors -> constants crosses runner -> core.
    assert m["within"] == 1 and m["across"] == 1, m
    assert _pair_edges(m, "runner", "core") == 1, m


def test_control_new_cross_pair_still_counts(make_pkg):
    base = {
        "log": "X = 1\n",
        "session_keeper": "Y = 1\n",
        "runner": "from .log import X\n",
    }
    before = _measure(make_pkg(base))
    after = _measure(make_pkg(dict(base, runner="from .log import X\nfrom .session_keeper import Y\n")))
    assert after["across"] == before["across"] + 1, (before, after)
    assert _pair_edges(after, "runner", "core") == 2, after


def test_control_anchored_subsystem_tokens(make_pkg):
    # leading-token anchors still classify: vpn_*, app_* route prefix, subpackages.
    work = make_pkg({
        "constants": "X = 1\n",
        "vpn_runtime": "from .constants import X\n",
        "app_runners": "from .constants import X\n",
        "detect": "from .constants import X\n",
    })
    m = _measure(work)
    assert m["within"] == 0 and m["across"] == 3, m
    for sub in ("vpn", "runner", "recognizer"):
        assert _pair_edges(m, sub, "core") == 1, (sub, m)


def test_statements_field_keeps_raw_import_count(make_pkg):
    work = make_pkg({
        "log": "X = 1\n",
        "runner": "from .log import X\nfrom .log import X\n",
    })
    m = _measure(work)
    assert m["statements"] == 2 and m["total_edges"] == 1, m
