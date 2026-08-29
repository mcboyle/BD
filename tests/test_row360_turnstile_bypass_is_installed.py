"""Row 360: optional dependency declarations are not runtime capabilities."""
from __future__ import annotations

import ast
import importlib.machinery
import inspect
import os
from pathlib import Path
import re
import subprocess
import sys
import textwrap
import types

from flask import Flask
from packaging.requirements import Requirement

import bdctl
from bulk_downloader import app_knowledge
from bulk_downloader import app_scrapling
from bulk_downloader import doctor
from bulk_downloader import friendly_error as friendly_error_module
from bulk_downloader import knowledge
from bulk_downloader import runner
from bulk_downloader import scrapling_adapter


BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parents[1]
_CAPABILITY_NAMES = ("Adaptor", "StealthyFetcher")


def _partial_scrapling(*capabilities: str) -> types.ModuleType:
    partial = types.ModuleType("scrapling")
    partial.__spec__ = importlib.machinery.ModuleSpec("scrapling", loader=None)
    if "Adaptor" in capabilities:
        partial.Adaptor = type("Adaptor", (), {})
    if "StealthyFetcher" in capabilities:
        class StealthyFetcher:
            fetch_calls = 0

            @classmethod
            def fetch(cls, *args, **kwargs):
                cls.fetch_calls += 1
                return types.SimpleNamespace(
                    cookies=[{"name": "cf_clearance", "value": "row360"}],
                    html_content="<html>challenge cleared</html>",
                )

        partial.StealthyFetcher = StealthyFetcher
    return partial


def _installed_capability_count(module: types.ModuleType) -> int:
    return sum(hasattr(module, name) for name in _CAPABILITY_NAMES)


def _usable_capability_count(module: types.ModuleType) -> int:
    adaptor = getattr(module, "Adaptor", None)
    fetcher = getattr(module, "StealthyFetcher", None)
    return sum((
        callable(adaptor),
        callable(getattr(fetcher, "fetch", None)),
    ))


class _BrokenScrapling(types.ModuleType):
    def __getattr__(self, name):
        raise RuntimeError(f"broken capability probe: {name}")


def _broken_scrapling() -> types.ModuleType:
    broken = _BrokenScrapling("scrapling")
    broken.__spec__ = importlib.machinery.ModuleSpec("scrapling", loader=None)
    return broken


def test_declared_but_incomplete_scrapling_is_not_available(monkeypatch):
    partial = _partial_scrapling()

    assert partial.__spec__.name == "scrapling", (
        "precondition: the fixture is a declared importable Scrapling module"
    )
    assert _installed_capability_count(partial) == 0, (
        "precondition: the partial install exposes exactly zero usable capabilities"
    )
    assert _usable_capability_count(partial) == 0
    monkeypatch.setitem(sys.modules, "scrapling", partial)

    assert scrapling_adapter.is_available() is False


def test_optional_manifest_installs_the_turnstile_fetcher_extra():
    manifest = _REPO / "requirements-optional.txt"
    assert manifest.is_file(), "precondition: the optional capability manifest exists"
    requirements = [
        Requirement(line)
        for raw in manifest.read_text(encoding="utf-8").splitlines()
        if (line := raw.strip()) and not line.startswith("#")
    ]
    scrapling = [req for req in requirements if req.name.lower() == "scrapling"]

    assert len(scrapling) == 1, (
        "precondition: exactly one manifest entry declares Scrapling; "
        f"found {len(scrapling)}"
    )
    assert set(scrapling[0].extras) == {"fetchers"}, (
        "Scrapling is declared without its fetchers extra, so installing the "
        "manifest does not install the Turnstile bypass"
    )


def test_recovery_install_is_a_negative_control_for_turnstile(monkeypatch):
    recovery_only = _partial_scrapling("Adaptor")
    assert _installed_capability_count(recovery_only) == 1, (
        "precondition: exactly one of the two capabilities is installed"
    )
    assert _usable_capability_count(recovery_only) == 1
    assert not hasattr(recovery_only, "StealthyFetcher"), (
        "precondition: the negative control lacks the Turnstile fetcher"
    )
    monkeypatch.setitem(sys.modules, "scrapling", recovery_only)
    scrapling_adapter.reset_stats()

    assert scrapling_adapter.is_available() is True
    assert scrapling_adapter.is_stealthy_fetcher_available() is False
    refused = scrapling_adapter.bypass_turnstile("https://row360.invalid/")
    assert refused.ok is False
    assert refused.error == "stealthy_fetcher_unavailable"
    assert scrapling_adapter.stats()["turnstile_failed"] == 0


def test_capability_probe_separates_unavailable_unknown_and_available(monkeypatch):
    recovery_only = _partial_scrapling("Adaptor")
    assert _installed_capability_count(recovery_only) == 1, (
        "precondition: the unavailable fixture has recovery but not bypass"
    )
    monkeypatch.setitem(sys.modules, "scrapling", recovery_only)

    unavailable = scrapling_adapter.capability_status()
    assert unavailable == {
        "adaptive_selectors": {
            "available": True,
            "status": "available",
            "reason": "adaptor_available",
        },
        "turnstile_bypass": {
            "available": False,
            "status": "unavailable",
            "reason": "stealthy_fetcher_unavailable",
        },
    }

    broken = _broken_scrapling()
    assert sum(name in broken.__dict__ for name in _CAPABILITY_NAMES) == 0, (
        "precondition: the exceptional fixture exposes zero measurable capabilities"
    )
    monkeypatch.setitem(sys.modules, "scrapling", broken)

    unknown = scrapling_adapter.capability_status()
    assert sum(item["available"] for item in unknown.values()) == 0
    assert {item["status"] for item in unknown.values()} == {"unknown"}
    assert {item["reason"] for item in unknown.values()} == {
        "capability_probe_failed:RuntimeError"
    }

    declared_only = _partial_scrapling("Adaptor")
    declared_only.StealthyFetcher = type("StealthyFetcher", (), {})
    assert _installed_capability_count(declared_only) == 2, (
        "precondition: the negative control declares exactly both symbols"
    )
    assert _usable_capability_count(declared_only) == 1, (
        "precondition: only recovery is usable because fetch is absent"
    )
    monkeypatch.setitem(sys.modules, "scrapling", declared_only)

    declared_status = scrapling_adapter.capability_status()["turnstile_bypass"]
    assert declared_status == {
        "available": False,
        "status": "unavailable",
        "reason": "stealthy_fetcher_unavailable",
    }

    complete = _partial_scrapling(*_CAPABILITY_NAMES)
    assert _installed_capability_count(complete) == 2, (
        "precondition: the positive control exposes exactly both capabilities"
    )
    assert _usable_capability_count(complete) == 2, (
        "precondition: both declared capabilities expose their runtime callables"
    )
    monkeypatch.setitem(sys.modules, "scrapling", complete)

    available = scrapling_adapter.capability_status()
    assert sum(item["available"] for item in available.values()) == 2
    assert {item["status"] for item in available.values()} == {"available"}
    scrapling_adapter.reset_stats()
    bypass = scrapling_adapter.bypass_turnstile("https://row360.invalid/")
    assert complete.StealthyFetcher.fetch_calls == 1
    assert bypass.ok is True
    assert len(bypass.cookies) == 1
    assert scrapling_adapter.stats()["turnstile_bypassed"] == 1


def test_status_api_reports_the_measured_turnstile_capability(monkeypatch):
    recovery_only = _partial_scrapling("Adaptor")
    assert _installed_capability_count(recovery_only) == 1, (
        "precondition: status is measured against recovery without a bypass"
    )
    monkeypatch.setitem(sys.modules, "scrapling", recovery_only)
    monkeypatch.setattr(app_scrapling, "_app__SCRAP_AVAILABLE", lambda: True)
    monkeypatch.setattr(
        app_scrapling, "_app__scrap_adapter", lambda: scrapling_adapter
    )
    scrapling_adapter.reset_stats()
    app = Flask("row360-status")
    registered = app_scrapling.register_routes(app)
    assert registered == 4, "precondition: all four Scrapling routes were registered"

    response = app.test_client().get("/api/scrapling/status")
    body = response.get_json()

    assert response.status_code == 200
    assert body["ok"] is True
    assert body["available"] is True
    assert body["stealthy_fetcher"] is False
    assert body["capabilities"]["turnstile_bypass"] == {
        "available": False,
        "status": "unavailable",
        "reason": "stealthy_fetcher_unavailable",
    }
    assert body["stats"] == {
        "fingerprints_built": 0,
        "recoveries_attempted": 0,
        "recoveries_succeeded": 0,
        "recovery_success_rate": 0.0,
        "turnstile_detected": 0,
        "turnstile_bypassed": 0,
        "turnstile_failed": 0,
        "turnstile_success_rate": 0.0,
    }


def test_friendly_error_advertises_bypass_only_when_installed(monkeypatch):
    recovery_only = _partial_scrapling("Adaptor")
    assert _installed_capability_count(recovery_only) == 1, (
        "precondition: the negative control has no Turnstile fetcher"
    )
    monkeypatch.setitem(sys.modules, "scrapling", recovery_only)

    absent_state = scrapling_adapter.capability_status()["turnstile_bypass"]
    assert absent_state == {
        "available": False,
        "status": "unavailable",
        "reason": "stealthy_fetcher_unavailable",
    }, "precondition: the injected measurement says the bypass is unavailable"
    absent_message = friendly_error_module.friendly_error(
        "Turnstile challenge blocked this page",
        context={"turnstile_bypass": absent_state},
    )
    assert absent_message == (
        "Cloudflare Turnstile. Bypass unavailable "
        "(stealthy_fetcher_unavailable). Use Take Over."
    )

    complete = _partial_scrapling(*_CAPABILITY_NAMES)
    assert _installed_capability_count(complete) == 2, (
        "precondition: the positive control installs exactly both capabilities"
    )
    assert _usable_capability_count(complete) == 2
    monkeypatch.setitem(sys.modules, "scrapling", complete)

    available_state = scrapling_adapter.capability_status()["turnstile_bypass"]
    assert available_state == {
        "available": True,
        "status": "available",
        "reason": "stealthy_fetcher_available",
    }, "precondition: the injected measurement says the bypass is available"
    present_message = friendly_error_module.friendly_error(
        "Turnstile challenge blocked this page",
        context={"turnstile_bypass": available_state},
    )
    assert present_message == (
        "Cloudflare Turnstile. Scrapling/nodriver bypass usually works."
    )

    unknown_message = friendly_error_module.friendly_error(
        "Turnstile challenge blocked this page"
    )
    assert unknown_message == (
        "Cloudflare Turnstile. Bypass availability unknown "
        "(measurement_not_supplied). Use Take Over."
    )


def test_runner_refuses_to_invoke_an_uninstalled_bypass(monkeypatch):
    calls = {
        "content": 0,
        "evaluate": 0,
        "goto": 0,
        "bypass": 0,
        "add_cookies": 0,
    }
    events = []

    class FakeOwner:
        config = {"use_scrapling_turnstile": True}

        @staticmethod
        def log_event(event, message, *, url):
            events.append((event, message, url))

    class FakePage:
        @staticmethod
        def content():
            calls["content"] += 1
            return '<div class="cf-turnstile"></div>'

        @staticmethod
        def evaluate(_expression):
            calls["evaluate"] += 1
            return "row360-agent"

        @staticmethod
        def goto(_url, **_kwargs):
            calls["goto"] += 1

    class FakeContext:
        @staticmethod
        def add_cookies(cookies):
            assert len(cookies) == 1
            calls["add_cookies"] += 1

    owner = FakeOwner()
    page = FakePage()
    context = FakeContext()
    url = "https://row360.invalid/"
    assert sum(bool(value) for value in owner.config.values()) == 1, (
        "precondition: exactly one runner opt-in flag is enabled"
    )
    assert scrapling_adapter.is_turnstile_page(page.content()) is True, (
        "precondition: the fake page contains an actual Turnstile signal"
    )
    calls["content"] = 0

    original_bypass = scrapling_adapter.bypass_turnstile

    def counted_bypass(*args, **kwargs):
        calls["bypass"] += 1
        return original_bypass(*args, **kwargs)

    monkeypatch.setattr(scrapling_adapter, "bypass_turnstile", counted_bypass)
    recovery_only = _partial_scrapling("Adaptor")
    assert _installed_capability_count(recovery_only) == 1, (
        "precondition: the runner negative control has recovery but no bypass"
    )
    monkeypatch.setitem(sys.modules, "scrapling", recovery_only)
    monkeypatch.setattr(runner, "_SCRAPLING_AVAILABLE", True)
    monkeypatch.setattr(runner, "_scrap", scrapling_adapter)

    assert runner._translate_failed_message(
        "Turnstile challenge blocked this page"
    ) == (
        "Cloudflare Turnstile. Bypass unavailable "
        "(stealthy_fetcher_unavailable). Use Take Over."
    )
    assert runner._try_scrapling_turnstile(owner, page, context, url) == (
        "unavailable"
    )
    assert calls == {
        "content": 0,
        "evaluate": 0,
        "goto": 0,
        "bypass": 0,
        "add_cookies": 0,
    }
    assert len(events) == 0

    broken = _broken_scrapling()
    assert sum(name in broken.__dict__ for name in _CAPABILITY_NAMES) == 0, (
        "precondition: the runner UNKNOWN control exposes exactly zero symbols"
    )
    monkeypatch.setitem(sys.modules, "scrapling", broken)
    assert runner._translate_failed_message(
        "Turnstile challenge blocked this page"
    ) == (
        "Cloudflare Turnstile. Bypass availability unknown "
        "(capability_probe_failed:RuntimeError). Use Take Over."
    )
    assert runner._try_scrapling_turnstile(owner, page, context, url) == "unknown"
    assert sum(calls.values()) == 0
    assert len(events) == 0

    complete = _partial_scrapling(*_CAPABILITY_NAMES)
    assert _installed_capability_count(complete) == 2, (
        "precondition: the runner positive control has exactly both capabilities"
    )
    assert _usable_capability_count(complete) == 2
    monkeypatch.setitem(sys.modules, "scrapling", complete)
    scrapling_adapter.reset_stats()

    assert runner._translate_failed_message(
        "Turnstile challenge blocked this page"
    ) == (
        "Cloudflare Turnstile. Scrapling/nodriver bypass usually works."
    )
    assert runner._try_scrapling_turnstile(owner, page, context, url) == "bypassed"
    assert calls == {
        "content": 1,
        "evaluate": 1,
        "goto": 1,
        "bypass": 1,
        "add_cookies": 1,
    }
    assert complete.StealthyFetcher.fetch_calls == 1
    assert len(events) == 2
    assert [event[0] for event in events] == [
        "turnstile_detected",
        "turnstile_bypassed",
    ]
    stats = scrapling_adapter.stats()
    assert stats["turnstile_detected"] == 1
    assert stats["turnstile_bypassed"] == 1
    process_tree = ast.parse(textwrap.dedent(
        inspect.getsource(runner.SiteRunner._process_one)
    ))
    dispatch_calls = [
        node for node in ast.walk(process_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_try_scrapling_turnstile"
    ]
    assert len(dispatch_calls) == 1, (
        "_process_one must dispatch through exactly one measured Turnstile seam"
    )
    update_tree = ast.parse(textwrap.dedent(
        inspect.getsource(runner.SiteRunner._update_job_current)
    ))
    translation_calls = [
        node for node in ast.walk(update_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_translate_failed_message"
    ]
    assert len(translation_calls) == 1, (
        "_update_job_current must use exactly one measured failure translator"
    )


def test_knowledge_recipe_never_recommends_an_unmeasured_bypass():
    unavailable = {
        "available": False,
        "status": "unavailable",
        "reason": "stealthy_fetcher_unavailable",
    }
    assert sum(bool(value) for value in unavailable.values()) == 2, (
        "precondition: unavailable state has exactly status and reason truthy"
    )
    unavailable_recipes = knowledge.diagnostic_recipes_for(
        "Cloudflare challenge", turnstile_bypass=unavailable
    )
    assert len(unavailable_recipes) == 1
    assert len(unavailable_recipes[0]["steps"]) == 3
    assert unavailable_recipes[0]["steps"][-1] == (
        "Scrapling bypass unavailable (stealthy_fetcher_unavailable); "
        "use Take Over or FlareSolverr"
    )
    unavailable_catalog = knowledge.all_recipes(turnstile_bypass=unavailable)
    assert len(unavailable_catalog) == 6
    assert sum(
        "Scrapling adapter" in step
        for recipe in unavailable_catalog
        for step in recipe["steps"]
    ) == 0

    unknown_recipes = knowledge.diagnostic_recipes_for("Cloudflare challenge")
    assert len(unknown_recipes) == 1
    assert unknown_recipes[0]["steps"][-1] == (
        "Scrapling bypass availability unknown (measurement_not_supplied); "
        "use Take Over or FlareSolverr"
    )

    available = {
        "available": True,
        "status": "available",
        "reason": "stealthy_fetcher_available",
    }
    assert sum(bool(value) for value in available.values()) == 3, (
        "precondition: the available state has exactly three truthy fields"
    )
    available_recipes = knowledge.diagnostic_recipes_for(
        "Cloudflare challenge", turnstile_bypass=available
    )
    assert len(available_recipes) == 1
    assert available_recipes[0]["steps"][-1] == (
        "If repeated, the site enabled stricter ruleset — try nodriver via "
        "Scrapling adapter"
    )
    available_catalog = knowledge.all_recipes(turnstile_bypass=available)
    assert len(available_catalog) == 6
    assert sum(
        "Scrapling adapter" in step
        for recipe in available_catalog
        for step in recipe["steps"]
    ) == 1


def test_knowledge_api_injects_the_measured_bypass_state(monkeypatch):
    unavailable = {
        "available": False,
        "status": "unavailable",
        "reason": "stealthy_fetcher_unavailable",
    }
    assert sum(bool(value) for value in unavailable.values()) == 2, (
        "precondition: the API fixture is the exact unavailable state"
    )
    monkeypatch.setattr(
        app_knowledge, "_turnstile_bypass_state", lambda: unavailable
    )
    app = Flask("row360-knowledge")
    registered = app_knowledge.register_routes(app)
    assert registered == 5, "precondition: all five knowledge routes registered"

    response = app.test_client().get(
        "/api/knowledge/recipes?message=Cloudflare%20challenge"
    )
    recipes = response.get_json()["recipes"]

    assert response.status_code == 200
    assert len(recipes) == 1
    assert recipes[0]["steps"][-1] == (
        "Scrapling bypass unavailable (stealthy_fetcher_unavailable); "
        "use Take Over or FlareSolverr"
    )


def test_doctor_measures_the_bypass_and_recommends_the_fetcher_extra(monkeypatch):
    scrapling_dep = [(
        "scrapling",
        "scrapling",
        "adaptive scraping recovery and Turnstile bypass",
    )]
    assert len(scrapling_dep) == 1, (
        "precondition: doctor is isolated to exactly the Scrapling dependency"
    )
    monkeypatch.setattr(doctor, "_OPTIONAL_DEPS", scrapling_dep)
    recovery_only = _partial_scrapling("Adaptor")
    assert _installed_capability_count(recovery_only) == 1
    assert _usable_capability_count(recovery_only) == 1
    monkeypatch.setitem(sys.modules, "scrapling", recovery_only)

    checks = doctor.dependency_checks()

    assert len(checks) == 1
    assert checks[0] == {
        "status": doctor.WARN,
        "test": "dep:scrapling",
        "message": (
            "installed without usable Turnstile bypass "
            "(stealthy_fetcher_unavailable)"
        ),
        "detail": {
            "unlocks": "adaptive scraping recovery and Turnstile bypass",
            "installed": True,
            "available": False,
            "capability_status": "unavailable",
            "reason": "stealthy_fetcher_unavailable",
            "hint": "pip install 'scrapling[fetchers]'",
        },
    }

    broken = _broken_scrapling()
    assert sum(name in broken.__dict__ for name in _CAPABILITY_NAMES) == 0
    monkeypatch.setitem(sys.modules, "scrapling", broken)
    unknown_checks = doctor.dependency_checks()
    assert len(unknown_checks) == 1
    assert unknown_checks[0]["status"] == doctor.WARN
    assert unknown_checks[0]["message"] == (
        "Turnstile bypass availability unknown "
        "(capability_probe_failed:RuntimeError)"
    )
    assert unknown_checks[0]["detail"]["capability_status"] == "unknown"

    complete = _partial_scrapling(*_CAPABILITY_NAMES)
    assert _usable_capability_count(complete) == 2
    monkeypatch.setitem(sys.modules, "scrapling", complete)
    available_checks = doctor.dependency_checks()
    assert len(available_checks) == 1
    assert available_checks[0]["status"] == doctor.OK
    assert available_checks[0]["message"] == (
        "installed; Turnstile bypass usable"
    )


def test_cli_preserves_unknown_instead_of_flattening_it_to_false(
        monkeypatch, capsys):
    body = {
        "ok": True,
        "available": False,
        "stealthy_fetcher": False,
        "capabilities": {
            "adaptive_selectors": {
                "available": False,
                "status": "unknown",
                "reason": "capability_probe_failed:RuntimeError",
            },
            "turnstile_bypass": {
                "available": False,
                "status": "unknown",
                "reason": "capability_probe_failed:RuntimeError",
            },
        },
        "stats": {},
    }
    assert len(body["capabilities"]) == 2, (
        "precondition: the CLI receives exactly both UNKNOWN capabilities"
    )
    monkeypatch.setattr(bdctl, "_request", lambda *args, **kwargs: body)

    bdctl.cmd_scrapling_status(types.SimpleNamespace())
    output = capsys.readouterr().out

    assert output.count("turnstile_bypass:") == 1
    assert (
        "turnstile_bypass: unknown "
        "(capability_probe_failed:RuntimeError)"
    ) in output


def test_provision_probe_has_three_exact_verdicts_and_is_wired(tmp_path):
    states = {
        "available": {
            "available": True,
            "status": "available",
            "reason": "stealthy_fetcher_available",
        },
        "unavailable": {
            "available": False,
            "status": "unavailable",
            "reason": "missing_dependency:patchright",
        },
        "unknown": {
            "available": False,
            "status": "unknown",
            "reason": "capability_probe_failed:RuntimeError",
        },
    }
    assert len(states) == 3, "precondition: the probe fixture has exactly 3 verdicts"
    assert {
        name: scrapling_adapter.turnstile_probe_verdict(state)
        for name, state in states.items()
    } == {
        "available": (0, "available:stealthy_fetcher_available"),
        "unavailable": (1, "unavailable:missing_dependency:patchright"),
        "unknown": (2, "unknown:capability_probe_failed:RuntimeError"),
    }

    setup_path = _REPO / "scripts" / "cloud-setup.sh"
    assert setup_path.is_file(), "precondition: cloud setup exists"
    setup = setup_path.read_text(encoding="utf-8")
    probe = "-m bulk_downloader.scrapling_adapter --probe-turnstile"
    assert setup.count(probe) == 1, (
        "cloud setup does not execute exactly one runtime Turnstile probe"
    )
    assert setup.count("Scrapling Turnstile bypass UNAVAILABLE") == 1
    assert setup.count("Scrapling Turnstile bypass UNKNOWN") == 1

    header = re.search(r"^probe_scrapling_turnstile\(\)\s*\{", setup, re.M)
    assert header, "precondition: the runtime probe function exists"
    tail = setup[header.start():]
    close = re.search(r"^\}", tail, re.M)
    assert close, "precondition: the runtime probe function has a closing brace"
    function_source = tail[:close.end()]
    fake_python = tmp_path / "venv" / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$PROBE_OUTPUT\"\nexit \"$PROBE_RC\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    scenarios = [
        (0, "available:stealthy_fetcher_available", 0,
         "available:stealthy_fetcher_available"),
        (1, "unavailable:missing_dependency:patchright", 1,
         "unavailable:missing_dependency:patchright"),
        (2, "unknown:capability_probe_failed:RuntimeError", 2,
         "unknown:capability_probe_failed:RuntimeError"),
        (1, "Traceback: import crashed", 2,
         "unknown:probe_exit_1:Traceback: import crashed"),
        (0, "malformed success", 2,
         "unknown:probe_exit_0:malformed success"),
    ]
    assert len(scenarios) == 5, (
        "precondition: exactly five process/verdict combinations are exercised"
    )
    for process_rc, process_output, expected_rc, expected_output in scenarios:
        env = dict(os.environ)
        env.update(
            PROBE_RC=str(process_rc),
            PROBE_OUTPUT=process_output,
        )
        result = subprocess.run(
            ["bash", "-c", function_source + "\nprobe_scrapling_turnstile"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == expected_rc
        assert result.stdout.strip() == expected_output
        assert result.stderr == ""
