"""O1567 ORDER-TEMPLATES-TO-REPO cut 2 (fx-templates-missing-9): built-in
templates for the 9 sites the Q-TEMPLATES-IN-REPO audit found with none.

Denominator: the order's 9. Seven get a template (site_templates/_data_extractor_hosts.py);
two were false gaps and get none here:
  * o1513-a6-tplbang2-live runs on www.bang.com, already resolved by the bang_originals
    built-in (positive control below);
  * kellymadisonmedia's applied_template on the VMs is the learned
    user_b4b_teenfidelity_o1517_1790644224, which cut 1 (fx-templates-vm-seed) seeds.

Every URL is the site's last PASS URL, verbatim from /api/history on the VM that owns it.
None of those passes used a download selector, so the templates carry no learned.download:
the load-time gap-fill (app._gap_fill_builtin_download_template) only applies a template that has
one, so already-passing sites are not re-templated. Started by H6 (kimi); finished by H5-B.
"""
from __future__ import annotations

import re

BD_GATE_SCOPE = "module"

from bulk_downloader.site_templates import TEMPLATES, get, suggest_for_url

# (template_id, last-PASS URL verbatim from the VM's /api/history)
CASES = [
    ("dailymotion", "https://www.dailymotion.com/video/xbe8y8e?ref=t165_test2_run"),
    ("ok_ru", "https://ok.ru/video/15848648215119"),
    ("fullporner", "https://fullporner.com/watch/6aba5725ee50424b5f304d89"),
    ("porndoe", "https://porndoe.com/watch/pd0a1a9s7v1x"),
    ("pussyspace",
     "https://www.pussyspace.com/vid-6169108-fubuki-cosplay-full-video-one-punch-man-sweet-darling/"),
    ("scrolller", "https://scrolller.com/yeah-fuck-it-3uxzsyef64?ref=t165_test2_run"),
    ("hustlerunlimited", "https://hustlerunlimited.com/videos/milf-tutor-schooled-my-tool/"),
]

NEW_IDS = [cid for cid, _url in CASES]


def test_every_new_template_resolves_for_its_last_pass_url():
    missing = [(cid, url) for cid, url in CASES if cid not in suggest_for_url(url)]
    assert not missing, f"built-in templates not resolved: {missing}"


def test_each_last_pass_url_resolves_to_exactly_its_own_new_template():
    wrong = [(url, suggest_for_url(url)) for cid, url in CASES
             if [i for i in suggest_for_url(url) if i in NEW_IDS] != [cid]]
    assert not wrong, wrong


def test_new_template_ids_occur_exactly_once_in_the_corpus():
    ids = [t["id"] for t in TEMPLATES]
    dupes = [cid for cid in NEW_IDS if ids.count(cid) != 1]
    assert not dupes, f"template ids not unique: {dupes}"


def test_new_templates_carry_the_pass_url_and_no_unmeasured_selector():
    for cid, url in CASES:
        tpl = get(cid)
        assert isinstance(tpl, dict), f"{cid}: no built-in template"
        assert tpl.get("name"), f"{cid}: no name"
        assert url in tpl.get("description", ""), f"{cid}: description lacks its PASS URL"
        for pat in tpl.get("patterns") or []:
            re.compile(pat)
        assert tpl.get("patterns"), f"{cid}: no patterns"
        assert not (tpl.get("learned") or {}).get("download"), (
            f"{cid}: a download block would be gap-filled onto the passing VM sites")


def test_ok_ru_pattern_does_not_claim_lookalike_hosts():
    assert "ok_ru" not in suggest_for_url("https://www.book.ru/video/1")
    assert "ok_ru" not in suggest_for_url("https://tiktok.ru/video/1")
    assert "ok_ru" in suggest_for_url("https://m.ok.ru/video/15848648215119")


def test_tplbang2_host_is_already_covered_no_duplicate_added():
    # o1513-a6-tplbang2-live last PASS (test2 history, 21:16:20Z, ref=o1567b) is www.bang.com.
    ids = suggest_for_url("https://www.bang.com/video/aHk6mkgmNzZxCY8f/"
                          "emma-rosie-can-barely-fit-stoney-curtis-cock-in-her-pussy?ref=o1567b")
    assert ids.count("bang_originals") == 1, ids
    assert not set(ids) & set(NEW_IDS), ids


def test_unrelated_host_resolves_to_none_of_the_new_templates():
    ids = suggest_for_url("https://example.invalid/watch/abc")
    assert not (set(ids) & set(NEW_IDS)), ids
