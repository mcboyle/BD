"""bulk_downloader.class_normalizer -- Dynamic CSS class hash normalizer and unminifier.

Normalizes compiled, hashed CSS-in-JS and CSS Modules class names into stable
semantic prefixes and generates resilient wildcard selectors across site builds.
0 site logins touched (Rule 21).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Union

# ── Framework and Pattern Recognizers ─────────────────────────────────────────

# CSS Modules: e.g. button_btn__3x7fA, container___2K9x. The tail must be HASH-SHAPED (letters/digits
# only, with a digit or an uppercase letter): a plain BEM element "menu__item" or "nav__link--active"
# is a stable class as-is and must not be normalised away (correctness REFUTE E3).
_CSS_MODULES_RE = re.compile(
    r'^(?P<prefix>[a-zA-Z0-9_-]+?)(?P<delim>_{2,3})(?P<hash>(?=[a-zA-Z0-9]*[0-9A-Z])[a-zA-Z0-9]{3,16})$'
)

# Styled-Components component identifier: e.g. sc-bdVaJa, sc-htpNat-1
_STYLED_COMPONENTS_RE = re.compile(r'^(?P<prefix>sc-[a-zA-Z0-9]+)(?:-(?P<idx>\d+))?$')

# Emotion: css-<hash> or css-<hash>-<Label>. The LABEL after the hash is the only stable part; a
# labelless Emotion class carries nothing stable (REFUTE E2: '[class*="css-"]' matched every element).
_EMOTION_RE = re.compile(r'^css-(?P<hash>[a-z0-9]{4,12})(?:-(?P<label>[A-Za-z][A-Za-z0-9_-]*))?$')

# BEM with trailing hash: e.g. menu__item--active-a9b8c
_BEM_HASH_RE = re.compile(r'^(?P<prefix>[a-zA-Z0-9_]+__[a-zA-Z0-9_-]+?)-(?P<hash>[a-f0-9]{5,10})$')

# Generic hyphenated hash suffix: e.g. card-7f8a9b2c (7+ hex chars: a 6-char tail is a hex colour utility
# such as bg-ff0000, not a build hash)
_GENERIC_HASH_RE = re.compile(r'^(?P<prefix>[a-zA-Z0-9_-]+?)-(?P<hash>[a-f0-9]{7,12})$')

# A class name that is a valid unescaped CSS identifier for the ".name" form
_CSS_IDENT_RE = re.compile(r'^-?[_a-zA-Z][_a-zA-Z0-9-]*$')

UNSTABLE = ""  # wildcard_selector of a class that carries no stable part (labelless Emotion hash)


def _attr_value(text: str) -> str:
    """Quote a value for a [class...="..."] attribute selector (backslash and double quote escaped)."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def plain_class_selector(class_name: str, tag: Optional[str] = None) -> str:
    """Exact-token selector for a plain class. Tailwind-style names ("md:flex", "w-1/2",
    "hover:bg-red-500") are not CSS identifiers and ".md:flex" throws in querySelectorAll
    (REFUTE E1); those use the exact-token attribute form [class~="..."]."""
    tag_prefix = tag or ""
    if _CSS_IDENT_RE.match(class_name):
        return f"{tag_prefix}.{class_name}"
    return f'{tag_prefix}[class~="{_attr_value(class_name)}"]'


def token_prefix_selector(prefix: str, tag: Optional[str] = None) -> str:
    """Selector matching a class TOKEN that starts with `prefix` -- anchored at the attribute start
    or after a space, so 'card-' never matches 'discard-btn' (REFUTE E4)."""
    t, v = tag or "", _attr_value(prefix)
    return f'{t}[class^="{v}"], {t}[class*=" {v}"]'


def token_suffix_selector(suffix: str, tag: Optional[str] = None) -> str:
    """Selector matching a class TOKEN that ends with `suffix` (Emotion 'css-<hash>-Label')."""
    t, v = tag or "", _attr_value(suffix)
    return f'{t}[class$="{v}"], {t}[class*="{v} "]'


@dataclass
class NormalizedClass:
    """Normalized representation of a CSS class name."""
    original: str
    prefix: str
    hash_part: Optional[str] = None
    framework: str = "plain"  # "css-modules", "styled-components", "emotion", "bem-hash", "generic-hash", "plain"
    wildcard_selector: str = ""


def is_hashed_class(class_name: str) -> bool:
    """Check whether a class name appears to contain a compiled dynamic hash."""
    if not class_name or not isinstance(class_name, str):
        return False
    norm = normalize_class_name(class_name)
    return norm.framework != "plain"


def extract_stable_prefix(class_name: str) -> str:
    """Extract the stable semantic prefix from a class name, stripping the hash."""
    norm = normalize_class_name(class_name)
    return norm.prefix


def normalize_class_name(class_name: str, tag: Optional[str] = None) -> NormalizedClass:
    """Normalize a single CSS class name into its prefix, hash, and framework."""
    if not class_name or not isinstance(class_name, str):
        return NormalizedClass(original="", prefix="", hash_part=None, framework="plain", wildcard_selector="")

    raw = class_name.strip()
    if not raw:
        return NormalizedClass(original="", prefix="", hash_part=None, framework="plain", wildcard_selector="")

    # 1. CSS Modules: name__hash or name___hash
    m = _CSS_MODULES_RE.match(raw)
    if m:
        prefix = m.group("prefix")
        hash_part = m.group("hash")
        delim = m.group("delim")
        wildcard = token_prefix_selector(f"{prefix}{delim}", tag)
        return NormalizedClass(
            original=raw,
            prefix=prefix,
            hash_part=hash_part,
            framework="css-modules",
            wildcard_selector=wildcard,
        )

    # 2. Styled-Components: sc-bdVaJa
    m = _STYLED_COMPONENTS_RE.match(raw)
    if m:
        prefix = m.group("prefix")
        wildcard = token_prefix_selector(prefix, tag)
        return NormalizedClass(
            original=raw,
            prefix=prefix,
            hash_part=None,
            framework="styled-components",
            wildcard_selector=wildcard,
        )

    # 3. Emotion: css-1a2b3c4 (unstable: no label) or css-1a2b3c4-MyButton (label is the prefix)
    m = _EMOTION_RE.match(raw)
    if m:
        label = m.group("label") or ""
        prefix = label
        hash_part = m.group("hash")
        wildcard = token_suffix_selector(f"-{label}", tag) if label else UNSTABLE
        return NormalizedClass(
            original=raw,
            prefix=prefix,
            hash_part=hash_part,
            framework="emotion",
            wildcard_selector=wildcard,
        )

    # 4. BEM with hash suffix: menu__item--active-a9b8c
    m = _BEM_HASH_RE.match(raw)
    if m:
        prefix = m.group("prefix")
        hash_part = m.group("hash")
        wildcard = token_prefix_selector(f"{prefix}-", tag)
        return NormalizedClass(
            original=raw,
            prefix=prefix,
            hash_part=hash_part,
            framework="bem-hash",
            wildcard_selector=wildcard,
        )

    # 5. Generic hash suffix: card-7f8a9b2c
    m = _GENERIC_HASH_RE.match(raw)
    if m:
        prefix = m.group("prefix")
        hash_part = m.group("hash")
        wildcard = token_prefix_selector(f"{prefix}-", tag)
        return NormalizedClass(
            original=raw,
            prefix=prefix,
            hash_part=hash_part,
            framework="generic-hash",
            wildcard_selector=wildcard,
        )

    # Plain un-hashed class (exact token; escaped when it is not a CSS identifier)
    wildcard = plain_class_selector(raw, tag)
    return NormalizedClass(
        original=raw,
        prefix=raw,
        hash_part=None,
        framework="plain",
        wildcard_selector=wildcard,
    )


def compile_wildcard_selector(
    class_name: str,
    tag: Optional[str] = None,
    match_type: str = "contains",
) -> str:
    """Compile a resilient CSS selector for a class name.

    Every form is anchored to a CLASS TOKEN (never a bare substring of the attribute):
      - 'contains' / 'starts_with': a token starting with the stable prefix
        ([class^="p"], [class*=" p"]) -- the same thing, since the prefix is a token prefix by construction
      - 'ends_with': a token ending with the stable part ([class$="p"], [class*="p "])
    A plain class is an exact token; a labelless Emotion hash has no stable part and yields "" (UNSTABLE).
    """
    if not class_name or not class_name.strip():
        return ""

    norm = normalize_class_name(class_name, tag=tag)
    if norm.framework == "plain":
        return norm.wildcard_selector
    if norm.framework == "emotion":
        return norm.wildcard_selector  # token-suffix on the label, or UNSTABLE

    if norm.framework == "css-modules":
        m = _CSS_MODULES_RE.match(norm.original)
        stable = f'{norm.prefix}{m.group("delim") if m else "__"}'
    elif norm.framework == "styled-components":
        stable = norm.prefix
    else:  # bem-hash, generic-hash
        stable = f"{norm.prefix}-"

    if match_type == "ends_with":
        return token_suffix_selector(stable, tag)
    return token_prefix_selector(stable, tag)


def normalize_class_list(
    class_list: Union[str, list[str]],
    tag: Optional[str] = None,
) -> list[NormalizedClass]:
    """Normalize a space-separated class attribute string or list of class names."""
    if isinstance(class_list, str):
        tokens = [t.strip() for t in class_list.split() if t.strip()]
    elif isinstance(class_list, (list, tuple)):
        tokens = [str(t).strip() for t in class_list if str(t).strip()]
    else:
        return []

    return [normalize_class_name(tok, tag=tag) for tok in tokens]


def compile_element_selector(
    class_names: Union[str, list[str]],
    tag: Optional[str] = None,
) -> str:
    """Compile a resilient selector combining stable class patterns for an element."""
    normalized = normalize_class_list(class_names, tag=tag)
    if not normalized:
        return tag or ""

    # Prefer semantic hashed classes (CSS modules / styled-components) over plain utility classes;
    # a class with no stable part (labelless Emotion hash, UNSTABLE) is never chosen.
    ranked = sorted(
        (n for n in normalized if n.wildcard_selector != UNSTABLE),
        key=lambda n: (
            0 if n.framework == "css-modules" else
            1 if n.framework == "styled-components" else
            2 if n.framework != "plain" else 3
        )
    )
    if not ranked:
        return tag or ""
    primary = ranked[0]
    return compile_wildcard_selector(primary.original, tag=tag)
