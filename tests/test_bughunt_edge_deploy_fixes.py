"""federation-mesh F6 F7 F8 F9 (finder bd-worker-S68-D, probes P34-P38): /api/edge_deploy artifacts.

F6: _bd_service always emitted depends_on flaresolverr, and the routes default flaresolverr off, so the
compose file named a service it did not define.
F7: /all mapped image/port to the compose keys only, so systemd kept port 5000 and k8s the default image.
F8: with_vpn added a gluetun sidecar that no service routed through.
F9: inputs were interpolated raw into YAML and unit text, so a newline added keys and directives. A
non-integer port on /all was an uncaught ValueError, and an out-of-range port was accepted.
"""

from __future__ import annotations

import pytest
import yaml

from bulk_downloader import edge_deploy as ed


BD_GATE_SCOPE = "module"


def _compose(client, **body):
    r = client.post("/api/edge_deploy/compose", json=body)
    assert r.status_code == 200, (r.status_code, r.get_json())
    return yaml.safe_load(r.get_json()["yaml"])["services"]


def _all(client, **body):
    r = client.post("/api/edge_deploy/all", json=body)
    assert r.status_code == 200, (r.status_code, r.get_json())
    return r.get_json()["artifacts"]


def _dangling(services):
    return {name: [d for d in (svc.get("depends_on") or []) if d not in services]
            for name, svc in services.items() if any(d not in services for d in (svc.get("depends_on") or []))}


def test_f6_depends_on_names_only_services_in_the_stack(fresh_app):
    svc = _compose(fresh_app, with_flaresolverr=False)
    assert not _dangling(svc), (
        "EDGE-F6: with_flaresolverr=False rendered services %s with dangling depends_on %r"
        % (sorted(svc), _dangling(svc)))
    for flags in ({}, {"with_qbittorrent": True}, {"with_vpn": True}, {"with_qbittorrent": True, "with_vpn": True}):
        svc = _compose(fresh_app, **flags)
        assert not _dangling(svc), (flags, _dangling(svc))
    assert "depends_on" not in ed.compose_yaml(include_flaresolverr=False)


def test_f6_control_flaresolverr_on_keeps_the_dependency(fresh_app):
    svc = _compose(fresh_app, with_flaresolverr=True)
    assert "flaresolverr" in svc
    assert svc["bulkdownloader"]["depends_on"] == ["flaresolverr"]


def test_f7_all_honours_image_and_port_in_every_artifact(fresh_app):
    a = _all(fresh_app, image="my/img:1", port=8123)
    k8s = [d for d in yaml.safe_load_all(a["k8s.yaml"]) if d and d["kind"] == "Deployment"][0]
    k8s_image = k8s["spec"]["template"]["spec"]["containers"][0]["image"]
    exec_start = [ln for ln in a["bulkdownloader.service"].splitlines() if ln.startswith("ExecStart=")]
    assert k8s_image == "my/img:1" and exec_start[0].endswith(" --port 8123"), (
        "EDGE-F7: /all image my/img:1 port 8123 -> k8s image %r, systemd %r" % (k8s_image, exec_start))
    compose = yaml.safe_load(a["docker-compose.yml"])["services"]["bulkdownloader"]
    assert compose["image"] == "my/img:1" and compose["ports"] == ["8123:5000"], compose
    # The route default port (7777) reaches the unit too, not the module's 5000.
    assert "--port 7777" in _all(fresh_app)["bulkdownloader.service"]


def test_f8_vpn_sidecar_carries_the_bulkdownloader_traffic(fresh_app):
    svc = _compose(fresh_app, with_vpn=True, port=8123)
    bd = svc["bulkdownloader"]
    assert bd.get("network_mode") == "service:vpn", (
        "EDGE-F8: with_vpn -> services %s, bulkdownloader network_mode %r (sidecar routes nothing)"
        % (sorted(svc), bd.get("network_mode")))
    # Compose refuses ports beside network_mode service:<x>; the publish moves to the sidecar.
    assert "ports" not in bd and svc["vpn"]["ports"] == ["8123:5000"], (bd.get("ports"), svc["vpn"])
    assert "vpn" in bd["depends_on"], bd


def test_f8_control_no_vpn_publishes_on_bulkdownloader(fresh_app):
    svc = _compose(fresh_app, port=8123)
    assert "vpn" not in svc
    assert "network_mode" not in svc["bulkdownloader"]
    assert svc["bulkdownloader"]["ports"] == ["8123:5000"]


@pytest.mark.parametrize("route,body", [
    ("compose", {"image": "img:1\n    privileged: true"}),
    ("compose", {"install_dir": "/a\n      - /:/host\n      - /b"}),
    ("all", {"install_dir": "/x\nExecStartPre=/bin/true"}),
    ("all", {"tz": "UTC\n    privileged: true"}),
])
def test_f9_control_characters_are_refused_not_interpolated(fresh_app, route, body):
    r = fresh_app.post("/api/edge_deploy/" + route, json=body)
    out = r.get_json() or {}
    leaked = "privileged: true" in str(out) or "\nExecStartPre=" in str(out.get("artifacts", ""))
    assert r.status_code == 400 and not leaked, (
        "EDGE-F9: %s %r -> %s, injected text in output: %s" % (route, body, r.status_code, leaked))


@pytest.mark.parametrize("field,value,key", [
    ("image", "img:1 #pinned", "image"),
    ("install_dir", "/srv/my bd: dir", "volumes"),
    ("tz", "{privileged: true}", "environment"),
])
def test_f9_yaml_metacharacters_round_trip_as_plain_strings(fresh_app, field, value, key):
    bd = _compose(fresh_app, **{field: value})["bulkdownloader"]
    want = {"image": value, "volumes": value + ":/install", "environment": "TZ=" + value}[key]
    got = bd[key] if key == "image" else bd[key][0]
    assert got == want, "EDGE-F9: %s=%r rendered %s as %r" % (field, value, key, got)
    assert "privileged" not in bd


@pytest.mark.parametrize("route", ["compose", "all"])
@pytest.mark.parametrize("port", ["abc", 99999, 0, -1, True, 80.5, [8080]])
def test_f9_bad_port_is_a_400(fresh_app, route, port):
    r = fresh_app.post("/api/edge_deploy/" + route, json={"port": port})
    assert r.status_code == 400, "EDGE-F9: %s port %r -> %s (want 400)" % (route, port, r.status_code)
    assert r.get_json()["ok"] is False


def test_f9_control_well_formed_input_renders(fresh_app):
    svc = _compose(fresh_app, port="8123", tz="Europe/Berlin", install_dir="/data/bd")
    bd = svc["bulkdownloader"]
    assert bd["ports"] == ["8123:5000"]
    assert bd["environment"][0] == "TZ=Europe/Berlin"
    assert bd["volumes"][0] == "/data/bd:/install"


def test_f9_systemd_escapes_specifiers_and_quotes():
    unit = ed.systemd_unit(install_path='/opt/a"b%i', python_path="/opt/my py/bin/python3")
    assert 'Environment="BD_INSTALL_DIR=/opt/a\\"b%%i"' in unit, unit
    assert "WorkingDirectory=/opt/a\"b%%i\n" in unit, unit
    assert 'ExecStart="/opt/my py/bin/python3" -m bulk_downloader.app --port 5000' in unit, unit
    with pytest.raises(ValueError):
        ed.systemd_unit(user="bd\nExecStartPre=/bin/true")


ASTRAL = "/data/bd\U0001F600"


@pytest.mark.parametrize("loader", [yaml.SafeLoader, getattr(yaml, "CSafeLoader", yaml.SafeLoader)],
                         ids=["SafeLoader", "CSafeLoader"])
def test_r2_f1_astral_chars_round_trip_through_compose_and_k8s(fresh_app, loader):
    # REFUTE R256-B F1: json.dumps wrote U+1F600 as a surrogate pair escape, which YAML refuses.
    try:
        bd = yaml.load(_compose_raw(fresh_app, install_dir=ASTRAL, image="img\U0001F600"), Loader=loader)
        k8s = [d for d in yaml.load_all(ed.k8s_manifests(namespace="ns\U0001F600", image="i\U0001F600",
                                                         storage_class="sc\U0001F600"), Loader=loader) if d]
    except yaml.YAMLError as e:
        pytest.fail("EDGE-R2-F1: astral value made the YAML unparseable: %s" % str(e).splitlines()[0])
    svc = bd["services"]["bulkdownloader"]
    assert svc["volumes"][0] == ASTRAL + ":/install" and svc["image"] == "img\U0001F600", (
        "EDGE-R2-F1: astral value came back as %r / %r" % (svc["volumes"][0], svc["image"]))
    dep = [d for d in k8s if d["kind"] == "Deployment"][0]
    assert dep["metadata"]["namespace"] == "ns\U0001F600"
    assert dep["spec"]["template"]["spec"]["containers"][0]["image"] == "i\U0001F600"
    assert yaml.load(ed._yq(ASTRAL), Loader=loader) == ASTRAL


@pytest.mark.parametrize("ch", ["\u0080", "\u0085", "\u009f", "￾", "ñ", " ", "\\", '"'])
def test_r2_f1_control_bmp_and_escape_chars_still_round_trip(ch):
    # Raw ensure_ascii=False output would break C1 controls and U+FFFE (probe_yq.out); these stay \u-escaped.
    value = "/d" + ch + "\\ud83d\\ude00"
    for loader in (yaml.SafeLoader, getattr(yaml, "CSafeLoader", yaml.SafeLoader)):
        assert yaml.load("k: " + ed._yq(value), Loader=loader)["k"] == value, (ch, ed._yq(value))


def _compose_raw(client, **body):
    r = client.post("/api/edge_deploy/compose", json=body)
    assert r.status_code == 200, (r.status_code, r.get_json())
    return r.get_json()["yaml"]


def test_control_module_defaults_render_as_before():
    assert ed.compose_yaml().count("depends_on:\n      - flaresolverr\n") == 1
    assert "--port 5000" in ed.systemd_unit()
    assert "image: bulkdownloader:latest" in ed.k8s_manifests()
