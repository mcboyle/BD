"""fx-templates-vm-seed: the 34 O1517 learned templates resolve as built-ins with the VM file absent.

The 34-entry user_templates.json (identical on all 15 fleet VMs) was never tracked, so a fresh
install lost every learned template. The 12 that are either the only template for their site or
the applied_template on the VMs today are seeded into site_templates/_data_learned_o1517.py; the
other 22 duplicate a built-in for the same site, which keeps resolving that host. Every one of
the 34 must resolve through the built-in lookup alone."""
import json
import re

import pytest

BD_GATE_SCOPE = "module"

# (VM template id, a URL of its site, the built-in id that must resolve it)
_SEEDED = [
    ("user_b6b_cumlouder_o1517_v2_1790641428", "https://www.cumlouder.com/", None),
    ("user_b4b_whoreshub_o1517_1790641782", "https://www.whoreshub.com/", None),
    ("user_b4b_teenfidelity_o1517_1790644224", "https://members.kellymadisonmedia.com/", None),
    ("user_b4b_pornhoarder_o1517_1790644732", "https://pornhoarder.gd/", None),
    ("user_b6b_porndig_o1517_1790650668", "https://www.porndig.com/", None),
    ("user_b6b_justporn_o1517_1790650668", "https://www.justporn.com/", None),
    ("user_b4b_stepsiblingscaught_o1517_1790641966", "https://stepsiblingscaught.com/", None),
    ("user_a9a_naughtyamerica_o1517_1790650832", "https://members.naughtyamerica.com/", None),
    ("user_b1b_xnxx_o1517_1790650872", "https://www.xnxx.com/", None),
    ("user_b7b_dfxtra_o1517_1790651319", "https://www.dfxtra.com/", None),
    ("user_b9b_nubiles_o1517_1790651353", "https://members.nubiles.net/", None),
    ("user_b9b_wowgirls_o1517_1790651781", "https://venus.wowgirls.com/", None),
]
_DEDUPED = [
    ("user_a1a_bang_o1517_1790639781", "https://www.bang.com/", "bang_originals"),
    ("user_b6b_cumlouder_o1517_1790641197", "https://www.cumlouder.com/",
     "user_b6b_cumlouder_o1517_v2_1790641428"),
    ("user_b4b_redtube_o1517_1790641638", "https://www.redtube.com/", "aylo_free_tubes"),
    ("user_b4b_youjizz_o1517_1790641703", "https://www.youjizz.com/", "youjizz"),
    ("user_b4b_ultrafilms_o1517_1790641937", "https://www.ultrafilms.com/", "ultrafilms"),
    ("user_b4b_tiny4k_o1517_1790642003", "https://www.tiny4k.com/", "pornpros_tiny4k"),
    ("user_b4b_site_ma_bangbros_o1517_1790642027", "https://site-ma.bangbros.com/",
     "bangbros_network"),
    ("user_b4b_xhamster_o1517_1790642137", "https://xhamster.com/", "xhamster"),
    ("user_b4b_porntrex_o1517_1790644253", "https://www.porntrex.com/", "porntrex"),
    ("user_b6b_nookies_o1517_1790645015", "https://www.nookies.com/", "nookies"),
    ("user_b6b_nookies_o1517_v2_1790645709", "https://www.nookies.com/", "nookies"),
    ("user_b9b_spankbang_o1517_1790648098", "https://spankbang.com/", "spankbang"),
    ("user_a1a_pornone_o1517_1790650709", "https://pornone.com/", "pornone"),
    ("user_a8a_nubiles_porn_o1517_1790650857", "https://nubiles-porn.com/", "nubiles_network"),
    ("user_a9a_africancasting_o1517_1790650956", "https://africancasting.com/", "africancasting"),
    ("user_a8a_filthykings_o1517_1790650983", "https://filthykings.com/", "filthykings"),
    ("user_b1b_site_ma_brazzers_o1517_1790651264", "https://site-ma.brazzers.com/", "brazzers"),
    ("user_b7b_reptyle_o1517_1790651424", "https://reptyle.com/", "reptyle_teamskeet"),
    ("user_b7b_reptyle_o1517_v2_1790651960", "https://reptyle.com/", "reptyle_teamskeet"),
    ("user_a8a_newsensations_o1517_1790651443", "https://newsensations.com/", "new_sensations"),
    ("user_a5a_evilangel_o1517_1790652044", "https://evilangel.com/", "evilangel"),
    ("user_a5a_nubilefilms_o1517_1790652634", "https://nubilefilms.com/", "nubiles_network"),
]
_VM_SET = [(tid, url, want or tid) for tid, url, want in _SEEDED + _DEDUPED]


@pytest.fixture
def no_vm_file(tmp_path, monkeypatch):
    """Point the user-template overlay at a path that does not exist (the fresh-install state)."""
    from bulk_downloader import user_templates as ut
    path = tmp_path / "user_templates.json"
    monkeypatch.setattr(ut, "USER_TEMPLATES_FILE", path)
    monkeypatch.chdir(tmp_path)  # save_user_template writes selector_history.json into cwd
    assert not path.exists()
    assert ut.list_user_templates() == []
    return path


def _builtin(template):
    from bulk_downloader.site_templates import TEMPLATES
    return any(t is template for t in TEMPLATES)


def test_the_denominator_is_the_34_entry_vm_set():
    ids = [tid for tid, _, _ in _VM_SET]
    assert len(ids) == 34 and len(set(ids)) == 34
    assert len(_SEEDED) == 12 and len(_DEDUPED) == 22


def test_the_overlay_probe_can_say_yes(no_vm_file):
    """Positive control: the patched path IS the one the lookup reads."""
    from bulk_downloader import site_templates as st
    no_vm_file.write_text(json.dumps({"version": 1, "templates": [{
        "id": "user_probe_control", "name": "probe", "description": "",
        "patterns": [r"probe-control\.invalid"],
        "learned": {"download": {"row_selectors": ["video"]}}}]}))
    assert st.get("user_probe_control")["name"] == "probe"
    assert st.suggest_for_url("https://probe-control.invalid/")[0] == "user_probe_control"


@pytest.mark.parametrize("tid,url,want", _VM_SET, ids=[t for t, _, _ in _VM_SET])
def test_every_vm_template_resolves_through_the_builtin_lookup(no_vm_file, tid, url, want):
    from bulk_downloader import site_templates as st
    resolved = st.get(want)
    assert resolved is not None, f"{tid}: {want} does not resolve with the VM file absent"
    assert _builtin(resolved), f"{tid}: {want} resolved outside the built-in TEMPLATES"
    assert want in st.suggest_for_url(url), f"{tid}: {url} does not suggest {want}"


def test_seeded_entries_have_the_builtin_template_shape():
    from bulk_downloader.site_templates._data_learned_o1517 import ITEMS
    assert sorted(t["id"] for t in ITEMS) == sorted(tid for tid, _, _ in _SEEDED)
    for t in ITEMS:
        assert set(t) <= {"id", "name", "description", "patterns", "learned", "config_defaults"}
        assert t["learned"]["download"], t["id"]
        assert all(re.compile(p) for p in t["patterns"]), t["id"]


def test_seeded_entries_carry_no_credential():
    from bulk_downloader import user_templates as ut
    from bulk_downloader.site_templates._data_learned_o1517 import ITEMS
    control = {"id": "c", "learned": {"login": {"cookies": "x", "password": "y"}}}
    assert ut._secret_keys_in(control) == ["learned.login.cookies", "learned.login.password"]
    assert [t["id"] for t in ITEMS if ut._secret_keys_in(t)] == []
    text = json.dumps(ITEMS)
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text), "an email address is committed"
    assert re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", "probe@example.invalid")


_EDIT = "div.operator-edited video source[src*='.mp4']"


def _vm_like_user_file(path, tid):
    """The user file every VM carries today: the seeded entry, as a user teach."""
    import copy
    from bulk_downloader.site_templates._data_learned_o1517 import ITEMS
    entry = copy.deepcopy(next(t for t in ITEMS if t["id"] == tid))
    entry["source"] = "user_teach"
    path.write_text(json.dumps({"version": 1, "templates": [entry]}))
    return entry


def test_an_operator_edit_of_a_seeded_user_id_wins_over_the_builtin_copy(no_vm_file):
    """Lens A10 R1: save_user_template(tid=<seeded id>) must be what get() applies."""
    import copy
    from bulk_downloader import site_templates as st
    from bulk_downloader import user_templates as ut
    tid = "user_b1b_xnxx_o1517_1790650872"
    orig = _vm_like_user_file(no_vm_file, tid)
    learned = copy.deepcopy(orig["learned"])
    learned["download"]["row_selectors"] = [_EDIT] + learned["download"]["row_selectors"][1:]
    ok, res = ut.save_user_template(name=orig["name"], description=orig["description"],
                                    patterns=orig["patterns"], learned=learned,
                                    config_defaults=orig.get("config_defaults"), tid=tid)
    assert ok, res
    assert ut.get_user_template(tid)["learned"]["download"]["row_selectors"][0] == _EDIT
    assert st.get(tid)["learned"]["download"]["row_selectors"][0] == _EDIT, (
        "the built-in seed shadowed the operator's edit")


def test_a_seeded_id_held_in_the_user_file_is_listed_once_as_user(no_vm_file):
    from bulk_downloader import site_templates as st
    tid = "user_b1b_xnxx_o1517_1790650872"
    assert [t["source"] for t in st.list_templates() if t["id"] == tid] == ["builtin"]
    _vm_like_user_file(no_vm_file, tid)
    assert [t["source"] for t in st.list_templates() if t["id"] == tid] == ["user"]
