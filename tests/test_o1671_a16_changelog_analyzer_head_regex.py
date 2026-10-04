BD_GATE_SCOPE = "module"
from tools.changelog_analyzer import parse

_FIXTURE = """# Changelog

## v1.0.x — 2026-09-30 → 2026-10-01
- fix: range head, not a release

## v1.0.7.x — 2026-10-07 (wildcard head, not a release)
- feat: dropped

## v1.0.0 — 2026-10-01 (Em-dash)
- fix: a bug

## v1.0.1 - 2026-10-02 (Hyphen)
- feat: a feature

## v1.0.2 -- 2026-10-03 (Double-hyphen)
- fix: another bug

## v1.0.3 – 2026-10-04 (En-dash)
- feat: another feature

## v1.0.4 (No dash, no date)
- fix: small bug

## v1.0.5 2026-10-05 (No dash, date)
- feat: test

## v1.0.6

- fix: bare head bullet
- feat: second bullet

## v1.0.6.1 — 2026-10-06 (Four-part)
- feat: four-part version
"""

_EXPECTED = [
    # (version, date, title, bullets, fixes)
    ("1.0.0", "2026-10-01", "Em-dash", 1, 1),
    ("1.0.1", "2026-10-02", "Hyphen", 1, 0),
    ("1.0.2", "2026-10-03", "Double-hyphen", 1, 1),
    ("1.0.3", "2026-10-04", "En-dash", 1, 0),
    ("1.0.4", None, "No dash, no date", 1, 1),
    ("1.0.5", "2026-10-05", "No dash, date", 1, 0),
    ("1.0.6", None, "", 2, 1),
    ("1.0.6.1", "2026-10-06", "Four-part", 1, 0),
]


def test_head_regex(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text(_FIXTURE, encoding="utf-8")
    res = parse(str(tmp_path))
    got = [(r["version"], r["date"], r["title"], r["bullets"], r["fixes"])
           for r in res["releases"]]
    assert got == _EXPECTED
    assert res["count"] == len(_EXPECTED)
    assert res["unparsed_headings"] == 2
