"""Row 362: template claims need an executable, fail-closed selector witness."""
from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent
_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_DROPDOWN = _FIXTURES / "row362_gamma_dropdown.html"
_PRESENT = _FIXTURES / "row362_gamma_options_present.html"
_VERIFIER_MODULE = _REPO / "bulk_downloader" / "template_selector_verifier.py"
_SAFE_TRIGGER = (
    "[class*='ScenePlayerHeaderPlus-IconItem']"
    ":has([class*='Icon-Download'])"
)
_SAFE_ROWS = "a[class*='DownloadOption'][href*='/movieaction/download/']"
_EVILANGEL_LITERAL = "a.download-link[href*='.mp4']"


def _api():
    return importlib.import_module("bulk_downloader.template_selector_verifier")


def _template(template_id: str) -> dict:
    from bulk_downloader.site_templates import TEMPLATES

    matches = [item for item in TEMPLATES if item.get("id") == template_id]
    assert len(matches) == 1, (
        f"precondition: expected one committed {template_id!r} template, "
        f"found {len(matches)}"
    )
    return matches[0]


def _independent_selector_entries(template: dict) -> list[tuple[str, object]]:
    """Independent schema walk; never calls the production enumerator."""
    found: list[tuple[str, object]] = []
    special = {"user_field", "pass_field", "submit_btn"}

    def walk(value: object, path: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = (*path, str(key))
                if key in special or "selector" in key.lower():
                    if isinstance(child, list):
                        found.extend(
                            (".".join((*child_path, f"[{index}]")), item)
                            for index, item in enumerate(child)
                        )
                    elif isinstance(child, str):
                        found.append((".".join(child_path), child))
                walk(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, (*path, f"[{index}]"))

    walk(template, (str(template.get("id", "<missing-id>")),))
    return found


def _soup(path: Path) -> BeautifulSoup:
    assert path.is_file(), f"precondition: fixture is absent: {path}"
    text = path.read_text(encoding="utf-8")
    assert text.strip(), f"precondition: fixture is empty: {path}"
    return BeautifulSoup(text, "html.parser")


def _entry(report: dict, selector: str) -> dict:
    matches = [item for item in report.get("selectors", [])
               if item.get("selector") == selector]
    assert len(matches) == 1, (
        f"precondition: selector {selector!r} occurs {len(matches)} times "
        f"in report: {report}"
    )
    return matches[0]


def _clean_python_env() -> dict[str, str]:
    env = dict(os.environ)
    for name in ("BD_WORK_TREE", "PYTHONHOME", "PYTHONPATH"):
        env.pop(name, None)
    env["PYTHONNOUSERSITE"] = "1"
    return env


def test_malformed_miss_and_hit_are_three_distinct_measured_states():
    malformed = "a[href*='.mp4'"
    absent = "a.definitely-absent[href]"
    present = _SAFE_ROWS
    soup = _soup(_PRESENT)
    assert malformed.count("[") == malformed.count("]") + 1, (
        "precondition: malformed control no longer has an unclosed bracket"
    )
    assert len(soup.select(absent)) == 0, (
        "precondition: absent control unexpectedly matches the fixture"
    )
    assert len(soup.select(present)) == 8, (
        "precondition: present control must have an exact nonzero denominator"
    )
    synthetic = {
        "id": "row362_status_controls",
        "learned": {"download": {"row_selectors": [malformed, absent, present]}},
    }

    report = _api().verify_template_source(synthetic, _PRESENT)

    malformed_row = _entry(report, malformed)
    assert malformed_row["status"] == "MALFORMED", malformed_row
    assert malformed_row["status"] != "MISS", malformed_row
    assert malformed_row["count"] is None, malformed_row
    assert "Unexpected token" in malformed_row["error"], malformed_row
    miss_row = _entry(report, absent)
    assert miss_row["status"] == "MISS" and miss_row["count"] == 0, miss_row
    hit_row = _entry(report, present)
    assert hit_row["status"] == "HIT" and hit_row["count"] == 8, hit_row


def test_the_exact_committed_evilangel_literal_is_valid_and_resolves():
    template = _template("evilangel")
    independent = _independent_selector_entries(template)
    occurrences = [value for _path, value in independent
                   if value == _EVILANGEL_LITERAL]
    assert occurrences == [_EVILANGEL_LITERAL], (
        "precondition: the test must exercise the exact committed literal"
    )
    soup = _soup(_PRESENT)
    assert len(soup.select(_EVILANGEL_LITERAL)) == 1, (
        "precondition: the offline syntax control must contain exactly one hit"
    )
    synthetic = {
        "id": "row362_evilangel_literal_control",
        "learned": {"download": {"row_selectors": [_EVILANGEL_LITERAL]}},
    }

    report = _api().verify_template_source(synthetic, _PRESENT)

    row = _entry(report, _EVILANGEL_LITERAL)
    assert row["status"] == "HIT" and row["count"] == 1, row
    assert row["error"] == "", row


def test_the_complete_committed_corpus_parses_with_a_reconciled_denominator():
    from bulk_downloader.site_templates import TEMPLATES

    assert TEMPLATES, "precondition: committed template corpus is empty"
    ids = [item.get("id") for item in TEMPLATES]
    assert all(isinstance(item, str) and item for item in ids), ids
    assert len(ids) == len(set(ids)), "precondition: template ids are not unique"
    independent = [entry for template in TEMPLATES
                   for entry in _independent_selector_entries(template)]
    assert independent, "precondition: selector denominator is zero"

    report = _api().audit_committed_selector_syntax()

    assert report["template_count"] == len(TEMPLATES), report
    assert report["selector_count"] == len(independent), report
    assert report["checked_count"] == report["selector_count"] > 0, report
    assert report["malformed_count"] == 0, report
    assert report["unknown_count"] == 0, report
    assert report["verdict"] == "OK", report


def test_unreachable_subject_is_unknown_and_never_ok(tmp_path: Path):
    template = _template("gamma_kosmos")
    independent = _independent_selector_entries(template)
    assert independent, "precondition: gamma_kosmos has no selector denominator"
    missing = tmp_path / "subject-does-not-exist.html"
    assert not missing.exists(), "precondition: unreachable path unexpectedly exists"

    report = _api().verify_template_source("gamma_kosmos", missing)

    assert report["selector_count"] == len(independent) > 0, report
    assert report["verdict"] == "UNKNOWN", report
    assert report["ok"] is False, report
    assert report["subject"]["status"] == "UNKNOWN", report
    assert all(item["status"] == "UNKNOWN" for item in report["selectors"]), report


def test_selectorless_template_is_unknown_over_an_empty_denominator():
    template = _template("youtube")
    independent = _independent_selector_entries(template)
    assert independent == [], (
        "precondition: selectorless control unexpectedly gained a denominator"
    )
    assert _PRESENT.is_file(), f"precondition: fixture is absent: {_PRESENT}"

    report = _api().verify_template_source("youtube", _PRESENT)

    assert report["selector_count"] == 0, report
    assert report["verdict"] == "UNKNOWN", report
    assert report["ok"] is False, report
    assert report["subject"]["status"] == "UNKNOWN", report


def test_dropdown_fixture_is_opened_once_before_rows_are_counted():
    soup = _soup(_DROPDOWN)
    assert len(soup.select(_SAFE_TRIGGER)) == 1, (
        "precondition: compound trigger must identify one download control"
    )
    latent = soup.select_one("template#option-template")
    assert latent is not None, "precondition: latent option template is absent"
    latent_soup = BeautifulSoup(latent.decode_contents(), "html.parser")
    latent.extract()
    assert len(soup.select(_SAFE_ROWS)) == 0, (
        "precondition: dropdown fixture already exposes its options"
    )
    assert len(latent_soup.select(_SAFE_ROWS)) == 8, (
        "precondition: dropdown template must carry exactly eight latent options"
    )

    report = _api().verify_template_source("gamma_kosmos", _DROPDOWN)

    row = _entry(report, _SAFE_ROWS)
    assert row["initial_count"] == 0, row
    assert row["status"] == "HIT" and row["count"] == 8, row
    interaction = report["interaction"]
    assert interaction["clicked"] is True, interaction
    assert interaction["click_count"] == 1, interaction
    assert interaction["selector"] == _SAFE_TRIGGER, interaction


def test_blocked_trigger_makes_hidden_rows_unknown_not_ok(tmp_path: Path):
    subject = tmp_path / "blocked-trigger.html"
    subject.write_text(
        "<!doctype html><button id='blocked' disabled>Download</button>",
        encoding="utf-8",
    )
    soup = _soup(subject)
    blocked = soup.select_one("button#blocked[disabled]")
    assert blocked is not None, "precondition: disabled trigger control is absent"
    assert len(soup.select(_SAFE_ROWS)) == 0, (
        "precondition: blocked-trigger subject unexpectedly exposes rows"
    )
    synthetic = {
        "id": "row362_blocked_trigger",
        "learned": {"download": {
            "trigger_selectors": ["button#blocked"],
            "row_selectors": [_SAFE_ROWS],
        }},
    }

    report = _api().verify_template_source(synthetic, subject, timeout=0.25)

    row = _entry(report, _SAFE_ROWS)
    assert row["status"] == "UNKNOWN" and row["count"] == 0, row
    assert report["interaction"]["error"], report["interaction"]
    assert report["verdict"] == "UNKNOWN", report
    assert report["ok"] is False, report


def test_absent_direct_link_row_is_a_measured_miss_not_unknown():
    absent = "a.row362-direct-link-absent[href]"
    soup = _soup(_PRESENT)
    assert len(soup.select(absent)) == 0, (
        "precondition: direct-link MISS control unexpectedly matches"
    )
    synthetic = {
        "id": "row362_direct_link_miss",
        "learned": {"download": {"row_selectors": [absent]}},
    }

    report = _api().verify_template_source(synthetic, _PRESENT)

    row = _entry(report, absent)
    assert row["status"] == "MISS" and row["count"] == 0, row
    assert report["interaction"]["error"] == "", report["interaction"]
    assert report["verdict"] == "MISS" and report["ok"] is False, report


def test_negative_control_known_to_match_does_not_report_miss():
    """RED anchor: current committed claim needs a real positive/negative witness."""
    template = _template("gamma_kosmos")
    soup = _soup(_PRESENT)
    assert len(soup.select(_SAFE_ROWS)) == 8, (
        "precondition: positive-control fixture must have exactly eight hits"
    )
    absent = "a.row362-negative-control-absent[href]"
    assert len(soup.select(absent)) == 0, (
        "precondition: negative control unexpectedly matches the fixture"
    )
    assert _VERIFIER_MODULE.is_file(), (
        "BD-TEMPLATE-VERIFY-MISSING: committed selectors have no executable witness"
    )
    independent_values = [value for _path, value
                          in _independent_selector_entries(template)]
    assert independent_values.count(_SAFE_ROWS) == 1, (
        "precondition: committed gamma template lacks the measured row selector"
    )

    measured_template = {
        **template,
        "learned": {
            **template.get("learned", {}),
            "download": {
                **template.get("learned", {}).get("download", {}),
                "row_selectors": [_SAFE_ROWS, absent],
            },
        },
    }
    report = _api().verify_template_source(measured_template, _PRESENT)

    hit = _entry(report, _SAFE_ROWS)
    assert hit["status"] == "HIT" and hit["count"] == 8, hit
    miss = _entry(report, absent)
    assert miss["status"] == "MISS" and miss["count"] == 0, miss
    assert report["interaction"]["clicked"] is False, report["interaction"]
    assert report["verdict"] == "HIT" and report["ok"] is True, report


def test_fixture_option_labels_and_urls_reconcile_at_all_eight_heights():
    from bulk_downloader.detect import parse_size_bytes, res_score

    soup = _soup(_PRESENT)
    options = soup.select(_SAFE_ROWS)
    assert len(options) == 8, (
        "precondition: option reconciliation needs exactly eight rows"
    )
    measured: list[tuple[int, int, int, str]] = []
    for option in options:
        label = option.get_text(" ", strip=True)
        href = str(option.get("href") or "")
        label_match = re.search(r"\b(\d{3,4})p\b", label, re.I)
        url_match = re.search(r"/(\d{3,4})p/([^/?#]+)$", urlparse(href).path, re.I)
        assert label_match is not None, f"precondition: label has no height: {label!r}"
        assert url_match is not None, f"precondition: URL has no height/format: {href!r}"
        label_height = int(label_match.group(1))
        url_height = int(url_match.group(1))
        size = parse_size_bytes(label)
        assert size > 0, f"precondition: label has no parseable size: {label!r}"
        measured.append((label_height, url_height, size, url_match.group(2).lower()))

    expected = [160, 240, 360, 480, 540, 720, 1080, 2160]
    assert [row[0] for row in measured] == expected, measured
    assert [row[1] for row in measured] == expected, measured
    assert all(row[3] == "mp4" for row in measured), measured
    sizes = [row[2] for row in measured]
    assert all(left < right for left, right in zip(sizes, sizes[1:])), measured
    labels = [option.get_text(" ", strip=True) for option in options]
    assert [res_score(label) for label in labels] == expected, labels


def test_compound_trigger_excludes_the_six_sibling_controls():
    soup = _soup(_PRESENT)
    broad = "[class*='ScenePlayerHeaderPlus-IconItem']"
    assert len(soup.select(broad)) == 7, (
        "precondition: broad selector control must expose all seven icon actions"
    )
    assert len(soup.select(_SAFE_TRIGGER)) == 1, (
        "precondition: icon-qualified selector must expose only Download"
    )
    assert "Download" in soup.select_one(_SAFE_TRIGGER).get_text(" ", strip=True)

    template = _template("gamma_kosmos")
    measured_template = {
        **template,
        "learned": {
            **template.get("learned", {}),
            "download": {
                **template.get("learned", {}).get("download", {}),
                "trigger_selectors": [_SAFE_TRIGGER],
                "row_selectors": [_SAFE_ROWS],
            },
        },
    }
    report = _api().verify_template_source(measured_template, _PRESENT)

    row = _entry(report, _SAFE_TRIGGER)
    assert row["status"] == "HIT" and row["count"] == 1, row


def test_gamma_login_uses_the_measured_input_submit_control():
    template = _template("gamma_kosmos")
    values = [value for _path, value in _independent_selector_entries(template)]
    assert values.count("#submit") == 1, (
        "precondition: gamma template must carry the measured submit selector"
    )
    old_guesses = [
        "button[type='submit'].Button",
        "form button[type='submit']",
        "button:has-text('Sign In')",
        "button:has-text('Login')",
        "button:has-text('Log In')",
    ]
    soup = _soup(_PRESENT)
    assert len(soup.select("#submit")) == 1, (
        "precondition: login fixture must expose one measured control"
    )
    assert len(soup.select(old_guesses[0])) == 0, (
        "precondition: class-qualified submit guess unexpectedly matches"
    )
    assert len(soup.select(old_guesses[1])) == 0, (
        "precondition: form-button submit guess unexpectedly matches"
    )
    button_texts = {item.get_text(" ", strip=True) for item in soup.select("button")}
    assert button_texts.isdisjoint({"Sign In", "Login", "Log In"}), (
        "precondition: text submit guess unexpectedly matches the fixture"
    )

    report = _api().verify_template_source("gamma_kosmos", _PRESENT)

    submit = _entry(report, "#submit")
    assert submit["status"] == "HIT" and submit["count"] == 1, submit
    misses = [_entry(report, selector) for selector in old_guesses]
    assert all(row["status"] == "MISS" and row["count"] == 0 for row in misses), misses


def test_tracked_cli_reports_the_same_offline_hit_contract():
    tool = _REPO / "toolchain" / "bin" / "bd-template-verify"
    assert tool.is_file(), f"precondition: tracked CLI is absent: {tool}"
    assert _PRESENT.is_file(), f"precondition: fixture is absent: {_PRESENT}"
    run = subprocess.run(
        [sys.executable, str(tool), "gamma_kosmos", str(_PRESENT), "--json"],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=30,
        env=_clean_python_env(),
    )
    assert run.returncode == 0, run.stdout + run.stderr
    report = json.loads(run.stdout)
    assert report["selector_count"] > 0, report
    row = _entry(report, _SAFE_ROWS)
    assert row["status"] == "HIT" and row["count"] == 8, row
    assert report["verdict"] == "HIT", report


def test_installed_cli_resolves_pointer_but_missing_dependency_is_unknown(tmp_path: Path):
    """A bare installed interpreter cannot judge the selftest and must say so."""
    source_tool = _REPO / "toolchain" / "bin" / "bd-template-verify"
    source_resolver = _REPO / "toolchain" / "bin" / "_bd_work_tree.py"
    assert source_tool.is_file(), f"precondition: source tool is absent: {source_tool}"
    assert source_resolver.is_file(), (
        f"precondition: checkout resolver is absent: {source_resolver}"
    )

    rich = subprocess.run(
        [sys.executable, str(source_tool), "--selftest"],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=30,
        env=_clean_python_env(),
    )
    assert rich.returncode == 0, rich.stdout + rich.stderr
    assert rich.stdout.count(
        "SELFTEST PASS: controls=2 unknown=0 missing_subject=1"
    ) == 1, rich.stdout
    assert "BD-TEMPLATE-VERIFY-SELFTEST-UNKNOWN" not in rich.stdout + rich.stderr

    checkout = tmp_path / "checkout-without-venv"
    package = checkout / "bulk_downloader"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("# isolated selftest checkout\n", encoding="utf-8")
    shutil.copy2(_VERIFIER_MODULE, package / _VERIFIER_MODULE.name)
    initialized = subprocess.run(
        ["git", "init", "--quiet", str(checkout)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert initialized.returncode == 0, initialized.stdout + initialized.stderr
    assert not (checkout / "venv" / "bin" / "python").exists(), (
        "precondition: installed-layout checkout unexpectedly has a rich interpreter"
    )

    installed = tmp_path / "installed-suite"
    installed.mkdir()
    tool = installed / source_tool.name
    resolver = installed / source_resolver.name
    shutil.copy2(source_tool, tool)
    shutil.copy2(source_resolver, resolver)
    pointer = installed / ".bd-work-tree"
    pointer.write_text(f"{checkout.resolve()}\n", encoding="utf-8")
    pointer.chmod(0o600)
    assert pointer.read_text(encoding="utf-8") == f"{checkout.resolve()}\n", (
        "precondition: installed pointer does not name the isolated checkout"
    )

    bare_python = Path("/usr/bin/python3")
    assert bare_python.is_file(), (
        f"precondition: installed-layout control lacks bare Python: {bare_python}"
    )
    env = _clean_python_env()
    dependency_probe = subprocess.run(
        [str(bare_python), "-S", "-c", (
            "import importlib.util; "
            "print(int(importlib.util.find_spec('playwright') is None))"
        )],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    assert dependency_probe.returncode == 0, dependency_probe.stderr
    assert dependency_probe.stdout.strip() == "1", (
        "precondition: bare interpreter can import the dependency meant to be unavailable"
    )

    run = subprocess.run(
        [str(bare_python), "-S", str(tool), "--selftest"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )

    diagnostic = "BD-TEMPLATE-VERIFY-SELFTEST-UNKNOWN: controls=2 unknown=2"
    assert run.returncode == 2, run.stdout + run.stderr
    assert run.stderr.count(diagnostic) == 1, run.stdout + run.stderr
    assert "selector parser unavailable: ModuleNotFoundError" in run.stderr, run.stderr
    assert "Traceback" not in run.stdout + run.stderr, run.stdout + run.stderr
    assert "SELFTEST PASS" not in run.stdout + run.stderr, run.stdout + run.stderr


def test_transform_control_only_imports_the_verifier_contract():
    api = _api()
    assert callable(api.verify_template_source)
    assert callable(api.audit_committed_selector_syntax)
