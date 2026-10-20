"""BH1-64 withhold-text-free-text-bearer.

egress_withhold.withhold_text replaced only URL-shaped tokens, one token at a
time, so a credential after ``Bearer``, ``Authorization:`` (any case) or a
``token=`` followed by whitespace was served as is in every free-text and
diagnostic field: ``-H Authorization: Bearer SECRETCANARY`` came back
unchanged. The credential is now MASK (``***``); prose and ordinary URLs keep
today's output.
"""
from __future__ import annotations

import random
import re

import pytest

from bulk_downloader import egress_withhold as W

BD_GATE_SCOPE = "module"

C = "SECRETCANARY"

# Each input below was served with SECRETCANARY in it at BASE 13eaeafc.
MASKED = {
    "curl-bearer": (f"-H Authorization: Bearer {C}", "-H Authorization: ***"),
    "bare-bearer": (f"Bearer {C}", "Bearer ***"),
    "bearer-colon": (f"Bearer: {C}", "Bearer: ***"),
    "auth-basic": (f"Authorization: Basic {C}", "Authorization: ***"),
    "auth-bearer": (f"Authorization: Bearer {C}", "Authorization: ***"),
    "auth-mixed-case": (f"AUTHORIZATION: bearer {C}", "AUTHORIZATION: ***"),
    "auth-lower-tabs": (f"authorization:\tBearer\t{C}", "authorization:\t***"),
    "auth-json": (f'"Authorization": "Bearer {C}"', '"Authorization": ***'),
    "auth-digest-rest": (f'Proxy-Authorization: Digest u="a", response="{C}"\nnext ok',
                         "Proxy-Authorization: ***\nnext ok"),
    "auth-next-line": (f"Authorization:\n{C}", "Authorization:\n***"),
    "token-space": (f"token= {C}", "[url withheld] ***"),
    "token-spaced-eq": (f"access_token = {C}", "access_token = ***"),
    "token-mixed-case": (f"refused: Token = {C} (401)", "refused: Token = *** (401)"),
}

# Byte-identical to BASE (measured): prose with the word "token", ordinary
# URLs, a glued token= that the URL pass already withheld.
UNCHANGED = {
    "prose-token": "the token was rejected",
    "prose-token-expired": "token expired, please log in again",
    "plain-url": "see https://example.com/a for docs",
    "glued-token": "[url withheld]",
}
UNCHANGED_INPUT = {"glued-token": f"token={C}"}


@pytest.mark.parametrize("name", sorted(MASKED))
def test_credential_after_trigger_is_masked(name):
    text, want = MASKED[name]
    got = W.withhold_text(text)
    assert C not in got, f"BH1-64 LEAK {name}: {got!r}"
    assert got == want


@pytest.mark.parametrize("name", sorted(UNCHANGED))
def test_prose_and_urls_keep_todays_output(name):
    text = UNCHANGED_INPUT.get(name, UNCHANGED[name])
    want = UNCHANGED[name] if name in UNCHANGED_INPUT else text.replace(
        "https://example.com/a", W.PLACEHOLDER)
    assert W.withhold_text(text) == want


def test_dict_keys_and_lists_are_masked():
    got = W.withhold_text({f"Authorization: Bearer {C}": [f"Bearer {C}"]})
    assert got == {"Authorization: ***": ["Bearer ***"]}


def test_diagnostic_field_is_masked():
    got = W.diagnostic_view({"error": f"HTTP 401 for -H 'Authorization: Bearer {C}'",
                             "count": 1})
    assert C not in got["error"] and got["count"] == 1


def test_served_form_is_stable():
    once = W.withhold_text(f"-H Authorization: Bearer {C}")
    assert W.withhold_text(once) == once


# r2 (SEC F1): a separator after Bearer (space, then ":" or "=") was taken as
# the credential and the value after it served.
SEPARATOR = {
    "bearer-space-eq": (f"Bearer = {C}", "Bearer = ***"),
    "bearer-space-colon": (f"bearer : {C}", "bearer : ***"),
    "bearer-newline-eq": (f"Bearer\n= {C}", "Bearer\n= ***"),
    # ":=" alone is a token the URL pass withholds; it stays withheld.
    "bearer-double-sep": (f"Bearer := {C}", f"Bearer {W.PLACEHOLDER} ***"),
    "token-double-eq": (f"access_token = = {C}", "access_token = = ***"),
}


@pytest.mark.parametrize("name", sorted(SEPARATOR))
def test_bearer_separator_is_not_the_credential(name):
    text, want = SEPARATOR[name]
    got = W.withhold_text(text)
    assert C not in got, f"BH1-64 LEAK {name}: {got!r}"
    assert got == want


def test_bearer_separator_diagnostic_field_is_masked():
    got = W.diagnostic_view({"error": f"HTTP 401 Bearer = {C}"})
    assert C not in got["error"], f"BH1-64 LEAK diag-separator: {got!r}"


def _base(text):
    # BASE 13eaeafc withhold_text for a str: the per-token URL pass alone.
    return W._TOKEN.sub(W._token, text)


# r2 (corr F1): a trigger glued inside a token BASE withheld masked only the
# token's tail; the head was then judged alone and served.
GLUED = {
    "json-compact-auth": '{"password":"%s","authorization":"tok","next":"https://h.example/x"}' % C,
    "glued-bearer-colon": '"%sbearer:a/b' % C,
    "glued-bearer-eq": '%s"bearer:=' % C,
}


@pytest.mark.parametrize("name", sorted(GLUED))
def test_glued_trigger_keeps_base_withholding(name):
    text = GLUED[name]
    got = W.withhold_text(text)
    assert C not in got, f"BH1-64 LEAK {name}: {got!r}"
    assert got == _base(text) == W.PLACEHOLDER


def test_glued_trigger_diagnostic_field_is_withheld():
    err = 'upstream said {"password":"%s","authorization":"x","next":"https://h/x"}' % C
    got = W.diagnostic_view({"error": err})
    assert C not in got["error"], f"BH1-64 LEAK diag-glued: {got!r}"


_FRAG = ["Authorization:", "authorization=", "Bearer", "bearer:", "token=", "access_token =",
         "https://u:", "@h.example/x?", "the", "token", "of", "\n", "\r\n", "\t", " ", "  ",
         '"', "'", ":", "=", "www.", "%3A", C, C, C, "http://h/", "a/b", "&x=1"]
_TRIGGER = re.compile(r"authorization|bearer|token", re.IGNORECASE)


def _corpus(seed, n=4000):
    rnd = random.Random(seed)
    return ["".join(rnd.choice(_FRAG) for _ in range(rnd.randint(1, 12))) for _ in range(n)]


def test_never_serves_what_base_withheld():
    leaks = [s for s in _corpus(1964) if C not in _base(s) and C in W.withhold_text(s)]
    assert not leaks, f"BH1-64 LEAK differential {len(leaks)}: {leaks[0]!r} -> {W.withhold_text(leaks[0])!r}"


def test_no_trigger_text_is_byte_identical_to_base():
    differ = [s for s in _corpus(7) if not _TRIGGER.search(s) and W.withhold_text(s) != _base(s)]
    assert not differ, differ[:3]


def test_served_form_is_stable_on_corpus():
    unstable = [s for s in _corpus(11) if W.withhold_text(W.withhold_text(s)) != W.withhold_text(s)]
    assert not unstable, unstable[:3]
