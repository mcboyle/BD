#!/usr/bin/env python3
"""Independent expected-view contract for capture and navigator completeness."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping, Sequence


THEMES = ("light", "dark")

NAV = (
    ("/", "Home"), ("/queue", "Queue"), ("/history", "History"),
    ("/activity", "Activity"), ("/needs-review", "Needs review"),
    ("/library", "Library"), ("/sites", "Sites"),
    ("/capture", "Capture"), ("/templates", "Template Manager"),
    ("/dom-analyzer", "DOM analyzer"),
    ("/ai-teach", "AI selector repair"),
    ("/pools-macros", "Pools & macros"), ("/batch-ops", "Batch ops"),
    ("/imports", "Imports"), ("/import-views", "Import & saved views"),
    ("/dedup", "Dedup"), ("/rebalance", "Storage Rebalance"),
    ("/maintenance", "Maintenance"), ("/backup", "Backup"),
    ("/integrations", "Integrations"),
    ("/notifications", "Notifications"), ("/vpn", "VPN"),
    ("/cluster", "Cluster"), ("/secrets", "Secrets"),
    ("/settings", "Settings"),
    ("/settings/advanced", "Settings · Advanced"),
    ("/dashboard", "System Overview"),
    ("/more-actions", "More actions"), ("/logs/diff", "Logs diff"),
)

DRILL = (
    ("/sites/__probe__", "Site detail"),
    ("/sites/__probe__/actions", "Site · Actions"),
    ("/sites/__probe__/inspect", "Site · Inspect (dry-run)"),
    ("/sites/__probe__/payload-actions", "Site · Payload actions"),
    ("/sites/__probe__/settings", "Site · Settings"),
)

SUBTABS = (
    ("/sites", ("All", "Active", "Paused", "Issues")),
    ("/activity", ("24h", "7d", "30d", "All")),
    ("/history", ("History", "Events", "Logs", "Saved")),
)


def slug(value: str) -> str:
    return (value.strip("/").replace("/", "_").replace(" ", "-")
            .replace("·", "").replace("(", "").replace(")", "").lower()
            or "home")


@dataclass(frozen=True)
class ViewSpec:
    cat: str
    route: str
    label: str
    filename: str


EXPECTED_VIEWS = (
    *(ViewSpec("nav", route, label, f"nav_{slug(route)}.png")
      for route, label in NAV),
    *(ViewSpec("drillin", route, label, f"drill_{slug(route)}.png")
      for route, label in DRILL),
    *(ViewSpec(
        "subtab", f"{route} › {tab}", f"{route} › {tab}",
        f"subtab_{slug(route)}_{index}_{tab.lower().replace(' ', '')}.png",
      )
      for route, tabs in SUBTABS
      for index, tab in enumerate(tabs, 1)),
    ViewSpec("popup", "Add-site wizard", "Add-site wizard (modal)",
             "popup_addsite_wizard.png"),
    ViewSpec("popup", "Command palette", "Command palette (⌘K)",
             "popup_command_palette.png"),
    ViewSpec("popup", "Mobile nav drawer",
             "Mobile nav / More drawer (414px)", "popup_mobile_drawer.png"),
    ViewSpec("cockpit", "/cockpit", "Cockpit / Review Center",
             "cockpit_home.png"),
    ViewSpec("popup", "Cockpit gear popover",
             "Cockpit gear / theme popover", "cockpit_gear_popover.png"),
    ViewSpec("popup", "Cockpit nav dropdown", "Cockpit nav dropdown",
             "cockpit_nav_dropdown.png"),
)

_VIEW_BY_KEY = {(view.cat, view.route): view for view in EXPECTED_VIEWS}


class ManifestContractError(RuntimeError):
    """The capture population or its measurement is unavailable/invalid."""


def expected_view(cat: str, route: str) -> ViewSpec:
    try:
        return _VIEW_BY_KEY[(cat, route)]
    except KeyError as exc:
        raise ManifestContractError(
            f"view is outside the expected population: {cat}:{route}") from exc


def manifest_key(row: Mapping[str, object]) -> tuple[str, str, str]:
    try:
        cat = row["cat"]
        route = row["route"]
        theme = row["theme"]
    except (KeyError, TypeError) as exc:
        raise ManifestContractError(
            "manifest row is malformed: cat, route, and theme are required") from exc
    if not all(isinstance(value, str) and value for value in (cat, route, theme)):
        raise ManifestContractError(
            "manifest row is malformed: cat, route, and theme must be nonempty strings")
    return cat, route, theme


def expected_manifest_keys() -> set[tuple[str, str, str]]:
    keys = {
        (view.cat, view.route, theme)
        for view in EXPECTED_VIEWS
        for theme in THEMES
    }
    if not keys:
        raise ManifestContractError("expected manifest population is empty")
    return keys


def _describe(keys: Sequence[tuple[str, str, str]]) -> str:
    return ", ".join(f"{cat}:{route}:{theme}" for cat, route, theme in keys)


def validate_manifest(rows):
    if not isinstance(rows, list):
        raise ManifestContractError("manifest measurement is not a JSON row list")

    keyed_rows = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ManifestContractError(f"manifest row {index} is not an object")
        keyed_rows.append((manifest_key(row), row))

    expected = expected_manifest_keys()
    counts = Counter(key for key, _row in keyed_rows)
    actual = set(counts)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    duplicates = sorted(key for key, count in counts.items() if count != 1)
    if missing:
        raise ManifestContractError(
            f"missing {len(missing)} expected row(s): {_describe(missing)}")
    if unexpected:
        raise ManifestContractError(
            f"unexpected {len(unexpected)} row(s): {_describe(unexpected)}")
    if duplicates:
        raise ManifestContractError(
            f"duplicate {len(duplicates)} row(s): {_describe(duplicates)}")

    bad_labels = []
    failed = []
    for key, row in keyed_rows:
        cat, route, _theme = key
        view = expected_view(cat, route)
        if row.get("label") != view.label:
            bad_labels.append(key)
        err = row.get("err")
        if (row.get("status") != "ok" or not isinstance(row.get("file"), str)
                or not row.get("file") or not isinstance(err, int) or err != 0):
            failed.append(key)
    if bad_labels:
        raise ManifestContractError(
            f"mislabeled {len(bad_labels)} row(s): {_describe(sorted(bad_labels))}")
    if failed:
        raise ManifestContractError(
            f"failed {len(failed)} row(s): {_describe(sorted(failed))}")
    return rows


def load_manifest(path: str | Path):
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
        rows = json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestContractError(
            f"manifest measurement unavailable: {path}: {exc}") from exc
    return validate_manifest(rows)


if len(EXPECTED_VIEWS) != len(_VIEW_BY_KEY):
    raise ManifestContractError("expected logical-view population contains duplicates")
