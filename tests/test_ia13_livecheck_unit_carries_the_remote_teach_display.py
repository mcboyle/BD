"""IA-13 -- bd-livecheck.service carries DISPLAY when remote-teach set one up.

install_remote_teach.sh gives the app its Xvfb display through the drop-in
bulkdownloader.service.d/10-display.conf (Environment=DISPLAY=:99). The livecheck
unit written by tools/install_livecheck_timer.sh had no DISPLAY, so on a
remote-teach host L2 WARNed "headed launch needs a display -- none on this host"
while :99 was running.

The installer is run for real with stub `sudo` / `systemctl` on PATH: the stub
sudo captures what `tee <unit>` would write, so nothing touches /etc. The drop-in
location is pointed at a fixture through BD_DISPLAY_DROPIN.

Negative control: with no drop-in the unit must NOT invent a DISPLAY (a headless
host without remote-teach has none, and a made-up one turns the WARN into a
launch failure).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "tools" / "install_livecheck_timer.sh"

_SUDO = """#!/usr/bin/env bash
if [ "$1" = "tee" ] && [ "$2" = "-a" ]; then cat >> "$CAPTURE_DIR/$(basename "$3")"; exit 0; fi
if [ "$1" = "tee" ]; then cat > "$CAPTURE_DIR/$(basename "$2")"; exit 0; fi
exec "$@"
"""
_SYSTEMCTL = "#!/usr/bin/env bash\nexit 0\n"


def _run(tmp_path: Path, dropin: Path) -> str:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("sudo", _SUDO), ("systemctl", _SYSTEMCTL)):
        p = bindir / name
        p.write_text(body)
        p.chmod(0o755)
    capture = tmp_path / "capture"
    capture.mkdir()
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}",
               CAPTURE_DIR=str(capture), BD_DISPLAY_DROPIN=str(dropin))
    env.pop("DISPLAY", None)
    proc = subprocess.run(["bash", str(_SCRIPT)], env=env, capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-800:]
    unit = capture / "bd-livecheck.service"
    assert unit.is_file(), f"installer wrote no service unit; captured: {os.listdir(capture)}"
    return unit.read_text()


def _service_env(unit: str) -> list:
    """Environment= lines of the [Service] section only (a line in another section is not seen)."""
    section, env = "", []
    for ln in unit.splitlines():
        ln = ln.strip()
        if ln.startswith("[") and ln.endswith("]"):
            section = ln
        elif section == "[Service]" and ln.startswith("Environment="):
            env.append(ln)
    return env


def test_the_unit_carries_display_from_the_remote_teach_dropin(tmp_path):
    dropin = tmp_path / "10-display.conf"
    dropin.write_text('[Service]\nEnvironment=DISPLAY=:99\n'
                      'Environment="BD_NOVNC_URL=http://h:6080/vnc.html"\n')
    env = _service_env(_run(tmp_path, dropin))
    assert "Environment=DISPLAY=:99" in env, (
        f"IA-13: bd-livecheck.service has no DISPLAY although 10-display.conf sets :99; env={env}"
    )
    assert not any("BD_NOVNC_URL" in e for e in env), "only DISPLAY is carried, not the noVNC URL"


def test_negative_control_no_dropin_no_display(tmp_path):
    env = _service_env(_run(tmp_path, tmp_path / "absent-10-display.conf"))
    assert any(e.startswith("Environment=BD_HOME=") for e in env), f"probe broken: env={env}"
    assert not any("DISPLAY" in e for e in env), f"a DISPLAY was invented: {env}"
