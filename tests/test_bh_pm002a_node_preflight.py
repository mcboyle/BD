"""PM002a: target-tree Node requirements are checked before deploy mutation."""
import json
from pathlib import Path

import pytest
import test_deploy_script as deploy

BD_GATE_SCOPE = "module"


@pytest.fixture
def fx(monkeypatch):
    seed_files = deploy._seed_files

    def old_frontend(*args, **kwargs):
        files = seed_files(*args, **kwargs)
        files["frontend/package.json"] = json.dumps({"engines": {"node": ">=18"}})
        return files

    monkeypatch.setattr(deploy, "_seed_files", old_frontend)
    fixture = deploy._setup()
    fixture.env["FAKE_NODE_VERSION"] = "v20.0.0"
    fixture.env["FAKE_NODE_RC"] = "0"
    fixture.env["NODE_LOG"] = str(Path(fixture.work) / "node.log")
    deploy._write_exec(Path(fixture.binroot) / "node", '''#!/bin/bash
printf '%s\\n' "$*" >> "$NODE_LOG"
printf '%s\\n' "$FAKE_NODE_VERSION"
exit "$FAKE_NODE_RC"
''')
    deploy._bundle_current(fixture)
    return fixture


def _target(fx, package):
    return deploy._advance_origin(fx, "new frontend requirement",
                                  rel="frontend/package.json", text=package)


def _require(fx, requirement=">=20"):
    return _target(fx, json.dumps({"engines": {"node": requirement}}))


def _assert_refused_without_deploy_mutation(fx, result, old, reason):
    assert result.returncode == 2, deploy._ctx(result)
    assert f"NODE-{reason}" in result.stderr, deploy._ctx(result)
    assert deploy._head(fx.clone) == old, "refusal must precede tree reset"
    assert deploy._read(Path(fx.clone) / deploy.MARKER_REL).strip() == old
    for log in ("pip", "npm", "inv", "sudo", "systemctl"):
        assert deploy._lines(fx.logs[log]) == [], (log, deploy._ctx(result))
    assert deploy._read(fx.env["SVC_STATE"]).strip() == "active"


def test_node18_refused_using_target_requirement_before_mutation(fx):
    old = deploy._head(fx.clone)
    target = _require(fx)
    assert target != old
    assert json.loads((Path(fx.clone) / "frontend/package.json").read_text())[
        "engines"]["node"] == ">=18"
    fx.env["FAKE_NODE_VERSION"] = "v18.19.1"
    result = deploy._deploy(fx)
    _assert_refused_without_deploy_mutation(fx, result, old, "VERSION-UNSUPPORTED")
    assert "v18.19.1" in result.stderr and ">=20" in result.stderr
    assert deploy._lines(fx.env["NODE_LOG"]) == ["--version"]


@pytest.mark.parametrize("version", ["v20.0.0", "v22.23.2"])
def test_supported_node_reaches_frontend_build(fx, version):
    target = _require(fx)
    fx.env["FAKE_NODE_VERSION"] = version
    result = deploy._deploy(fx)
    assert result.returncode == 0, deploy._ctx(result)
    assert deploy._head(fx.clone) == target
    assert "ci" in deploy._read(fx.logs["npm"])
    assert "run build" in deploy._read(fx.logs["npm"])
    assert deploy._read(Path(fx.clone) / deploy.MARKER_REL).strip() == target


def test_minimum_comes_from_target_not_a_hardcoded_twenty(fx):
    old = deploy._head(fx.clone)
    _require(fx, ">=22")
    result = deploy._deploy(fx)
    _assert_refused_without_deploy_mutation(fx, result, old, "VERSION-UNSUPPORTED")
    assert ">=22" in result.stderr


@pytest.mark.parametrize("package", [
    "not JSON", "{}", '{"engines": {}}', '{"engines": {"node": null}}',
    '{"engines": {"node": ">=20.1"}}', '{"engines": {"node": ">=20 || >=18"}}',
])
def test_unmeasured_requirement_is_a_named_refusal(fx, package):
    old = deploy._head(fx.clone)
    _target(fx, package)
    result = deploy._deploy(fx)
    _assert_refused_without_deploy_mutation(fx, result, old, "REQUIREMENT-UNKNOWN")


@pytest.mark.parametrize("version,returncode", [
    ("not-node", "0"), ("", "0"), ("v20", "0"), ("v20.0.0-rc.1", "0"),
    ("v20.0.0", "127"),
])
def test_unmeasured_node_is_a_named_refusal(fx, version, returncode):
    old = deploy._head(fx.clone)
    _require(fx)
    fx.env.update(FAKE_NODE_VERSION=version, FAKE_NODE_RC=returncode)
    result = deploy._deploy(fx)
    _assert_refused_without_deploy_mutation(fx, result, old, "VERSION-UNKNOWN")
    assert deploy._lines(fx.env["NODE_LOG"]) == ["--version"]


def test_missing_target_package_is_a_named_refusal(fx):
    old = deploy._head(fx.clone)
    deploy._git(fx.seed, "rm", "frontend/package.json")
    deploy._git(fx.seed, "commit", "-m", "package absent")
    deploy._git(fx.seed, "push", "origin", "HEAD:refs/heads/main")
    result = deploy._deploy(fx)
    _assert_refused_without_deploy_mutation(fx, result, old, "REQUIREMENT-UNKNOWN")
