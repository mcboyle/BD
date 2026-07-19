"""CloakBrowser install-path fix + rrweb/snapdom auto-vendor — installer tests.

Finding C (v3.66.162 live census): the stash venv had no ``cloakbrowser`` on
the normal install path. This patch wires it onto ``install_linux.sh`` (online
index AND local/offline wheelhouses) via a non-fatal, non-destructive step, and
adds an idempotent rrweb/snapdom *auto-restore* step (offline-first, then npm
install-time-only) that keeps the runtime assets local-vendored (never CDN).

Static file-inspection tests for the custom runner: zero-arg, stdlib-only, no
network, no real ``cloakbrowser`` import (it is not installed here).
"""
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_INSTALL_SH = _REPO / "install_linux.sh"
_REQ_CLOAK = _REPO / "requirements-cloak.txt"
_REQ_CORE = _REPO / "requirements.txt"
_REQ_OPT = _REPO / "requirements-optional.txt"
_REQ_DEV = _REPO / "requirements-dev.txt"
_VENDOR_MD = _REPO / "bulk_downloader" / "vendor" / "VENDOR.md"
_RRWEB = _REPO / "bulk_downloader" / "vendor" / "rrweb" / "rrweb.min.js"
_SNAPDOM = _REPO / "bulk_downloader" / "vendor" / "snapdom" / "snapdom.js"
_DOM_RECORDER = _REPO / "bulk_downloader" / "dom_recorder.py"


def _read(p):
    return p.read_text(encoding="utf-8")


def _active_lines(text):
    out = []
    for raw in text.splitlines():
        line = re.sub(r"(^|\s)#.*$", "", raw).strip()
        if line:
            out.append(line)
    return out


def _cloak_block(sh):
    start = sh.find("CloakBrowser stealth backend")
    end = sh.find("Vendored DOM-capture assets", start)
    assert start != -1 and end != -1, "cloak block markers not found"
    return sh[start:end]


def _vendor_block(sh):
    start = sh.find("Vendored DOM-capture assets (rrweb + snapdom) — auto-restore")
    end = sh.find("D3 U1: Frontend SPA build", start)
    assert start != -1 and end != -1, "vendor block markers not found"
    return sh[start:end]


# ── CloakBrowser install-path (preserved) ────────────────────────────────────
def test_requirements_cloak_file_exists_and_pins_cloakbrowser():
    assert _REQ_CLOAK.is_file()
    pins = [l for l in _active_lines(_read(_REQ_CLOAK)) if l.lower().startswith("cloakbrowser")]
    assert pins and any("[geoip]" in l for l in pins), f"need cloakbrowser[geoip] pin; got {pins}"
    # v3.66.539: floor raised to >=0.4.5 -- the verified backend on the live box is
    # cloakbrowser 0.4.5 driving stealth Chromium 146 (resolve_backend()=cloakbrowser).
    # 0.3.31 predates the Chromium-146 binary channel; the floor now matches reality.
    assert any(">=0.4.5" in l for l in pins), f"need cloakbrowser floor >=0.4.5; got {pins}"


def test_cloakbrowser_on_normal_install_path():
    sh = _read(_INSTALL_SH)
    assert "cloakbrowser" in sh and "requirements-cloak.txt" in sh


def test_installer_provisions_browser_binary():
    assert re.search(r"-m\s+cloakbrowser\s+install", _read(_INSTALL_SH))


def test_installer_reports_status():
    assert re.search(r"-m\s+cloakbrowser\s+info", _read(_INSTALL_SH))


def test_installer_has_import_availability_check():
    assert "import cloakbrowser" in _read(_INSTALL_SH)


def test_cloak_step_is_non_fatal():
    sh = _read(_INSTALL_SH)
    assert not re.search(r"^\s*set\s+-e\b", sh, re.M)
    assert not re.search(r"set\s+-o\s+errexit", sh)
    assert "fall back to Playwright" in _cloak_block(sh)


def test_playwright_install_still_present():
    assert "playwright install chromium" in _read(_INSTALL_SH)


def test_installer_adds_no_runtime_launch_args():
    sh = _read(_INSTALL_SH)
    forbidden = [
        "proxy=", "humanize=True", "geoip=True", "stealth_args=False",
        "--fingerprint", "--fingerprint-platform", "--fingerprint-webrtc-ip",
        "--fingerprint-fonts-dir", "--disable-http2",
    ]
    hits = [t for t in forbidden if t in sh]
    assert not hits, f"installer must not carry runtime launch args: {hits}"


def test_installer_references_vendor_asset_paths():
    sh = _read(_INSTALL_SH)
    assert "vendor/rrweb/rrweb.min.js" in sh and "vendor/snapdom/snapdom.js" in sh


def test_cloakbrowser_isolated_to_cloak_requirements():
    for f in (_REQ_CORE, _REQ_OPT, _REQ_DEV):
        active = _active_lines(_read(f))
        assert not any("cloakbrowser" in l.lower() for l in active), f"cloakbrowser leaked into {f.name}"


def test_offline_install_path_present():
    block = _cloak_block(_read(_INSTALL_SH))
    assert "--no-index" in block and "--find-links" in block
    assert "/wheels" in block
    assert "bd_cloak" in block and "pip-wheels" in block
    assert "BD_CLOAK_PACK" in block


def test_offline_tried_before_network():
    block = _cloak_block(_read(_INSTALL_SH))
    fl = block.find("--find-links")
    online = block.find("online: installing CloakBrowser from the package index")
    assert fl != -1 and online != -1 and fl < online


def test_cloak_step_non_destructive_and_idempotent():
    sh = _read(_INSTALL_SH)
    block = _cloak_block(sh)
    assert not re.search(r"\brm\b", block)
    assert not re.search(r"\b(mv|truncate)\b", block)
    assert ("_venv_ok" in sh) and ("Reusing existing venv" in sh)


def test_explicit_availability_verdict():
    block = _cloak_block(_read(_INSTALL_SH))
    assert "CloakBrowser available" in block
    assert "CloakBrowser unavailable; browser flows will fall back to Playwright" in block


# ── rrweb / snapdom auto-vendor (new requirement) ────────────────────────────
def test_rrweb_snapdom_assets_present_in_repo():
    assert _RRWEB.is_file() and _RRWEB.stat().st_size > 0
    assert _SNAPDOM.is_file() and _SNAPDOM.stat().st_size > 0


def test_rrweb_snapdom_auto_restore_present():
    """The installer must AUTO-RESTORE missing assets, not merely check them."""
    block = _vendor_block(_read(_INSTALL_SH))
    assert "Restoring vendored DOM-capture assets" in block
    assert "_ensure_vendor_asset" in block
    assert "_restore_vendor_offline" in block and "_restore_vendor_npm" in block


def test_rrweb_snapdom_restore_targets_vendor_dir():
    block = _vendor_block(_read(_INSTALL_SH))
    assert 'bulk_downloader/vendor/rrweb/rrweb.min.js' in block
    assert 'bulk_downloader/vendor/snapdom/snapdom.js' in block
    # the ensure-calls map the pinned npm-relative source paths to those targets
    assert "rrweb/umd/rrweb.min.js" in block
    assert "@zumer/snapdom/dist/snapdom.js" in block


def test_rrweb_snapdom_pinned_versions_match_vendor_md():
    """Installer specs must match the authoritative vendor/VENDOR.md pins."""
    block = _vendor_block(_read(_INSTALL_SH))
    assert "rrweb@2.0.1" in block
    assert "@zumer/snapdom@2.12.8" in block
    md = _read(_VENDOR_MD)
    for token in ("2.0.1", "2.12.8", "umd/rrweb.min.js", "dist/snapdom.js"):
        assert token in md, f"VENDOR.md missing {token}"


def test_rrweb_snapdom_no_cdn():
    """Restore must keep assets local-vendored; never CDN runtime loading.
    (We match real URLs / CDN hosts — not the literal word "CDN", which appears
    in the script's own no-CDN assurances.)"""
    block = _vendor_block(_read(_INSTALL_SH))
    assert not re.search(r"https?://|unpkg\.com|jsdelivr|cdnjs", block, re.I), \
        "restore path must not fetch from a CDN/URL"
    # dom_recorder loads from disk and must not fetch assets over the network
    dr = _read(_DOM_RECORDER)
    assert ".read_text(" in dr and "vendor" in dr
    assert not re.search(r"(fetch|requests?\.get|urlopen)\s*\(\s*['\"]https?://", dr), \
        "dom_recorder must not fetch assets over the network"
    # dom_recorder loads from disk and explicitly does not CDN-fall-back
    dr = _read(_DOM_RECORDER)
    assert ".read_text(" in dr and "vendor" in dr
    assert not re.search(r"(fetch|requests?\.get|urlopen)\s*\(\s*['\"]https?://", dr), \
        "dom_recorder must not fetch assets over the network"


def test_rrweb_snapdom_npm_is_install_time_only():
    """npm may be used at install time only: build in a temp dir, copy one JS,
    remove the temp dir. No node_modules in the tree; no runtime npm dependency."""
    block = _vendor_block(_read(_INSTALL_SH))
    assert "mktemp -d" in block, "npm fallback must build in a throwaway temp dir"
    assert re.search(r'rm -rf "\$_vtmp"', block), "npm temp dir must be removed"
    assert re.search(r"npm install[^\n]*--prefix", block), "npm install must target the temp prefix"
    # must copy a single runtime file into vendor, not the whole node_modules
    assert not re.search(r"cp\s+-r[^\n]*node_modules[^\n]*vendor", block)
    # runtime must not depend on node_modules
    dr = _read(_DOM_RECORDER)
    assert "node_modules" not in dr, "dom_recorder must not reference node_modules"


def test_rrweb_snapdom_present_message_and_verify():
    block = _vendor_block(_read(_INSTALL_SH))
    # idempotent "do nothing when present" message + guard
    assert "rrweb/snapdom vendored assets present" in block
    assert "BD_VENDOR_REFRESH" in block
    # explicit post-restore verification (non-empty) + clear failure diagnostic
    assert re.search(r'\[ -s "\$_rrweb" \]', block) and re.search(r'\[ -s "\$_snapdom" \]', block)
    assert "STILL missing" in block and "DOM capture will be UNAVAILABLE" in block


def test_vendor_restore_non_destructive():
    """The only deletion in the vendor block is the npm throwaway temp dir."""
    block = _vendor_block(_read(_INSTALL_SH))
    rms = re.findall(r"rm\b[^\n]*", block)
    assert rms, "expected the npm temp-dir cleanup"
    for r in rms:
        assert "_vtmp" in r, f"unexpected destructive rm in vendor block: {r!r}"
    # never wipes frontend / node_modules / vendor / caches, never deletes the assets
    assert not re.search(r"rm[^\n]*(frontend|node_modules|/cache|_rrweb|_snapdom)", block)


def test_dom_recorder_unchanged_loads_local_vendored():
    dr = _read(_DOM_RECORDER)
    assert "_VENDOR" in dr and ".read_text(" in dr
    assert "rrweb.min.js" in dr and "snapdom.js" in dr
