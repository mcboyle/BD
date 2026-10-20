"""BH1-71 credential-text-forms-followup.

bh1-64 made withhold_text mask the credential after ``Authorization:``,
``Bearer`` and ``token=``; every other spelling of the same header still served
it: a JSON key (``"access_token": "X"``, ``"bearer": "X"``), ``token: X``,
``X-Api-Key: X``, a fullwidth / ratio colon or fullwidth equals, a dash or em
dash, NBSP or a newline before the separator, a zero-width space / BOM / ``+``
right after ``Bearer``, and a kept diagnostic segment that ends in the trigger
(``token=`` kept, ``X`` served). Each form below is now MASK, prose stays.
"""
from __future__ import annotations

import random
import re
import time

import pytest

from bulk_downloader import egress_withhold as W

BD_GATE_SCOPE = "module"

C = "SECRETCANARY"

# Each input was served with SECRETCANARY in it at BASE dc2dce95.
MASKED = {
    "json-access-token": (f'"access_token": "{C}"', '"access_token": ***'),
    "json-bearer": (f'"bearer": "{C}"', '"bearer": ***'),
    "json-token-single-quoted": (f"'token': '{C}'", "'token': ***"),
    "json-x-api-key": (f'"x-api-key": "{C}"', '"x-api-key": ***'),
    "token-colon": (f"token: {C}", "token: ***"),
    "token-newline-colon": (f"token\n: {C}", "token\n: ***"),
    "token-newline-eq": (f"token\n= {C}", "token\n= ***"),
    "token-nbsp-eq": (f"token\xa0= {C}", "token\xa0= ***"),
    "x-api-key": (f"X-Api-Key: {C}", "X-Api-Key: ***"),
    "api-key-spaced-eq": (f"api_key = {C}", "api_key = ***"),
    "apikey-colon": (f"ApiKey: {C}", "ApiKey: ***"),
    "bearer-fullwidth-colon": (f"Bearer： {C}", "Bearer： ***"),
    "bearer-fullwidth-colon-glued": (f"Bearer：{C}", "Bearer：***"),
    "bearer-ratio-colon": (f"Bearer ∶ {C}", "Bearer ∶ ***"),
    "bearer-fullwidth-eq": (f"Bearer ＝ {C}", "Bearer ＝ ***"),
    "bearer-dash": (f"Bearer - {C}", "Bearer - ***"),
    "bearer-em-dash": (f"Bearer — {C}", "Bearer — ***"),
    "bearer-zwsp": (f"Bearer​{C}", "Bearer​***"),
    "bearer-zwsp-space": (f"Bearer​ {C}", "Bearer​ ***"),
    "bearer-bom": (f"Bearer﻿{C}", "Bearer﻿***"),
    "bearer-plus": (f"Bearer+{C}", "Bearer+***"),
    "auth-nbsp-colon": (f"Authorization\xa0: {C}", "Authorization\xa0: ***"),
    "auth-newline-colon": (f"Authorization\n: {C}", "Authorization\n: ***"),
    "auth-fullwidth-colon": (f"Authorization：{C}", "Authorization：***"),
    "auth-em-dash": (f"Authorization — {C}", "Authorization — ***"),
}

# Already closed (bh1-64 or the per-token pass); pinned so they stay closed.
PINNED = {
    "bearer-nbsp-colon": (f"Bearer\xa0: {C}", "Bearer\xa0: ***"),
    "bearer-nbsp": (f"Bearer\xa0{C}", "Bearer\xa0***"),
    "bearer-newline": (f"Bearer\n{C}", "Bearer\n***"),
    "auth-json-bearer": (f'"Authorization": "Bearer {C}"', '"Authorization": ***'),
    "token-colon-glued": (f"token:{C}", W.PLACEHOLDER),
    "x-api-key-glued": (f"X-Api-Key:{C}", W.PLACEHOLDER),
    "api-key-eq-glued": (f"api_key={C}", W.PLACEHOLDER),
}

# Ordinary prose around the trigger words: byte-identical to the input.
PROSE = [
    "token bucket refill",
    "max_tokens: 100 (tokens: 5 left)",
    "the bearer-token header was absent",
    "api keys: rotate monthly",
    "x-api-key header missing",
    "the token expired at 10:00",
    "refresh-token-rotation is on",
    "pass the token - it expired",
    "tokens - 12 left",
    "apikeys: none",
]

# (value, keep, served): a kept segment that ends in the trigger no longer
# serves the credential that follows it; the kept segment itself is verbatim.
KEPT = [
    (f"status Bearer {C}", ("status Bearer",), "status Bearer ***"),
    (f"token={C}", ("token=",), "token=***"),
    (f"Authorization: Bearer {C} and more", ("Authorization:",), "Authorization: ***"),
    (f"Authorization: Bearer {C}", ("Authorization: Bearer",), "Authorization: Bearer ***"),
    (f"x token={C} y token={C}", ("token=",), "x token=*** y token=***"),
    (f"Bearer​{C}", ("Bearer",), "Bearer​***"),
    (f"use Bearer {C} here", ("use",), "use Bearer *** here"),
    ("Bearer OK ok", ("Bearer OK",), "Bearer OK ok"),
]


@pytest.mark.parametrize("name", sorted(MASKED))
def test_form_is_fully_masked(name):
    text, served = MASKED[name]
    got = W.withhold_text(text)
    assert C not in got, f"BH1-71 LEAK {name}: {text!r} -> {got!r}"
    assert got == served, f"BH1-71 FORM {name}: {got!r} != {served!r}"


@pytest.mark.parametrize("name", sorted(MASKED))
def test_form_is_masked_in_diagnostic_view(name):
    text, served = MASKED[name]
    got = W.diagnostic_view({"error": text, "count": 1}, ("count",))
    assert C not in got["error"], f"BH1-71 LEAK diagnostic {name}: {got['error']!r}"
    assert got == {"error": served, "count": 1}


@pytest.mark.parametrize("name", sorted(PINNED))
def test_closed_form_stays_closed(name):
    text, served = PINNED[name]
    assert W.withhold_text(text) == served


@pytest.mark.parametrize("text", PROSE)
def test_prose_is_byte_identical(text):
    assert W.withhold_text(text) == text


@pytest.mark.parametrize("value,keep,served", KEPT)
def test_kept_trigger_does_not_serve_the_credential(value, keep, served):
    got = W._withhold_except(value, keep)
    assert C not in got, f"BH1-71 LEAK kept {keep!r}: {value!r} -> {got!r}"
    assert got == served
    for seg in keep:
        assert seg in got


def test_served_forms_are_stable():
    for name, (text, _) in {**MASKED, **PINNED}.items():
        once = W.withhold_text(text)
        assert W.withhold_text(once) == once, f"BH1-71 UNSTABLE {name}: {once!r}"


_FRAG = [C, " ", "\n", "\xa0", "​", ":", "=", "：", "—", "-", "+", '"', "'",
         "Bearer", "token", "access_token", "X-Api-Key", "Authorization", "api_key",
         "http://h/p?q=1", "www.x.y", "plain", "tokens"]
_TRIGGER = re.compile(r"authorization|bearer|token|api[-_]?key", re.IGNORECASE)


def _base(text):
    return W._TOKEN.sub(W._token, text)


def _corpus(seed, n=4000):
    rnd = random.Random(seed)
    return ["".join(rnd.choice(_FRAG) for _ in range(rnd.randint(1, 12))) for _ in range(n)]


def test_never_serves_what_base_withheld():
    leaks = [s for s in _corpus(1971) if C not in _base(s) and C in W.withhold_text(s)]
    assert not leaks, f"BH1-71 LEAK differential {len(leaks)}: {leaks[0]!r} -> {W.withhold_text(leaks[0])!r}"


def test_no_trigger_text_is_byte_identical_to_base():
    differ = [s for s in _corpus(71) if not _TRIGGER.search(s) and W.withhold_text(s) != _base(s)]
    assert not differ, differ[:3]


def _pieces(text, keep):
    """(kept segment before, unkept part) in _withhold_except's own split."""
    out, i, ctx = [], 0, ""
    while True:
        hits = [(j, -len(k), k) for k in keep for j in (text.find(k, i),) if j >= 0]
        if not hits:
            break
        j, _, seg = min(hits)
        out.append((ctx, text[i:j]))
        i, ctx = j + len(seg), seg
    out.append((ctx, text[i:]))
    return out


def test_kept_trigger_serves_only_what_withhold_text_serves():
    """A kept segment splits the text; each unkept part may serve
    SECRETCANARY only where withhold_text of (kept segment + part) does, so a
    kept trailing trigger (``token=`` / ``Bearer``) never serves its value."""
    keep = ("Bearer", "token=", "X-Api-Key:", "status")
    leaks = [s for s in _corpus(171) if C in W._withhold_except(s, keep)
             and not any(C in W.withhold_text(k + part) for k, part in _pieces(s, keep))]
    assert not leaks, f"BH1-71 LEAK kept {len(leaks)}: {leaks[0]!r} -> {W._withhold_except(leaks[0], keep)!r}"


def _best_ms(text, runs=3):
    best = float("inf")
    for _ in range(runs):
        t0 = time.perf_counter()
        W.withhold_text(text)
        best = min(best, time.perf_counter() - t0)
    return best * 1000


@pytest.mark.parametrize("name,make", [
    ("bearer-space-colon", lambda n: "Bearer" + " :" * n),
    ("bearer-space-dash", lambda n: "Bearer" + " -" * n),
    ("bearer-zwsp-run", lambda n: "Bearer" + "​" * n),
    ("token-newline-eq", lambda n: "token" + "\n=" * n),
    ("authorization-space", lambda n: "Authorization" + " " * n),
    ("x-api-key-repeat", lambda n: "x-api-key: " * n),
])
def test_new_branches_are_linear_time(name, make):
    n = 20000
    small, large = _best_ms(make(n)), _best_ms(make(4 * n))
    assert large < 8 * max(small, 0.5) and large < 2000, \
        f"BH1-71 SLOW {name}: N={small:.1f}ms 4N={large:.1f}ms"
