"""Phase 9.12 -- selector self-consistency (RED-first)."""
from bulk_downloader import selector_check

_DOM = '''
<div><button id="dl" class="download-btn">Download</button>
<a class="link" href="#">x</a><a class="link" href="#">y</a>
<a class="link" href="#">z</a><a class="link" href="#">w</a>
<a class="link" href="#">v</a><a class="link" href="#">u</a>
<span id="hp" class="honeypot">trap</span></div>
'''

def test_zero_match_rejected():
    r=selector_check.check_candidate("#nope", _DOM)
    assert r["status"]=="reject" and r["reason"]=="zero-match"

def test_single_match_approved():
    r=selector_check.check_candidate("#dl", _DOM)
    assert r["status"]=="approve" and r["count"]==1

def test_too_many_review():
    r=selector_check.check_candidate(".link", _DOM)
    assert r["status"]=="review" and r["reason"]=="too-many-matches"

def test_brittle_warned():
    r=selector_check.check_candidate("#dl:nth-child(2)", _DOM)
    assert r["status"]=="warn" and "brittle" in r["reason"]

def test_honeypot_review():
    r=selector_check.check_candidate("#hp", _DOM)
    assert r["status"]=="review" and "honeypot" in r["reason"]

def test_model_cannot_approve():
    # deterministic reject/review/warn cannot be promoted to approve by the model
    assert selector_check.merge_model("reject", True)=="reject"
    assert selector_check.merge_model("review", True)=="review"
    assert selector_check.merge_model("warn", True)=="warn"
    assert selector_check.merge_model("approve", False)=="approve"

def test_resolver_is_authority():
    r=selector_check.resolve("#dl", _DOM)
    assert r["count"]==1
