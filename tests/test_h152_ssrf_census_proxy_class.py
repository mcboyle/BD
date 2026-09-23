"""H152: the httpx SSRF census must not print "all pinned" while some guards are conditional.

An explicit ``proxy=`` beside ``transport=`` mounts httpx's own proxy transport, so the
guarded transport is never entered (pinned in the source, bypassed at runtime whenever a
proxy is set). That is declared and deliberate at the tunnel sites
(tests/test_row703_a_proxy_shadows_the_guarded_transport.py). The defect was the artifact:
``tools/ssrf_client_census.py`` printed "N constructions ... all pinned" with no qualifier,
and that line is what gets pasted into verdicts. Each construction now carries a proxy
class -- none / explicit / unknown (a ``**kwargs`` that may carry proxy=) -- and the
headline counts the conditional ones instead of calling them pinned.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import ssrf_client_census as census_tool  # noqa: E402

_HEADER = (
    "import httpx\n"
    "from bulk_downloader.ssrf_transport import guarded_transport, PINNED\n\n\n"
)
_PLAIN = "def plain():\n    return httpx.Client(transport=guarded_transport(PINNED))\n\n\n"
_PROXIED = "def proxied(p):\n    return httpx.Client(transport=guarded_transport(PINNED), proxy=p)\n\n\n"
_SPLAT = "def splat(**kw):\n    return httpx.Client(transport=guarded_transport(PINNED), **kw)\n"


def _line_of(source: str, needle: str) -> int:
    return next(i for i, text in enumerate(source.splitlines(), 1) if needle in text)


def _tree(tmp_path: Path, source: str) -> Path:
    root = tmp_path / "tree"
    path = root / "bulk_downloader" / "client.py"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True, stdin=subprocess.DEVNULL)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, stdin=subprocess.DEVNULL)
    return root


def test_each_construction_carries_its_proxy_class():
    source = _HEADER + _PLAIN + _PROXIED + _SPLAT
    found = {c.line: c for c in census_tool.constructions_in("bulk_downloader/client.py", source)}
    assert len(found) == 3
    assert all(c.pinned for c in found.values())
    assert found[_line_of(source, "return httpx.Client(transport=guarded_transport(PINNED))")].proxy == "none"
    assert found[_line_of(source, "proxy=p")].proxy == "explicit"
    assert found[_line_of(source, "(PINNED), **kw)")].proxy == "unknown"


def test_mounts_is_a_shadowing_keyword_too():
    source = _HEADER + "def mounted(m):\n    return httpx.Client(transport=guarded_transport(PINNED), mounts=m)\n"
    (c,) = census_tool.constructions_in("bulk_downloader/client.py", source)
    assert c.proxy == "explicit"


def test_an_unproxied_population_still_reads_all_pinned(tmp_path):
    # Positive control: the qualifier appears only when there is something to qualify.
    state, detail, result = census_tool.verdict(_tree(tmp_path, _HEADER + _PLAIN))
    assert state == census_tool.OK, detail
    assert detail.endswith("all pinned"), detail
    assert result.proxy_bypassed == () and result.proxy_unknown == ()


def test_a_proxied_construction_is_counted_and_named_not_called_pinned(tmp_path):
    # Acceptance 2 (negative control): add proxy= and the headline must move.
    source = _HEADER + _PLAIN + _PROXIED + _SPLAT
    state, detail, result = census_tool.verdict(_tree(tmp_path, source))
    assert state == census_tool.OK, detail
    assert "all pinned" not in detail, detail
    assert "1 pinned unconditionally" in detail, detail
    assert "1 transport-bypassed when proxy= is set" in detail, detail
    assert "1 proxy UNKNOWN" in detail, detail
    assert f"bulk_downloader/client.py:{_line_of(source, 'proxy=p')}" in detail, detail
    assert f"bulk_downloader/client.py:{_line_of(source, '(PINNED), **kw)')}" in detail, detail
    assert [c.proxy for c in result.proxy_bypassed] == ["explicit"]
    assert [c.proxy for c in result.proxy_unknown] == ["unknown"]


def test_the_per_construction_listing_tags_the_proxy_class(tmp_path, capsys):
    source = _HEADER + _PLAIN + _PROXIED + _SPLAT
    assert census_tool.main(["--root", str(_tree(tmp_path, source))]) == 0
    out = capsys.readouterr().out.splitlines()
    by_line = {int(l.split()[1].rsplit(":", 1)[1]): l for l in out if l.startswith(("PINNED", "UNPINNED"))}
    assert "proxy=" not in by_line[_line_of(source, "return httpx.Client(transport=guarded_transport(PINNED))")]
    assert by_line[_line_of(source, "proxy=p")].endswith(" proxy=explicit")
    assert by_line[_line_of(source, "(PINNED), **kw)")].endswith(" proxy=UNKNOWN")


def test_the_repo_headline_names_every_conditional_guard():
    state, detail, result = census_tool.verdict(ROOT)
    assert result is not None, detail
    assert state == census_tool.OK, detail
    conditional = result.proxy_bypassed + result.proxy_unknown
    # Today no site passes proxy= explicitly (row703's PROXY_SHADOWED registry is empty) and four
    # take **kwargs (its STAR_KWARGS_CONSTRUCTIONS); either way the headline may not say all pinned.
    assert result.proxy_unknown, "four **kwargs constructions exist on this tree; empty means the probe went blind"
    assert "all pinned" not in detail, detail
    for c in conditional:
        assert c.where in detail, (c.where, detail)
