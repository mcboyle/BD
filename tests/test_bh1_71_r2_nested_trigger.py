"""BH1-71 r2 nested-trigger: a captured credential that is itself a trigger.

r1 masked ``token:`` / ``X-Api-Key:`` / a JSON key as triggers, each taking one
token; when that token was itself a trigger (``token: Bearer X``,
``"access_token": "Bearer X"``, ``token: Authorization: Basic X``) the mask
covered the trigger word and served X, which BASE 10bd9c10 masked. The mask
now runs on through every trigger inside the credential, in withhold_text and
in _withhold_except's kept-segment path alike. The invariant rows load the
REAL BASE module (vendored, pinned by sha256 + git blob) and compare whole
outputs: the tree never serves a SECRETCANARY that BASE withheld.
"""
from __future__ import annotations

import hashlib
import random
import re
import time
import types
from pathlib import Path

import pytest

from bulk_downloader import egress_withhold as W

BD_GATE_SCOPE = "module"

C = "SECRETCANARY"
_BASE_PATH = Path(__file__).parent / "fixtures" / "bh1_71_r2" / "egress_withhold_base_10bd9c10.py.txt"
_BASE_SHA256 = "ad1b227956d785fa48b44c237044e128186388a5524512284e60378f56f4a381"
_BASE_BLOB = "a1d2b310420c42266fad23c69c732b37bc1be2b4"  # 10bd9c10:bulk_downloader/egress_withhold.py


def _load_base():
    src = _BASE_PATH.read_bytes()
    mod = types.ModuleType("bh1_71_r2_base_egress_withhold")
    exec(compile(src, str(_BASE_PATH), "exec"), mod.__dict__)
    return mod


B = _load_base()

# Served with SECRETCANARY on the r1 tree e33acf50 (withhold_text).
NESTED = {
    "token-bearer": (f"token: Bearer {C}", "token: ***"),
    "json-access-token-bearer": (f'{{"access_token": "Bearer {C}"}}', '{"access_token": ***'),
    "api-key-bearer": (f"api_key: Bearer {C}", "api_key: ***"),
    "x-api-key-bearer": (f"X-Api-Key: Bearer {C}", "X-Api-Key: ***"),
    "token-authorization-basic": (f"token: Authorization: Basic {C}", "token: ***"),
    "token-newline-authorization": (f"token:\nAuthorization: Basic {C}", "token:\n***"),
    "api-key-authorization-basic": (f"api-key: Authorization: Basic {C}", "api-key: ***"),
    "invalid-token-bearer": (f"invalid token: Bearer {C}", "invalid token: ***"),
    "api-key-eq-bearer": (f"api_key = Bearer {C}", "api_key = ***"),
    "json-token-bearer": (f'"token": "Bearer {C}"', '"token": ***'),
    "json-bearer-bearer": (f'"bearer": "Bearer {C}"', '"bearer": ***'),
    "token-authorization": (f"token: Authorization: {C}", "token: ***"),
    "token-glued-bearer": (f"token: plainBearer {C}", "token: ***"),
    "token-glued-api-key-bearer": (f"token: api_key:x;Bearer {C}", "token: ***"),
    "token-eq-space-bearer": (f"token = Bearer {C}", "token = ***"),
    "token-arrow": (f"token => {C}", "token [url withheld] ***"),
    "api-key-arrow": (f"api_key => {C}", "api_key [url withheld] ***"),
}

# Served with SECRETCANARY on e33acf50 by _withhold_except's kept-segment path.
NESTED_KEPT = {
    "kept-missing-bearer": (f"Missing Bearer Authorization: Basic {C}", ("Missing Bearer",), "Missing Bearer ***"),
    "kept-bad-token": (f"bad token: Authorization: Basic {C}", ("bad token",), "bad token: ***"),
    "kept-token": (f"token=Bearer {C}", ("token",), "token=***"),
}


@pytest.mark.parametrize("name", sorted(NESTED))
def test_nested_trigger_is_masked(name):
    text, want = NESTED[name]
    got = W.withhold_text(text)
    assert C not in got, f"BH1-71r2 NESTED LEAK {name}: {text!r} -> {got!r}"
    assert got == want


@pytest.mark.parametrize("name", sorted(NESTED_KEPT))
def test_nested_trigger_after_kept_segment_is_masked(name):
    text, keep, want = NESTED_KEPT[name]
    got = W._withhold_except(text, keep)
    assert C not in got, f"BH1-71r2 KEPT NESTED LEAK {name}: {text!r} -> {got!r}"
    assert got == want


def test_base_fixture_is_the_real_base_module():
    raw = _BASE_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == _BASE_SHA256
    assert hashlib.sha1(b"blob %d\0" % len(raw) + raw).hexdigest() == _BASE_BLOB
    assert B.withhold_text(f"token: Bearer {C}") == "token: Bearer ***"


_ADV = [f"token: Bearer {C}", f'"token": "Bearer {C}"', f"access_token: Bearer {C}", f"X-Api-Key: Bearer {C}",
        f"api_key = Bearer {C}", f'"bearer": "Bearer {C}"', f"token: Authorization: {C}", f"invalid token: Bearer {C}",
        f"token=Bearer {C}", f"token: Bearer {C} more", f"Bearer :{C}", f"token=:{C}", f"Bearer -{C}", f"Bearer --{C}",
        f"Token: Bearer {C}", f"x-api-key: Authorization: Bearer {C}", f"api-key: token={C}", f"token:: {C}",
        f"token: {C}", f"Authorization: {C}", f"Bearer {C}"]
_FRAG = [C, " ", "\n", "\xa0", "​", ":", "=", "：", "—", "-", "+", '"', "'", "Bearer", "token",
         "access_token", "X-Api-Key", "Authorization", "api_key", "Token:", "http://h/p?q=1", "www.x.y", "plain",
         "tokens", "Basic", "Bearer " + C, "token: " + C]
_NEST = ["token: ", "token=", "api_key: ", "X-Api-Key: ", '"access_token": "', '"bearer": "', "Bearer ", "Bearer:",
         "Authorization: ", "Authorization: Basic ", "bad token: ", "\n", " ", "plain", "=>", ":", '"', "⠀",
         "Missing ", "http://h/p?q=1", C]
_KEEP = ("Bearer", "token=", "X-Api-Key:", "status", "Token:")
_TRIGGER = re.compile(r"authorization|bearer|token|api[-_]?key", re.IGNORECASE)


def _corpus(seed, frag, n, hi):
    rnd = random.Random(seed)
    return ["".join(rnd.choice(frag) for _ in range(rnd.randint(1, hi))) for _ in range(n)]


def _numbered(text):
    count = iter(range(10 ** 6))
    return re.sub(C, lambda _: f"{C}{next(count):04d}Q", text)


def _served(out):
    return set(re.findall(C + r"\d{4}Q", out))


def _leaks(corpus, base, tree):
    """Inputs where the tree serves a SECRETCANARY BASE withheld: whole-string
    (BASE output has none, tree output has one) or per numbered occurrence."""
    leaks = []
    for text in corpus:
        if C not in base(text) and C in tree(text):
            leaks.append(text)
        numbered = _numbered(text)
        if not _served(tree(numbered)) <= _served(base(numbered)):
            leaks.append(numbered)
    return leaks


def _except(mod, keep):
    return lambda text: mod._withhold_except(text, keep)


_CASES = {
    "corr-withhold-text": (_ADV + _corpus(7, _FRAG, 20000, 14), B.withhold_text, W.withhold_text),
    "corr-withhold-except": (_ADV + _corpus(7, _FRAG, 20000, 14), _except(B, _KEEP), _except(W, _KEEP)),
    "nested-withhold-text": (_corpus(101, _NEST, 5000, 10), B.withhold_text, W.withhold_text),
    "nested-except-bad-token": (_corpus(101, _NEST, 5000, 10), _except(B, ("bad token",)), _except(W, ("bad token",))),
    "nested-except-token": (_corpus(102, _NEST, 5000, 10), _except(B, ("token",)), _except(W, ("token",))),
    "nested-except-bearer": (_corpus(103, _NEST, 5000, 10), _except(B, ("Bearer",)), _except(W, ("Bearer",))),
    "nested-except-missing-bearer": (_corpus(104, _NEST, 5000, 10), _except(B, ("Missing Bearer",)),
                                     _except(W, ("Missing Bearer",))),
}


@pytest.mark.parametrize("name", sorted(_CASES))
def test_tree_never_serves_what_base_withheld(name):
    corpus, base, tree = _CASES[name]
    masked = [s for s in corpus if C in s and C not in base(s)]
    assert len(masked) >= 200, f"BASE-masked denominator too small: {len(masked)}"
    leaks = _leaks(corpus, base, tree)
    assert not leaks, f"BH1-71r2 LEAK vs BASE {name} {len(leaks)}/{len(corpus)}: {leaks[0]!r} -> {tree(leaks[0])!r}"


def test_no_trigger_text_is_byte_identical_to_base():
    corpus = [s for s in _corpus(71, _FRAG, 4000, 12) if not _TRIGGER.search(s)]
    assert len(corpus) >= 200
    differ = [s for s in corpus if W.withhold_text(s) != B.withhold_text(s)]
    assert not differ, differ[:3]


def _best_ms(fn, text, runs=3):
    best = float("inf")
    for _ in range(runs):
        t0 = time.perf_counter()
        fn(text)
        best = min(best, time.perf_counter() - t0)
    return best * 1000


@pytest.mark.parametrize("name,text", [("percent-run", "%" * 400000), ("letter-run", "a" * 400000),
                                       ("words", "plain words " * 40000)])
def test_trigger_free_scan_is_not_slower_than_base(name, text):
    """tests/test_bd_sec_2_withhold.py holds withhold_text('%' x 400000) under
    50 ms; the r1 trigger set scanned it 2.3x slower than BASE."""
    def scan(mod):
        return _best_ms(mod._CREDENTIAL_TEXT.search, text, runs=5)
    base, tree = scan(B), scan(W)
    assert W.withhold_text(text) == B.withhold_text(text)
    assert tree < 1.25 * base + 1, f"BH1-71r2 SLOWER THAN BASE {name}: BASE={base:.1f}ms tree={tree:.1f}ms"


@pytest.mark.parametrize("name,make", [
    ("token-bearer", lambda n: "token: Bearer " * n),
    ("token-glued-bearer", lambda n: "token: " + "xBearer:" * n),
    ("token-glued-bearer-tail", lambda n: "token: " + "xBearer:" * n + " Bearer y"),
    ("token-authorization-glued", lambda n: "token: " + "Authorization:" * n),
    ("authorization-lines", lambda n: "Authorization: a\n" * n + "token: x"),
    ("token-lines", lambda n: "token: a\n" * n + "Authorization: x"),
    ("json-nest", lambda n: '"token": "Bearer ' * n),
])
@pytest.mark.parametrize("view", ["withhold_text", "withhold_except"])
def test_nested_scan_is_linear_time(name, make, view):
    fn = W.withhold_text if view == "withhold_text" else _except(W, ("token",))
    n = 5000
    small, large = _best_ms(fn, make(n)), _best_ms(fn, make(4 * n))
    assert large < 8 * max(small, 0.5) and large < 2000, \
        f"BH1-71r2 SLOW {name}/{view}: N={small:.1f}ms 4N={large:.1f}ms"
