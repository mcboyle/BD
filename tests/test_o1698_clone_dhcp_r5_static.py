"""o1698-clone-dhcp-r5-static (O1698 / O1707 (1)): lens clones get a pre-flighted static IP from an operator pool.

r4 went live: the clone was customised but never got a lease. DIAGNOSIS (harness-work/FIX/o1698-clone-dhcp-r5-guest-diag)
found two things. The template's 99-static.yaml (dhcp4: false) overrides LinuxPrep's DHCP file. And the router's DHCP
pool overlaps static fleet IPs: it offered spare9's 10.0.70.187. PM RULING-pm-dhcp-overlap-0542Z item 2: write the
static-pool cut, do not run it live.

r5: no DHCP path. The IP comes from $BD_CLONE_IP_POOL (pool + the router's dhcp-range, both operator input; absent ->
REFUSED). Each IP is pre-flighted: outside the DHCP range, not in the live fleet table, no ping, no ARP. The probes are
proven on the source's own address first. The IP is applied with `govc vm.customize -ip/-netmask/-gateway bd-lens-dhcp`
on the powered-off clone, and checked again right before the NIC is connected.

Harness-cut shape (O1045): opt in with BD_TEST_O1698_CLONE_DHCP_R5_STATIC=1. Candidate: BD_O1698_CLONE_DHCP_R5_STATIC_CANDIDATE.
Hermetic: govc, ping and ip are fakes holding state in tmp JSON files; GOVC_URL points at .invalid; no vCenter.
The fake vCenter models what DHCP would do if it ran: an uncustomised-IP clone on the wire is offered 10.0.70.187.
So the r4 candidate (DHCP) shows the hazard as RED.
"""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get(
    "BD_O1698_CLONE_DHCP_R5_STATIC_CANDIDATE",
    "/home/mboyle/bd-persist/harness-work/FIX/o1698-clone-dhcp-r5-static/bd-clone-dhcp.py",
)
pytestmark = pytest.mark.skipif(
    os.environ.get("BD_TEST_O1698_CLONE_DHCP_R5_STATIC") != "1", reason="opt-in: BD_TEST_O1698_CLONE_DHCP_R5_STATIC=1"
)

SRC = "/DC/vm/Linux/Spare/Spare1"
SRC_IP = "10.0.70.197"
SRC_MAC = "00:50:56:83:54:d5"
SPARE9 = "/DC/vm/Linux/Spare/spare9"
SPARE9_IP = "10.0.70.187"  # the router's live DHCP offer (DIAGNOSIS live2/priv.txt) == spare9's static address
POOL = ["10.0.70.240", "10.0.70.241", "10.0.70.242", "10.0.70.243"]
POOL_FILE = "# operator-named (test)\npool 10.0.70.240-10.0.70.243\ndhcp-range\t10.0.70.100-10.0.70.199\n"

SPARE1 = json.loads(
    '{"name":"Spare1","config":{"name":"Spare1","guestId":"ubuntu64Guest","changeVersion":"2026-10-03T01:28:07.331935Z",'
    '"files":{"vmPathName":"[vsanDatastore] spare1-dir/Spare1.vmx"},'
    '"hardware":{"numCPU":16,"memoryMB":49152,"device":[{"key":200,"deviceInfo":{"label":"IDE 0"}},'
    '{"key":1000,"deviceInfo":{"label":"SCSI controller 0"}},{"key":2000,"deviceInfo":{"label":"Hard disk 1"}},'
    '{"key":4000,"deviceInfo":{"label":"Network adapter 1","summary":"DVSwitch: 50 03 bc 60"},'
    '"macAddress":"00:50:56:83:54:d5","addressType":"assigned","connectable":{"migrateConnect":"unset",'
    '"startConnected":true,"allowGuestControl":false,"connected":true,"status":"ok"}}]}},'
    '"runtime":{"powerState":"poweredOn"},"guest":{"ipAddress":"10.0.70.197","hostName":"spare1",'
    '"net":[{"network":"Infrastructure 25GB","ipAddress":["10.0.70.197","fe80::250:56ff:fe83:54d5"],'
    '"macAddress":"00:50:56:83:54:d5","connected":true,"deviceConfigId":4000}]}}'
)


def fleet_vm(name: str, ip: str | None, power: str = "poweredOn") -> dict:
    vm = json.loads(json.dumps(SPARE1))
    vm["name"] = vm["config"]["name"] = name
    vm["runtime"]["powerState"] = power
    vm["guest"] = {"ipAddress": ip, "net": [{"ipAddress": [ip] if ip else [], "connected": bool(ip)}]}
    return vm


DATASTORES = ["vsanDatastore", "datastore1", "datastore1 (1)", "datastore1 (2)", "datastore1 (3)",
              "HA-Heartbeat-01", "HA-Heartbeat-02"]

FAKE_GOVC = r'''
import copy, json, os, sys, time
st_path = os.environ["FAKE_GOVC_STATE"]
def save():
    tmp = st_path + ".tmp"
    json.dump(st, open(tmp, "w"))
    os.replace(tmp, st_path)
st = json.load(open(st_path))
argv = sys.argv[1:]
with open(st["log"], "a") as f:
    f.write(json.dumps(argv) + "\n")
verb, args = argv[0], argv[1:]
vms, sc = st["vms"], st["scenario"]
if verb in sc.get("fail_verbs", []):
    print("fake govc: injected failure", file=sys.stderr)
    sys.exit(1)
def opt(name):
    return args[args.index(name) + 1] if name in args else None
def nic(vm):
    return [d for d in vm["config"]["hardware"]["device"] if "macAddress" in d][0]
def set_ip(vm, ip):
    vm["guest"] = {"ipAddress": ip, "net": [{"ipAddress": [ip] if ip else [], "connected": bool(ip)}]}
def on_wire(vm):
    if vm["runtime"]["powerState"] == "poweredOn" and nic(vm)["connectable"]["connected"]:
        if not vm.get("_cust_ok"):
            ip = vm["_src_ip"]                                   # not customised: the source's static address
        elif vm.get("_fixed_ip"):
            ip = sc.get("guest_ip", vm["_fixed_ip"])             # CustomizationFixedIp
        else:
            ip = sc.get("dhcp_offer", "10.0.70.187")             # DHCP: the router's pool overlaps fleet IPs
        set_ip(vm, ip)
    else:
        set_ip(vm, sc.get("isolated_ip"))
if verb == "find":
    for p in vms:
        if opt("-name") is None and p in sc.get("find_all_omits", []):
            continue  # a partial inventory listing: the fleet table misses this VM
        if opt("-name") is None or p.rsplit("/", 1)[1] == opt("-name"):
            print(p)
elif verb == "vm.info":
    paths = [a for a in args if a != "-json"]
    found = [vms[p] for p in paths if p in vms]
    wired = any("_clone" in vm and vm["runtime"]["powerState"] == "poweredOn" and nic(vm)["connectable"]["connected"]
                for vm in found)
    if sc.get("garble") == "vm.info-when-wired" and wired:
        print('{"virtualMachines": [{"name": "trunc')
    else:
        print(json.dumps({"virtualMachines": found or None}, indent=2))
elif verb == "vm.clone":
    ds = opt("-ds") or os.environ.get("GOVC_DATASTORE")
    stores = st.get("datastores", [])
    if ds is None and len(stores) > 1:
        print("govc: default datastore resolves to multiple instances, please specify", file=sys.stderr)
        sys.exit(1)
    if ds is not None and ds not in stores:
        print(f"govc: datastore '{ds}' not found", file=sys.stderr)
        sys.exit(1)
    src = vms[opt("-vm")]
    name = args[-1]
    c = copy.deepcopy(src)
    c["name"] = c["config"]["name"] = name
    nic(c)["macAddress"] = "00:50:56:aa:bb:cc"
    c["_clone"], c["_cust"], c["_events"], c["_src_ip"] = True, opt("-customization"), [], src["guest"]["ipAddress"]
    c["runtime"]["powerState"] = "poweredOff" if "-on=false" in args else "poweredOn"
    vms[(opt("-folder") or "/DC/vm") + "/" + name] = c
    on_wire(c)
elif verb == "vm.customize":
    vm = vms[opt("-vm")]
    if vm["runtime"]["powerState"] != "poweredOff":
        print("govc: CustomizeVM_Task: the VM must be powered off", file=sys.stderr)
        sys.exit(1)
    if args[-1] != "bd-lens-dhcp":
        print(f"govc: customization spec {args[-1]!r} not found", file=sys.stderr)
        sys.exit(1)
    vm["_cust"], vm["_fixed_ip"] = args[-1], opt("-ip")
    vm["_mask"], vm["_gw"] = opt("-netmask"), opt("-gateway")
elif verb in ("device.connect", "device.disconnect"):
    vm = vms[opt("-vm")]
    conn = nic(vm)["connectable"]
    conn["connected"] = conn["startConnected"] = verb == "device.connect"
    on_wire(vm)
    if verb == "device.connect" and sc.get("touch_source"):
        nic(vms[sc["touch_source"]])["connectable"]["connected"] = False
elif verb == "vm.power":
    vm = vms[args[-1]]
    if "-on" in args:
        vm["runtime"]["powerState"] = "poweredOn"
        if vm.get("_cust") == "bd-lens-dhcp":
            vm["_events"] = {"succeeded": ["CustomizationSucceeded"], "failed": ["CustomizationNetworkSetupFailed"],
                             "none": []}[sc.get("cust", "succeeded")]
            vm["_cust_ok"] = "CustomizationSucceeded" in vm["_events"]
        steal = sc.get("steal")  # another fleet VM comes up on the pool IP while the clone customises
        if steal and "_clone" in vm:
            set_ip(vms[steal["path"]], steal["ip"])
    else:
        vm["runtime"]["powerState"] = "poweredOff"
    on_wire(vm)
elif verb == "events" and sc.get("garble") == "events":
    print('{"createdTime": "2026-10-03T0')
elif verb == "events":
    t = opt("-type")
    for e in vms[args[-1]].get("_events", []):
        if e == t:
            print(json.dumps({"createdTime": "2026-10-03T03:00:00Z", "category": "info", "message": e}, indent=2))
else:
    print("fake govc: unknown verb " + verb, file=sys.stderr)
    sys.exit(9)
save()
'''

# ping / ip: one fake, mode from argv[0]. State: answer (ips that reply), answer_from_call {ip: n} (replies from its
# n-th ping on), arp {ip: mac} (neighbour learned after a ping), offlink [ips], ping_rc {ip: rc} (probe error).
FAKE_NET = r'''
import json, os, sys
st_path = os.environ["FAKE_NET_STATE"]
st = json.load(open(st_path))
mode, args = os.path.basename(sys.argv[0]), sys.argv[1:]
with open(st["log"], "a") as f:
    f.write(json.dumps([mode, *args]) + "\n")
ip = args[-1]
if mode == "ping":
    n = st.setdefault("pings", {}).get(ip, 0) + 1
    st["pings"][ip] = n
    json.dump(st, open(st_path, "w"))
    if ip in st.get("ping_rc", {}):
        print("ping: connect: Network is unreachable", file=sys.stderr)
        sys.exit(st["ping_rc"][ip])
    after = st.get("answer_from_call", {}).get(ip)
    ok = ip in st.get("answer", []) or (after is not None and n >= after)
    print(f"2 packets transmitted, {2 if ok else 0} received")
    sys.exit(0 if ok else 1)
if args[:2] == ["neigh", "show"]:
    mac = st.get("arp", {}).get(ip)
    print(f"{ip} dev ens18 lladdr {mac} REACHABLE" if mac else f"{ip} dev ens18 FAILED")
elif args[:3] == ["-o", "route", "get"]:
    via = " via 10.0.70.1" if ip in st.get("offlink", []) else ""
    print(f"{ip}{via} dev ens18 src 10.0.70.164 uid 1000 \\    cache ")
else:
    sys.exit(9)
'''

WRITE_VERBS = {"vm.clone", "device.connect", "device.disconnect", "vm.power", "vm.destroy", "vm.customize"}


def load_candidate():
    spec = importlib.util.spec_from_file_location("bd_clone_dhcp_r5_candidate", CANDIDATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def default_vms() -> dict:
    return {
        SRC: json.loads(json.dumps(SPARE1)),
        SPARE9: fleet_vm("spare9", SPARE9_IP),
        "/DC/vm/Linux/Spare/spare2": fleet_vm("spare2", "10.0.70.193"),
        "/DC/vm/vRealize": fleet_vm("vRealize", None, "poweredOff"),
    }


class Rig:
    def __init__(self, tmp: Path, scenario: dict, vms: dict | None = None, net: dict | None = None,
                 pool_text: str | None = POOL_FILE):
        self.tmp = tmp
        self.state = tmp / "state.json"
        self.log = tmp / "govc.log"
        self.log.write_text("")
        self.netlog = tmp / "net.log"
        self.netlog.write_text("")
        self.netstate = tmp / "net.json"
        govc = tmp / "govc"
        govc.write_text(f"#!{sys.executable}\n{FAKE_GOVC}")
        govc.chmod(0o755)
        for name in ("ping", "ip"):
            p = tmp / name
            p.write_text(f"#!{sys.executable}\n{FAKE_NET}")
            p.chmod(0o755)
        self.state.write_text(json.dumps({"log": str(self.log), "vms": vms if vms is not None else default_vms(),
                                          "scenario": scenario, "datastores": DATASTORES}))
        n = {"answer": [SRC_IP, SPARE9_IP, "10.0.70.193"], "arp": {SRC_IP: SRC_MAC}}
        n.update(net or {})
        n["log"] = str(self.netlog)
        self.netstate.write_text(json.dumps(n))
        self.env = {
            **{k: v for k, v in os.environ.items() if k not in ("GOVC_DATASTORE", "BD_CLONE_IP_POOL")},
            "LC_ALL": "C",
            "BD_GOVC": str(govc),
            "BD_CLONE_PING": str(tmp / "ping"),
            "BD_CLONE_IP": str(tmp / "ip"),
            "FAKE_GOVC_STATE": str(self.state),
            "FAKE_NET_STATE": str(self.netstate),
            "GOVC_URL": "https://fake.invalid/sdk",
            "BD_CLONE_DHCP_POLL": "0.01",
        }
        if pool_text is not None:
            pool = tmp / "pool.txt"
            pool.write_text(pool_text)
            self.env["BD_CLONE_IP_POOL"] = str(pool)

    def run(self, *args, env_drop=()):
        env = {k: v for k, v in self.env.items() if k not in env_drop}
        return subprocess.run([sys.executable, CANDIDATE, *args], capture_output=True, text=True, env=env, timeout=120,
                              check=False)

    def clone(self, *extra, name="lens-dhcp-1", env_drop=()):
        return self.run("clone", "--source", SRC, "--name", name, "--cust-timeout", "0.3", "--lease-timeout", "0.3",
                        *extra, env_drop=env_drop)

    def popen_clone(self, *extra, name="lens-dhcp-1"):
        args = ["clone", "--source", SRC, "--name", name, *extra]
        return subprocess.Popen([sys.executable, CANDIDATE, *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, env=self.env)

    def calls(self):
        return [json.loads(ln) for ln in self.log.read_text().splitlines() if ln.strip()]

    def net_calls(self):
        return [json.loads(ln) for ln in self.netlog.read_text().splitlines() if ln.strip()]

    def vms(self):
        return json.loads(self.state.read_text())["vms"]

    def clone_vm(self, name="lens-dhcp-1"):
        return self.vms()["/DC/vm/" + name]


def nic(vm):
    return next(d for d in vm["config"]["hardware"]["device"] if "macAddress" in d)


def isolated(vm):
    c = nic(vm)["connectable"]
    return vm["runtime"]["powerState"] == "poweredOff" and not c["connected"] and not c["startConnected"]


def index_of(calls, pred):
    for i, c in enumerate(calls):
        if pred(c):
            return i
    raise AssertionError(f"no matching call in {calls}")


def writes(rig):
    return [c for c in rig.calls() if c[0] in WRITE_VERBS]


def assert_fleet_never_written(calls):
    bad = [c for c in calls if c[0] in WRITE_VERBS and c[0] != "vm.clone" and any(p.startswith("/DC/vm/Linux") for p in c)]
    assert not bad, f"write call named a fleet VM: {bad}"


def spec_from_stdout(out: str) -> dict:
    """The vSphere JSON printed after 'SPEC applied to the clone by vm.customize'."""
    body = out.split("SPEC applied to the clone by vm.customize", 1)[1].split("\n", 1)[1]
    return json.JSONDecoder().raw_decode(body)[0]


def test_candidate_present():
    assert os.path.isfile(CANDIDATE), f"BD_O1698_CLONE_DHCP_R5_STATIC_CANDIDATE={CANDIDATE} is not a file"
    assert os.access(CANDIDATE, os.X_OK), f"{CANDIDATE} is not executable"


# ---- (a) no operator pool -> REFUSED, no DHCP fallback -----------------------------------------------------------


def test_a_no_pool_env_is_refused_before_any_call(tmp_path):
    rig = Rig(tmp_path, {"cust": "succeeded"}, pool_text=None)
    r = rig.clone("--apply")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "RESULT: REFUSED BD_CLONE_IP_POOL is unset" in r.stdout and "COULD NOT LOOK" in r.stdout, r.stdout
    assert rig.calls() == [] and rig.net_calls() == []
    assert "/DC/vm/lens-dhcp-1" not in rig.vms()


def test_a_unreadable_pool_file_is_refused(tmp_path):
    rig = Rig(tmp_path, {"cust": "succeeded"})
    rig.env["BD_CLONE_IP_POOL"] = str(tmp_path / "absent.txt")
    r = rig.clone("--apply")
    assert r.returncode == 2 and "unreadable" in r.stdout and "COULD NOT LOOK" in r.stdout, r.stdout + r.stderr
    assert rig.calls() == []


def test_a_pool_without_dhcp_range_is_refused(tmp_path):
    rig = Rig(tmp_path, {"cust": "succeeded"}, pool_text="pool 10.0.70.240\n")
    r = rig.clone("--apply")
    assert r.returncode == 2 and "no `dhcp-range` line" in r.stdout, r.stdout + r.stderr
    assert rig.calls() == []


@pytest.mark.parametrize("text,needle", [
    ("pool 10.0.70.1\ndhcp-range 10.0.70.100-10.0.70.199\n", "network, broadcast or gateway"),
    ("pool 10.0.71.5\ndhcp-range 10.0.70.100-10.0.70.199\n", "outside 10.0.70.0/24"),
    ("pool 10.0.70.250-10.0.70.240\ndhcp-range 10.0.70.100-10.0.70.199\n", "runs backwards"),
    ("pool 10.0.70.240\nrange 10.0.70.100-10.0.70.199\n", "unknown key 'range'"),
])
def test_a_bad_pool_file_is_refused(tmp_path, text, needle):
    rig = Rig(tmp_path, {"cust": "succeeded"}, pool_text=text)
    r = rig.clone("--apply")
    assert r.returncode == 2 and needle in r.stdout, r.stdout + r.stderr
    assert rig.calls() == []


# ---- (b) a taken / DHCP-range pool IP is skipped; all taken -> REFUSED ---------------------------------------------


@pytest.mark.parametrize("why,kw,needle", [
    ("fleet", {"vms_extra": {"/DC/vm/Linux/x": ("x", "10.0.70.240")}}, "SKIP in the fleet table (x)"),
    ("ping", {"net": {"answer": [SRC_IP, "10.0.70.240"]}}, "SKIP answers ping"),
    ("arp", {"net": {"arp": {SRC_IP: SRC_MAC, "10.0.70.240": "00:11:22:33:44:55"}}}, "SKIP ARP neighbour 00:11:22:33:44:55"),
    ("dhcp", {"pool_text": "pool 10.0.70.150\npool 10.0.70.241\ndhcp-range 10.0.70.100-10.0.70.199\n"},
     "SKIP inside the router DHCP range"),
])
def test_b_taken_pool_ip_is_skipped_for_the_next(tmp_path, why, kw, needle):
    vms = default_vms()
    for path, (name, ip) in kw.get("vms_extra", {}).items():
        vms[path] = fleet_vm(name, ip)
    rig = Rig(tmp_path, {"cust": "succeeded"}, vms=vms, net=kw.get("net"), pool_text=kw.get("pool_text", POOL_FILE))
    r = rig.clone("--apply")
    assert r.returncode == 0, r.stdout + r.stderr
    assert needle in r.stdout, r.stdout
    assert "PREFLIGHT 10.0.70.241: FREE" in r.stdout
    assert "RESULT: FOUND 10.0.70.241 on /DC/vm/lens-dhcp-1" in r.stdout
    cust = next(c for c in rig.calls() if c[0] == "vm.customize")
    assert cust[cust.index("-ip") + 1] == "10.0.70.241"


def test_b_every_pool_ip_taken_is_refused_without_a_write(tmp_path):
    vms = default_vms()
    vms["/DC/vm/Linux/x"] = fleet_vm("x", "10.0.70.240")
    net = {"answer": [SRC_IP, "10.0.70.241"], "arp": {SRC_IP: SRC_MAC, "10.0.70.242": "00:11:22:33:44:55"}}
    pool = "pool 10.0.70.240-10.0.70.242\npool 10.0.70.150\ndhcp-range 10.0.70.100-10.0.70.199\n"
    rig = Rig(tmp_path, {"cust": "succeeded"}, vms=vms, net=net, pool_text=pool)
    r = rig.clone("--apply")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "RESULT: REFUSED no free IP in the 4-address pool" in r.stdout
    assert writes(rig) == []
    assert "/DC/vm/lens-dhcp-1" not in rig.vms()


def test_b_spare9_dhcp_offer_address_in_pool_is_skipped(tmp_path):
    """The live hazard: the router offers spare9's 10.0.70.187. An operator pool that holds it never hands it out."""
    rig = Rig(tmp_path, {"cust": "succeeded"}, pool_text=f"pool {SPARE9_IP}\npool 10.0.70.240\ndhcp-range 10.0.70.10-10.0.70.20\n")
    r = rig.clone("--apply")
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"PREFLIGHT {SPARE9_IP}: SKIP in the fleet table (spare9)" in r.stdout
    assert rig.clone_vm()["guest"]["ipAddress"] == "10.0.70.240"


@pytest.mark.parametrize("net,needle", [
    ({"answer": []}, "ping probe cannot say yes"),
    ({"arp": {}}, "ARP probe cannot say yes"),
    ({"offlink": [SRC_IP]}, "is not on-link from this host"),
    ({"ping_rc": {SRC_IP: 2}}, "ping 10.0.70.197 rc=2"),
])
def test_b_probe_that_cannot_say_yes_is_could_not_look(tmp_path, net, needle):
    rig = Rig(tmp_path, {"cust": "succeeded"}, net=net)
    r = rig.clone("--apply")
    assert r.returncode == 3 and needle in r.stdout, r.stdout + r.stderr
    assert writes(rig) == []


def test_b_fleet_table_missing_the_source_is_could_not_look(tmp_path):
    """r2 (lens D1-D mutant G): the source HAS an IP but the fleet table misses it -> the table cannot say yes."""
    rig = Rig(tmp_path, {"cust": "succeeded", "find_all_omits": [SRC]})
    r = rig.clone("--apply")
    assert r.returncode == 3, r.stdout + r.stderr
    assert f"fleet table probe cannot say yes: the source's own {SRC_IP} is not in it" in r.stdout
    assert writes(rig) == []


def test_b_offlink_pool_ip_is_could_not_look(tmp_path):
    """r2 (lens mutant F): a pool IP the hub reaches via a router -> ARP cannot be observed -> COULD NOT LOOK."""
    rig = Rig(tmp_path, {"cust": "succeeded"}, net={"offlink": ["10.0.70.240"]})
    r = rig.clone("--apply")
    assert r.returncode == 3 and "10.0.70.240 is not on-link from this host" in r.stdout, r.stdout + r.stderr
    assert writes(rig) == []


def test_b_source_without_ipv4_is_could_not_look(tmp_path):
    vms = default_vms()
    vms[SRC]["guest"] = {"ipAddress": None, "net": []}
    rig = Rig(tmp_path, {"cust": "succeeded"}, vms=vms)
    r = rig.clone("--apply")
    assert r.returncode == 3 and "the source reports no IPv4" in r.stdout, r.stdout + r.stderr
    assert writes(rig) == []


# ---- (c) a clean IP -> CustomizationFixedIp with that IP + gateway / mask ------------------------------------------


def test_c_clean_ip_spec_carries_fixed_ip_gateway_mask(tmp_path):
    rig = Rig(tmp_path, {"cust": "succeeded"})
    r = rig.clone("--apply")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PREFLIGHT probes proven on 10.0.70.197" in r.stdout
    item = spec_from_stdout(r.stdout)
    adapter = item["spec"]["nicSettingMap"][0]["adapter"]
    assert adapter["ip"] == {"_vimtype": "vim.vm.customization.FixedIp", "ipAddress": "10.0.70.240"}
    assert adapter["subnetMask"] == "255.255.255.0" and adapter["gateway"] == ["10.0.70.1"]
    assert load_candidate().spec_fixed_ip(item) == ("10.0.70.240", "255.255.255.0", ["10.0.70.1"])
    calls = rig.calls()
    cust = calls[index_of(calls, lambda c: c[0] == "vm.customize")]
    assert cust == ["vm.customize", "-vm", "/DC/vm/lens-dhcp-1", "-ip", "10.0.70.240", "-netmask", "255.255.255.0",
                    "-gateway", "10.0.70.1", "bd-lens-dhcp"]
    vm = rig.clone_vm()
    assert vm["_fixed_ip"] == "10.0.70.240" and vm["guest"]["ipAddress"] == "10.0.70.240"
    assert "RESULT: FOUND 10.0.70.240 on /DC/vm/lens-dhcp-1 (static from the pool" in r.stdout


def test_c_order_clone_disconnect_customize_power_events_recheck_connect(tmp_path):
    rig = Rig(tmp_path, {"cust": "succeeded"})
    r = rig.clone("--apply")
    assert r.returncode == 0, r.stdout + r.stderr
    calls = rig.calls()
    clone = index_of(calls, lambda c: c[0] == "vm.clone")
    assert "-customization" not in calls[clone] and "-on=false" in calls[clone]
    disc = index_of(calls, lambda c: c[0] == "device.disconnect")
    cust = index_of(calls, lambda c: c[0] == "vm.customize")
    on = index_of(calls, lambda c: c[0] == "vm.power" and "-on" in c)
    ok = index_of(calls, lambda c: c[0] == "events" and "CustomizationSucceeded" in c)
    conn = index_of(calls, lambda c: c[0] == "device.connect")
    fleet_reads = [i for i, c in enumerate(calls) if c[0] == "find" and "-name" not in c]
    assert clone < disc < cust < on < ok < conn
    assert len(fleet_reads) == 2 and fleet_reads[0] < clone and ok < fleet_reads[1] < conn, fleet_reads
    pings = [c for c in rig.net_calls() if c[0] == "ping" and c[-1] == "10.0.70.240"]
    assert len(pings) == 2, "pre-flight + re-check before connect"
    assert_fleet_never_written(calls)
    assert rig.vms()[SRC] == SPARE1 and "SOURCE UNTOUCHED" in r.stdout


def test_c_ip_taken_between_preflight_and_connect_never_connects(tmp_path):
    rig = Rig(tmp_path, {"cust": "succeeded"}, net={"answer_from_call": {"10.0.70.240": 2}})
    r = rig.clone("--apply")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "COULD NOT LOOK 10.0.70.240 no longer free before connect (answers ping); NIC never connected" in r.stdout
    assert not [c for c in rig.calls() if c[0] == "device.connect"]
    assert isolated(rig.clone_vm())


def test_c_fleet_vm_takes_ip_between_preflight_and_connect_never_connects(tmp_path):
    """r2 (lens mutant L): the re-check's fleet-table leg. spare2 comes up on .240 (ICMP blocked, no ARP seen)."""
    rig = Rig(tmp_path, {"cust": "succeeded", "steal": {"path": "/DC/vm/Linux/Spare/spare2", "ip": "10.0.70.240"}})
    r = rig.clone("--apply")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "PREFLIGHT 10.0.70.240: FREE" in r.stdout
    assert "COULD NOT LOOK 10.0.70.240 no longer free before connect (in the fleet table (spare2)); NIC never connected" in r.stdout
    assert not [c for c in rig.calls() if c[0] == "device.connect"]
    assert isolated(rig.clone_vm())


def test_c_isolated_guest_on_another_address_never_connects(tmp_path):
    """r2 (lens mutant D): the pre-connect `pre - {ip}` branch."""
    rig = Rig(tmp_path, {"cust": "succeeded", "isolated_ip": "10.0.70.99"})
    r = rig.clone("--apply")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "COULD NOT LOOK clone shows ['10.0.70.99'] while isolated, not 10.0.70.240; NIC never connected" in r.stdout
    assert not [c for c in rig.calls() if c[0] == "device.connect"]
    assert isolated(rig.clone_vm())


def test_c_isolated_guest_already_on_the_pool_ip_still_connects(tmp_path):
    """r2 (lens mutant E): a static guest may report its IP while isolated. The re-check must leave the clone itself
    out of the fleet table, or it would read its own address as taken."""
    rig = Rig(tmp_path, {"cust": "succeeded", "isolated_ip": "10.0.70.240"})
    r = rig.clone("--apply")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT: FOUND 10.0.70.240 on /DC/vm/lens-dhcp-1" in r.stdout
    assert [c for c in rig.calls() if c[0] == "device.connect"]


def test_c_guest_on_another_address_is_could_not_look_and_isolated(tmp_path):
    rig = Rig(tmp_path, {"cust": "succeeded", "guest_ip": "10.0.70.99"})
    r = rig.clone("--apply")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "not the pre-flighted 10.0.70.240" in r.stdout
    assert isolated(rig.clone_vm())


def test_c_spec_converts_to_real_pyvmomi_fixed_ip():
    vim = pytest.importorskip("pyVmomi").vim
    mod = load_candidate()
    obj = mod.to_vim(mod.build_spec_item(ip="10.0.70.240"), vim)
    ad = obj.spec.nicSettingMap[0].adapter
    assert isinstance(ad.ip, vim.vm.customization.FixedIp) and ad.ip.ipAddress == "10.0.70.240"
    assert ad.subnetMask == "255.255.255.0" and list(ad.gateway) == ["10.0.70.1"]
    assert mod.spec_is_dhcp_nic0(mod.build_spec_item()), "the stored base spec stays DHCP (spec subcommand)"


def test_c_dry_run_live_reads_and_probes_only(tmp_path):
    rig = Rig(tmp_path, {})
    r = rig.clone()
    assert r.returncode == 0 and "RESULT: DRY-RUN clone" in r.stdout, r.stdout + r.stderr
    assert "PREFLIGHT 10.0.70.240: FREE" in r.stdout and "vm.customize -vm '<CLONE path from find>' -ip 10.0.70.240" in r.stdout
    assert {c[0] for c in rig.calls()} <= {"find", "vm.info"}
    assert "/DC/vm/lens-dhcp-1" not in rig.vms()


def test_c_source_json_dry_run_makes_no_call(tmp_path):
    rig = Rig(tmp_path, {})
    fixture = tmp_path / "spare1.json"
    fixture.write_text(json.dumps({"virtualMachines": [SPARE1]}))
    r = rig.clone("--dry-run", "--source-json", str(fixture))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PREFLIGHT not run (--source-json" in r.stdout
    assert f"vm.clone -vm {SRC} -on=false -ds vsanDatastore lens-dhcp-1" in r.stdout
    assert rig.calls() == [] and rig.net_calls() == []


# ---- carried from r4: the guard and fail-closed isolation ---------------------------------------------------------


def test_r4_source_address_on_the_wire_is_duplicate_and_isolated(tmp_path):
    rig = Rig(tmp_path, {"cust": "succeeded", "guest_ip": SRC_IP})
    r = rig.clone("--apply")
    assert r.returncode == 4, r.stdout + r.stderr
    assert f"RESULT: FOUND DUPLICATE ['{SRC_IP}'] (source address)" in r.stdout
    assert isolated(rig.clone_vm())


def test_r4_source_address_while_isolated_never_connects(tmp_path):
    rig = Rig(tmp_path, {"cust": "succeeded", "isolated_ip": SRC_IP})
    r = rig.clone("--apply")
    assert r.returncode == 4 and "before connect; NIC never connected" in r.stdout, r.stdout + r.stderr
    assert not [c for c in rig.calls() if c[0] == "device.connect"]
    assert isolated(rig.clone_vm())


def test_r4_customization_failed_never_connects(tmp_path):
    rig = Rig(tmp_path, {"cust": "failed"})
    r = rig.clone("--apply")
    assert r.returncode == 3 and "customization reported CustomizationNetworkSetupFailed" in r.stdout, r.stdout
    assert not [c for c in rig.calls() if c[0] == "device.connect"]
    assert isolated(rig.clone_vm())


def test_r4_customize_failure_never_powers_on(tmp_path):
    rig = Rig(tmp_path, {"cust": "succeeded", "fail_verbs": ["vm.customize"]})
    r = rig.clone("--apply")
    assert r.returncode == 3 and "govc vm.customize rc=1" in r.stdout, r.stdout + r.stderr
    assert not [c for c in rig.calls() if c[0] == "vm.power" and "-on" in c]
    assert isolated(rig.clone_vm())


def test_r4_garbled_vm_info_after_connect_isolates_clone(tmp_path):
    rig = Rig(tmp_path, {"cust": "succeeded", "garble": "vm.info-when-wired"})
    r = rig.clone("--apply")
    assert r.returncode == 3 and "Traceback" not in r.stderr, r.stdout + r.stderr
    assert "clone left powered off, NIC disconnected" in r.stdout
    assert isolated(rig.clone_vm())


def test_r4_sigterm_mid_customization_wait_isolates_clone(tmp_path):
    rig = Rig(tmp_path, {"cust": "none"})
    p = rig.popen_clone("--apply", "--cust-timeout", "60", "--lease-timeout", "60")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if '["events",' in rig.log.read_text():
            break
        time.sleep(0.05)
    else:
        p.kill()
        pytest.fail("candidate never reached the customization poll")
    p.send_signal(signal.SIGTERM)
    out, _ = p.communicate(timeout=60)
    assert p.returncode == 3 and "COULD NOT LOOK Interrupted: SIGTERM" in out, out
    assert isolated(rig.clone_vm())


def test_r4_existing_clone_name_is_refused_without_any_write(tmp_path):
    vms = default_vms()
    vms["/DC/vm/lens-dhcp-1"] = fleet_vm("lens-dhcp-1", None, "poweredOff")
    rig = Rig(tmp_path, {"cust": "succeeded"}, vms=vms)
    r = rig.clone("--apply")
    assert r.returncode == 2 and "REFUSED a VM named lens-dhcp-1 already exists" in r.stdout, r.stdout + r.stderr
    assert writes(rig) == []


def test_r4_write_guard_refuses_any_vm_but_this_runs_clone():
    mod = load_candidate()
    g = mod.Govc(apply=True)
    g.clone_path = "/DC/vm/lens-dhcp-1"
    with pytest.raises(AssertionError, match="not the clone this run created"):
        g.write("vm.customize", "-vm", SRC, "-ip", "10.0.70.240", "bd-lens-dhcp")
    with pytest.raises(AssertionError, match="not the clone this run created"):
        g.write("vm.power", "-off", SPARE9)


def test_r4_no_destroy_path_in_candidate():
    text = Path(CANDIDATE).read_text()
    for needle in ('"vm.destroy"', "Destroy_Task", "vm.unregister"):
        assert needle not in text, needle
