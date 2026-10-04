"""O1698 vmware-mcp-hardcoded-cred: the vmware-clones MCP server carries no credential and clones powered off.

Harness cut (RULING-harness-cut-repo-wt.md): the subject is the site-packages module mcp_server_vmware.py, not repo code.
Opt-in: BD_TEST_O1698_VMWARE_MCP_HARDCODED_CRED=1 plus BD_O1698_VMWARE_MCP_HARDCODED_CRED_CANDIDATE=<module path>
  GREEN  /home/mboyle/bd-persist/harness-work/FIX/o1698-vmware-mcp-hardcoded-cred/mcp_server_vmware.py
  RED    the live /home/mboyle/.local/lib/python3.12/site-packages/mcp_server_vmware.py (or its FIX .base copy)
SECRET RULES: no assertion or message prints a source line or a credential. Source checks report COUNTS only; the fake
govc records argv, the test credential it was handed, and only WHETHER GOVC_URL carries userinfo (never the URL).
SAFETY: the module is driven in a child python whose subprocess.run sends any govc call to the fake and refuses any
other program, so the real govc (and vCenter) is never reached, not even by the RED module.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_O1698_VMWARE_MCP_HARDCODED_CRED_CANDIDATE", "")
OPTED_IN = os.environ.get("BD_TEST_O1698_VMWARE_MCP_HARDCODED_CRED") == "1"
pytestmark = pytest.mark.skipif(not (OPTED_IN and CANDIDATE), reason="candidate opt-in required")
PYTHON = os.environ.get("BD_VMWARE_MCP_PYTHON", "/usr/bin/python3")  # the interpreter whose user site has `mcp`

CRED_IN_URL = re.compile(r'://[^/ "]*:[^/ "]*@')
PASSWORD_LITERAL = re.compile(r"""(?i)\b\w*(pass(word)?|passwd|pwd|secret)\w*\s*[:=]\s*f?["'][^"'{}]+["']""")

FAKE_GOVC = r"""#!{python}
import json, os, sys, urllib.parse
with open(os.environ["FAKE_GOVC_LOG"], "a") as fh:
    fh.write(json.dumps({{"argv": sys.argv[1:], "user": os.environ.get("GOVC_USERNAME"),
                         "password": os.environ.get("GOVC_PASSWORD"),
                         "url_has_userinfo": "@" in os.environ.get("GOVC_URL", "")}}) + "\n")
print("fake-govc ok")
if os.environ.get("FAKE_GOVC_ECHO") == "1":  # an auth failure that forwards the password, raw and percent-encoded
    pw = os.environ.get("GOVC_PASSWORD") or ""
    print("POST https://" + os.environ.get("GOVC_USERNAME", "") + ":" + urllib.parse.quote(pw, safe="") + "@vc/sdk")
    print("auth failed: incorrect user name or password (" + pw + ")", file=sys.stderr)
    sys.exit(1)
"""

DRIVER = r"""
import importlib.machinery, importlib.util, json, os, subprocess, sys
module_path, host, call = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
real_run = subprocess.run
def guarded_run(cmd, *a, **kw):
    if not cmd or os.path.basename(str(cmd[0])) != "govc":
        raise SystemExit("REFUSED: driver only lets the module run govc (got another program)")
    return real_run([os.environ["FAKE_GOVC"], *cmd[1:]], *a, **kw)
subprocess.run = guarded_run
sys.argv = ["mcp_server_vmware", "--host", host]
loader = importlib.machinery.SourceFileLoader("mcp_server_vmware_under_test", module_path)  # any suffix (.py.base too)
spec = importlib.util.spec_from_loader(loader.name, loader)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.subprocess.run = guarded_run
print(json.dumps({"returned": getattr(mod, call["fn"])(**call["kwargs"])}))
"""


@pytest.fixture
def rig(tmp_path):
    module = Path(CANDIDATE)
    assert module.is_file(), f"candidate module absent: {module}"
    probe = subprocess.run([PYTHON, "-c", "import mcp.server.fastmcp"], capture_output=True, text=True, check=False)
    assert probe.returncode == 0, f"COULD NOT LOOK: {PYTHON} cannot import mcp.server.fastmcp"
    fake = tmp_path / "govc"
    fake.write_text(FAKE_GOVC.format(python=PYTHON))
    fake.chmod(0o755)
    log = tmp_path / "govc.jsonl"

    def call(fn: str, host: str = "10.0.70.1", cred: str | None = "", cred_path: str = "", echo_cred: bool = False,
             **kwargs) -> tuple[str, list[dict]]:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GOVC_", "BD_VC_CRED"))}
        env.update(FAKE_GOVC=str(fake), FAKE_GOVC_LOG=str(log), PYTHONDONTWRITEBYTECODE="1", LC_ALL="C")
        if cred is not None:
            path = tmp_path / "vc-cred.txt"
            path.write_text(cred)
            env["BD_VC_CRED_FILE"] = str(path)
        if cred_path:
            env["BD_VC_CRED_FILE"] = cred_path
        if echo_cred:
            env["FAKE_GOVC_ECHO"] = "1"
        r = subprocess.run([PYTHON, "-c", DRIVER, str(module), host, json.dumps({"fn": fn, "kwargs": kwargs})],
                           capture_output=True, text=True, timeout=60, env=env, check=False)
        assert r.returncode == 0, f"driver failed rc={r.returncode}: {r.stderr[-800:]}"
        runs = [json.loads(ln) for ln in log.read_text().splitlines()] if log.exists() else []
        log.unlink(missing_ok=True)
        return json.loads(r.stdout.strip().splitlines()[-1])["returned"], runs

    return call


GOOD = "# test credential, not a real one\nVCENTER_USER=vc-test-user\nVCENTER_PASSWORD=vc=test=pw\nESXI_USER=esxi-test\nESXI_PASSWORD=esxi-pw\n"


def test_source_carries_no_credential_in_a_url_or_a_password_literal():
    # positive control first: both patterns can say yes (rule 7)
    assert len(CRED_IN_URL.findall('u = "https://someone:hunter2@10.0.0.1/sdk"')) == 1
    assert len(PASSWORD_LITERAL.findall('admin_password = "hunter2"')) == 1
    src = Path(CANDIDATE).read_text()
    in_url = len(CRED_IN_URL.findall(src))
    literal = len(PASSWORD_LITERAL.findall(src))
    assert (in_url, literal) == (0, 0), f"VMWARE-CRED: credential-in-URL count {in_url}, password-literal count {literal}"


def test_vm_clone_powers_off_by_default(rig):
    _out, runs = rig("vm_clone", cred=GOOD, clone_name="c1", template_or_vm="spare1")
    assert len(runs) == 1 and runs[0]["argv"][0] == "vm.clone", "VMWARE-CRED: govc vm.clone not run exactly once"
    assert "-on=false" in runs[0]["argv"], "VMWARE-CRED: vm_clone does not pass -on=false by default"
    assert runs[0]["argv"][-3:] == ["-vm", "spare1", "c1"]


def test_vm_clone_power_on_is_an_explicit_opt_in(rig):
    _out, runs = rig("vm_clone", cred=GOOD, clone_name="c1", template_or_vm="spare1", snapshot="s0", power_on=True)
    assert len(runs) == 1 and "-on=true" in runs[0]["argv"] and "-on=false" not in runs[0]["argv"]
    assert runs[0]["argv"][-5:] == ["-snapshot", "s0", "-vm", "spare1", "c1"]


@pytest.mark.parametrize("host,user,password", [("10.0.70.1", "vc-test-user", "vc=test=pw"),
                                                ("10.0.30.9", "esxi-test", "esxi-pw")])
def test_cred_file_reaches_govc_as_username_and_password_not_in_the_url(rig, host, user, password):
    _out, runs = rig("vm_list", host=host, cred=GOOD)
    assert len(runs) == 1, "VMWARE-CRED: govc not run"
    assert (runs[0]["user"], runs[0]["password"]) == (user, password), "VMWARE-CRED: cred file pair not passed to govc"
    assert runs[0]["url_has_userinfo"] is False, "VMWARE-CRED: GOVC_URL still carries userinfo"


@pytest.mark.parametrize("cred,needle", [(None, "BD_VC_CRED_FILE is not set"),
                                         ("VCENTER_USER=only-user\n", "has no VCENTER_PASSWORD")])
def test_missing_credential_is_could_not_look_and_govc_never_runs(rig, cred, needle):
    out, runs = rig("vm_destroy", cred=cred, vm_name="x")
    assert out.startswith("COULD NOT LOOK") and needle in out, f"VMWARE-CRED: wrong answer without a credential: {out[:120]}"
    assert runs == [], "VMWARE-CRED: govc ran with no credential"


def test_unreadable_cred_file_is_could_not_look(rig, tmp_path):
    out, runs = rig("vm_info", cred=None, cred_path=str(tmp_path / "absent.txt"), vm_name="x")
    assert out.startswith("COULD NOT LOOK") and "unreadable (FileNotFoundError)" in out, out[:120]
    assert runs == []


def test_answers_never_echo_the_password(rig):
    out, runs = rig("vm_info", host="10.0.30.9", cred="ESXI_PASSWORD=esxi-pw\n", vm_name="x")  # user missing
    assert out.startswith("COULD NOT LOOK") and runs == [] and "esxi-pw" not in out
    out, _ = rig("vm_list", cred=GOOD)
    assert "vc=test=pw" not in out


@pytest.mark.parametrize("fn,kwargs,argv", [("vm_list", {}, ["find", "/", "-type", "m"]),
                                            ("vm_info", {"vm_name": "v1"}, ["vm.info", "v1"]),
                                            ("vm_destroy", {"vm_name": "v1"}, ["vm.destroy", "v1"])])
def test_list_info_destroy_run_the_same_govc_command(rig, fn, kwargs, argv):
    out, runs = rig(fn, cred=GOOD, **kwargs)
    assert [r["argv"] for r in runs] == [argv] and "fake-govc ok" in out


TOOLS = [("vm_list", {}), ("vm_info", {"vm_name": "v1"}), ("vm_clone", {"clone_name": "c1", "template_or_vm": "spare1"}),
         ("vm_destroy", {"vm_name": "v1"})]


@pytest.mark.parametrize("fn,kwargs", TOOLS)
def test_govc_output_carrying_the_password_comes_back_redacted(rig, fn, kwargs):
    out, runs = rig(fn, cred=GOOD, echo_cred=True, **kwargs)
    assert len(runs) == 1 and runs[0]["password"] == "vc=test=pw", "VMWARE-CRED: fake govc not handed the password"
    leaked = sum(form in out for form in ("vc=test=pw", "vc%3Dtest%3Dpw"))  # raw (stderr) and URL-encoded (stdout)
    assert leaked == 0, f"VMWARE-CRED: govc output returned with {leaked} password form(s) unredacted"
    assert out.count("<redacted>") == 2 and "auth failed" in out and "vc-test-user" in out, out[:200]


@pytest.mark.parametrize("fn,kwargs", TOOLS)
def test_corrupt_utf8_cred_file_is_could_not_look(rig, tmp_path, fn, kwargs):
    bad = tmp_path / "corrupt.txt"
    bad.write_bytes(b"VCENTER_USER=vc-test-user\nVCENTER_PASSWORD=vc=test=pw\xff\xfe\n")
    out, runs = rig(fn, cred=None, cred_path=str(bad), **kwargs)
    assert "vc=test=pw" not in out, "VMWARE-CRED: corrupt cred file answer carries the password"
    assert out.startswith("COULD NOT LOOK") and "unreadable (UnicodeDecodeError)" in out, f"VMWARE-CRED: {out[:120]}"
    assert runs == [], "VMWARE-CRED: govc ran on a corrupt cred file"
