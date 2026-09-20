"""Color contrast and text-indent visibility analyzer — Row 944.

Detects non-rendered or disguised text anchors in layout inspection:
1. Zero-contrast or imperceptible text anchors (<1.05:1 contrast ratio),
   including transparent font styling (color: rgba(0,0,0,0)), transparent
   named color, or matching text and background colors.
2. Text-indent disguise links using extreme negative indentation (e.g. -9999px)
   to push anchor text off-screen.
3. Standard visible links with perceptible contrast (>=1.05:1) and normal indent.

Conforms to WCAG 2.1 relative luminance and contrast ratio algorithms.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

# Minimum contrast ratio below which text is considered imperceptible.
# Per Row 944 spec: excluding imperceptible text anchors (<1.05:1 contrast).
MIN_CONTRAST_RATIO: float = 1.05

# Threshold in pixels below which text-indent is considered an off-screen disguise.
TEXT_INDENT_DISGUISE_THRESHOLD: float = -100.0

DEFAULT_FOREGROUND_COLOR: str = "#000000"
DEFAULT_BACKGROUND_COLOR: str = "#ffffff"

# Basic named CSS colors mapped to (R, G, B, A)
_NAMED_COLORS: dict[str, tuple[int, int, int, float]] = {
    "transparent": (0, 0, 0, 0.0),
    "aliceblue": (240, 248, 255, 1.0),
    "antiquewhite": (250, 235, 215, 1.0),
    "aqua": (0, 255, 255, 1.0),
    "aquamarine": (127, 255, 212, 1.0),
    "azure": (240, 255, 255, 1.0),
    "beige": (245, 245, 220, 1.0),
    "bisque": (255, 228, 196, 1.0),
    "black": (0, 0, 0, 1.0),
    "blanchedalmond": (255, 235, 205, 1.0),
    "blue": (0, 0, 255, 1.0),
    "blueviolet": (138, 43, 226, 1.0),
    "brown": (165, 42, 42, 1.0),
    "burlywood": (222, 184, 135, 1.0),
    "cadetblue": (95, 158, 160, 1.0),
    "chartreuse": (127, 255, 0, 1.0),
    "chocolate": (210, 105, 30, 1.0),
    "coral": (255, 127, 80, 1.0),
    "cornflowerblue": (100, 149, 237, 1.0),
    "cornsilk": (255, 248, 220, 1.0),
    "crimson": (220, 20, 60, 1.0),
    "cyan": (0, 255, 255, 1.0),
    "darkblue": (0, 0, 139, 1.0),
    "darkcyan": (0, 139, 139, 1.0),
    "darkgoldenrod": (184, 134, 11, 1.0),
    "darkgray": (169, 169, 169, 1.0),
    "darkgreen": (0, 100, 0, 1.0),
    "darkgrey": (169, 169, 169, 1.0),
    "darkkhaki": (189, 183, 107, 1.0),
    "darkmagenta": (139, 0, 139, 1.0),
    "darkolivegreen": (85, 107, 47, 1.0),
    "darkorange": (255, 140, 0, 1.0),
    "darkorchid": (153, 50, 204, 1.0),
    "darkred": (139, 0, 0, 1.0),
    "darksalmon": (233, 150, 122, 1.0),
    "darkseagreen": (143, 188, 143, 1.0),
    "darkslateblue": (72, 61, 139, 1.0),
    "darkslategray": (47, 79, 79, 1.0),
    "darkslategrey": (47, 79, 79, 1.0),
    "darkturquoise": (0, 206, 209, 1.0),
    "darkviolet": (148, 0, 211, 1.0),
    "deeppink": (255, 20, 147, 1.0),
    "deepskyblue": (0, 191, 255, 1.0),
    "dimgray": (105, 105, 105, 1.0),
    "dimgrey": (105, 105, 105, 1.0),
    "dodgerblue": (30, 144, 255, 1.0),
    "firebrick": (178, 34, 34, 1.0),
    "floralwhite": (255, 250, 240, 1.0),
    "forestgreen": (34, 139, 34, 1.0),
    "fuchsia": (255, 0, 255, 1.0),
    "gainsboro": (220, 220, 220, 1.0),
    "ghostwhite": (248, 248, 255, 1.0),
    "gold": (255, 215, 0, 1.0),
    "goldenrod": (218, 165, 32, 1.0),
    "gray": (128, 128, 128, 1.0),
    "green": (0, 128, 0, 1.0),
    "greenyellow": (173, 255, 47, 1.0),
    "grey": (128, 128, 128, 1.0),
    "honeydew": (240, 255, 240, 1.0),
    "hotpink": (255, 105, 180, 1.0),
    "indianred": (205, 92, 92, 1.0),
    "indigo": (75, 0, 130, 1.0),
    "ivory": (255, 255, 240, 1.0),
    "khaki": (240, 230, 140, 1.0),
    "lavender": (230, 230, 250, 1.0),
    "lavenderblush": (255, 240, 245, 1.0),
    "lawngreen": (124, 252, 0, 1.0),
    "lemonchiffon": (255, 250, 205, 1.0),
    "lightblue": (173, 216, 230, 1.0),
    "lightcoral": (240, 128, 128, 1.0),
    "lightcyan": (224, 255, 255, 1.0),
    "lightgoldenrodyellow": (250, 250, 210, 1.0),
    "lightgray": (211, 211, 211, 1.0),
    "lightgreen": (144, 238, 144, 1.0),
    "lightgrey": (211, 211, 211, 1.0),
    "lightpink": (255, 182, 193, 1.0),
    "lightsalmon": (255, 160, 122, 1.0),
    "lightseagreen": (32, 178, 170, 1.0),
    "lightskyblue": (135, 206, 250, 1.0),
    "lightslategray": (119, 136, 153, 1.0),
    "lightslategrey": (119, 136, 153, 1.0),
    "lightsteelblue": (176, 196, 222, 1.0),
    "lightyellow": (255, 255, 224, 1.0),
    "lime": (0, 255, 0, 1.0),
    "limegreen": (50, 205, 50, 1.0),
    "linen": (250, 240, 230, 1.0),
    "magenta": (255, 0, 255, 1.0),
    "maroon": (128, 0, 0, 1.0),
    "mediumaquamarine": (102, 205, 170, 1.0),
    "mediumblue": (0, 0, 205, 1.0),
    "mediumorchid": (186, 85, 211, 1.0),
    "mediumpurple": (147, 112, 219, 1.0),
    "mediumseagreen": (60, 179, 113, 1.0),
    "mediumslateblue": (123, 104, 238, 1.0),
    "mediumspringgreen": (0, 250, 154, 1.0),
    "mediumturquoise": (72, 209, 204, 1.0),
    "mediumvioletred": (199, 21, 133, 1.0),
    "midnightblue": (25, 25, 112, 1.0),
    "mintcream": (245, 255, 250, 1.0),
    "mistyrose": (255, 228, 225, 1.0),
    "moccasin": (255, 228, 181, 1.0),
    "navajowhite": (255, 222, 173, 1.0),
    "navy": (0, 0, 128, 1.0),
    "oldlace": (253, 245, 230, 1.0),
    "olive": (128, 128, 0, 1.0),
    "olivedrab": (107, 142, 35, 1.0),
    "orange": (255, 165, 0, 1.0),
    "orangered": (255, 69, 0, 1.0),
    "orchid": (218, 112, 214, 1.0),
    "palegoldenrod": (238, 232, 170, 1.0),
    "palegreen": (152, 251, 152, 1.0),
    "paleturquoise": (175, 238, 238, 1.0),
    "palevioletred": (219, 112, 147, 1.0),
    "papayawhip": (255, 239, 213, 1.0),
    "peachpuff": (255, 218, 185, 1.0),
    "peru": (205, 133, 63, 1.0),
    "pink": (255, 192, 203, 1.0),
    "plum": (221, 160, 221, 1.0),
    "powderblue": (176, 224, 230, 1.0),
    "purple": (128, 0, 128, 1.0),
    "rebeccapurple": (102, 51, 153, 1.0),
    "red": (255, 0, 0, 1.0),
    "rosybrown": (188, 143, 143, 1.0),
    "royalblue": (65, 105, 225, 1.0),
    "saddlebrown": (139, 69, 19, 1.0),
    "salmon": (250, 128, 114, 1.0),
    "sandybrown": (244, 164, 96, 1.0),
    "seagreen": (46, 139, 87, 1.0),
    "seashell": (255, 245, 238, 1.0),
    "sienna": (160, 82, 45, 1.0),
    "silver": (192, 192, 192, 1.0),
    "skyblue": (135, 206, 235, 1.0),
    "slateblue": (106, 90, 205, 1.0),
    "slategray": (112, 128, 144, 1.0),
    "slategrey": (112, 128, 144, 1.0),
    "snow": (255, 250, 250, 1.0),
    "springgreen": (0, 255, 127, 1.0),
    "steelblue": (70, 130, 180, 1.0),
    "tan": (210, 180, 140, 1.0),
    "teal": (0, 128, 128, 1.0),
    "thistle": (216, 191, 216, 1.0),
    "tomato": (255, 99, 71, 1.0),
    "turquoise": (64, 224, 208, 1.0),
    "violet": (238, 130, 238, 1.0),
    "wheat": (245, 222, 179, 1.0),
    "white": (255, 255, 255, 1.0),
    "whitesmoke": (245, 245, 245, 1.0),
    "yellow": (255, 255, 0, 1.0),
    "yellowgreen": (154, 205, 50, 1.0),
}  # the 148 CSS named colours + transparent

# rgb()/rgba()/hsl()/hsla(): legacy comma syntax AND CSS Color 4 space syntax
# ("rgb(0 0 0 / 0)"), channels as numbers or percentages, alpha as a number or
# a percentage
_NUM = r"[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?"
_FUNC_RE = re.compile(
    rf"^(rgba?|hsla?)\s*\(\s*({_NUM}%?(?:deg|grad|rad|turn)?)\s*[,\s]\s*({_NUM}%?)\s*[,\s]\s*({_NUM}%?)"
    rf"\s*(?:[,/]\s*({_NUM}%?)\s*)?\)$",
    re.IGNORECASE,
)
_HEX_RE = re.compile(r"^#([0-9a-f]{3,8})$", re.IGNORECASE)
_HUE_UNIT_RE = re.compile(rf"^({_NUM})(deg|grad|rad|turn)?$", re.IGNORECASE)


def _channel(token: str) -> int:
    """One rgb channel: 0-255 number or 0-100% percentage, clamped."""
    if token.endswith("%"):
        return max(0, min(255, round(float(token[:-1]) * 2.55)))
    return max(0, min(255, round(float(token))))


def _alpha(token: str | None) -> float:
    if token is None:
        return 1.0
    a = float(token[:-1]) / 100.0 if token.endswith("%") else float(token)
    return max(0.0, min(1.0, a))


def _hsl_to_rgb(h: float, s: float, l: float) -> tuple[int, int, int]:
    """CSS hsl() (hue in degrees, s/l as 0-1 fractions) to 0-255 rgb."""
    h = (h % 360.0) / 360.0
    s, l = max(0.0, min(1.0, s)), max(0.0, min(1.0, l))
    if s == 0.0:
        v = round(l * 255)
        return (v, v, v)
    q = l * (1 + s) if l < 0.5 else l + s - l * s
    p = 2 * l - q

    def hue(t: float) -> float:
        t %= 1.0
        if t < 1 / 6:
            return p + (q - p) * 6 * t
        if t < 1 / 2:
            return q
        if t < 2 / 3:
            return p + (q - p) * (2 / 3 - t) * 6
        return p
    return (round(hue(h + 1 / 3) * 255), round(hue(h) * 255), round(hue(h - 1 / 3) * 255))


def parse_color(val: str | None) -> tuple[int, int, int, float]:
    """Parse a CSS color string into an (r, g, b, alpha) tuple.

    Supports:
    - Hex colors: #rgb, #rgba, #rrggbb, #rrggbbaa
    - Functional notation: rgb(r, g, b), rgba(r, g, b, a)
    - Named colors: 'transparent', 'black', 'white', etc.

    Also: CSS Color 4 space syntax "rgb(r g b / a)", percentage channels and
    alpha, hsl()/hsla(), the full 148-name CSS colour table.

    Returns None for anything that is not a colour (a url(), "none", an
    unknown keyword): the CALLER decides what an unknown colour means
    (a background falls back to the page default; a foreground is admitted
    as the default text colour) -- never opaque black, which made a sprite
    background "invisible" and an unknown-syntax transparent font "visible".
    """
    if not val:
        return None

    val = val.strip().lower()

    if val in _NAMED_COLORS:
        return _NAMED_COLORS[val]

    m_fn = _FUNC_RE.match(val)
    if m_fn:
        fn, c1, c2, c3, a_str = m_fn.groups()
        try:
            if fn.startswith("rgb"):
                return (_channel(c1), _channel(c2), _channel(c3), _alpha(a_str))
            if not (c2.endswith("%") and c3.endswith("%")):
                return None
            hm = _HUE_UNIT_RE.match(c1.rstrip("%"))
            hue_val, unit = float(hm.group(1)), (hm.group(2) or "deg").lower()
            hue_deg = {"deg": hue_val, "grad": hue_val * 0.9, "rad": hue_val * 180.0 / 3.141592653589793,
                       "turn": hue_val * 360.0}[unit]
            r, g, b = _hsl_to_rgb(hue_deg, float(c2[:-1]) / 100.0, float(c3[:-1]) / 100.0)
            return (r, g, b, _alpha(a_str))
        except (ValueError, AttributeError):
            return None

    m_hex = _HEX_RE.match(val)
    if m_hex:
        h = m_hex.group(1)
        if len(h) == 3:  # #rgb
            return (
                int(h[0] * 2, 16),
                int(h[1] * 2, 16),
                int(h[2] * 2, 16),
                1.0,
            )
        elif len(h) == 4:  # #rgba
            return (
                int(h[0] * 2, 16),
                int(h[1] * 2, 16),
                int(h[2] * 2, 16),
                int(h[3] * 2, 16) / 255.0,
            )
        elif len(h) == 6:  # #rrggbb
            return (
                int(h[0:2], 16),
                int(h[2:4], 16),
                int(h[4:6], 16),
                1.0,
            )
        elif len(h) == 8:  # #rrggbbaa
            return (
                int(h[0:2], 16),
                int(h[2:4], 16),
                int(h[4:6], 16),
                int(h[6:8], 16) / 255.0,
            )

    return None


def _split_top_level(value: str) -> list[str]:
    """Whitespace-split a CSS value, keeping function calls like
    rgb(0 0 0 / 0) and url(a b.png) as single tokens."""
    tokens, buf, depth = [], [], 0
    for ch in value:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch.isspace() and depth == 0:
            if buf:
                tokens.append("".join(buf)); buf = []
        else:
            buf.append(ch)
    if buf:
        tokens.append("".join(buf))
    return tokens


def background_color_of(value: str | None) -> tuple[int, int, int, float] | None:
    """The colour in a `background` / `background-color` value, or None when
    it carries no colour (url(sprite.png) no-repeat, none, inherit): the
    shorthand's first token that parses as a colour; url()/none/repeat/
    position/size words are not colours. Only the first layer is judged."""
    if not value:
        return None
    first_layer = _split_top_level(value.strip())
    for token in first_layer:
        token = token.rstrip(",")
        if token.lower().startswith("url("):
            continue
        color = parse_color(token)
        if color is not None:
            return color
    return None


def composite_color(
    fg: tuple[int, int, int, float], bg: tuple[int, int, int, float]
) -> tuple[int, int, int]:
    """Composite a foreground RGBA color over an opaque or semi-transparent background.

    Returns the resulting opaque (r, g, b) tuple.
    """
    fg_r, fg_g, fg_b, fg_a = fg
    bg_r, bg_g, bg_b, _ = bg

    if fg_a <= 0.0:
        return (bg_r, bg_g, bg_b)
    if fg_a >= 1.0:
        return (fg_r, fg_g, fg_b)

    r = round(fg_r * fg_a + bg_r * (1.0 - fg_a))
    g = round(fg_g * fg_a + bg_g * (1.0 - fg_a))
    b = round(fg_b * fg_a + bg_b * (1.0 - fg_a))
    return (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))


def compute_relative_luminance(
    color: tuple[int, int, int] | tuple[int, int, int, float] | str,
) -> float:
    """Compute relative luminance according to WCAG 2.1 specifications.

    L = 0.2126 * R + 0.7152 * G + 0.0722 * B
    """
    if isinstance(color, str):
        r, g, b, _ = parse_color(color) or (0, 0, 0, 1.0)
    elif len(color) == 4:
        r, g, b, _ = color
    else:
        r, g, b = color

    # Convert sRGB values (0..255) to linearized values (0.0..1.0)
    def linearize(c: int) -> float:
        v = c / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r_lin = linearize(r)
    g_lin = linearize(g)
    b_lin = linearize(b)

    return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin


def compute_contrast_ratio(
    color1: tuple[int, int, int, float] | str,
    color2: tuple[int, int, int, float] | str,
) -> float:
    """Compute contrast ratio between two colors per WCAG 2.1.

    If color1 (foreground) has transparency (alpha < 1.0), it is
    first composited against color2 (background).
    Formula: (L1 + 0.05) / (L2 + 0.05) where L1 >= L2.
    """
    fg_rgba = (parse_color(color1) or (0, 0, 0, 1.0)) if isinstance(color1, str) else color1
    bg_rgba = (parse_color(color2) or (255, 255, 255, 1.0)) if isinstance(color2, str) else color2

    # Composite foreground on background if foreground has alpha
    if fg_rgba[3] < 1.0:
        effective_fg = composite_color(fg_rgba, bg_rgba)
    else:
        effective_fg = (fg_rgba[0], fg_rgba[1], fg_rgba[2])

    effective_bg = (bg_rgba[0], bg_rgba[1], bg_rgba[2])

    lum1 = compute_relative_luminance(effective_fg)
    lum2 = compute_relative_luminance(effective_bg)

    l_max = max(lum1, lum2)
    l_min = min(lum1, lum2)

    return (l_max + 0.05) / (l_min + 0.05)


def parse_text_indent(indent_val: str | float | None) -> float:
    """Parse a CSS text-indent property into a floating point pixel value.

    Recognizes px, em, rem, pt, and raw numbers.
    Assumes standard browser conversion ratios (1em = 16px, 1rem = 16px, 1pt = 1.333px).
    """
    if indent_val is None:
        return 0.0
    if isinstance(indent_val, (int, float)):
        return float(indent_val)

    s = indent_val.strip().lower()
    if not s:
        return 0.0

    # Match numeric portion and optional unit
    m = re.match(r"^([+-]?[\d.]+)\s*([a-z%]*)$", s)
    if not m:
        return 0.0

    val = float(m.group(1))
    unit = m.group(2)

    if unit in ("px", ""):
        return val
    elif unit in ("em", "rem"):
        return val * 16.0
    elif unit == "pt":
        return val * 1.33333
    elif unit == "%":
        # 100% of container width approx 1000px for disguise heuristic
        return val * 10.0

    return val


def is_text_indent_disguise(
    indent_val: str | float | None,
    threshold: float = TEXT_INDENT_DISGUISE_THRESHOLD,
) -> bool:
    """Check whether text-indent indicates an off-screen disguise."""
    return parse_text_indent(indent_val) <= threshold


def parse_inline_styles(style_str: str | None) -> dict[str, str]:
    """Parse a CSS inline style string into a dictionary of property-value pairs."""
    styles: dict[str, str] = {}
    if not style_str:
        return styles

    for rule in style_str.split(";"):
        rule = rule.strip()
        if not rule or ":" not in rule:
            continue
        prop, val = rule.split(":", 1)
        # the CSS priority suffix is not part of the value: "transparent !important" is transparent
        # (correctness REFUTE E1: every !important-suffixed disguise read as an unknown keyword)
        styles[prop.strip().lower()] = _IMPORTANT_RE.sub("", val).strip()

    return styles


_IMPORTANT_RE = re.compile(r"\s*!\s*important\s*$", re.IGNORECASE)


@dataclass
class VisibilityAnalysisResult:
    """Outcome of layout visibility analysis for an anchor."""

    is_visible: bool
    contrast_ratio: float
    is_text_indent_disguise: bool
    text_indent: float
    reason: str
    color: tuple[int, int, int, float]
    background_color: tuple[int, int, int, float]


def analyze_anchor_visibility(
    text: str = "",
    style: str | dict[str, str] | None = None,
    default_fg: str = DEFAULT_FOREGROUND_COLOR,
    default_bg: str = DEFAULT_BACKGROUND_COLOR,
) -> VisibilityAnalysisResult:
    """Analyze visibility of an anchor based on CSS style properties and text."""
    styles_dict: dict[str, str] = (
        parse_inline_styles(style) if isinstance(style, str) else (style or {})
    )

    color_str = styles_dict.get("color", default_fg)
    indent_str = styles_dict.get("text-indent")

    # background: the colour token of the shorthand; no colour (a sprite,
    # "none") or a transparent one composites over the page default
    page_bg = parse_color(default_bg) or (255, 255, 255, 1.0)
    bg_color = background_color_of(styles_dict.get("background-color")) \
        or background_color_of(styles_dict.get("background"))
    if bg_color is None:
        bg_color = page_bg
    elif bg_color[3] < 1.0:
        bg_color = (*composite_color(bg_color, page_bg), 1.0)
    # foreground: an unknown colour keyword is the default text colour
    # (admitted), never black-by-accident
    fg_color = parse_color(color_str)
    if fg_color is None:
        fg_color = parse_color(default_fg) or (0, 0, 0, 1.0)

    # Compute contrast ratio with alpha compositing
    ratio = compute_contrast_ratio(fg_color, bg_color)

    # Compute text indent
    indent_px = parse_text_indent(indent_str)
    has_indent_disguise = is_text_indent_disguise(indent_px)

    # Zero / imperceptible contrast check
    is_zero_contrast = ratio < MIN_CONTRAST_RATIO

    if has_indent_disguise:
        return VisibilityAnalysisResult(
            is_visible=False,
            contrast_ratio=ratio,
            is_text_indent_disguise=True,
            text_indent=indent_px,
            reason=f"Text-indent disguise link ({indent_px:.1f}px <= {TEXT_INDENT_DISGUISE_THRESHOLD}px)",
            color=fg_color,
            background_color=bg_color,
        )

    if is_zero_contrast:
        if fg_color[3] <= 0.0:
            reason = f"Zero-contrast transparent font (alpha={fg_color[3]}, contrast={ratio:.2f}:1)"
        else:
            reason = (
                f"Imperceptible contrast ratio ({ratio:.2f}:1 < {MIN_CONTRAST_RATIO}:1)"
            )
        return VisibilityAnalysisResult(
            is_visible=False,
            contrast_ratio=ratio,
            is_text_indent_disguise=False,
            text_indent=indent_px,
            reason=reason,
            color=fg_color,
            background_color=bg_color,
        )

    return VisibilityAnalysisResult(
        is_visible=True,
        contrast_ratio=ratio,
        is_text_indent_disguise=False,
        text_indent=indent_px,
        reason="visible",
        color=fg_color,
        background_color=bg_color,
    )


def is_anchor_visible(
    style: str | dict[str, str] | None = None,
    default_bg: str = DEFAULT_BACKGROUND_COLOR,
) -> tuple[bool, str]:
    """Convenience predicate returning (is_visible, reason)."""
    res = analyze_anchor_visibility(style=style, default_bg=default_bg)
    return res.is_visible, res.reason


class ContrastAnalyzer:
    """DOM Layout inspector for detecting invisible text anchors."""

    def __init__(
        self,
        min_contrast_ratio: float = MIN_CONTRAST_RATIO,
        text_indent_threshold: float = TEXT_INDENT_DISGUISE_THRESHOLD,
        default_fg: str = DEFAULT_FOREGROUND_COLOR,
        default_bg: str = DEFAULT_BACKGROUND_COLOR,
    ):
        self.min_contrast_ratio = min_contrast_ratio
        self.text_indent_threshold = text_indent_threshold
        self.default_fg = default_fg
        self.default_bg = default_bg

    def analyze_element(self, element: Any) -> VisibilityAnalysisResult:
        """Inspect a BeautifulSoup Tag element (or mock), traversing ancestors for background."""
        # Extract element style
        elem_style_str = element.get("style", "") if hasattr(element, "get") else ""
        styles = parse_inline_styles(elem_style_str)

        # If background color is not specified on the element itself, check parents
        resolved_bg = styles.get("background-color") or styles.get("background")
        curr = getattr(element, "parent", None)
        while not resolved_bg and curr is not None and getattr(curr, "name", None):
            parent_style = curr.get("style", "") if hasattr(curr, "get") else ""
            p_styles = parse_inline_styles(parent_style)
            resolved_bg = p_styles.get("background-color") or p_styles.get("background")
            curr = getattr(curr, "parent", None)

        if not resolved_bg:
            resolved_bg = self.default_bg

        text = element.get_text() if hasattr(element, "get_text") else str(element)
        return analyze_anchor_visibility(
            text=text,
            style=styles,
            default_fg=self.default_fg,
            default_bg=resolved_bg,
        )

    def is_visible(self, element: Any) -> bool:
        """Predicate checking if element is visible."""
        return self.analyze_element(element).is_visible


def filter_visible_anchors(anchors: Iterable[Any]) -> list[Any]:
    """Filter an iterable of anchor elements, keeping only perceptible, non-disguised anchors."""
    analyzer = ContrastAnalyzer()
    return [a for a in anchors if analyzer.is_visible(a)]
