"""Row 703 gate 1 -- A ``proxy=`` BESIDE ``transport=`` MAKES THE GUARD INERT.

WHY THIS GATE EXISTS.  ``bulk_downloader/ssrf_transport.py`` installs a guarded
transport on all 53 httpx client constructions in the package, and
``tools/ssrf_client_census.py`` reports ``OK: 53 constructions ... all pinned``.
Nine of those constructions ALSO pass ``proxy=``.  In httpx 0.28.1 a client
built with ``proxy=`` mounts its own ``HTTPTransport`` under an ``all://``
pattern, and every request matching that pattern is served by the MOUNT -- the
transport handed to ``transport=`` is never consulted.  The guard is installed
and INERT, and the census still prints OK.

THAT IS NOT A DEFECT AT THESE NINE SITES.  Every one of them proxies through a
VPN/SOCKS tunnel (``_eff_proxy`` / ``get_socks_url_for_site`` / ``_cp_proxy``),
and ``ssrf_transport``'s own module docstring rules that a proxied request is
resolved AT the proxy on purpose: "pinning a proxied request locally would move
the lookup off the tunnel".  The defect the bounce named is that NOTHING IN THE
TREE SAYS SO.  A tenth construction that adds ``proxy=`` beside ``transport=``
-- an ordinary later edit -- inherits an inert guard silently, and the census,
the 200 tests row 703 declares and the 939 shard gate all stay green.

WHAT THIS GATE ASSERTS, AND WHAT IT DELIBERATELY DOES NOT.
  * It does NOT change the seam and does NOT ask any of the nine to change.
  * It PINS THE SET.  A new proxied construction is RED until it is declared
    here with a reason.  A declared entry that disappears is RED too, so the
    registry cannot rot into a list of ghosts.
  * It re-derives the population from the tree with its OWN binding resolver
    and CROSS-CHECKS that against ``ssrf_client_census``.  Two independent
    scans that could have disagreed.
  * It asserts the httpx behaviour itself, in process.  If an httpx upgrade
    stops shadowing, this file goes RED and the whole premise gets re-read
    rather than silently surviving.

THREE OUTCOMES.  A file that does not parse is COULD NOT LOOK -- raised, never
folded into a pass.  An empty population is COULD NOT LOOK, never OK.
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys
from typing import Dict, List, Set, Tuple

import httpx
import pytest

# Repo-wide: the subject is every httpx client construction in the package, so
# this gate needs a CI shard of its own (tests/test_v3_66_939_ci_gate_shards_
# cover_every_gate.py enforces that for a repo-wide scope).
BD_GATE_SCOPE = "repo-wide"

REPO = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = "bulk_downloader"

# Keywords that mount a transport of httpx's own choosing and therefore shadow
# anything handed to transport=.  ``mounts=`` is the explicit form of the same
# thing; ``proxies=`` was httpx's pre-0.26 spelling and is kept so a revert to
# the old name does not walk past this gate.
SHADOWING_KEYWORDS = ("proxy", "proxies", "mounts")


# ---------------------------------------------------------------------------
# THE REGISTRY.  file:line -> why this site proxies on purpose.
# Every entry is a construction that passes BOTH transport= and a shadowing
# keyword.  The guarded transport at these sites is INERT BY DESIGN; the
# refusal that still applies is named per entry.
# ---------------------------------------------------------------------------
PROXY_SHADOWED: Dict[str, str] = {}

# Of the registry, the sites whose DECLARED policy is the strict one.  At these
# five, PUBLIC_ONLY's "every resolved address must classify public" refusal is
# not enforced by the transport when a proxy is configured; the reason column
# above names what carries the refusal instead.  Split out so that a site
# MOVING between the two halves is a visible change, not a silent one.
PUBLIC_ONLY_AND_PROXIED: Set[str] = set()

# THE RESIDUAL, STATED SO IT IS NOT INHERITED AS A BLIND SPOT.  A keyword scan
# cannot see a proxy arriving through **kwargs.  These are every construction
# in the package that takes **kwargs at all.  None of them is proxied today --
# FOUND NONE, measured, not assumed -- but a proxy could be introduced into one
# without touching its call site, and this gate would not see it.  A NEW
# **kwargs construction is RED, which forces that judgement to be made once.
# A proxy passed POSITIONALLY is not possible: httpx.Client takes no positional
# parameters, and the scan asserts zero positional constructions below.
STAR_KWARGS_CONSTRUCTIONS: Set[str] = {
    "bulk_downloader/runner_telemetry.py:301",
    "bulk_downloader/runner_transport.py:2209",
    "bulk_downloader/runner_transport.py:2576",
    "bulk_downloader/runner_transport.py:2807",
}


# ---------------------------------------------------------------------------
# An independent binding resolver.  Deliberately NOT the census's: the point of
# a cross-check is that the two could disagree.
# ---------------------------------------------------------------------------
class _Constructions(ast.NodeVisitor):
    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.module_aliases: Set[str] = set()      # names bound to the httpx module
        self.class_aliases: Dict[str, str] = {}    # local name -> Client / AsyncClient
        self.rows: List[dict] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "httpx":
                self.module_aliases.add(alias.asname or "httpx")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "httpx":
            for alias in node.names:
                if alias.name in ("Client", "AsyncClient"):
                    self.class_aliases[alias.asname or alias.name] = alias.name
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        # One level of rebinding: X = httpx.Client, or X = Client.
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
            if (isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name)
                    and value.value.id in self.module_aliases
                    and value.attr in ("Client", "AsyncClient")):
                self.class_aliases[target] = value.attr
            elif isinstance(value, ast.Name) and value.id in self.class_aliases:
                self.class_aliases[target] = self.class_aliases[value.id]
        self.generic_visit(node)

    def _kind(self, func: ast.expr):
        if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.value.id in self.module_aliases
                and func.attr in ("Client", "AsyncClient")):
            return func.attr
        if isinstance(func, ast.Name) and func.id in self.class_aliases:
            return self.class_aliases[func.id]
        return None

    def visit_Call(self, node: ast.Call) -> None:
        kind = self._kind(node.func)
        if kind is not None:
            keywords = {kw.arg for kw in node.keywords if kw.arg}
            self.rows.append({
                "where": f"{self.rel}:{node.lineno}",
                "kind": kind,
                "keywords": keywords,
                "positional": len(node.args),
                "star_kwargs": any(kw.arg is None for kw in node.keywords),
                "shadowing": sorted(keywords & set(SHADOWING_KEYWORDS)),
                "has_transport": "transport" in keywords,
            })
        self.generic_visit(node)


def _scan_source(rel: str, source: str) -> List[dict]:
    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError as exc:                       # COULD NOT LOOK
        raise AssertionError(f"COULD NOT LOOK: {rel} does not parse: {exc}") from exc
    visitor = _Constructions(rel)
    visitor.visit(tree)
    return visitor.rows


def _scan_tree(root: pathlib.Path, package: str = PACKAGE) -> List[dict]:
    """Every httpx client construction under ``root/<package>``, from the tree."""
    rows: List[dict] = []
    base = root / package
    if not base.is_dir():                            # COULD NOT LOOK
        raise AssertionError(f"COULD NOT LOOK: {base} is not a directory")
    for path in sorted(base.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        rows.extend(_scan_source(rel, path.read_text(encoding="utf-8", errors="strict")))
    return rows


def _tracked_scan() -> List[dict]:
    """The scan restricted to git-TRACKED files, so an untracked scratch file
    in a developer's checkout cannot turn this gate red or green."""
    proc = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z", f"{PACKAGE}/*.py", f"{PACKAGE}/**/*.py"],
        capture_output=True, text=True)
    if proc.returncode != 0:                         # COULD NOT LOOK
        raise AssertionError(
            f"COULD NOT LOOK: git ls-files failed in {REPO}: {proc.stderr.strip()}")
    tracked = {p for p in proc.stdout.split("\0") if p}
    if not tracked:                                  # COULD NOT LOOK, never OK
        raise AssertionError(
            f"COULD NOT LOOK: git tracks zero {PACKAGE}/**/*.py under {REPO}")
    return [r for r in _scan_tree(REPO) if r["where"].rsplit(":", 1)[0] in tracked]


@pytest.fixture(scope="module")
def scan() -> List[dict]:
    return _tracked_scan()


# ---------------------------------------------------------------------------
# 1. The httpx behaviour this whole gate rests on, asserted in process.
# ---------------------------------------------------------------------------
class _MarkerTransport(httpx.BaseTransport):
    """Records whether httpx ever consulted it."""

    def __init__(self) -> None:
        self.consulted = False

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.consulted = True
        return httpx.Response(204, request=request)


def test_httpx_consults_an_installed_transport_when_no_proxy_is_passed():
    """POSITIVE CONTROL for the test below: without a proxy the marker fires."""
    marker = _MarkerTransport()
    with httpx.Client(transport=marker) as client:
        response = client.get("http://example.invalid/probe")
    assert marker.consulted, (
        "the marker transport was NOT consulted even without a proxy -- this probe "
        "cannot say YES, so its NO below would prove nothing")
    assert response.status_code == 204


def test_a_proxy_shadows_the_installed_transport_in_this_httpx():
    """The defect the registry exists for.  RED if httpx stops shadowing, which
    is exactly when every entry in the registry needs re-reading.

    Asserted on httpx's own ROUTING DECISION, not by sending a request: this
    gate opens no socket, so it stays honest under the socket recorder and
    cannot be turned green by a firewall."""
    marker = _MarkerTransport()
    client = httpx.Client(transport=marker, proxy="http://127.0.0.1:1/")
    try:
        assert client._transport is marker, (
            "httpx no longer stores the installed transport where this gate looks; "
            "the shadow check below is unreliable -- re-read this gate")
        assert client._mounts, (
            f"httpx {httpx.__version__} mounted NOTHING for proxy= -- the shadow this "
            "gate and the registry describe may be gone. Re-read ssrf_transport.py's "
            "proxy ruling and every PROXY_SHADOWED entry before deleting anything.")
        routed = client._transport_for_url(httpx.URL("http://example.invalid/probe"))
        assert routed is not marker, (
            f"httpx {httpx.__version__} routes a proxied request to the INSTALLED "
            "transport. The nine PROXY_SHADOWED sites may now be guarded after all -- "
            "re-read this gate rather than trusting the registry's reasons.")
        assert not marker.consulted, "the marker must not be consulted by routing alone"
    finally:
        client.close()


def test_httpx_routes_to_the_installed_transport_when_no_proxy_is_passed():
    """POSITIVE CONTROL for the routing accessor: the same call must be able to
    return the marker, or its "is not marker" above proves nothing."""
    marker = _MarkerTransport()
    with httpx.Client(transport=marker) as client:
        routed = client._transport_for_url(httpx.URL("http://example.invalid/probe"))
    assert routed is marker, (
        "_transport_for_url did not return the installed transport even without a "
        "proxy -- this probe cannot say YES, so its NO above is not a measurement")


# ---------------------------------------------------------------------------
# 2. The population, and the cross-check against the census.
# ---------------------------------------------------------------------------
def test_the_population_is_measured_and_is_not_empty(scan):
    """Anti-vacuity.  Every assertion below is over this population; an empty
    one would make all of them pass for free."""
    assert len(scan) >= 50, (
        f"FOUND {len(scan)} httpx client constructions in {PACKAGE}/ -- far below the "
        "53 row 703 installed. An emptied population passes every check below for "
        "free; treat this as COULD NOT LOOK, not as OK.")
    assert all(row["has_transport"] for row in scan), (
        "a construction with no transport= reached this gate; "
        "tests/test_row703_ssrf_transport_is_installed_everywhere.py owns that failure")


def test_this_gates_scan_agrees_with_the_census_over_the_same_population(scan):
    """TWO SCANS THAT COULD HAVE DISAGREED.  The census resolves bindings its own
    way and judges each construction against the seam factory; this file resolves
    them independently and only reads keywords. They share the tree and nothing
    else. A divergence means one of the two resolvers has drifted."""
    sys.path.insert(0, str(REPO / "tools"))
    try:
        import ssrf_client_census as census_module
    except ImportError as exc:                       # COULD NOT LOOK
        pytest.fail(f"COULD NOT LOOK: tools/ssrf_client_census.py is not importable: {exc}")
    try:
        result = census_module.census(REPO)
    except census_module.CensusUnavailable as exc:   # COULD NOT LOOK
        pytest.fail(f"COULD NOT LOOK: the census cannot measure the population: {exc}")
    census_sites = {c.where for c in result.constructions if c.kind in ("Client", "AsyncClient")}
    mine = {row["where"] for row in scan}
    assert mine == census_sites, (
        "this gate's scan and tools/ssrf_client_census.py disagree about the httpx "
        f"construction population.\n  only this gate sees: {sorted(mine - census_sites)}"
        f"\n  only the census sees: {sorted(census_sites - mine)}")


# ---------------------------------------------------------------------------
# 3. The registry.
# ---------------------------------------------------------------------------
def test_no_client_level_proxy_can_shadow_the_guard(scan):
    """THE GATE. Any client-level proxy restores httpx's bypass."""
    found = {row["where"] for row in scan if row["shadowing"]}
    declared = set(PROXY_SHADOWED)
    undeclared = sorted(found - declared)
    ghosts = sorted(declared - found)
    assert not undeclared, (
        f"{len(undeclared)} httpx client construction(s) pass a shadowing keyword "
        f"({'/'.join(SHADOWING_KEYWORDS)}=) BESIDE transport=. In httpx "
        f"{httpx.__version__} the guarded transport at these sites is INERT: the "
        "request is served by the mounted proxy transport and never reaches the "
        "guard, while tools/ssrf_client_census.py still reports them pinned.\n"
        "  " + "\n  ".join(undeclared) + "\n"
        "Pass the proxy to guarded_transport(..., proxy=...) so the origin is "
        "classified before the proxy transport is called.")
    assert not ghosts, (
        "PROXY_SHADOWED declares construction(s) that no longer exist:\n  "
        + "\n  ".join(ghosts)
        + "\nRemove them; a registry of ghosts stops naming anything.")


def test_every_registry_entry_carries_a_non_trivial_reason():
    """A registry whose reasons are empty strings would pass the set check while
    documenting nothing."""
    thin = sorted(k for k, v in PROXY_SHADOWED.items() if len(v.split()) < 8)
    assert not thin, (
        "PROXY_SHADOWED entries with no real reason -- say what carries the refusal "
        "at that site now that the transport does not:\n  " + "\n  ".join(thin))


def test_the_public_only_half_of_the_registry_is_declared_separately(scan):
    """PUBLIC_ONLY is the strict policy: it refuses every address that does not
    classify public. Proxied, the transport enforces none of it. A site moving
    into or out of this half must be a visible edit."""
    sys.path.insert(0, str(REPO / "tools"))
    import ssrf_client_census as census_module
    policy = {c.where: c.policy for c in census_module.census(REPO).constructions}
    actual = {w for w in PROXY_SHADOWED if policy.get(w) == "public-only"}
    assert actual == PUBLIC_ONLY_AND_PROXIED, (
        "the PUBLIC_ONLY half of the proxied set changed.\n"
        f"  now public-only and proxied: {sorted(actual)}\n"
        f"  declared:                    {sorted(PUBLIC_ONLY_AND_PROXIED)}\n"
        "A PINNED site that became PUBLIC_ONLY while still proxied has taken on a "
        "refusal its transport cannot deliver; say in PROXY_SHADOWED what does.")


# ---------------------------------------------------------------------------
# 4. The residual: forms a keyword scan cannot see.
# ---------------------------------------------------------------------------
def test_no_construction_passes_positional_arguments(scan):
    """httpx.Client takes no positional parameters, so a proxy cannot arrive that
    way. Asserted rather than assumed."""
    positional = sorted(row["where"] for row in scan if row["positional"])
    assert not positional, (
        "construction(s) passing positional arguments to httpx.Client -- a shadowing "
        "argument could hide there, invisible to a keyword scan:\n  "
        + "\n  ".join(positional))


def test_every_star_kwargs_construction_is_declared(scan):
    """FOUND NONE IS NOT THE SAME AS NONE EXIST.  A proxy inside **kwargs is
    invisible to this gate. The known set is pinned so a new one forces the
    judgement to be made rather than inherited."""
    found = {row["where"] for row in scan if row["star_kwargs"]}
    assert found == STAR_KWARGS_CONSTRUCTIONS, (
        "the set of httpx constructions taking **kwargs changed. This gate CANNOT see "
        "a proxy passed through **kwargs, so each of these is an unmeasured site.\n"
        f"  new:  {sorted(found - STAR_KWARGS_CONSTRUCTIONS)}\n"
        f"  gone: {sorted(STAR_KWARGS_CONSTRUCTIONS - found)}\n"
        "For a new one: prove at its call site that the mapping it splats cannot "
        "carry proxy/proxies/mounts, then declare it here.")


# ---------------------------------------------------------------------------
# 5. Controls on the detector itself, on fixture trees.
# ---------------------------------------------------------------------------
_CLEAN_FIXTURE = {
    f"{PACKAGE}/__init__.py": "",
    f"{PACKAGE}/plain.py": (
        "import httpx\n"
        "from bulk_downloader.ssrf_transport import guarded_transport, PINNED\n"
        "def go():\n"
        "    with httpx.Client(timeout=1, transport=guarded_transport(PINNED)) as c:\n"
        "        return c.get('https://example.com/')\n"),
    f"{PACKAGE}/aliased.py": (
        "import httpx as hx\n"
        "from bulk_downloader.ssrf_transport import guarded_transport as gt, PUBLIC_ONLY as po\n"
        "def go():\n"
        "    return hx.AsyncClient(transport=gt(po))\n"),
    f"{PACKAGE}/rebound.py": (
        "import httpx\n"
        "from bulk_downloader.ssrf_transport import guarded_transport, PINNED\n"
        "_C = httpx.Client\n"
        "def go():\n"
        "    return _C(transport=guarded_transport(PINNED))\n"),
}
_CLEAN_COUNT = 3


def _write_fixture(root: pathlib.Path, files: Dict[str, str]) -> pathlib.Path:
    for rel, source in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return root


def test_the_detector_finds_the_clean_fixture_and_names_nothing(tmp_path):
    """NEGATIVE CONTROL. Three constructions, none proxied, all three seen."""
    rows = _scan_tree(_write_fixture(tmp_path / "clean", _CLEAN_FIXTURE))
    assert len(rows) == _CLEAN_COUNT, (
        f"the resolver found {len(rows)} of {_CLEAN_COUNT} constructions in a fixture "
        "whose count is known -- plain, aliased-module and rebound forms must all "
        "resolve, or a real proxied site could hide behind an alias")
    assert [r for r in rows if r["shadowing"]] == [], (
        "the detector named a proxy in a fixture that has none -- it would name the "
        "whole tree and prove nothing")


@pytest.mark.parametrize("spelling", SHADOWING_KEYWORDS)
def test_a_proxied_construction_added_to_a_clean_tree_is_named(tmp_path, spelling):
    """POSITIVE CONTROL, one per spelling. The detector must say YES."""
    drifted = dict(_CLEAN_FIXTURE)
    drifted[f"{PACKAGE}/added.py"] = (
        "import httpx\n"
        "from bulk_downloader.ssrf_transport import guarded_transport, PUBLIC_ONLY\n"
        "def go(p):\n"
        f"    return httpx.Client({spelling}=p, transport=guarded_transport(PUBLIC_ONLY))\n")
    rows = _scan_tree(_write_fixture(tmp_path / spelling, drifted))
    named = [r["where"] for r in rows if r["shadowing"]]
    assert len(rows) == _CLEAN_COUNT + 1
    assert named == [f"{PACKAGE}/added.py:4"], (
        f"a construction passing {spelling}= beside transport= was NOT named: {named}")


def test_an_alias_does_not_hide_a_proxied_construction(tmp_path):
    """The narrowness that would defeat this gate: a site that imports the class
    directly, or under another name, instead of calling httpx.Client."""
    drifted = dict(_CLEAN_FIXTURE)
    drifted[f"{PACKAGE}/sneaky.py"] = (
        "from httpx import Client as _Cl\n"
        "from bulk_downloader.ssrf_transport import guarded_transport, PINNED\n"
        "def go(p):\n"
        "    return _Cl(proxy=p, transport=guarded_transport(PINNED))\n")
    rows = _scan_tree(_write_fixture(tmp_path / "sneaky", drifted))
    assert [r["where"] for r in rows if r["shadowing"]] == [f"{PACKAGE}/sneaky.py:4"]


def test_an_unparseable_file_is_COULD_NOT_LOOK_and_never_a_pass(tmp_path):
    """A syntax error must raise, not silently shrink the population to a set
    that happens to contain no proxies."""
    broken = dict(_CLEAN_FIXTURE)
    broken[f"{PACKAGE}/broken.py"] = "def go(:\n"
    root = _write_fixture(tmp_path / "broken", broken)
    with pytest.raises(AssertionError, match="COULD NOT LOOK"):
        _scan_tree(root)


def test_a_missing_package_is_COULD_NOT_LOOK_and_never_a_pass(tmp_path):
    with pytest.raises(AssertionError, match="COULD NOT LOOK"):
        _scan_tree(tmp_path / "nothing-here")
