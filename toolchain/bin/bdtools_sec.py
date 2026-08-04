#!/usr/bin/env python3
"""bdtools_sec.py -- shared library for the Wave-0 egress/security primitives.

Provides: URL network-risk classification, the outbound-fetch sink inventory
(byte-parity with bd-ssrf's definitions), the shared Finding schema, a --json
emitter, and STATE.json helpers that read guards_full_sha256 (NOT the 8-char
`guards` map -- the bdkit_common lesson).

Consumers: bd-url-classify, bd-path-scan, bd-secret-floor, bd-fetch-policy,
bd-host-guard (Wave 0) and later egress waves. Stdlib-only. Read-only.

Self-test:  python3 bdtools_sec.py --selftest
"""
import ast
import fnmatch
import glob
import ipaddress
import json
import os
import re
import sys
import zipfile
from urllib.parse import urlsplit

# ---------------------------------------------------------------- constants
def _resolve_default_work():
    """Resolve the work-tree root instead of hardcoding the sandbox path -- this
    is the shared DEFAULT_WORK behind ~most analysis tools, so fixing it here
    ports all of them at once (fix the cause, not each symptom). Order:
      1. $BD_ROOT, if it holds a bulk_downloader/ package (explicit override);
      2. a bounded walk up from this file to the repo root, marker
         bulk_downloader/__init__.py (works when toolchain/ is inside the tree,
         i.e. a git clone);
      3. the legacy sandbox default /home/claude/work (the sandbox keeps the
         toolchain in /home/claude/bin, a sibling of /home/claude/work, so the
         walk finds nothing and this fallback preserves sandbox behaviour).
    """
    env = os.environ.get("BD_ROOT")
    if env and os.path.isfile(os.path.join(env, "bulk_downloader", "__init__.py")):
        return env
    d = os.path.dirname(os.path.realpath(__file__))
    for _ in range(8):
        if os.path.isfile(os.path.join(d, "bulk_downloader", "__init__.py")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return "/home/claude/work"


DEFAULT_WORK = _resolve_default_work()
ALLOWED_SCHEMES = ("http", "https")

# Suite-wide exit contract. A tool that could not evaluate must never print a
# green verdict -- CANNOT-EVALUATE is a third state, and it is not success.
EXIT_OK = 0                 # scanned a real corpus, clean
EXIT_FINDINGS = 1           # scanned a real corpus, findings
EXIT_CANNOT_EVALUATE = 2    # absent/empty/unreadable corpus: no verdict minted

# Stable, machine-readable reasons for EXIT_CANNOT_EVALUATE, so a caller can
# branch on cause instead of string-matching stderr.
REASON_ABSENT = "ABSENT"
REASON_EMPTY = "EMPTY"
REASON_UNREADABLE = "UNREADABLE"
REASON_NO_INTERPRETER = "NO_INTERPRETER"


def _imports_pytest(exe, timeout=30):
    """Can this interpreter actually import pytest? Proven, never assumed."""
    import subprocess
    try:
        return subprocess.run([exe, "-c", "import pytest"],
                              capture_output=True, timeout=timeout).returncode == 0
    except Exception:
        return False


def resolve_test_interpreter(work=None, _probe=None):
    """The interpreter that can actually run THIS PROJECT's tests, or None.

    NOT bare "python3" (@851). In the cloud container that resolves to 3.11
    WITHOUT the project dependencies, and run_tests.py under it reports failures
    that do not exist. CLAUDE.md section 5 records precisely that incident -- "a
    full test band was measured on 3.11 and reported seven failures that did not
    exist" -- and four runner tools had the literal hardcoded, bd-band among
    them, which is the tool section 4 tells you to band with.

    NOT bare sys.executable either, which is the seductive one-line fix: that is
    whatever launched THIS tool, so `python3 toolchain/bin/bd-band` puts the
    defect straight back. The candidate order below prefers the project venv,
    and EVERY candidate is probed rather than trusted -- an interpreter that
    exists is not an interpreter that can run the suite.

    Returning None is a VERDICT, not a fallback: the caller must exit
    EXIT_CANNOT_EVALUATE. A band whose failures are interpreter artifacts is
    worse than no band, because it is read as a result.
    """
    import shutil
    work = work or DEFAULT_WORK
    probe = _probe or _imports_pytest
    cands = [os.path.join(work, "venv", "bin", "python"),
             os.path.join(DEFAULT_WORK, "venv", "bin", "python"),
             sys.executable,
             shutil.which("python3") or ""]
    seen = set()
    for c in cands:
        if not c or c in seen:
            continue
        seen.add(c)
        if os.path.exists(c) and probe(c):
            return c
    return None

# Cloud-metadata endpoints (IP + well-known hostnames)
METADATA_IPS = {"169.254.169.254", "fd00:ec2::254"}
METADATA_HOSTS = {"metadata.google.internal"}

# Name-based specials (no DNS needed)
LOOPBACK_NAMES = {"localhost"}

# Sink inventory -- MUST stay byte-identical with bd-ssrf's FETCH/GUARD
# definitions (bd-host-guard's consistency selftest asserts parity).
FETCH_RE = re.compile(
    r'\b(requests\.(?:get|post|head)|httpx\.(?:get|post|head|Client)|urlopen|\.fetch\(|http_probe|deep_detect)\b')
GUARD_RE = re.compile(
    r'is_private_ip|is_internal|metadata|169\.254|127\.0\.0\.1|ssrf|_block_|allowlist|is_public_url|validate_url')

# Secret lexicon (aligned with bd-secrets' generic-assign class)
SECRET_WORD_RE = re.compile(r'(?i)\b(secret|token|api[_-]?key|password|passwd|cookie|authorization|bearer)\b')

SKIP_DIRS = ("__pycache__", "node_modules", "venv", ".venv", ".git")
TEST_MARKERS = ("/tests/", "/test_", "conftest")

# Risk ordering for sorting (higher = worse)
RISK_ORDER = {"metadata": 6, "loopback": 5, "private": 5, "link-local": 5,
              "reserved": 4, "special": 4, "unparseable": 3, "scheme": 3,
              "userinfo": 3, "dynamic": 2, "public-hostname": 1, "public": 0}


# ---------------------------------------------------------------- url classify
_NUMERICISH_HOST = re.compile(r'^[0-9xXoO.]+$')


def _canonical_ipv4(host):
    """Return a canonical IPv4Address for lenient/encoded forms that strict
    ipaddress rejects but the OS resolver accepts (127.1, decimal 2130706433,
    0x7f.1, octal). Returns None if the host isn't an encoded IPv4. Offline."""
    if not _NUMERICISH_HOST.match(host):
        return None
    import socket
    try:
        packed = socket.inet_aton(host)   # BSD-lenient parser, no DNS
        return ipaddress.IPv4Address(packed)
    except OSError:
        return None


def _classify_ip(ip_str):
    """Classify a literal IP string. Returns (classification, blocked?)."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return ("unparseable", True)
    if ip_str in METADATA_IPS or ip.exploded in METADATA_IPS:
        return ("metadata", True)
    if ip.is_loopback:
        return ("loopback", True)
    if ip.is_link_local:
        # 169.254.169.254 already caught above; the rest of 169.254/fe80
        return ("link-local", True)
    if ip.is_private:
        # RFC1918 + ULA fc00::/7 (fd00:ec2::254 is inside ULA but metadata wins above)
        return ("private", True)
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return ("special", True)
    return ("public", False)


def classify_url(url, allow_schemes=ALLOWED_SCHEMES, resolve_dns=False):
    """Classify one URL. Returns a dict:
      {url, scheme, host, port, has_userinfo, ip_literal, classification,
       verdict ('allowed'|'blocked'), reasons: [..], resolved: [..]}
    Deterministic and offline unless resolve_dns=True.
    """
    out = {"url": url, "scheme": None, "host": None, "port": None,
           "has_userinfo": False, "ip_literal": False,
           "classification": None, "verdict": "blocked", "reasons": [],
           "resolved": []}
    raw = url.strip()
    # Detect userinfo even in schemeless garbage (SSRF obfuscation probe)
    head = raw.split("/", 3)[2] if "://" in raw else raw.split("/", 1)[0]
    if "@" in head:
        out["has_userinfo"] = True

    if "://" not in raw:
        out["classification"] = "unparseable"
        out["reasons"].append("no scheme")
        if out["has_userinfo"]:
            out["reasons"].append("userinfo present (possible SSRF obfuscation)")
        return out

    try:
        parts = urlsplit(raw)
    except ValueError as e:
        out["classification"] = "unparseable"
        out["reasons"].append(f"parse error: {e}")
        return out

    out["scheme"] = parts.scheme.lower()
    if out["scheme"] not in allow_schemes:
        out["classification"] = "scheme"
        out["reasons"].append(f"scheme '{out['scheme']}' not in {list(allow_schemes)}")
        return out

    try:
        host = parts.hostname  # lowercased, brackets stripped for IPv6
        port = parts.port
    except ValueError as e:
        out["classification"] = "unparseable"
        out["reasons"].append(f"host/port parse error: {e}")
        return out
    if not host:
        out["classification"] = "unparseable"
        out["reasons"].append("empty host")
        return out
    out["host"], out["port"] = host, port
    if parts.username is not None:
        out["has_userinfo"] = True

    # Literal IP?
    try:
        ipaddress.ip_address(host)
        out["ip_literal"] = True
    except ValueError:
        out["ip_literal"] = False

    if out["ip_literal"]:
        cls, blocked = _classify_ip(host)
        out["classification"] = cls
        if blocked:
            out["reasons"].append(f"host is a {cls} address")
        else:
            out["verdict"] = "allowed"
    else:
        # encoded-IPv4 obfuscation (127.1 / decimal / hex / octal) that strict
        # ipaddress rejects but the resolver accepts -- canonicalize + classify.
        enc = _canonical_ipv4(host)
        if enc is not None:
            cls, blocked = _classify_ip(str(enc))
            out["ip_literal"] = True
            out["classification"] = cls
            out["reasons"].append(f"encoded IPv4 {host} -> {enc} ({cls})")
            if not blocked:
                # a public encoded-IP is still suspicious obfuscation; allow the
                # canonical public IP but keep the note.
                out["verdict"] = "allowed"
        else:
            h = host.lower().rstrip(".")
            if h in LOOPBACK_NAMES or h.endswith(".localhost"):
                out["classification"] = "loopback"
                out["reasons"].append("loopback hostname")
            elif h in METADATA_HOSTS:
                out["classification"] = "metadata"
                out["reasons"].append("cloud-metadata hostname")
            elif h.endswith(".internal") or h.endswith(".local"):
                out["classification"] = "private"
                out["reasons"].append("reserved private-use TLD")
            else:
                out["classification"] = "public-hostname"
                out["verdict"] = "allowed"
                if resolve_dns:
                    import socket
                    try:
                        infos = socket.getaddrinfo(h, port or 80, proto=socket.IPPROTO_TCP)
                        addrs = sorted({i[4][0] for i in infos})
                    except OSError as e:
                        out["verdict"] = "blocked"
                        out["reasons"].append(f"DNS resolution failed: {e} (fail-closed)")
                        addrs = []
                    for a in addrs:
                        cls, blocked = _classify_ip(a)
                        out["resolved"].append({"ip": a, "classification": cls})
                        if blocked:
                            out["verdict"] = "blocked"
                            out["reasons"].append(f"resolves to {cls} address {a} (DNS-rebind aware)")

    # userinfo-to-non-public: fail closed unless the host is provably public IP
    if out["has_userinfo"] and not (out["ip_literal"] and out["classification"] == "public"):
        out["verdict"] = "blocked"
        out["reasons"].append("userinfo present with non-provably-public host")
    elif out["has_userinfo"]:
        out["reasons"].append("userinfo present (flag)")
    return out


# ---------------------------------------------------------------- sink inventory
def enumerate_fetch_sinks(work=DEFAULT_WORK, strict=False, label="--work"):
    """Per-file outbound-fetch inventory, same definitions as bd-ssrf.
    Returns list of dicts: {rel, path, fetches:[(lineno, line)], guarded:bool}
    sorted like bd-ssrf (guarded last, most fetches first).

    strict=True guards the effective scan root. This globs work/bulk_downloader,
    and a glob over an absent directory yields nothing -- so consumers inherited
    "0 fetch sinks found", which reads as "no SSRF surface": the F-1 class wearing
    a security-relevant hat."""
    if strict:
        require_corpus(os.path.join(work, "bulk_downloader"), min_files=1,
                       label=label, patterns=("*.py",))
    rows = []
    for f in glob.glob(os.path.join(work, "bulk_downloader", "**", "*.py"), recursive=True):
        try:
            txt = open(f, errors="ignore").read()
        except Exception:  # why: this item is unreadable/unparseable; skip it, the loop continues over the rest
            continue
        fetches = [(i, l) for i, l in enumerate(txt.splitlines(), 1) if FETCH_RE.search(l)]
        if not fetches:
            continue
        guarded = bool(GUARD_RE.search(txt))
        rows.append({"rel": os.path.relpath(f, work), "path": f,
                     "fetches": fetches, "guarded": guarded, "text": txt})
    rows.sort(key=lambda r: (r["guarded"], -len(r["fetches"])))
    return rows


CORPUS_FLAGS = ("--tree", "--work", "--home", "--scan", "--corpus", "--root", "--src")
_CORPUS_MARKER_RE = re.compile(r'#\s*lint:\s*corpus-guard-ok')
_CORPUS_ARG_TEXT_RE = re.compile(
    r'add_argument\(\s*["\'](--tree|--work|--home|--scan|--corpus|--root|--src)["\']')
_CORPUS_GUARD_TEXT_RE = re.compile(
    r'require_corpus|require_source_tree|require_bundle|strict\s*=\s*True|lint:\s*corpus-guard-ok')

BUNDLE_MARKERS = ("BulkDownloader_v3_66_*.zip", "BulkDL_next_session_*.zip",
                  "STATE.json", "bdsuite_v3_66_*.zip")

_VERSION_RE = re.compile(r"__version__\s*=\s*['\"]([0-9][0-9.]*)['\"]")


def is_analysis_tool(src):
    """The suite's own classification: a tool opts into the analysis convention
    by building on the shared libs."""
    return ("bdtools_sec" in src or "bdtools_taint" in src)


def corpus_profile(src):
    """(declares, guarded, flags, parsed) -- ONE definition, every consumer.

    AST, not grep: a regex over raw text matches these flag names inside a tool's
    own STRING LITERALS, which is how the first cut of this check reported
    bd-tool-lint itself as a corpus tool (its fixtures contain the *text*
    add_argument("--tree")). parsed=False means WE COULD NOT SEE -- report that,
    never a clean result over a failed probe.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return (bool(_CORPUS_ARG_TEXT_RE.search(src)),
                bool(_CORPUS_GUARD_TEXT_RE.search(src)), [], False)
    declares, guarded, flags = False, False, []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fname = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        if fname == "add_argument":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and arg.value in CORPUS_FLAGS:
                    declares = True
                    if arg.value not in flags:
                        flags.append(arg.value)
        # The named predicates ARE the guard: both call require_corpus. A detector
        # that only knows the raw helper would report a properly guarded tool as
        # unguarded debt -- the checker blind to the very fix it demanded.
        if fname in ("require_corpus", "require_source_tree", "require_bundle"):
            guarded = True
        for kw in node.keywords:
            if (kw.arg == "strict" and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True):
                guarded = True
    if _CORPUS_MARKER_RE.search(src):
        guarded = True
    return (declares, guarded, flags, True)


def require_source_tree(work=DEFAULT_WORK, label="--work/--tree"):
    """'Is this actually a BulkDownloader work tree?' -- one predicate, one place.
    The tree must hold a bulk_downloader/ package with at least one .py. A dir
    that exists but does not is not a lenient input; it is an unscannable one."""
    return require_corpus(os.path.join(work, "bulk_downloader"), min_files=1,
                          label=label, patterns=("*.py",))


def require_bundle(home="/home/claude", label="--home"):
    """The other root: the bundle (release zips + STATE) a composer derives its
    reference from. The predicate asserts what a bundle IS -- min_files=1 over any
    file let a directory containing one readme.txt pass as a release bundle, and
    thirteen tools then rendered trust scores and deploy proofs against it."""
    return require_corpus(home, min_files=1, label=label, patterns=BUNDLE_MARKERS)


def tool_json(tool, *args, **kw):
    """Run a sibling tool for its --json -> (data, err). NEVER a bare None.

    I-3: five tools carried a private copy whose body was `except Exception:
    return None`, and every caller wrote `... or {}`. A TIMED-OUT dependency was
    indistinguishable from one reporting nothing, and a crashed sub-tool silently
    zeroed the factors it fed: the score fell and no one learned a tool had died.
    """
    import subprocess
    timeout = kw.get("timeout", 60)
    exe = os.path.join(os.path.dirname(os.path.realpath(__file__)), tool)
    if not os.path.isfile(exe):
        return None, "not present: %s" % tool
    try:
        r = subprocess.run([sys.executable, exe, "--json"] + list(args),
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "%s timed out after %ss" % (tool, timeout)
    except OSError as exc:
        return None, "%s could not run: %s" % (tool, exc)
    if not (r.stdout or "").strip():
        return None, "%s produced no --json (rc=%d)" % (tool, r.returncode)
    try:
        return json.loads(r.stdout), None
    except ValueError as exc:
        return None, "%s emitted unparseable --json (rc=%d): %s" % (tool, r.returncode, exc)


def detect_tree_version(root):
    """The version a TREE declares about ITSELF. None if it declares nothing."""
    best = None
    pkg = os.path.join(root, "bulk_downloader")
    for dirpath, dirs, names in os.walk(pkg if os.path.isdir(pkg) else root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            if not name.endswith(".py"):
                continue
            try:
                with open(os.path.join(dirpath, name), errors="ignore") as fh:
                    for line in fh:
                        m = _VERSION_RE.search(line)
                        if m:
                            try:
                                v = tuple(int(x) for x in m.group(1).split("."))
                            except ValueError:
                                continue
                            if best is None or v > best:
                                best = v
            except OSError:
                continue
    return ".".join(str(x) for x in best) if best else None


def assert_same_tree(home, work, label="verdict"):
    """A verdict assembled from two different trees is not a verdict.

    Composers take BOTH --home (the bundle: STATE, other tools' output) and
    --work (the source). Nothing checked they described the same thing, so a
    v9.9.9 work tree beside the 728 bundle produced a confident "trust score
    v3.66.728: 65/100" -- a number about one tree, labelled with the version of
    another. Corpus guards do not catch this: BOTH trees exist. Only comparing
    them does. The bundle's version comes from STATE (key: built_version), never
    from globbing home for __version__ -- that reads whatever a vendored wheel
    happens to declare.
    """
    st = load_state(home) or {}
    hv = st.get("built_version") or st.get("live_version") or st.get("version")
    wv = detect_tree_version(work)
    if wv is None:
        sys.stderr.write("CANNOT-EVALUATE %s: reason=%s (--work %s declares no "
                         "__version__)\n" % (label, REASON_EMPTY, work))
        sys.exit(EXIT_CANNOT_EVALUATE)
    if hv is None:
        sys.stderr.write("CANNOT-EVALUATE %s: reason=%s (--home %s has no version "
                         "reference)\n" % (label, REASON_EMPTY, home))
        sys.exit(EXIT_CANNOT_EVALUATE)
    if str(hv) != str(wv):
        sys.stderr.write("CANNOT-EVALUATE %s: reason=CROSS_TREE (--home is %s, "
                         "--work is %s -- a verdict blended from two trees is not "
                         "a verdict)\n" % (label, hv, wv))
        sys.exit(EXIT_CANNOT_EVALUATE)
    return str(wv)


def require_corpus(path, min_files=1, label="--tree", patterns=None):
    """Refuse to mint a verdict without a real corpus. (The F-1 precondition.)

    Exits EXIT_CANNOT_EVALUATE unless `path` is a readable directory holding at
    least `min_files` matching regular files. Returns the enumerated file list,
    so the caller scans exactly the validated set and the checked set cannot
    drift from the scanned set.

    patterns: fnmatch globs (e.g. ("*.py",)), or None = any regular file.

    Why this exists: iter_py() globs, and a glob over an absent directory
    yields nothing -- so every consumer inherited "zero files scanned, zero
    findings, green". A check whose search set structurally excludes the thing
    being asked about reports clean truthfully and is useless. Unknown is a
    third state, and it fails.
    """
    root = os.path.realpath(path)
    if not os.path.isdir(root):
        sys.stderr.write("CANNOT-EVALUATE %s %s: reason=%s (no such directory)\n"
                         % (label, path, REASON_ABSENT))
        sys.exit(EXIT_CANNOT_EVALUATE)
    files = []
    try:
        for dirpath, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in names:
                if patterns is None or any(fnmatch.fnmatch(name, p) for p in patterns):
                    fp = os.path.join(dirpath, name)
                    if os.path.isfile(fp):
                        files.append(fp)
    except OSError as exc:
        sys.stderr.write("CANNOT-EVALUATE %s %s: reason=%s (%s)\n"
                         % (label, path, REASON_UNREADABLE, exc))
        sys.exit(EXIT_CANNOT_EVALUATE)
    if len(files) < min_files:
        sys.stderr.write("CANNOT-EVALUATE %s %s: reason=%s (%d file(s), need >= %d)\n"
                         % (label, path, REASON_EMPTY, len(files), min_files))
        sys.exit(EXIT_CANNOT_EVALUATE)
    files.sort()
    return files


def _iter_py(work, subdir, include_tests):
    for f in glob.glob(os.path.join(work, subdir, "**", "*.py"), recursive=True):
        norm = f.replace("\\", "/")
        if any(s in norm for s in SKIP_DIRS):
            continue
        if not include_tests and any(m in norm for m in TEST_MARKERS):
            continue
        try:
            txt = open(f, errors="ignore").read()
        except Exception:  # why: this item is unreadable/unparseable; skip it, the loop continues over the rest
            continue
        yield f, os.path.relpath(f, work), txt


def iter_py(work=DEFAULT_WORK, subdir="bulk_downloader", include_tests=False,
            strict=False, label="--work"):
    """Yield (path, rel, text) for source .py files, skipping vendor dirs.

    strict=True guards the EFFECTIVE scan root -- os.path.join(work, subdir) --
    not merely `work`. That is deliberate: guarding `work` alone would pass for
    a directory that exists but contains no bulk_downloader/, and the scan would
    still walk nothing and still report green. The guarded set and the scanned
    set must be the same set, or the guard has the same blind spot it was
    written to close.

    The check is eager (it fires at call time, not on first next()), so a caller
    that builds the iterator but never advances it still cannot slip past.
    """
    if strict:
        require_corpus(os.path.join(work, subdir), min_files=1, label=label,
                       patterns=("*.py",))
    return _iter_py(work, subdir, include_tests)


# ---------------------------------------------------------------- Finding schema
def finding(tool, rule, severity, file, line, message, **extra):
    """The shared Finding shape every Wave-0+ tool emits."""
    assert severity in ("info", "low", "medium", "high"), severity
    d = {"tool": tool, "rule": rule, "severity": severity,
         "file": file, "line": line, "message": message}
    if extra:
        d["extra"] = extra
    return d


def emit(obj, as_json=False, stream=None):
    """Print obj as JSON when requested; otherwise caller does pretty text."""
    stream = stream or sys.stdout
    if as_json:
        json.dump(obj, stream, indent=2, sort_keys=True, default=str)
        stream.write("\n")
        return True
    return False


# ---------------------------------------------------------------- STATE helpers
def find_state_zip(home="/home/claude"):
    """Newest BulkDL_next_session_*.zip carrying STATE.json."""
    cands = sorted(glob.glob(os.path.join(home, "BulkDL_next_session_*.zip")),
                   key=os.path.getmtime, reverse=True)
    return cands[0] if cands else None


def load_state(home="/home/claude"):
    """Load STATE.json from disk (nextsess dir) or the newest version pack."""
    for p in (os.path.join(home, "nextsess", "STATE.json"),
              os.path.join(home, "STATE.json")):
        if os.path.exists(p):
            return json.load(open(p))
    zp = find_state_zip(home)
    if zp:
        with zipfile.ZipFile(zp) as z:
            return json.loads(z.read("STATE.json"))
    return None


# ---------------------------------------------------------------- guard set (R1)
# ONE definition of "which files are release guards", suite-wide.
#
# @807 (MIRROR RESIDUAL R1): this list was RE-TYPED as a literal in bd-cut
# (GUARDS) and bd-band-derive (GUARD_BASES) with zero STATE reads in either.
# Declaring a new guard set is the SUPPORTED operator flow and STATE.json
# carries `guards_full_sha256` as the authority -- so a declared change would
# silently not reach the band deriver or the cut driver, and both would go on
# guarding yesterday's set. Not theoretical: CAP-ROBUST (tools/capture_session.py),
# Cut 3.2 (dom_recorder.py) and the guard-SHA-gated HUD each require a declared
# SHA, and R1 fires on the first cut that moves one.
#
# The literal remains as a FALLBACK, because an empty guard set is a gate that
# cannot fire -- but the fallback is VISIBLE via guard_source(), since a silent
# fallback is exactly how the mirror drifted in the first place.
_GUARD_FALLBACK = (
    "bulk_downloader/extraction_core.py",
    "bulk_downloader/session_capture.py",
    "tools/capture_session.py",
    "bulk_downloader/dom_capture.py",
    "bulk_downloader/dom_recorder.py",
    "bulk_downloader/capture_bodies.py",
    "tools/build_release.py",
)


def _guard_map(home="/home/claude"):
    """(guard_paths_tuple, source_string). Never raises; never returns empty."""
    try:
        st = load_state(home)
        if st:
            g = st.get("guards_full_sha256")
            if isinstance(g, dict) and g:
                return tuple(sorted(g.keys())), "STATE (guards_full_sha256)"
    except Exception as e:  # why: surfaced in the source string, never swallowed
        return _GUARD_FALLBACK, f"fallback literal (STATE unreadable: {type(e).__name__})"
    return _GUARD_FALLBACK, "fallback literal (no STATE guards_full_sha256 found)"


def guard_paths(home="/home/claude"):
    """Release-guard file paths, DERIVED from STATE when it is readable."""
    return _guard_map(home)[0]


def guard_basenames(home="/home/claude"):
    """Just the basenames -- what a changed-file check compares against."""
    return tuple(sorted({os.path.basename(p) for p in guard_paths(home)}))


def guard_source(home="/home/claude"):
    """Where the guard set came from. A caller that wants to SAY SO can."""
    return _guard_map(home)[1]


def state_guard_shas(state):
    """Return the FULL sha256 guard map. Never falls back to the 8-char
    `guards` map silently -- that was the bdkit_common false-alarm bug."""
    full = state.get("guards_full_sha256")
    if not isinstance(full, dict) or not full:
        raise KeyError("STATE.json has no guards_full_sha256 map "
                       "(refusing to fall back to the short `guards` map)")
    return full


# ---------------------------------------------------------------- endpoint catalog
_CATALOG_ROW = re.compile(
    r'^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(\S+)\s+CSRF:\s+(yes|no)\b(?:\s+[-\u2014]?\s*(.*))?$')
MUTATING = ("POST", "PUT", "PATCH", "DELETE")


def find_catalog(work=DEFAULT_WORK):
    for p in (os.path.join(work, "ENDPOINT_CATALOG.md"),
              os.path.join(work, "docs", "ENDPOINT_CATALOG.md")):
        if os.path.exists(p):
            return p
    return None


def parse_endpoint_catalog(work=DEFAULT_WORK):
    """Parse ENDPOINT_CATALOG.md -> list of {method, path, csrf(bool), desc}.
    The catalog is the authoritative route+CSRF surface (kept in sync by
    tests/test_endpoint_catalog_in_sync.py), so audits read it instead of
    re-deriving routes."""
    p = find_catalog(work)
    if not p:
        return []
    rows = []
    for line in open(p, errors="ignore"):
        m = _CATALOG_ROW.match(line.strip())
        if not m:
            continue
        rows.append({"method": m.group(1), "path": m.group(2),
                     "csrf": m.group(3) == "yes", "desc": (m.group(4) or "").strip()})
    return rows


# ---------------------------------------------------------------- dev-gate detection
DEV_GATE = re.compile(
    r'is_dev_mode\s*\(|_dev_mode_guard|BD_DEV_MODE|_dt\.is_dev_mode|dev_tools\.is_dev_mode')


def module_map(work=DEFAULT_WORK, strict=False, label="--work"):
    """{relative-path: text} for all source modules (no tests/vendor)."""
    return {rel: txt for _p, rel, txt in iter_py(work, strict=strict, label=label)}


# ---------------------------------------------------------------- selftest
def _selftest():
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        tag = "PASS" if cond else "FAIL"
        print(f"  {tag}  {name}" + (f"  ({detail})" if detail and not cond else ""))
        ok = ok and cond

    # 1. URL classification negative-control table (the load-bearing one)
    table = [
        ("http://169.254.169.254/latest/meta-data", "blocked", "metadata"),
        ("http://[fd00:ec2::254]/", "blocked", "metadata"),
        ("http://127.0.0.1:5555/api", "blocked", "loopback"),
        ("http://[::1]/", "blocked", "loopback"),
        ("http://localhost:5555/", "blocked", "loopback"),
        ("http://10.0.70.20/", "blocked", "private"),        # stash!
        ("http://192.168.1.1/", "blocked", "private"),
        ("http://[fc00::1]/", "blocked", "private"),
        ("http://169.254.1.1/", "blocked", "link-local"),
        ("http://metadata.google.internal/", "blocked", "metadata"),
        ("http://svc.internal/", "blocked", "private"),
        ("ftp://example.com/x", "blocked", "scheme"),
        ("https://example.com/", "allowed", "public-hostname"),
        ("https://93.184.216.34/", "allowed", "public"),
    ]
    for url, verdict, cls in table:
        r = classify_url(url)
        check(f"classify {url}", r["verdict"] == verdict and r["classification"] == cls,
              f"got {r['verdict']}/{r['classification']}")
    # userinfo obfuscation rows
    r = classify_url("http://user:pass@evil@internal/")
    check("userinfo flag (obfuscated)", r["has_userinfo"] and r["verdict"] == "blocked",
          str(r))
    r = classify_url("user:pass@evil@internal")
    check("userinfo flag (schemeless)", r["has_userinfo"] and r["verdict"] == "blocked",
          str(r))
    r = classify_url("https://alice@example.com/")
    check("userinfo on public hostname blocked (fail-closed)", r["verdict"] == "blocked", str(r))

    # 2. Determinism/offline: classify runs with no DNS by default
    r = classify_url("https://definitely-not-a-real-host-xyz.example/")
    check("offline default (no DNS attempted)", r["verdict"] == "allowed"
          and r["resolved"] == [], str(r))

    # 3. Finding schema
    f = finding("bd-x", "BD-TEST", "medium", "a.py", 3, "msg", hint="y")
    check("finding shape", set(f) == {"tool", "rule", "severity", "file", "line",
                                      "message", "extra"})
    try:
        finding("bd-x", "R", "urgent", "a.py", 1, "m")
        check("finding rejects bad severity", False)
    except AssertionError:
        check("finding rejects bad severity", True)

    # 4. STATE schema -- REAL STATE.json: must read guards_full_sha256, and the
    #    values must be full 64-char shas (the short map would be 8).
    st = load_state()
    if st is None:
        check("STATE.json loadable", False, "no version pack found")
    else:
        check("STATE.json loadable", True)
        try:
            full = state_guard_shas(st)
            lens = {len(v) for v in full.values()}
            check("guard shas are FULL sha256 (64 hex)", lens == {64}, str(lens))
            check("7 guards present", len(full) == 7, str(len(full)))
        except KeyError as e:
            check("guards_full_sha256 present", False, str(e))
        # prove the trap exists: the short map is ALSO present and would differ
        short = st.get("guards")
        check("short `guards` trap present in real schema",
              isinstance(short, dict) and any(len(str(v)) < 64 for v in short.values()))

    # 5. Sink inventory parity smoke (counts stable across two runs)
    a = enumerate_fetch_sinks()
    b = enumerate_fetch_sinks()
    check("sink inventory deterministic", [r["rel"] for r in a] == [r["rel"] for r in b]
          and len(a) > 0, f"{len(a)} files")

    # 6. Endpoint catalog parses with mutating CSRF-classified routes
    cat = parse_endpoint_catalog()
    mut = [r for r in cat if r["method"] in MUTATING]
    check("endpoint catalog parses (rows > 0)", len(cat) > 0, f"{len(cat)} rows")
    check("catalog has mutating routes", len(mut) > 0, f"{len(mut)} mutating")
    check("catalog CSRF is boolean", all(isinstance(r["csrf"], bool) for r in cat))

    # 7. dev-gate regex matches the real guard
    mm = module_map()
    gated = sum(1 for t in mm.values() if DEV_GATE.search(t))
    check("dev-gate detected in tree", gated > 0, f"{gated} modules")

    print(("SELFTEST PASS" if ok else "SELFTEST FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print(__doc__)
