"""Row 809 -- requirements.txt pins httpx without the SOCKS extra it needs.

WHY THIS GATE EXISTS. ``requirements.txt`` pins ``httpx>=0.25,<1.0`` for direct
HTTP downloads. httpx 0.28.1's own METADATA (``Requires-Dist``) declares
``socksio`` only under its optional ``socks`` extra -- a plain
``pip install -r requirements.txt`` never installs it. Any code path that
constructs a SOCKS-proxied ``httpx.Client`` (VPN/SOCKS tunnel transport,
``get_socks_url_for_site`` callers, the manual runner) raises ``ImportError``
in that clean env, at CONSTRUCTION time, inside
``httpx._transports.default.HTTPTransport.__init__``. Row 703's fixture
correction did not fix this: it corrected a test double, not the manifest.

WHAT THIS GATE ASSERTS. ``requirements.txt`` declares the capability httpx
itself uses to gate SOCKS support -- either the ``[socks]`` extra on the httpx
line, or a standalone ``socksio`` pin -- so a clean env built from the shipped
manifest alone can construct a guarded SOCKS transport. It does not touch any
version bound or policy pin beyond that declaration.

THREE OUTCOMES. A requirements.txt that cannot be read, or that carries zero or
more than one httpx requirement line, is COULD NOT LOOK (raised), never folded
into a pass -- test_httpx_requirement_line_is_singular is the positive control
that the extraction itself is sound before anything is asserted about its
content.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

BD_GATE_SCOPE = "module"

REPO = pathlib.Path(__file__).resolve().parents[1]
REQUIREMENTS = REPO / "requirements.txt"

_HTTPX_LINE_RE = re.compile(r"^httpx(\[[^\]]*\])?\s*[><=!~,.\dA-Za-z]*\s*$")


def _requirements_lines() -> list[str]:
    text = REQUIREMENTS.read_text(encoding="utf-8")
    assert text, f"{REQUIREMENTS} is empty -- could not look, not a pass"
    return [ln.strip() for ln in text.splitlines()]


def _httpx_requirement_line() -> str:
    matches = [ln for ln in _requirements_lines() if _HTTPX_LINE_RE.match(ln)]
    assert len(matches) == 1, (
        f"expected exactly one httpx requirement line in {REQUIREMENTS}, "
        f"found {len(matches)}: {matches!r}"
    )
    return matches[0]


def test_httpx_requirement_line_is_singular():
    """Positive control: the extraction itself finds the one real line first."""
    line = _httpx_requirement_line()
    assert line.startswith("httpx"), line


def test_requirements_declares_the_socks_capability_httpx_needs():
    """RED on 900c08fc: the httpx line has no [socks] extra and no socksio pin."""
    line = _httpx_requirement_line()
    text = "\n".join(_requirements_lines())
    has_extra = "[socks]" in line
    has_standalone_pin = bool(re.search(r"^socksio[><=!~]", text, re.MULTILINE))
    assert has_extra or has_standalone_pin, (
        f"requirements.txt declares httpx as {line!r} with no 'socks' extra and "
        "no standalone socksio pin. A clean env `pip install -r requirements.txt` "
        "cannot construct a SOCKS-proxied httpx.Client -- it raises ImportError "
        "at httpx._transports.default.HTTPTransport.__init__ (row 809)."
    )


def _run_socks_construction_probe(*, mask_socksio: bool) -> subprocess.CompletedProcess:
    script = (
        "import sys\n"
        + ("sys.modules['socksio'] = None\n" if mask_socksio else "")
        + "for m in list(sys.modules):\n"
        + "    if m.startswith('httpx') or m.startswith('httpcore'):\n"
        + "        del sys.modules[m]\n"
        + "import httpx\n"
        + "try:\n"
        + "    httpx.Client(proxy='socks5://127.0.0.1:1080')\n"
        + "    print('RESULT: constructed')\n"
        + "except ImportError as exc:\n"
        + "    print('RESULT: ImportError: ' + str(exc))\n"
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_negative_control_socks_construction_fails_without_socksio():
    """Prove the probe can say NO for the intended reason (FLEET_RULE 7)."""
    proc = _run_socks_construction_probe(mask_socksio=True)
    assert proc.returncode == 0, proc.stderr
    occurrences = proc.stdout.count("socksio")
    assert occurrences == 1, (
        f"expected exactly 1 mention of 'socksio' in httpx's own failure text, "
        f"got {occurrences}: {proc.stdout!r}"
    )
    assert "RESULT: ImportError: Using SOCKS proxy, but the 'socksio' package " \
        "is not installed." in proc.stdout, proc.stdout


def test_positive_control_socks_construction_succeeds_when_socksio_present():
    """Prove the fixture builds a nonzero, working shape before any verdict."""
    proc = _run_socks_construction_probe(mask_socksio=False)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "RESULT: constructed", proc.stdout
