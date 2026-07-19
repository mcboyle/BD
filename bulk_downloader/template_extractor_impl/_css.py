"""template_extractor_impl._css -- shared CSS-escape leaf (verbatim)."""

import re


_CSS_SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


def _css_escape(s: str) -> str:
    """Escape a class or ID for use in a CSS selector. Falls back
    to attribute syntax for unusual chars."""
    if not s:
        return ""
    if _CSS_SAFE.match(s):
        return s
    # Otherwise escape problem chars with backslash
    return re.sub(r"([^A-Za-z0-9_-])", r"\\\1", s)


def _css_escape_attr(s: str) -> str:
    """For attribute values inside single quotes — escape backslash
    and single quote only."""
    if not isinstance(s, str):
        return ""
    return s.replace("\\", "\\\\").replace("'", "\\'")
