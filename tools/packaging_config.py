"""packaging_config -- ONE source of truth for freezing BulkDownloader.

MOD-7 cut 1. The packaging inputs (entry point, data roots, hidden imports,
excludes) live here so both the pyinstaller path and the Nuitka path (cut 2)
read the SAME list, and so the list is DERIVED from the live tree rather than
hand-maintained and silently stale.

Why derive rather than list: a frozen binary's failure mode is a check whose
denominator excludes the thing it needs. A static import follower cannot see
`importlib.import_module("literal")` or `spec_from_file_location`, so those
targets must be declared -- but declaring them by hand means the next dynamic
dispatch site added to the app is invisible to the packager and ImportErrors
only when a user runs the binary. So hidden_imports is COMPUTED from source at
import time (import_module literals + the provider dispatch submodules), and the
790 test suite re-derives the same set and asserts the config is a superset. If
a new dynamic-import site appears, the config picks it up and the test still
passes; if the derivation regresses, the test fails in-band, not on a user's
machine.

Consumed by tools/build_nuitka.py (the command builder). This module writes
nothing and launches nothing; it is pure data + derivation.
"""
import os
import re

# repo root = parent of tools/
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The entry point systemd actually launches (install_service.sh ExecStart=
# ${PYEXE} ${APP_DIR}/downloader_ui.py). Freeze THIS, not app.py -- app.py is
# the Flask factory, downloader_ui.py is the process.
ENTRY_POINT = "downloader_ui.py"

PRODUCT_NAME = "BulkDownloader"

# Non-.py roots the running app serves or reads. A missing one is a runtime
# 404/500 in the frozen binary, invisible until launch. Each entry is
# (src_rel, dest_rel); dest mirrors src so in-package paths resolve unchanged.
DATA_DIRS = [
    ("bulk_downloader/static", "bulk_downloader/static"),
    ("bulk_downloader/locales", "bulk_downloader/locales"),
    ("bulk_downloader/vendor", "bulk_downloader/vendor"),
    ("frontend/dist", "frontend/dist"),
]

# Modules a static follower would exclude. Third-party dynamic backends the app
# resolves by name (curl_cffi's impersonation, cloakbrowser, keyring's platform
# backend) -- declared because the follower rooted at downloader_ui.py may not
# reach them, and their absence is a silent runtime failure, not a build error.
_EXTRA_HIDDEN = [
    "bulk_downloader",  # the package itself (frozen as a package)
    "curl_cffi",
    "cloakbrowser",
    "keyring.backends",
]

# Heavyweight / build-time-only trees that must NOT bloat the binary. Excluding
# tests keeps the frozen artifact from carrying the suite; excluding pyinstaller
# avoids a packager depending on the other packager.
EXCLUDES = [
    "tests",
    "PyInstaller",
    "pyinstaller",
    "pytest",
    "_pytest",
    # v3.66.793 (MOD-7 adopt): scipy + pywt are transitive-only (BD imports
    # neither directly -- they arrive via the perceptual-dedup / videohash
    # path). Excluding them was proven safe on the build host (frozen binary
    # correctness held: / + /api/health PASS) and trims ~127 MB off the
    # standalone build. If BD ever adds a DIRECT import of either,
    # test_v3_66_793 fails and this exclude must be re-decided.
    "scipy",
    "pywt",
]

_IMPORT_MODULE_RE = re.compile(r"import_module\(\s*['\"]([A-Za-z0-9_.]+)['\"]")


def _derive_dynamic_imports(repo=REPO):
    """DERIVED, not listed: every bulk_downloader.* target reached via
    importlib.import_module("literal"), plus every provider_resolve_impl
    submodule (reached via name dispatch). Recomputed from source at import
    time so a new dynamic-import site cannot silently escape the bundle."""
    found = set()
    pkg = os.path.join(repo, "bulk_downloader")
    if os.path.isdir(pkg):
        for dp, dns, fns in os.walk(pkg):
            dns[:] = [d for d in dns if d != "__pycache__"]
            for fn in fns:
                if not fn.endswith(".py"):
                    continue
                try:
                    body = open(os.path.join(dp, fn), errors="replace").read()
                except OSError:
                    continue
                for m in _IMPORT_MODULE_RE.finditer(body):
                    t = m.group(1)
                    # module-literals only; skip relative "...app" fragments
                    if t.startswith("bulk_downloader") and ".." not in t:
                        found.add(t)
        pri = os.path.join(pkg, "provider_resolve_impl")
        if os.path.isdir(pri):
            for fn in os.listdir(pri):
                if fn.endswith(".py") and fn != "__init__.py":
                    found.add("bulk_downloader.provider_resolve_impl.%s" % fn[:-3])
    return sorted(found)


def hidden_imports(repo=REPO):
    """The full hidden-import list: derived dynamic targets UNION the declared
    third-party backends. Sorted + de-duplicated."""
    return sorted(set(_derive_dynamic_imports(repo)) | set(_EXTRA_HIDDEN))


CONFIG = {
    "entry_point": ENTRY_POINT,
    "product_name": PRODUCT_NAME,
    "data_dirs": DATA_DIRS,
    "hidden_imports": hidden_imports(),
    "excludes": EXCLUDES,
    "repo": REPO,
}


def summary():
    """Human-readable one-screen dump (for --show / manual inspection)."""
    lines = ["packaging_config -- BulkDownloader freeze inputs",
             "  entry_point : %s" % CONFIG["entry_point"],
             "  product     : %s" % CONFIG["product_name"],
             "  data_dirs   : %d" % len(CONFIG["data_dirs"])]
    for s, d in CONFIG["data_dirs"]:
        lines.append("      %s -> %s" % (s, d))
    lines.append("  hidden      : %d (derived + declared)" % len(CONFIG["hidden_imports"]))
    for h in CONFIG["hidden_imports"]:
        lines.append("      %s" % h)
    lines.append("  excludes    : %s" % ", ".join(CONFIG["excludes"]))
    return "\n".join(lines)


if __name__ == "__main__":
    print(summary())
