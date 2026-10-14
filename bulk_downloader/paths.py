"""Download-root containment: the one bound for destructive file operations.

A path a request names is only a lookup key. Before a route unlinks, moves or
overwrites it, ``within_download_roots`` must place its fully resolved form
inside a configured download root. Both sides are compared as realpaths, so a
symlink anywhere in the path (or a ``..``) is judged by where it really lands,
and a sibling directory that merely shares a prefix (``/lib1`` vs ``/lib10``)
is never inside: the match is the root itself or ``root + os.sep``.
"""
from __future__ import annotations

import os


def _real(path) -> str:
    """realpath of an absolute path; "" when the path is unusable."""
    try:
        s = os.fspath(path)
    except TypeError:
        return ""
    if isinstance(s, bytes) or not s or "\x00" in s or not os.path.isabs(s):
        return ""
    try:
        return os.path.realpath(s)
    except (OSError, ValueError):
        return ""


def download_roots(s_cfg) -> list:
    """Every directory a site's files may live in, in config order, deduped:
    each download_dir (library_final.download_roots), then per site its
    spillover_dirs lines (where the runner lands downloads when the primary
    is full, split as hooks.resolve_download_dir does) and its
    storage_tier_dir (where the tier migration moves them), and, when any
    site's download_dir is blank, the deployment default. A blank or
    non-string entry is skipped; an unresolvable one is skipped by
    ``within_download_roots`` -- never widened."""
    from . import library_final
    from .hooks import _split_lines
    roots = list(library_final.download_roots(s_cfg))
    seen = set(roots)
    for cfg in (s_cfg or {}).values():
        cfg = cfg or {}
        spill = cfg.get("spillover_dirs")
        tier = cfg.get("storage_tier_dir")
        extra = _split_lines(spill) if isinstance(spill, str) else []
        if isinstance(tier, str) and tier.strip():
            extra.append(tier.strip())
        for d in extra:
            if d not in seen:
                seen.add(d)
                roots.append(d)
    if any(not str((c or {}).get("download_dir") or "").strip()
           for c in (s_cfg or {}).values()):
        # A site naming no download_dir downloads into the deployment
        # default: the resolver runner._resolve_write_dir calls, imported
        # lazily as it does. A blank default adds nothing -- never "" or "/".
        try:
            import importlib
            d = str(getattr(importlib.import_module("bulk_downloader.app"),
                            "_oi_default_download_dir")() or "").strip()
        except Exception:
            d = ""
        if d and d not in seen:
            roots.append(d)
    return roots


def _configured_roots() -> list:
    from . import app_state
    return download_roots(app_state.s_cfg)


def within_download_roots(p, roots=None) -> bool:
    """True when ``p`` resolves inside one of ``roots`` (default: every
    configured root, ``download_roots``). Relative, empty, NUL-bearing or
    unresolvable paths, and an empty root list, are outside."""
    real = _real(p)
    if not real:
        return False
    for root in (_configured_roots() if roots is None else roots):
        root_real = _real(root)
        if not root_real:
            continue
        if real == root_real or real.startswith(os.path.join(root_real, "")):
            return True
    return False
