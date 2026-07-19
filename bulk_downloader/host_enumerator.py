"""bulk_downloader.host_enumerator -- A-DISCO cut 2: a bounded, host-confined
enumerator for level-4 discovery.

Given the root URL of an ALREADY-APPROVED host, walk it to depth and return the
in-host candidate URLs -- for a later stage (cut 3) to classify, score, and queue.
This module deliberately does NOT: fetch off-host, classify/score, or enqueue.
Those are other modules' jobs; here we only enumerate, safely.

THE SEAMS ARE INJECTED (fetch, link-extraction, seen-set, politeness clock), the
way ``automation_pipeline`` injects its stage impls: cut 3 wires the real fetch,
the ``discovery_seen`` dedup, and the rate-limit clock without this module
re-implementing any of them. That keeps this a standalone, pure, offline-testable
crawler with no cross-module import edge.

TWO SAFETY PROPERTIES, and why:

  * HOST-CONFINED. Only links whose registrable host matches the root are
    followed or collected. Discovery stays inside the already-approved host and
    can never wander onto -- let alone auto-approve -- a new host. This is the
    charter's "not a whole-web crawler" made mechanical.

  * BOUNDED BY DEFAULT (AR4). A budget that is only "active" once the operator
    configured one would let an unconfigured run enumerate a whole library
    unbounded -- the guardrail that isn't there by default, which at full
    enumeration is exactly the runaway AR4 forbids. So ``enumerate_host`` with no
    budget uses ``EnumBudget.default_safe`` (bounded on pages / candidates /
    depth / wall-clock), and a breach HALTS the crawl (checked BEFORE the next
    fetch) rather than being noticed after the fact. The halt is VISIBLE in the
    returned dict (``halted`` + ``halt_reason``): a cap that fires silently is not
    a cap.

Unset budget fields are uncapped (0), mirroring ``AutoBudget`` -- but the DEFAULT
path (no budget) is bounded, so unbounded is only ever an explicit, loud opt-in.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urljoin, urlsplit

_HREF_RE = re.compile(r"""href\s*=\s*["']([^"'#][^"']*)["']""", re.I)
_SKIP_SCHEME_RE = re.compile(r"^\s*(?:javascript:|mailto:|tel:|about:|data:)", re.I)


# ── host confinement ─────────────────────────────────────────────────────────

def _registrable(host: str) -> str:
    host = (host or "").lower().split(":")[0]
    parts = [p for p in host.split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _same_host(url: str, root_registrable: str) -> bool:
    try:
        return _registrable(urlsplit(url).netloc) == root_registrable
    except Exception:
        return False


def _default_extract_links(html: str, base_url: str) -> List[str]:
    """Absolute in-page hrefs, resolved against ``base_url``. Skips fragment-only,
    javascript:/mailto: and friends. Pure + regex-based (no parser dependency)."""
    out: List[str] = []
    for m in _HREF_RE.finditer(html or ""):
        href = m.group(1).strip()
        if not href or _SKIP_SCHEME_RE.match(href):
            continue
        try:
            absu = urljoin(base_url, href)
        except Exception:
            continue
        if absu.startswith("http"):
            out.append(absu)
    return out


# ── AR4 enumeration budget (mirrors automation_controller.AutoBudget) ────────

@dataclass
class EnumBudget:
    """The blast-radius ceiling on ONE enumeration run. Unset fields (0) are
    uncapped, the codebase budget idiom -- but ``enumerate_host`` uses
    ``default_safe`` when handed no budget, so the DEFAULT is always bounded.

    ``max_depth`` is hops-from-root, root = depth 1 (so ``max_depth=1`` fetches
    only the root; ``0`` = uncapped depth). ``delay_s`` is the politeness pause
    between fetches (rate-limiting -- never hammer an approved host)."""
    max_pages: int = 0        # pages fetched in one run (0 = uncapped)
    max_candidates: int = 0   # candidate URLs collected in one run (0 = uncapped)
    max_depth: int = 0        # crawl depth, root = 1 (0 = uncapped)
    wall_s: float = 0.0       # wall-clock ceiling for one run (0 = uncapped)
    delay_s: float = 0.0      # politeness delay between fetches (rate-limit)

    @classmethod
    def default_safe(cls) -> "EnumBudget":
        """A conservative bounded budget: what an unconfigured run gets, so
        enumeration is never unbounded by omission."""
        return cls(max_pages=50, max_candidates=200, max_depth=3,
                   wall_s=120.0, delay_s=1.0)

    def is_active(self) -> bool:
        return bool(self.max_pages or self.max_candidates or self.max_depth
                    or self.wall_s)

    def breach(self, *, pages: int = 0, candidates: int = 0,
               elapsed_s: float = 0.0) -> Optional[str]:
        """The name of the HALT ceiling that broke, or None. Depth is enforced
        structurally (the frontier stops expanding), so it is not a breach here."""
        if self.max_pages and pages >= self.max_pages:
            return "max_pages"
        if self.max_candidates and candidates >= self.max_candidates:
            return "max_candidates"
        if self.wall_s and elapsed_s >= self.wall_s:
            return "wall_s"
        return None


# ── the enumerator ───────────────────────────────────────────────────────────

def enumerate_host(root_url: str, *,
                   fetch_fn: Callable[[str], Optional[str]],
                   extract_links_fn: Optional[Callable[[str, str], List[str]]] = None,
                   seen_fn: Optional[Callable[[str], bool]] = None,
                   budget: Optional[EnumBudget] = None,
                   sleep_fn: Optional[Callable[[float], None]] = None,
                   ) -> Dict[str, Any]:
    """Enumerate an approved host from ``root_url``, returning its in-host
    candidate URLs, bounded by ``budget`` (``default_safe`` if None).

    ``fetch_fn(url) -> html|None`` (None = fetch failed, skipped). Off-host links
    and already-``seen_fn`` links are never fetched or collected. The result dict
    carries the counters and a VISIBLE halt: ``{root, host, pages_fetched,
    candidates, off_host_rejected, seen_skipped, fetch_failures, halted,
    halt_reason, elapsed_s}``.
    """
    bud = budget if budget is not None else EnumBudget.default_safe()
    extract = extract_links_fn or _default_extract_links
    seen = seen_fn or (lambda _u: False)
    sleep = sleep_fn or time.sleep

    out: Dict[str, Any] = {
        "root": root_url, "host": "", "pages_fetched": 0, "candidates": [],
        "off_host_rejected": 0, "seen_skipped": 0, "fetch_failures": 0,
        "halted": False, "halt_reason": "", "elapsed_s": 0.0,
    }

    root_reg = _registrable(urlsplit(root_url).netloc) if root_url else ""
    if not root_url or not root_reg:
        return out                              # bad root -> graceful empty
    out["host"] = root_reg

    started = time.time()
    frontier: List[tuple] = [(root_url, 1)]     # (url, depth); root = depth 1
    fetched: set = set()
    cand_set: set = set()
    candidates_ref: List[str] = out["candidates"]

    def _halt(reason: str) -> None:
        out["halted"] = True
        out["halt_reason"] = reason

    while frontier and not out["halted"]:
        url, depth = frontier.pop(0)
        if url in fetched:
            continue
        # HALT check BEFORE the fetch: a breach prevents the NEXT fetch.
        if bud.is_active():
            why = bud.breach(pages=out["pages_fetched"],
                             candidates=len(candidates_ref),
                             elapsed_s=time.time() - started)
            if why:
                _halt(why)
                break
        # politeness: pause between fetches (not before the first).
        if fetched and bud.delay_s:
            try:
                sleep(bud.delay_s)
            except Exception:
                pass
        try:
            html = fetch_fn(url)
        except Exception:
            html = None
        fetched.add(url)
        out["pages_fetched"] += 1
        if html is None:
            out["fetch_failures"] += 1
            continue
        try:
            links = extract(html, url)
        except Exception:
            links = []
        for link in links:
            if not _same_host(link, root_reg):
                out["off_host_rejected"] += 1
                continue
            if link in cand_set:
                continue
            try:
                if seen(link):
                    out["seen_skipped"] += 1
                    continue
            except Exception:
                pass                            # seen probe failed -> treat as new
            cand_set.add(link)
            candidates_ref.append(link)
            # candidate ceiling can trip mid-page (one page, many links).
            if bud.is_active() and bud.max_candidates \
                    and len(candidates_ref) >= bud.max_candidates:
                _halt("max_candidates")
                break
            # expand the frontier only within the depth budget.
            if (bud.max_depth == 0 or depth + 1 <= bud.max_depth):
                frontier.append((link, depth + 1))

    out["elapsed_s"] = round(time.time() - started, 3)
    return out
