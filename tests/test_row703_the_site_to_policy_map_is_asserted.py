"""Row 703 gate 2 -- THE SITE-TO-POLICY MAP IS ASSERTED, NOT MERELY PRINTED.

WHY THIS GATE EXISTS.  ``bulk_downloader/ssrf_transport.py`` offers two
policies and every one of the 53 httpx client constructions in the package
chooses one at its call site:

  ``PUBLIC_ONLY`` -- the strict one. Every address the hostname resolves to
      must classify public; a host resolving into RFC1918 / loopback / CGNAT
      is REFUSED.
  ``PINNED``      -- the permissive one. It keeps a site's base admissibility
      (LAN bridges, identity providers, VPN gateways stay reachable) and
      refuses only what can never be a legitimate HTTP peer.

A ONE-TOKEN EDIT -- ``guarded_transport(PUBLIC_ONLY)`` to
``guarded_transport(PINNED)`` -- converts "refuses a hostname resolving to
RFC1918" into "admits it".  Before this gate that edit turned NOTHING red:
``tools/ssrf_client_census.py`` PRINTS the policy of every site and asserts
only that a policy exists, ``tests/test_row703_ssrf_transport_is_installed_
everywhere.py`` asserts that both policies appear SOMEWHERE, and neither the
200 tests row 703 declares nor the 939 shard gate looks at which site got
which. Measured, not assumed: see DONE.md for the scratch-clone flip.

WHAT THIS GATE ASSERTS.  The whole map, site by site, against a population
re-derived from the tree.  Any change to any site's policy is RED and has to
be made deliberately, here, in the same commit.  It is ADDITIVE: it asks no
call site and no seam line to change.

THE ONE SITE A STATIC MAP CANNOT PIN is declared as such rather than papered
over -- see SHARED_BUILDER below.

THREE OUTCOMES.  Unparseable file, missing package or an unmeasurable
population is COULD NOT LOOK, raised, never folded into a pass.
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys
from typing import Dict, List, Optional, Set

import pytest

BD_GATE_SCOPE = "repo-wide"

REPO = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = "bulk_downloader"
SEAM_MODULE = "bulk_downloader.ssrf_transport"
FACTORY = "guarded_transport"

PUBLIC_ONLY = "public-only"
PINNED = "pinned"

# The one construction whose transport is bound OUTSIDE the call, from
# _SSRFGuardedTransport_factory()(allow_private_hosts=allow_private_hosts).
# Its effective policy is a RUNTIME argument, so no static map can pin it and
# this gate does not pretend to. It is declared here so that a SECOND such site
# -- a new construction whose policy stops being readable from the tree -- is
# RED rather than silently unmeasured.
SHARED_BUILDER = "bulk_downloader/provider_resolve_impl/_common.py:740"
SHARED_BUILDER_NOTE = (
    "policy is the runtime allow_private_hosts argument of "
    "_make_default_http_get; COULD NOT LOOK statically, by construction")

# ---------------------------------------------------------------------------
# THE MAP.  53 constructions.  Derived from the tree, declared here.
# Change a site's policy and you change this file in the same commit.
# ---------------------------------------------------------------------------
POLICY_MAP: Dict[str, str] = {
    "bulk_downloader/app_scrape_listing.py:70": PUBLIC_ONLY,
    "bulk_downloader/app_sites_auth.py:398": PINNED,
    "bulk_downloader/app_sites_auth.py:412": PINNED,
    "bulk_downloader/captcha_resolver.py:257": PINNED,
    "bulk_downloader/captcha_resolver.py:271": PINNED,
    "bulk_downloader/captcha_resolver.py:317": PINNED,
    "bulk_downloader/captcha_resolver.py:333": PINNED,
    "bulk_downloader/community_scrapers.py:260": PINNED,
    "bulk_downloader/community_scrapers.py:340": PINNED,
    "bulk_downloader/cookie_health.py:259": PINNED,
    "bulk_downloader/deep_detect/orchestrate.py:931": PUBLIC_ONLY,
    "bulk_downloader/extractors_dl8.py:624": PINNED,
    "bulk_downloader/extractors_jsonapi.py:170": PINNED,
    "bulk_downloader/extractors_jsonapi.py:368": PINNED,
    "bulk_downloader/flaresolverr_client.py:114": PINNED,
    "bulk_downloader/flaresolverr_client.py:351": PINNED,
    "bulk_downloader/flaresolverr_client.py:443": PINNED,
    "bulk_downloader/flaresolverr_client.py:465": PINNED,
    "bulk_downloader/flaresolverr_client.py:483": PINNED,
    "bulk_downloader/jd_bridge.py:184": PINNED,
    "bulk_downloader/mp4_metadata.py:317": PINNED,
    "bulk_downloader/multi_conn.py:186": PUBLIC_ONLY,
    "bulk_downloader/multi_conn.py:518": PUBLIC_ONLY,
    "bulk_downloader/oidc.py:57": PINNED,
    "bulk_downloader/oidc.py:97": PINNED,
    "bulk_downloader/oidc.py:124": PINNED,
    "bulk_downloader/qb_bridge.py:162": PINNED,
    "bulk_downloader/runner.py:1181": PUBLIC_ONLY,
    "bulk_downloader/runner_challenge.py:282": PINNED,
    "bulk_downloader/runner_challenge.py:296": PINNED,
    "bulk_downloader/runner_challenge.py:313": PINNED,
    "bulk_downloader/runner_challenge.py:327": PINNED,
    "bulk_downloader/runner_extractors.py:629": PUBLIC_ONLY,
    "bulk_downloader/runner_manual.py:419": PUBLIC_ONLY,
    "bulk_downloader/runner_telemetry.py:301": PUBLIC_ONLY,
    "bulk_downloader/runner_transport.py:491": PINNED,
    "bulk_downloader/runner_transport.py:1030": PINNED,
    "bulk_downloader/runner_transport.py:2209": PINNED,
    "bulk_downloader/runner_transport.py:2576": PINNED,
    "bulk_downloader/runner_transport.py:2656": PINNED,
    "bulk_downloader/runner_transport.py:2807": PINNED,
    "bulk_downloader/session_keeper.py:1216": PINNED,
    "bulk_downloader/tg_bot.py:162": PINNED,
    "bulk_downloader/tg_bot.py:185": PINNED,
    "bulk_downloader/tier_probe.py:299": PUBLIC_ONLY,
    "bulk_downloader/vpn_leak_tests.py:658": PINNED,
    "bulk_downloader/vpn_providers/mullvad.py:99": PINNED,
    "bulk_downloader/vpn_providers/mullvad.py:121": PINNED,
    "bulk_downloader/vpn_providers/mullvad.py:204": PINNED,
    "bulk_downloader/vpn_providers/pia.py:106": PINNED,
    "bulk_downloader/vpn_providers/pia.py:204": PINNED,
    "bulk_downloader/vpn_providers/pia.py:219": PINNED,
    SHARED_BUILDER: SHARED_BUILDER_NOTE,
}


# ---------------------------------------------------------------------------
# An independent policy resolver.  Deliberately NOT the census's.
# ---------------------------------------------------------------------------
class _PolicyScan(ast.NodeVisitor):
    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.module_aliases: Set[str] = set()       # httpx module
        self.class_aliases: Dict[str, str] = {}     # local name -> Client/AsyncClient
        self.seam_aliases: Set[str] = set()         # names bound to the seam MODULE
        self.factory_aliases: Set[str] = set()      # names bound to guarded_transport
        self.policy_aliases: Dict[str, str] = {}    # local name -> "public-only"/"pinned"
        self.rows: List[dict] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "httpx":
                self.module_aliases.add(alias.asname or "httpx")
            elif alias.name == SEAM_MODULE:
                self.seam_aliases.add(alias.asname or alias.name.split(".")[-1])
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if module == "httpx":
            for alias in node.names:
                if alias.name in ("Client", "AsyncClient"):
                    self.class_aliases[alias.asname or alias.name] = alias.name
        elif module == SEAM_MODULE or module.endswith(".ssrf_transport"):
            for alias in node.names:
                local = alias.asname or alias.name
                if alias.name == FACTORY:
                    self.factory_aliases.add(local)
                elif alias.name == "PUBLIC_ONLY":
                    self.policy_aliases[local] = PUBLIC_ONLY
                elif alias.name == "PINNED":
                    self.policy_aliases[local] = PINNED
        elif node.names and any(a.name == "ssrf_transport" for a in node.names):
            for alias in node.names:
                if alias.name == "ssrf_transport":
                    self.seam_aliases.add(alias.asname or alias.name)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
            if (isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name)
                    and value.value.id in self.module_aliases
                    and value.attr in ("Client", "AsyncClient")):
                self.class_aliases[target] = value.attr
            elif isinstance(value, ast.Name) and value.id in self.class_aliases:
                self.class_aliases[target] = self.class_aliases[value.id]
        self.generic_visit(node)

    def _client_kind(self, func: ast.expr) -> Optional[str]:
        if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.value.id in self.module_aliases
                and func.attr in ("Client", "AsyncClient")):
            return func.attr
        if isinstance(func, ast.Name) and func.id in self.class_aliases:
            return self.class_aliases[func.id]
        return None

    def _is_factory(self, func: ast.expr) -> bool:
        if isinstance(func, ast.Name):
            return func.id in self.factory_aliases
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            return func.attr == FACTORY and func.value.id in self.seam_aliases
        return False

    def _policy_token(self, node: ast.expr) -> Optional[str]:
        if isinstance(node, ast.Name):
            return self.policy_aliases.get(node.id)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in self.seam_aliases:
                return {"PUBLIC_ONLY": PUBLIC_ONLY, "PINNED": PINNED}.get(node.attr)
            return None
        if isinstance(node, ast.Constant) and node.value in (PUBLIC_ONLY, PINNED):
            return node.value
        return None

    def visit_Call(self, node: ast.Call) -> None:
        kind = self._client_kind(node.func)
        if kind is not None:
            policy = ""
            reason = "no transport= keyword"
            for keyword in node.keywords:
                if keyword.arg != "transport":
                    continue
                value = keyword.value
                if isinstance(value, ast.Call) and self._is_factory(value.func):
                    if not value.args:
                        reason = f"{FACTORY}() called with no policy argument"
                    else:
                        token = self._policy_token(value.args[0])
                        if token is None:
                            reason = (f"{FACTORY}(...) first argument does not resolve to "
                                      "PUBLIC_ONLY or PINNED")
                        else:
                            policy, reason = token, f"transport={FACTORY}({token})"
                else:
                    reason = "transport= is not a guarded_transport(...) call"
                break
            self.rows.append({"where": f"{self.rel}:{node.lineno}", "kind": kind,
                              "policy": policy, "reason": reason})
        self.generic_visit(node)


def _scan_source(rel: str, source: str) -> List[dict]:
    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError as exc:                       # COULD NOT LOOK
        raise AssertionError(f"COULD NOT LOOK: {rel} does not parse: {exc}") from exc
    visitor = _PolicyScan(rel)
    visitor.visit(tree)
    return visitor.rows


def _scan_tree(root: pathlib.Path, package: str = PACKAGE) -> List[dict]:
    base = root / package
    if not base.is_dir():                            # COULD NOT LOOK
        raise AssertionError(f"COULD NOT LOOK: {base} is not a directory")
    rows: List[dict] = []
    for path in sorted(base.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        rows.extend(_scan_source(rel, path.read_text(encoding="utf-8", errors="strict")))
    return rows


def _tracked_scan() -> List[dict]:
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
# Anti-vacuity first.
# ---------------------------------------------------------------------------
def test_the_population_is_measured_and_carries_both_policies(scan):
    assert len(scan) == len(POLICY_MAP), (
        f"the tree has {len(scan)} httpx client constructions and POLICY_MAP declares "
        f"{len(POLICY_MAP)}. Every assertion below is over this population; a shrunken "
        "one passes them for free.")
    live = {row["policy"] for row in scan if row["policy"]}
    assert live == {PUBLIC_ONLY, PINNED}, (
        f"the tree uses policies {sorted(live)}. A map with only one policy in it "
        "cannot detect a flip between two.")


def test_the_declared_map_uses_only_policies_the_seam_defines():
    sys.path.insert(0, str(REPO))
    from bulk_downloader import ssrf_transport
    allowed = set(ssrf_transport.POLICIES) | {SHARED_BUILDER_NOTE}
    unknown = sorted(f"{k} -> {v}" for k, v in POLICY_MAP.items() if v not in allowed)
    assert not unknown, (
        f"POLICY_MAP names policies the seam does not define "
        f"({sorted(ssrf_transport.POLICIES)}):\n  " + "\n  ".join(unknown))


# ---------------------------------------------------------------------------
# THE GATE.
# ---------------------------------------------------------------------------
def test_every_construction_has_the_policy_this_file_declares(scan):
    """A one-token PUBLIC_ONLY <-> PINNED flip is RED here and nowhere else."""
    actual = {row["where"]: row["policy"] for row in scan}
    declared = dict(POLICY_MAP)
    declared[SHARED_BUILDER] = ""          # the runtime-policy site scans as unmapped

    missing = sorted(set(declared) - set(actual))
    added = sorted(set(actual) - set(declared))
    changed = sorted(
        f"{where}: declared {declared[where] or '(runtime)'} but the tree says "
        f"{actual[where] or '(unmapped)'}"
        for where in set(declared) & set(actual) if declared[where] != actual[where])

    assert not changed, (
        f"{len(changed)} construction(s) do not carry the policy this map declares. A "
        "PUBLIC_ONLY -> PINNED change converts 'refuses a hostname resolving to RFC1918' "
        "into 'admits it', and nothing else in the tree notices:\n  "
        + "\n  ".join(changed)
        + "\nIf the change is intended, edit POLICY_MAP in the same commit and say why "
          "in the commit message.")
    assert not (missing or added), (
        "the construction population moved.\n"
        f"  declared but gone: {missing}\n"
        f"  in the tree, undeclared: {added}\n"
        "tests/test_row703_ssrf_transport_is_installed_everywhere.py owns whether a new "
        "construction is pinned at all; this file owns which policy it got.")


def test_exactly_one_construction_has_a_runtime_policy(scan):
    """The site a static map cannot pin, pinned as an exception of size one."""
    unmapped = sorted(row["where"] for row in scan if not row["policy"])
    assert unmapped == [SHARED_BUILDER], (
        "the set of constructions whose policy cannot be read from the tree changed:\n"
        f"  now: {unmapped}\n  declared: [{SHARED_BUILDER!r}]\n"
        "A construction whose policy is a runtime value is invisible to this gate. If a "
        "new one is intended, declare it here and say what asserts its policy instead.\n"
        "  reasons: " + "; ".join(
            f"{row['where']} -- {row['reason']}" for row in scan if not row["policy"]))


def test_the_public_only_sites_are_exactly_these(scan):
    """Stated as a set as well as a map: the strict-policy sites are the ones a
    reviewer reads first, and a flip out of this set is the dangerous direction."""
    actual = {row["where"] for row in scan if row["policy"] == PUBLIC_ONLY}
    declared = {w for w, p in POLICY_MAP.items() if p == PUBLIC_ONLY}
    assert actual == declared, (
        f"  no longer PUBLIC_ONLY (LOOSENED): {sorted(declared - actual)}\n"
        f"  newly PUBLIC_ONLY (tightened):    {sorted(actual - declared)}")


def test_this_gates_policies_agree_with_the_census(scan):
    """TWO RESOLVERS THAT COULD HAVE DISAGREED. tools/ssrf_client_census.py reads
    the policy its own way and calls the runtime site 'shared-builder'; this file
    reads it independently and calls that site unmapped. Everywhere else they must
    produce the same string."""
    sys.path.insert(0, str(REPO / "tools"))
    try:
        import ssrf_client_census as census_module
    except ImportError as exc:                       # COULD NOT LOOK
        pytest.fail(f"COULD NOT LOOK: tools/ssrf_client_census.py is not importable: {exc}")
    try:
        result = census_module.census(REPO)
    except census_module.CensusUnavailable as exc:   # COULD NOT LOOK
        pytest.fail(f"COULD NOT LOOK: the census cannot measure the population: {exc}")
    theirs = {c.where: c.policy for c in result.constructions
              if c.kind in ("Client", "AsyncClient")}
    mine = {row["where"]: row["policy"] for row in scan}
    assert set(mine) == set(theirs), (
        f"population divergence.\n  only here: {sorted(set(mine) - set(theirs))}"
        f"\n  only census: {sorted(set(theirs) - set(mine))}")
    disagree = sorted(
        f"{w}: this gate says {mine[w] or '(unmapped)'}, census says {theirs[w]}"
        for w in mine if mine[w] != theirs[w] and w != SHARED_BUILDER)
    assert not disagree, "the two policy resolvers disagree:\n  " + "\n  ".join(disagree)
    assert theirs[SHARED_BUILDER] == "shared-builder", (
        f"{SHARED_BUILDER} is no longer the census's shared-builder site; the runtime "
        "exception this gate declares may have moved")


# ---------------------------------------------------------------------------
# Controls on the detector, on fixture trees.
# ---------------------------------------------------------------------------
_FIXTURE = {
    f"{PACKAGE}/__init__.py": "",
    f"{PACKAGE}/strict.py": (
        "import httpx\n"
        "from bulk_downloader.ssrf_transport import guarded_transport, PUBLIC_ONLY\n"
        "def go():\n"
        "    return httpx.Client(transport=guarded_transport(PUBLIC_ONLY))\n"),
    f"{PACKAGE}/loose.py": (
        "import httpx\n"
        "from bulk_downloader.ssrf_transport import guarded_transport, PINNED\n"
        "def go():\n"
        "    return httpx.Client(transport=guarded_transport(PINNED))\n"),
    f"{PACKAGE}/aliased.py": (
        "import httpx as hx\n"
        "from bulk_downloader import ssrf_transport as st\n"
        "def go():\n"
        "    return hx.AsyncClient(transport=st.guarded_transport(st.PUBLIC_ONLY))\n"),
    f"{PACKAGE}/renamed.py": (
        "import httpx\n"
        "from bulk_downloader.ssrf_transport import guarded_transport as gt, PINNED as P\n"
        "def go():\n"
        "    return httpx.Client(transport=gt(P))\n"),
}
_FIXTURE_POLICY = {
    f"{PACKAGE}/strict.py:4": PUBLIC_ONLY,
    f"{PACKAGE}/loose.py:4": PINNED,
    f"{PACKAGE}/aliased.py:4": PUBLIC_ONLY,
    f"{PACKAGE}/renamed.py:4": PINNED,
}


def _write(root: pathlib.Path, files: Dict[str, str]) -> pathlib.Path:
    for rel, source in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return root


def test_the_resolver_reads_every_spelling_of_a_policy(tmp_path):
    """NEGATIVE CONTROL / calibration. Plain, module-attribute and renamed-import
    forms must all resolve, or a real flip could hide behind an alias."""
    rows = _scan_tree(_write(tmp_path / "clean", _FIXTURE))
    assert {r["where"]: r["policy"] for r in rows} == _FIXTURE_POLICY


def test_a_one_token_policy_flip_on_a_scratch_tree_is_detected(tmp_path):
    """POSITIVE CONTROL, and the exact edit the bounce named: flip ONE token,
    change nothing else, and the map must move."""
    flipped = dict(_FIXTURE)
    flipped[f"{PACKAGE}/strict.py"] = _FIXTURE[f"{PACKAGE}/strict.py"].replace(
        "guarded_transport(PUBLIC_ONLY)", "guarded_transport(PINNED)").replace(
        "guarded_transport, PUBLIC_ONLY", "guarded_transport, PINNED")
    rows = _scan_tree(_write(tmp_path / "flipped", flipped))
    after = {r["where"]: r["policy"] for r in rows}
    assert after != _FIXTURE_POLICY, "the flip was invisible to the resolver"
    assert after[f"{PACKAGE}/strict.py:4"] == PINNED
    assert len(rows) == len(_FIXTURE_POLICY), (
        "the flip must change a POLICY, not the population -- if the count moved, this "
        "control is testing the wrong thing")


def test_a_flip_back_restores_the_map(tmp_path):
    """RESTORE arm: the detector is not simply always-red after any edit."""
    rows = _scan_tree(_write(tmp_path / "restored", dict(_FIXTURE)))
    assert {r["where"]: r["policy"] for r in rows} == _FIXTURE_POLICY


def test_a_transport_that_is_not_the_factory_is_unmapped_not_guessed(tmp_path):
    """A construction whose transport comes from somewhere else must read as
    unmapped with a reason, never as a policy the resolver invented."""
    odd = dict(_FIXTURE)
    odd[f"{PACKAGE}/other.py"] = (
        "import httpx\n"
        "def go(t):\n"
        "    return httpx.Client(transport=t)\n")
    rows = _scan_tree(_write(tmp_path / "odd", odd))
    row = next(r for r in rows if r["where"] == f"{PACKAGE}/other.py:3")
    assert row["policy"] == ""
    assert "not a guarded_transport" in row["reason"]


def test_an_unparseable_file_is_COULD_NOT_LOOK_and_never_a_pass(tmp_path):
    broken = dict(_FIXTURE)
    broken[f"{PACKAGE}/broken.py"] = "def go(:\n"
    with pytest.raises(AssertionError, match="COULD NOT LOOK"):
        _scan_tree(_write(tmp_path / "broken", broken))


def test_a_missing_package_is_COULD_NOT_LOOK_and_never_a_pass(tmp_path):
    with pytest.raises(AssertionError, match="COULD NOT LOOK"):
        _scan_tree(tmp_path / "nothing-here")
