"""extension/background.js registrableDomain() is public-suffix aware and fails closed (O1671 AUDIT-12 line 3).

The behavioural assertions live in extension/test_registrable_domain.mjs (node,
vm-sandboxed background.js, no browser, no network). This file is the
pytest/worker-band entry point, mirroring tests/test_extension_autofill_scroll.py,
which also drives extension JS through node.

RED on main fe1b4dc99: evil.co.uk and bank.co.uk derive the same silent-origins
key ("co.uk"). Round 2 (lens F1): a suffix the bundle does not know no longer
degrades to last-two-labels -- bank.co.ke allowed must not make evil.co.ke
silent_allowed.

extension/public_suffix_data.js is GENERATED from the Public Suffix List by
``render_psl_data`` below; ``test_bundled_rules_match_their_source`` re-derives
it when the pinned source file is on the box.

RED COMMAND: node extension/test_registrable_domain.mjs
"""
import hashlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parents[1]
_PROBE = _REPO / "extension" / "test_registrable_domain.mjs"
_DATA = _REPO / "extension" / "public_suffix_data.js"

# The Debian/Ubuntu ``publicsuffix`` package, 20231001.0357-0.1.
PSL_SOURCE = Path("/usr/share/publicsuffix/public_suffix_list.dat")
PSL_SOURCE_SHA256 = "02074b85e6f1454055b82aa7812bcea05f6afd9076e9a60ae1eb30ad61a26881"
PSL_RULE_COUNT = 9107


def psl_rules(text: str) -> list[str]:
    """Every rule of both sections as ASCII (A-label) text, sorted, unique."""
    rules = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        rule = line.split()[0].lower()
        mark = "!" if rule.startswith("!") else ""
        labels = rule[len(mark):].split(".")
        rules.add(mark + ".".join(
            lab if lab == "*" else lab.encode("idna").decode("ascii") for lab in labels))
    return sorted(rules)


def render_psl_data(text: str, sha256: str) -> str:
    rules = psl_rules(text)
    return (
        "// GENERATED -- do not edit by hand. Public Suffix List rules for\n"
        "// registrableDomain() in background.js (O1671 AUDIT-12).\n"
        "// Source: publicsuffix.org list as shipped by the Debian/Ubuntu package\n"
        "// publicsuffix 20231001.0357-0.1 (ICANN and PRIVATE sections, IDN rules\n"
        "// as A-labels). Regenerate: tests/test_o1671_a12_registrable_domain.py\n"
        "// render_psl_data(). Loaded with importScripts(); never fetched.\n"
        "var BD_PSL = {\n"
        f'  source_sha256: "{sha256}",\n'
        f"  rule_count: {len(rules)},\n"
        "  rules: `\n" + "\n".join(rules) + "\n`,\n"
        "};\n"
    )


def _bundled_rules() -> list[str]:
    body = re.search(r"rules: `\n(.*?)\n`", _DATA.read_text(encoding="utf-8"), re.S)
    assert body, "A12-PSL-DATA: no rules block in extension/public_suffix_data.js"
    return body.group(1).split("\n")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_registrable_domain_is_public_suffix_aware():
    r = subprocess.run(["node", str(_PROBE)], capture_output=True, text=True, timeout=60, cwd=_REPO)
    assert r.returncode == 0, "node probe failed:\n" + r.stdout + r.stderr
    assert "FOUND NONE failing" in r.stdout, r.stdout
    assert "PASS evil.co.uk != bank.co.uk" in r.stdout, r.stdout
    assert "PASS F1 flow: evil.co.ke is not silent after bank.co.ke was allowed" in r.stdout, r.stdout


def test_bundled_rules_are_the_declared_list():
    rules = _bundled_rules()
    src = _DATA.read_text(encoding="utf-8")
    assert len(rules) == PSL_RULE_COUNT, f"A12-PSL-COUNT: {len(rules)} rules bundled, {PSL_RULE_COUNT} declared"
    assert f"rule_count: {PSL_RULE_COUNT}," in src and f'source_sha256: "{PSL_SOURCE_SHA256}"' in src
    assert rules == sorted(set(rules)), "A12-PSL-ORDER: bundled rules are not sorted and unique"
    bad = [r for r in rules if not re.fullmatch(r"!?(\*\.)?[a-z0-9-]+(\.[a-z0-9-]+)*", r)]
    assert not bad, f"A12-PSL-SHAPE: {len(bad)} rules are not plain ASCII suffix rules: {bad[:5]}"
    for needed in ("com", "co.uk", "co.ke", "github.io", "*.ck", "!www.ck", "xn--55qx5d.cn"):
        assert needed in rules, f"A12-PSL-MISSING: {needed}"


def test_bundled_rules_match_their_source():
    if not PSL_SOURCE.is_file():
        pytest.skip(f"{PSL_SOURCE} not on this box")
    raw = PSL_SOURCE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PSL_SOURCE_SHA256:
        pytest.skip("this box ships a different publicsuffix version than the pinned one")
    want = render_psl_data(raw.decode("utf-8"), PSL_SOURCE_SHA256)
    assert _DATA.read_text(encoding="utf-8") == want, (
        "A12-PSL-DRIFT: extension/public_suffix_data.js is not what render_psl_data() makes from the pinned source")
