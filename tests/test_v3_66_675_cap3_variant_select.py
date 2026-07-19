"""v3.66.675 -- CAP-3: runtime multi-variant template selection.

find_template_for_url picked among same-host templates by host specificity only;
when a host has multiple enabled template VARIANTS (e.g. different page layouts)
there was no way to choose the one that actually fits the page in hand. This cut
adds, in template_registry:
  * find_template_variants_for_url(url): every host-matching template (the variants).
  * score_template_against_html(template, html): variant fitness in [0,1] -- the
    fraction of the template's evaluable leaf CSS selectors that match >=1 element
    in the page (BS4; playwright-only selectors excluded from the denominator).
  * select_best_variant(url, html): the highest-scoring variant (ties -> most host-
    specific).
  * find_template_for_url(url, *, html=None): backward-compatible -- html=None keeps
    today's host-specificity behavior; html given + >1 variant -> the best-fit variant.

Wired at the safe dry_run seam (never fetches/downloads). Zero-arg tests via a temp
template dir.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from bulk_downloader import template_registry as tr


def _write_template(dirpath, name, host, selectors, status="enabled"):
    p = Path(dirpath) / f"{name}.template.json"
    p.write_text(json.dumps({"host": host, "status": status,
                             "selectors": selectors}), encoding="utf-8")
    return str(p)


def _two_variants(dirpath):
    # Variant A keys off a #player-classic download button.
    _write_template(dirpath, "site_a", "vids.example.com",
                    {"download": {"btn": "a.dl-classic"}})
    # Variant B keys off a #player-modern download control.
    _write_template(dirpath, "site_b", "vids.example.com",
                    {"download": {"btn": "button.dl-modern"}})


HTML_CLASSIC = '<html><body><a class="dl-classic" href="/x.mp4">get</a></body></html>'
HTML_MODERN = '<html><body><button class="dl-modern">download</button></body></html>'


def test_find_variants_lists_all_same_host_templates():
    d = tempfile.mkdtemp(prefix="cap3_")
    _two_variants(d)
    variants = tr.find_template_variants_for_url("https://vids.example.com/watch",
                                                 template_dirs=[d])
    hosts = [v.get("host") for v in variants]
    assert len(variants) == 2, variants
    assert hosts == ["vids.example.com", "vids.example.com"]


def test_score_discriminates_by_selector_presence():
    tA = {"selectors": {"download": {"btn": "a.dl-classic"}}}
    assert tr.score_template_against_html(tA, HTML_CLASSIC) > 0.0
    assert tr.score_template_against_html(tA, HTML_MODERN) == 0.0
    assert tr.score_template_against_html(tA, "") == 0.0


def test_select_best_variant_picks_the_fitting_layout():
    d = tempfile.mkdtemp(prefix="cap3_")
    _two_variants(d)
    best_classic = tr.select_best_variant("https://vids.example.com/watch",
                                          HTML_CLASSIC, template_dirs=[d])
    best_modern = tr.select_best_variant("https://vids.example.com/watch",
                                         HTML_MODERN, template_dirs=[d])
    assert best_classic["selectors"]["download"]["btn"] == "a.dl-classic"
    assert best_modern["selectors"]["download"]["btn"] == "button.dl-modern"


def test_find_template_for_url_html_none_is_unchanged():
    d = tempfile.mkdtemp(prefix="cap3_")
    # single template -> host-specificity path, html-agnostic
    _write_template(d, "solo", "solo.example.com",
                    {"download": {"btn": "a.dl"}})
    t1 = tr.find_template_for_url("https://solo.example.com/x", template_dirs=[d])
    t2 = tr.find_template_for_url("https://solo.example.com/x", template_dirs=[d],
                                  html=None)
    assert t1 is not None and t1 == t2
    assert t1["host"] == "solo.example.com"


def test_find_template_for_url_with_html_selects_variant():
    d = tempfile.mkdtemp(prefix="cap3_")
    _two_variants(d)
    got = tr.find_template_for_url("https://vids.example.com/watch",
                                   template_dirs=[d], html=HTML_MODERN)
    assert got["selectors"]["download"]["btn"] == "button.dl-modern"
