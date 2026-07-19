"""v3.66.470 INSTALL-WARN-SHADOW -- install_remote_teach.sh must detect a
higher-priority systemd drop-in that ALSO sets BD_NOVNC_URL (which would silently
shadow the value the installer bakes into 10-display.conf) and warn the operator.

The detection lives in a sliceable bash function ``novnc_shadow_winner`` (between
``# >>> novnc_shadow_winner`` / ``# <<< novnc_shadow_winner`` markers). This test
extracts JUST that function and exercises it against synthetic drop-in dirs, plus
a ``bash -n`` syntax gate on the whole installer. The live systemd behavior (the
warning firing during a real install + the embed resolution) is stash-only.

RED-first: on pristine @469 the function does not exist -> the slice is empty ->
the bash call errors / yields nothing for the shadow cases.
"""
import os
import re
import subprocess
import tempfile

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_REPO, "install_remote_teach.sh")
_BEGIN = "# >>> novnc_shadow_winner"
_END = "# <<< novnc_shadow_winner"


def _slice_function():
    text = open(_SCRIPT, encoding="utf-8").read()
    m = re.search(re.escape(_BEGIN) + r".*?" + re.escape(_END), text, re.S)
    assert m, "novnc_shadow_winner slice markers not found in install_remote_teach.sh"
    return m.group(0)


def _run_winner(dropin_dir, self_name="10-display.conf"):
    fn = _slice_function()
    script = fn + f'\nnovnc_shadow_winner "{dropin_dir}" "{self_name}"\n'
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert out.returncode == 0, f"winner fn errored: {out.stderr}"
    return out.stdout.strip()


def _write(d, name, body):
    with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
        fh.write(body)


_ENV_LINE = 'Environment="BD_NOVNC_URL=http://x:6080/vnc.html?resize=scale"\n'


def test_installer_syntax_is_clean():
    out = subprocess.run(["bash", "-n", _SCRIPT], capture_output=True, text=True)
    assert out.returncode == 0, f"bash -n failed:\n{out.stderr}"


def test_no_shadow_when_only_self_sets_it():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "10-display.conf", "[Service]\n" + _ENV_LINE)
        assert _run_winner(d) == "", "no higher-priority file -> no shadow"


def test_override_conf_shadows():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "10-display.conf", "[Service]\n" + _ENV_LINE)
        _write(d, "override.conf", "[Service]\n" + _ENV_LINE)
        assert _run_winner(d) == "override.conf"


def test_lower_priority_file_does_not_shadow():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "05-early.conf", "[Service]\n" + _ENV_LINE)  # sorts BEFORE 10-
        _write(d, "10-display.conf", "[Service]\n" + _ENV_LINE)
        assert _run_winner(d) == "", "a lexically-earlier file cannot win the Environment race"


def test_commented_assignment_is_not_a_shadow():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "10-display.conf", "[Service]\n" + _ENV_LINE)
        _write(d, "override.conf", "[Service]\n# " + _ENV_LINE)  # commented out
        assert _run_winner(d) == "", "a commented BD_NOVNC_URL is not an active assignment"


def test_lexically_greatest_shadow_wins():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "10-display.conf", "[Service]\n" + _ENV_LINE)
        _write(d, "20-a.conf", "[Service]\n" + _ENV_LINE)
        _write(d, "30-b.conf", "[Service]\n" + _ENV_LINE)
        assert _run_winner(d) == "30-b.conf", "the last-applied (greatest) drop-in is the real winner"


def test_unrelated_higher_priority_file_is_ignored():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "10-display.conf", "[Service]\n" + _ENV_LINE)
        _write(d, "99-other.conf", "[Service]\nEnvironment=DISPLAY=:99\n")
        assert _run_winner(d) == "", "a drop-in that doesn't set BD_NOVNC_URL is not a shadow"
