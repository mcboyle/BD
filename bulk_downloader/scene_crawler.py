"""Authenticated, resumable scene discovery for the Capture/Site GUI.

The crawler deliberately uses rendered pages instead of raw HTTP: paid-site
libraries are frequently gated, hydrated by JavaScript, or backed by infinite
scroll.  Discovery is conservative.  A page needs explicit members-area
evidence before an empty result can be called an empty library; otherwise the
run reports ``NOT_LOGGED_IN`` (A7 fail-closed direction).

Scene recognition is based on *path cohorts containing thumbnail images*, not
the most frequent path cohort.  Pagination/filter links are often the most
common shape on a listing, while measured scene cards consistently contain at
least some ``<img>`` descendants.
"""
from __future__ import annotations

import contextlib
import json
import re
import threading
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

from . import db
from .constants import AUTH_BODY_RE, AUTH_HINTS


STATE_IDLE = "IDLE"
STATE_RUNNING = "RUNNING"
STATE_COMPLETED = "COMPLETED"
STATE_NOT_LOGGED_IN = "NOT_LOGGED_IN"
STATE_FAILED = "FAILED"

SETTLE_SETTLED = "SETTLED"
SETTLE_UNKNOWN = "UNKNOWN"

# A scroll only tells the renderer to move; the listener that appends
# lazy-loaded cards runs at its next rendering opportunity, which is not
# ordered against the CDP round-trip that reads the DOM back.  The walk
# therefore waits for the anchor count to STOP CHANGING -- unchanged across
# SCROLL_SETTLE_QUIET_POLLS consecutive reads -- instead of for a fixed number
# of scroll events.  SCROLL_SETTLE_BUDGET_S bounds the whole wait for one
# listing page; expiry is reported as UNKNOWN, never as a settled page.
SCROLL_SETTLE_POLL_S = 0.05
SCROLL_SETTLE_QUIET_POLLS = 6
SCROLL_SETTLE_BUDGET_S = 10.0

_ACTIVE: dict[str, str] = {}
_ACTIVE_LOCK = threading.Lock()

_DATE_LINE_RE = re.compile(
    r"^(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
    r"\s+\d{1,2},?\s*\d{4}|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|"
    r"\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})$",
    re.I,
)
_TITLE_DELIMITERS = (" / ", " | ", " - ")

_ANCHOR_JS = r"""
(anchors) => anchors.map((a) => {
  const card = a.closest(
    "article, li, figure, [class*='card'], [class*='Card'], " +
    "[class*='item'], [class*='Item'], [data-card]"
  ) || a.parentElement;
  const titled = card ? card.querySelector(
    "h1, h2, h3, h4, h5, h6, [data-title], .title, .scene-title"
  ) : null;
  const titledValue = titled
    ? (titled.getAttribute("data-title") || titled.textContent || "")
    : "";
  const img = a.querySelector("img");
  return {
    url: a.href || "",
    text: a.innerText || "",
    title: a.getAttribute("title") || "",
    aria: a.getAttribute("aria-label") || "",
    img_alt: img ? (img.getAttribute("alt") || "") : "",
    nearest: titledValue,
    has_img: Boolean(img),
    class_name: typeof a.className === "string" ? a.className : "",
    rel: a.getAttribute("rel") || "",
  };
})
"""


class CrawlAlreadyRunning(RuntimeError):
    """Raised when the operator starts a second run for the same site."""

    def __init__(self, run_id: str):
        super().__init__(f"scene discovery already running ({run_id})")
        self.run_id = run_id


def _ensure_schema(db_path: str | None = None) -> None:
    with db.db_conn(db_path) as cx:
        cx.execute(
            """
            CREATE TABLE IF NOT EXISTS scene_discoveries (
                site_id TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                title_source TEXT NOT NULL DEFAULT 'card',
                card_context TEXT NOT NULL DEFAULT '',
                listing_url TEXT NOT NULL DEFAULT '',
                effective_listing_url TEXT NOT NULL DEFAULT '',
                discovered_at REAL NOT NULL,
                queued_at REAL,
                PRIMARY KEY (site_id, url)
            )
            """
        )
        cx.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_scene_discoveries_site_time
            ON scene_discoveries(site_id, discovered_at DESC)
            """
        )
        cx.execute(
            """
            CREATE TABLE IF NOT EXISTS scene_crawl_checkpoints (
                site_id TEXT PRIMARY KEY,
                listing_url TEXT NOT NULL,
                frontier_json TEXT NOT NULL DEFAULT '[]',
                completed INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            )
            """
        )
        cx.execute(
            """
            CREATE TABLE IF NOT EXISTS scene_listing_validators (
                site_id TEXT NOT NULL,
                listing_url TEXT NOT NULL,
                etag TEXT NOT NULL DEFAULT '',
                last_modified TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL,
                PRIMARY KEY (site_id, listing_url)
            )
            """
        )
        cx.execute(
            """
            CREATE TABLE IF NOT EXISTS scene_crawl_runs (
                run_id TEXT PRIMARY KEY,
                site_id TEXT NOT NULL,
                listing_url TEXT NOT NULL,
                state TEXT NOT NULL,
                started_at REAL NOT NULL,
                finished_at REAL,
                result_json TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT ''
            )
            """
        )
        cx.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_scene_crawl_runs_site_time
            ON scene_crawl_runs(site_id, started_at DESC)
            """
        )


def _clean_text(value: Any) -> str:
    lines = []
    for line in str(value or "").replace("\u00a0", " ").splitlines():
        normalized = " ".join(line.split()).strip(" -|/")
        if not normalized or _DATE_LINE_RE.fullmatch(normalized):
            continue
        lines.append(normalized)
    return " ".join(lines).strip()


def _card_title(anchor: dict[str, Any]) -> str:
    """Apply the measured, ordered listing-card title fallback ladder."""
    for key in ("text", "title", "aria", "img_alt", "nearest"):
        value = _clean_text(anchor.get(key))
        if value:
            return value
    return ""


def strip_repeated_title_templates(titles: Iterable[str]) -> list[str]:
    """Strip a trailing site-template segment only with repeated evidence.

    A delimiter inside one real title (for example ``A Real Title - Part
    Two``) is untouched.  A prefix is stripped only when the exact leading
    template repeats across at least two scene-page titles.
    """
    values = [str(title or "").strip() for title in titles]
    parsed: list[tuple[str, str, str] | None] = []
    prefixes: Counter[tuple[str, str]] = Counter()
    for value in values:
        choice = None
        for delimiter in _TITLE_DELIMITERS:
            parts = [part.strip() for part in value.split(delimiter)]
            if len(parts) < 2 or not all(parts):
                continue
            prefix = delimiter.join(parts[:-1])
            choice = (delimiter, prefix, parts[-1])
            prefixes[(delimiter, prefix)] += 1
            break
        parsed.append(choice)
    out = []
    for value, choice in zip(values, parsed):
        if choice and prefixes[(choice[0], choice[1])] >= 2:
            out.append(choice[2])
        else:
            out.append(value)
    return out


def _same_site(left: str, right: str) -> bool:
    a = (urlsplit(left).hostname or "").lower().strip(".")
    b = (urlsplit(right).hostname or "").lower().strip(".")
    if not a or not b:
        return False
    if a == b:
        return True
    # Conservative www/subdomain allowance without pulling in a public-suffix
    # dependency.  Loopback/IP hosts only pass the exact-match branch above.
    if re.fullmatch(r"[\d.]+", a) or re.fullmatch(r"[\d.]+", b):
        return False
    ap = a.split(".")
    bp = b.split(".")
    return len(ap) >= 2 and len(bp) >= 2 and ap[-2:] == bp[-2:]


def _path_parts(url: str) -> tuple[str, ...]:
    return tuple(part for part in urlsplit(url).path.split("/") if part)


def _cohort_signature(
    parts: tuple[str, ...],
    repeated: dict[tuple[int, int], set[str]],
) -> tuple[int, tuple[tuple[int, str], ...]]:
    fixed = tuple(
        (index, value)
        for index, value in enumerate(parts)
        if value in repeated.get((len(parts), index), set())
    )
    return len(parts), fixed


def _shape_label(
    signature: tuple[int, tuple[tuple[int, str], ...]],
    sample: dict[str, Any],
) -> str:
    length, fixed_pairs = signature
    fixed = dict(fixed_pairs)
    parts = _path_parts(sample["url"])
    rendered = []
    for index in range(length):
        if index in fixed:
            rendered.append(fixed[index])
        elif index < len(parts) and parts[index].isdigit():
            rendered.append("<n>")
        else:
            rendered.append("<slug>")
    return "/" + "/".join(rendered)


def _scene_cohort(
    anchors: Iterable[dict[str, Any]],
    listing_url: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return the image-bearing path cohort, including its text-only cards."""
    usable = []
    for anchor in anchors:
        url = str(anchor.get("url") or "")
        parts = _path_parts(url)
        if len(parts) < 2 or not _same_site(url, listing_url):
            continue
        if urlsplit(url).scheme not in ("http", "https"):
            continue
        copied = dict(anchor)
        copied["_parts"] = parts
        usable.append(copied)
    if not usable:
        return [], []

    per_position: dict[tuple[int, int], Counter[str]] = defaultdict(Counter)
    for anchor in usable:
        parts = anchor["_parts"]
        for index, value in enumerate(parts):
            per_position[(len(parts), index)][value] += 1
    repeated = {
        key: {value for value, count in counts.items() if count >= 2}
        for key, counts in per_position.items()
    }

    cohorts: dict[
        tuple[int, tuple[tuple[int, str], ...]], list[dict[str, Any]]
    ] = defaultdict(list)
    for anchor in usable:
        cohorts[_cohort_signature(anchor["_parts"], repeated)].append(anchor)

    candidates = []
    for signature, rows in cohorts.items():
        image_count = sum(bool(row.get("has_img")) for row in rows)
        if not image_count:
            continue
        # Thumbnail evidence dominates frequency.  Cohort size is the
        # tiebreaker, which lets one measured thumbnail identify sibling cards
        # whose anchors themselves contain no image (nubilefilms).
        candidates.append((image_count, len(rows), signature, rows))
    if not candidates:
        return [], []
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best_images, best_size = candidates[0][0], candidates[0][1]
    winning = [
        item for item in candidates
        if item[0] == best_images and item[1] == best_size
    ]
    scenes: list[dict[str, Any]] = []
    shapes: list[str] = []
    seen = set()
    for _images, _size, signature, rows in winning:
        shapes.append(_shape_label(signature, rows[0]))
        for row in rows:
            row.pop("_parts", None)
            if row["url"] in seen:
                continue
            seen.add(row["url"])
            scenes.append(row)
    return scenes, shapes


def _collect_anchors(page: Any) -> list[dict[str, Any]]:
    rows = page.locator("a[href]").evaluate_all(_ANCHOR_JS)
    return [dict(row) for row in (rows or []) if isinstance(row, dict)]


def _merge_anchors(
    aggregate: dict[str, dict[str, Any]],
    rows: Iterable[dict[str, Any]],
) -> None:
    for row in rows:
        url = str(row.get("url") or "")
        if not url:
            continue
        current = aggregate.get(url)
        if current is None:
            aggregate[url] = dict(row)
            continue
        current["has_img"] = bool(current.get("has_img") or row.get("has_img"))
        for key in ("text", "title", "aria", "img_alt", "nearest", "class_name", "rel"):
            if not current.get(key) and row.get(key):
                current[key] = row[key]


def _page_metrics(page: Any) -> tuple[int, int]:
    result = page.evaluate(
        "() => [Math.max(document.body?.scrollHeight || 0, "
        "document.documentElement?.scrollHeight || 0), "
        "document.querySelectorAll('a[href]').length]"
    )
    return int(result[0]), int(result[1])


_SCROLL_JS = """
() => new Promise((resolve) => {
  let done = false;
  const finish = () => { if (!done) { done = true; resolve(true); } };
  window.scrollTo(0, document.body.scrollHeight);
  // The listener that appends lazy-loaded cards runs during "update the
  // rendering", in the scroll steps, which precede animation-frame callbacks.
  // Resolving inside a frame callback therefore PROVES the scroll event was
  // already dispatched, instead of guessing at how long that takes.  The timer
  // is a ceiling for a document that never renders a frame -- it is a refusal
  // to hang, not the settle mechanism.
  setTimeout(finish, 1000);
  requestAnimationFrame(() => requestAnimationFrame(finish));
})
"""


def _scroll_to_end(page: Any) -> None:
    try:
        page.evaluate(_SCROLL_JS)
    except Exception:
        # A page that cannot run the frame barrier still gets the scroll; the
        # stability poll below remains the settle condition.
        page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")


def _settle_metrics(
    page: Any,
    *,
    before: tuple[int, int],
    deadline: float,
    poll_s: float,
    quiet_polls: int,
) -> tuple[tuple[int, int], bool, bool]:
    """Poll ``(height, links)`` until it stops changing, or ``deadline`` passes.

    Chromium dispatches the ``scroll`` listener that appends lazy-loaded cards
    at its next rendering opportunity, which is not ordered against the CDP
    round-trip that reads the DOM back.  A single post-scroll read therefore
    observes the *pre-scroll* page whenever the host is contended, and the walk
    then stops a whole batch of cards short while reporting a clean result.
    Stability -- the count has not moved across ``quiet_polls`` consecutive
    reads -- is the real settle condition; a fixed number of scroll events is
    not.  Expiry is reported as UNKNOWN (A7), never as a settled page.

    SETTLED is a bounded claim and says so: the count did not move across
    ``quiet_polls`` consecutive reads after a rendered frame.  A loader that
    appends asynchronously later than that window is not observable by any
    bounded walk, which is why ``_scroll_to_end`` closes the *dispatch* race
    causally rather than leaning on this window for it.

    Returns ``(metrics, settled, absorbed_late_growth)`` where
    ``absorbed_late_growth`` is True when the first read after the scroll still
    showed ``before`` but a later poll saw growth -- i.e. this call observed the
    race the old single read would have lost.
    """
    quiet_polls = max(1, int(quiet_polls))
    current = _page_metrics(page)
    first_read_was_stale = current == before
    stable = 1
    while stable < quiet_polls:
        if time.monotonic() >= deadline:
            return current, False, False
        page.wait_for_timeout(max(1, int(poll_s * 1000)))
        latest = _page_metrics(page)
        if latest == current:
            stable += 1
        else:
            current = latest
            stable = 1
    return current, True, first_read_was_stale and current != before


def _scroll_and_collect(
    page: Any,
    *,
    max_scrolls: int,
    settle_s: float,
    poll_s: float = SCROLL_SETTLE_POLL_S,
    quiet_polls: int = SCROLL_SETTLE_QUIET_POLLS,
    settle_budget_s: float = SCROLL_SETTLE_BUDGET_S,
) -> tuple[list[dict[str, Any]], int, str, int]:
    """Scroll a listing until its anchor count stops growing.

    Returns ``(anchor rows, growth steps, settle state, absorbed races)``.  The
    walk stops on OBSERVED STABILITY, never on a scroll-event count: a scroll is
    dispatched, a rendered frame proves the listener ran, and the count must
    then hold still across the quiet window before the page is called done.
    """
    aggregate: dict[str, dict[str, Any]] = {}
    _merge_anchors(aggregate, _collect_anchors(page))
    previous = _page_metrics(page)
    growth_steps = 0
    settle_state = SETTLE_SETTLED
    absorbed_races = 0
    # One shared budget per listing page bounds the added wall time even when a
    # page never stops growing; an exhausted budget degrades to the old single
    # read and is reported as UNKNOWN rather than as a settled page.
    budget_deadline = time.monotonic() + max(0.0, float(settle_budget_s))
    for _ in range(max(0, int(max_scrolls))):
        _scroll_to_end(page)
        if settle_s > 0:
            page.wait_for_timeout(int(settle_s * 1000))
        current, settled, absorbed = _settle_metrics(
            page,
            before=previous,
            deadline=budget_deadline,
            poll_s=poll_s,
            quiet_polls=quiet_polls,
        )
        _merge_anchors(aggregate, _collect_anchors(page))
        if absorbed:
            absorbed_races += 1
        if not settled:
            settle_state = SETTLE_UNKNOWN
        height, links = current
        previous_height, previous_links = previous
        if height > previous_height or links > previous_links:
            growth_steps += 1
        if settled and height == previous_height and links == previous_links:
            break
        previous = current
    return list(aggregate.values()), growth_steps, settle_state, absorbed_races


def _pager_urls(
    anchors: Iterable[dict[str, Any]],
    *,
    current_url: str,
    scene_urls: set[str],
) -> list[str]:
    candidates = []
    for anchor in anchors:
        url = str(anchor.get("url") or "")
        if not url or url == current_url or url in scene_urls:
            continue
        if not _same_site(url, current_url):
            continue
        text = _clean_text(anchor.get("text"))
        class_name = str(anchor.get("class_name") or "").lower()
        rel = str(anchor.get("rel") or "").lower().split()
        pageish = (
            "page" in class_name
            or "next" in rel
            or "prev" in rel
            or bool(re.fullmatch(r"\d+", text))
        )
        if not pageish:
            continue
        tail = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
        order = int(tail) if tail.isdigit() else 0
        candidates.append((order, url))
    return [url for _order, url in sorted(set(candidates), key=lambda item: (item[0], item[1]))]


def _negative_auth_page(page: Any, response_status: int | None = None) -> bool:
    if response_status in (401, 403):
        return True
    current = str(getattr(page, "url", "") or "").lower()
    if any(hint in current for hint in AUTH_HINTS):
        return True
    try:
        if page.locator('input[type="password"]').count() > 0:
            return True
    except Exception:
        pass
    try:
        return bool(AUTH_BODY_RE.search(page.content()[:20000]))
    except Exception:
        return False


def _members_evidence(
    page: Any,
    site_config: dict[str, Any],
    response_status: int | None = None,
) -> bool:
    if _negative_auth_page(page, response_status):
        return False
    try:
        if page.locator(
            'a[href*="logout" i], a[href*="signout" i], '
            'a[href*="sign-out" i], form[action*="logout" i]'
        ).count() > 0:
            return True
        if page.locator('[data-members-area="true"], [data-authenticated="true"]').count() > 0:
            return True
    except Exception:
        pass
    success_url = str(site_config.get("success_url") or "").strip()
    if success_url:
        try:
            success = urlsplit(success_url)
            current = urlsplit(str(page.url))
            if success.hostname == current.hostname and current.path.startswith(success.path):
                return True
        except Exception:
            pass
    # Members paths are explicit server-side evidence, but only after the
    # negative login-wall checks above.  This covers sites whose logged-in nav
    # omits a logout link.
    path = urlsplit(str(getattr(page, "url", "") or "")).path.lower()
    return "/members/" in path or path.startswith("/members/")


def _existing(site_id: str, db_path: str | None) -> dict[str, dict[str, Any]]:
    with db.db_conn(db_path) as cx:
        rows = cx.execute(
            "SELECT * FROM scene_discoveries WHERE site_id = ?", (site_id,)
        ).fetchall()
    return {str(row["url"]): dict(row) for row in rows}


def _insert_discovery(
    site_id: str,
    scene: dict[str, Any],
    *,
    listing_url: str,
    effective_url: str,
    db_path: str | None,
) -> dict[str, Any]:
    now = time.time()
    title = _card_title(scene)
    record = {
        "site_id": site_id,
        "url": scene["url"],
        "title": title,
        "title_source": "card",
        "card_context": title,
        "listing_url": listing_url,
        "effective_listing_url": effective_url,
        "discovered_at": now,
        "queued_at": None,
    }
    with db.db_conn(db_path) as cx:
        cx.execute(
            """
            INSERT OR IGNORE INTO scene_discoveries(
                site_id, url, title, title_source, card_context, listing_url,
                effective_listing_url, discovered_at, queued_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                site_id, record["url"], title, "card", title, listing_url,
                effective_url, now,
            ),
        )
    return record


def _update_title(record: dict[str, Any], db_path: str | None) -> None:
    with db.db_conn(db_path) as cx:
        cx.execute(
            """
            UPDATE scene_discoveries
            SET title = ?, title_source = ?, card_context = ?
            WHERE site_id = ? AND url = ?
            """,
            (
                record["title"], record["title_source"], record["card_context"],
                record["site_id"], record["url"],
            ),
        )


def _mark_queued(site_id: str, url: str, db_path: str | None) -> None:
    with db.db_conn(db_path) as cx:
        cx.execute(
            "UPDATE scene_discoveries SET queued_at = ? WHERE site_id = ? AND url = ?",
            (time.time(), site_id, url),
        )


def discovery_history(
    site_id: str,
    *,
    db_path: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    _ensure_schema(db_path)
    with db.db_conn(db_path) as cx:
        rows = cx.execute(
            """
            SELECT site_id, url, title, title_source, card_context, listing_url,
                   effective_listing_url, discovered_at, queued_at
            FROM scene_discoveries
            WHERE site_id = ?
            ORDER BY discovered_at DESC, url ASC
            LIMIT ?
            """,
            (site_id, max(1, min(int(limit), 1000))),
        ).fetchall()
    return [dict(row) for row in rows]


def _load_frontier(site_id: str, listing_url: str, db_path: str | None) -> list[str]:
    with db.db_conn(db_path) as cx:
        row = cx.execute(
            "SELECT * FROM scene_crawl_checkpoints WHERE site_id = ?", (site_id,)
        ).fetchone()
    if not row or row["listing_url"] != listing_url or int(row["completed"]):
        return []
    try:
        values = json.loads(row["frontier_json"] or "[]")
        return [str(value) for value in values if isinstance(value, str) and value]
    except Exception:
        return []


def _completed_checkpoint(
    site_id: str, listing_url: str, db_path: str | None
) -> bool:
    with db.db_conn(db_path) as cx:
        row = cx.execute(
            """
            SELECT listing_url, completed
            FROM scene_crawl_checkpoints
            WHERE site_id = ?
            """,
            (site_id,),
        ).fetchone()
    return bool(
        row
        and str(row["listing_url"]) == listing_url
        and int(row["completed"])
    )


def _load_listing_validators(
    site_id: str, listing_url: str, db_path: str | None
) -> dict[str, str]:
    with db.db_conn(db_path) as cx:
        row = cx.execute(
            """
            SELECT etag, last_modified
            FROM scene_listing_validators
            WHERE site_id = ? AND listing_url = ?
            """,
            (site_id, listing_url),
        ).fetchone()
    if not row:
        return {}
    headers = {}
    if str(row["etag"] or ""):
        headers["If-None-Match"] = str(row["etag"])
    if str(row["last_modified"] or ""):
        headers["If-Modified-Since"] = str(row["last_modified"])
    return headers


def _save_listing_validators(
    site_id: str,
    listing_url: str,
    validators: dict[str, str],
    db_path: str | None,
) -> None:
    with db.db_conn(db_path) as cx:
        cx.execute(
            """
            INSERT INTO scene_listing_validators(
                site_id, listing_url, etag, last_modified, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(site_id, listing_url) DO UPDATE SET
                etag = excluded.etag,
                last_modified = excluded.last_modified,
                updated_at = excluded.updated_at
            """,
            (
                site_id,
                listing_url,
                str(validators.get("etag") or ""),
                str(validators.get("last-modified") or ""),
                time.time(),
            ),
        )


def _response_validators(response: Any) -> dict[str, str]:
    raw = getattr(response, "headers", {}) if response is not None else {}
    if callable(raw):
        raw = raw()
    if not isinstance(raw, dict):
        return {}
    lowered = {str(key).lower(): str(value) for key, value in raw.items()}
    return {
        key: lowered[key]
        for key in ("etag", "last-modified")
        if lowered.get(key)
    }


def _save_frontier(
    site_id: str,
    listing_url: str,
    frontier: Iterable[str],
    *,
    completed: bool,
    db_path: str | None,
) -> None:
    payload = list(dict.fromkeys(str(url) for url in frontier if url))
    with db.db_conn(db_path) as cx:
        cx.execute(
            """
            INSERT INTO scene_crawl_checkpoints(
                site_id, listing_url, frontier_json, completed, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(site_id) DO UPDATE SET
                listing_url = excluded.listing_url,
                frontier_json = excluded.frontier_json,
                completed = excluded.completed,
                updated_at = excluded.updated_at
            """,
            (site_id, listing_url, json.dumps(payload), int(completed), time.time()),
        )


class _Pacer:
    def __init__(self, delay_s: float):
        self.delay_s = max(0.0, float(delay_s))
        self.max_delay_s = max(30.0, self.delay_s)
        self.requests = 0
        self._requests_by_host: dict[str, int] = defaultdict(int)
        self._delay_by_host: dict[str, float] = defaultdict(
            lambda: self.delay_s
        )

    @staticmethod
    def _host(url: str) -> str:
        return (urlsplit(str(url or "")).hostname or "").lower()

    def before_request(self, url: str) -> None:
        host = self._host(url)
        delay = self._delay_by_host[host]
        if self._requests_by_host[host] and delay:
            time.sleep(delay)
        self._requests_by_host[host] += 1
        self.requests += 1

    def after_response(self, url: str, response: Any) -> None:
        host = self._host(url)
        status = getattr(response, "status", None) if response else None
        current = self._delay_by_host[host]
        if status in (429, 503):
            # A direct throttle doubles only this host's pause.  The existing
            # site setting remains the floor, and the API's 30-second bound
            # remains the ceiling.
            self._delay_by_host[host] = min(
                self.max_delay_s,
                max(self.delay_s, current * 2.0 or 1.0),
            )
        elif isinstance(status, int) and 200 <= status < 400:
            # One successful response removes one backoff step, never the
            # operator-configured politeness floor.
            self._delay_by_host[host] = max(self.delay_s, current / 2.0)


def _goto(
    page: Any,
    url: str,
    pacer: _Pacer,
    *,
    conditional_headers: dict[str, str] | None = None,
) -> Any:
    pacer.before_request(url)
    matcher = handler = None
    conditional_responses: list[Any] = []
    if conditional_headers:
        def matcher(candidate: str) -> bool:
            return candidate == url

        def handler(route: Any) -> None:
            request_headers = dict(getattr(route.request, "headers", {}) or {})
            upstream = route.fetch(
                headers={**request_headers, **conditional_headers}
            )
            conditional_responses.append(upstream)
            if getattr(upstream, "status", None) == 304:
                # A fresh browser cache cannot render a 304 body.  Fulfill an
                # empty 200 document for Chromium while returning the observed
                # upstream 304 to the crawler below.
                route.fulfill(
                    status=200,
                    content_type="text/html; charset=utf-8",
                    body="",
                )
            else:
                route.fulfill(response=upstream)

        page.route(matcher, handler, times=1)
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
    finally:
        if matcher is not None and handler is not None:
            page.unroute(matcher, handler)
    if conditional_responses:
        upstream = conditional_responses[-1]
        if getattr(upstream, "status", None) == 304:
            response = upstream
    pacer.after_response(url, response)
    return response


def _clear_gates(
    page: Any,
    site_config: dict[str, Any],
    *,
    first_listing_page: bool,
    delay_s: float,
) -> None:
    # Use the canonical shared dismissal loop.  The login-wall scope is once;
    # per-page gates are retried on every listing/scene navigation.
    from . import interstitial

    settle = min(0.5, max(0.0, float(delay_s)))
    if first_listing_page:
        interstitial.dismiss(
            page,
            site_config.get("dismiss_selectors_login", ""),
            settle_s=settle,
        )
    interstitial.dismiss(
        page,
        site_config.get("dismiss_selectors", ""),
        settle_s=settle,
    )


def _page_title(page: Any, response_status: int | None) -> tuple[str, str]:
    if _negative_auth_page(page, response_status):
        return "", ""
    try:
        og = page.locator('meta[property="og:title" i]').first.get_attribute("content")
        if _clean_text(og):
            return _clean_text(og), "og:title"
    except Exception:
        pass
    try:
        title = _clean_text(page.title())
        if title:
            return title, "document.title"
    except Exception:
        pass
    try:
        heading = _clean_text(page.locator("h1").first.inner_text(timeout=1000))
        if heading:
            return heading, "h1"
    except Exception:
        pass
    return "", ""


def _resolve_scene_titles(
    page: Any,
    records: list[dict[str, Any]],
    *,
    site_config: dict[str, Any],
    limit: int,
    pacer: _Pacer,
    db_path: str | None,
) -> int:
    fetched: list[tuple[dict[str, Any], str, str]] = []
    for record in records[: max(0, int(limit))]:
        try:
            response = _goto(page, record["url"], pacer)
            status = getattr(response, "status", None) if response else None
            _clear_gates(
                page, site_config, first_listing_page=False, delay_s=pacer.delay_s
            )
            title, source = _page_title(page, status)
            if title:
                fetched.append((record, title, source))
        except Exception:
            continue
    stripped = strip_repeated_title_templates(item[1] for item in fetched)
    for (record, _raw, source), title in zip(fetched, stripped):
        if title:
            record["title"] = title
            record["title_source"] = source
            _update_title(record, db_path)
    return len(fetched)


def _run_base(
    *,
    state: str,
    effective_url: str,
    pages_walked: int,
    page_urls: list[str],
    scroll_growth_steps: int,
    scroll_settle_state: str,
    scroll_late_growth_steps: int,
    shapes: set[str],
    scenes: list[dict[str, Any]],
    discovered: int,
    queued: int,
    title_pages_fetched: int,
    zero_scenes_found: bool,
    listing_not_modified: bool = False,
    enqueue_errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "state": state,
        "effective_url": effective_url,
        "pages_walked": pages_walked,
        "page_urls": page_urls,
        "scroll_growth_steps": scroll_growth_steps,
        # SETTLED only when every listing page's anchor count was observed to
        # stop changing; UNKNOWN when a settle budget expired first (A7).
        "scroll_settle_state": scroll_settle_state,
        # Scrolls whose first read was stale and whose later poll saw growth --
        # the race a single post-scroll read would have lost.
        "scroll_late_growth_steps": scroll_late_growth_steps,
        "scene_shapes": sorted(shapes),
        "scenes": scenes,
        "discovered": discovered,
        "queued": queued,
        "title_pages_fetched": title_pages_fetched,
        "zero_scenes_found": bool(zero_scenes_found),
        "listing_not_modified": bool(listing_not_modified),
        "enqueue_errors": enqueue_errors or [],
    }


def _finish_run(run_id: str | None, result: dict[str, Any], db_path: str | None) -> None:
    if not run_id:
        return
    with db.db_conn(db_path) as cx:
        cx.execute(
            """
            UPDATE scene_crawl_runs
            SET state = ?, finished_at = ?, result_json = ?, error = ''
            WHERE run_id = ?
            """,
            (result["state"], time.time(), json.dumps(result), run_id),
        )


def crawl_with_page(
    page: Any,
    *,
    site_id: str,
    listing_url: str,
    site_config: dict[str, Any],
    newest_n: int = 50,
    max_pages: int = 5,
    max_scrolls: int = 8,
    delay_s: float = 1.0,
    title_fetch_limit: int = 50,
    enqueue_fn: Callable[[str, str], Any],
    db_path: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Discover, persist, resolve titles, and enqueue scenes using one page."""
    _ensure_schema(db_path)
    site_id = str(site_id or "").strip()
    listing_url = str(listing_url or "").strip()
    if not site_id:
        raise ValueError("site_id required")
    parsed = urlsplit(listing_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("listing_url must be an absolute http(s) URL")
    newest_n = max(0, int(newest_n))
    max_pages = max(1, min(int(max_pages), 500))
    max_scrolls = max(0, min(int(max_scrolls), 50))
    title_fetch_limit = max(0, min(int(title_fetch_limit), 1000))
    pacer = _Pacer(delay_s)

    existing = _existing(site_id, db_path)
    saved_frontier = _load_frontier(site_id, listing_url, db_path)
    conditional_headers = {}
    if (
        not saved_frontier
        and _completed_checkpoint(site_id, listing_url, db_path)
        and not any(row.get("queued_at") is None for row in existing.values())
    ):
        conditional_headers = _load_listing_validators(
            site_id, listing_url, db_path
        )
    to_visit = list(saved_frontier or [listing_url])
    scheduled = set(to_visit)
    visited: set[str] = set()
    new_records: list[dict[str, Any]] = []
    queue_records: dict[str, dict[str, Any]] = {}
    pages_walked = 0
    page_urls: list[str] = []
    effective_url = ""
    scroll_growth_steps = 0
    scroll_settle_state = SETTLE_SETTLED
    scroll_late_growth_steps = 0
    shapes: set[str] = set()
    authenticated = False
    saw_scene_cohort = False
    terminated_on_no_new = False
    stopped_on_depth = False
    pending_validators: dict[str, dict[str, str]] = {}

    while to_visit and pages_walked < max_pages:
        requested = to_visit.pop(0)
        if requested in visited:
            continue
        visited.add(requested)
        request_validators = (
            conditional_headers
            if pages_walked == 0 and requested == listing_url
            else None
        )
        response = _goto(
            page,
            requested,
            pacer,
            conditional_headers=request_validators,
        )
        status = getattr(response, "status", None) if response else None
        pages_walked += 1
        if status == 304:
            page_urls.append(requested)
            effective_url = requested
            result = _run_base(
                state=STATE_COMPLETED,
                effective_url=effective_url,
                pages_walked=pages_walked,
                page_urls=page_urls,
                scroll_growth_steps=scroll_growth_steps,
                scroll_settle_state=scroll_settle_state,
                scroll_late_growth_steps=scroll_late_growth_steps,
                shapes=shapes,
                scenes=[],
                discovered=0,
                queued=0,
                title_pages_fetched=0,
                zero_scenes_found=False,
                listing_not_modified=True,
            )
            _finish_run(run_id, result, db_path)
            return result
        current = str(page.url)
        page_urls.append(current)
        if not effective_url:
            effective_url = current
        _clear_gates(
            page,
            site_config,
            first_listing_page=pages_walked == 1,
            delay_s=delay_s,
        )
        anchors, growth, page_settle_state, absorbed = _scroll_and_collect(
            page,
            max_scrolls=max_scrolls,
            settle_s=min(1.0, max(0.0, float(delay_s))),
        )
        scroll_growth_steps += growth
        scroll_late_growth_steps += absorbed
        if page_settle_state == SETTLE_UNKNOWN:
            scroll_settle_state = SETTLE_UNKNOWN
        scenes, page_shapes = _scene_cohort(anchors, current)
        shapes.update(page_shapes)
        saw_scene_cohort = saw_scene_cohort or bool(scenes)

        if not _members_evidence(page, site_config, status):
            _save_frontier(
                site_id, listing_url, [], completed=True, db_path=db_path
            )
            result = _run_base(
                state=STATE_NOT_LOGGED_IN,
                effective_url=effective_url,
                pages_walked=pages_walked,
                page_urls=page_urls,
                scroll_growth_steps=scroll_growth_steps,
                scroll_settle_state=scroll_settle_state,
                scroll_late_growth_steps=scroll_late_growth_steps,
                shapes=shapes,
                scenes=[],
                discovered=0,
                queued=0,
                title_pages_fetched=0,
                zero_scenes_found=False,
            )
            _finish_run(run_id, result, db_path)
            return result
        authenticated = True
        # A validator is durable only after this response has passed the
        # members-area checks, and it is committed only when the whole listing
        # walk below completes.  A crash cannot advance the validator past the
        # discovery checkpoint it describes.
        if current == requested:
            pending_validators[requested] = _response_validators(response)

        scene_urls = {scene["url"] for scene in scenes}
        for pager_url in _pager_urls(
            anchors, current_url=current, scene_urls=scene_urls
        ):
            if pager_url not in scheduled and pager_url not in visited:
                scheduled.add(pager_url)
                to_visit.append(pager_url)

        step_new = 0
        for scene in scenes:
            url = scene["url"]
            old = existing.get(url)
            if old is not None:
                if old.get("queued_at") is None:
                    queue_records[url] = old
                continue
            if newest_n and len(new_records) >= newest_n:
                stopped_on_depth = True
                break
            record = _insert_discovery(
                site_id,
                scene,
                listing_url=listing_url,
                effective_url=current,
                db_path=db_path,
            )
            existing[url] = record
            new_records.append(record)
            queue_records[url] = record
            step_new += 1

        # The budget can also fill on the LAST scene this page showed, leaving
        # no further candidate to trip the check inside the loop above.  A
        # truncated scroll makes that shape common -- the scroll event may not
        # have been dispatched before the DOM was re-read -- so an exhausted
        # budget is a depth stop wherever it happened.  Without this the walk
        # spends a request on the pager and checkpoints there, and the scenes
        # this page has not shown yet are skipped until the library cycles.
        if newest_n and len(new_records) >= newest_n:
            stopped_on_depth = True

        # The newest-N boundary may be reached part-way through a page that
        # also exposed pager links.  Keep that unfinished page at the front of
        # the durable frontier; otherwise the next run jumps to the pager and
        # only returns to these scenes after cycling the library.
        if stopped_on_depth:
            to_visit = [current, *(url for url in to_visit if url != current)]

        # Persist the remaining traversal after every page.  A process stop
        # resumes from here instead of replaying a paid site's first page.
        _save_frontier(
            site_id,
            listing_url,
            to_visit,
            completed=not bool(to_visit),
            db_path=db_path,
        )
        if stopped_on_depth:
            break
        if step_new == 0:
            terminated_on_no_new = True
            break

    incomplete = bool(to_visit) and not terminated_on_no_new
    _save_frontier(
        site_id,
        listing_url,
        to_visit if incomplete else [],
        completed=not incomplete,
        db_path=db_path,
    )

    title_pages_fetched = _resolve_scene_titles(
        page,
        new_records,
        site_config=site_config,
        limit=title_fetch_limit,
        pacer=pacer,
        db_path=db_path,
    )

    queued = 0
    enqueue_errors: list[dict[str, str]] = []
    for record in queue_records.values():
        try:
            response = enqueue_fn(site_id, record["url"])
            if isinstance(response, dict) and response.get("ok") is False:
                raise RuntimeError(str(response.get("error") or "enqueue failed"))
            added = 1
            if isinstance(response, dict):
                added = int(response.get("added", 1) or 0)
            queued += added
            # A duplicate response still proves the URL is already in the
            # canonical queue, so it must not be submitted again next run.
            _mark_queued(site_id, record["url"], db_path)
            record["queued_at"] = time.time()
        except Exception as exc:
            enqueue_errors.append({
                "url": record["url"],
                "error": f"{type(exc).__name__}: {str(exc)[:200]}",
            })

    if not incomplete:
        for validator_url, validators in pending_validators.items():
            _save_listing_validators(
                site_id, validator_url, validators, db_path
            )

    result = _run_base(
        state=STATE_COMPLETED,
        effective_url=effective_url,
        pages_walked=pages_walked,
        page_urls=page_urls,
        scroll_growth_steps=scroll_growth_steps,
        scroll_settle_state=scroll_settle_state,
        scroll_late_growth_steps=scroll_late_growth_steps,
        shapes=shapes,
        scenes=new_records,
        discovered=len(new_records),
        queued=queued,
        title_pages_fetched=title_pages_fetched,
        zero_scenes_found=authenticated and not saw_scene_cohort,
        enqueue_errors=enqueue_errors,
    )
    _finish_run(run_id, result, db_path)
    return result


def _create_run(
    run_id: str,
    site_id: str,
    listing_url: str,
    db_path: str | None,
) -> None:
    _ensure_schema(db_path)
    with db.db_conn(db_path) as cx:
        cx.execute(
            """
            INSERT INTO scene_crawl_runs(
                run_id, site_id, listing_url, state, started_at,
                finished_at, result_json, error
            ) VALUES (?, ?, ?, ?, ?, NULL, '{}', '')
            """,
            (run_id, site_id, listing_url, STATE_RUNNING, time.time()),
        )


def _fail_run(run_id: str, error: str, db_path: str | None) -> None:
    with db.db_conn(db_path) as cx:
        cx.execute(
            """
            UPDATE scene_crawl_runs
            SET state = ?, finished_at = ?, error = ?
            WHERE run_id = ?
            """,
            (STATE_FAILED, time.time(), error[:500], run_id),
        )


@contextlib.contextmanager
def _runner_page(runner: Any, site_id: str):
    """Open a short-lived Cloak-routed page with BD's current cookie jar."""
    try:
        from . import session_keeper
        session_keeper.pause_site_keepers(site_id)
    except Exception:
        pass

    browser = context = page = pw = None
    try:
        # SiteRunner's launcher is the canonical route through cloak.py and
        # also preserves the site's proxy/VPN, fingerprint, plugin, and launch
        # settings.  Non-persistent mode avoids contending with a worker's main
        # profile while this polite crawler is active.
        browser, _persistent, pw, _backend = runner._launch_browser(
            headless=True, use_persistent=False, worker_idx=None
        )
        context = browser.new_context(**runner._context_options(headless=True))
        try:
            runner._install_stealth(context)
        except Exception:
            pass
        cookies = list(getattr(runner, "cookies", None) or [])
        if cookies:
            context.add_cookies(cookies)
        page = context.new_page()
        try:
            runner._apply_stealth_library_to_page(page)
        except Exception:
            pass
        yield page
    finally:
        for obj in (page, context, browser):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                pass


def start_background_crawl(
    *,
    site_id: str,
    listing_url: str,
    site_config: dict[str, Any],
    runner: Any,
    newest_n: int = 50,
    max_pages: int = 5,
    max_scrolls: int = 8,
    delay_s: float = 1.0,
    title_fetch_limit: int = 50,
    enqueue_fn: Callable[[str, str], Any],
    db_path: str | None = None,
) -> dict[str, Any]:
    """Start one bounded GUI-triggered crawl without blocking Flask."""
    run_id = uuid.uuid4().hex
    with _ACTIVE_LOCK:
        active = _ACTIVE.get(site_id)
        if active:
            raise CrawlAlreadyRunning(active)
        _ACTIVE[site_id] = run_id
    try:
        _create_run(run_id, site_id, listing_url, db_path)
    except Exception:
        with _ACTIVE_LOCK:
            if _ACTIVE.get(site_id) == run_id:
                _ACTIVE.pop(site_id, None)
        raise

    def work() -> None:
        try:
            with _runner_page(runner, site_id) as page:
                crawl_with_page(
                    page,
                    site_id=site_id,
                    listing_url=listing_url,
                    site_config=site_config,
                    newest_n=newest_n,
                    max_pages=max_pages,
                    max_scrolls=max_scrolls,
                    delay_s=delay_s,
                    title_fetch_limit=title_fetch_limit,
                    enqueue_fn=enqueue_fn,
                    db_path=db_path,
                    run_id=run_id,
                )
        except Exception as exc:
            _fail_run(
                run_id,
                f"{type(exc).__name__}: {str(exc)[:450]}",
                db_path,
            )
        finally:
            with _ACTIVE_LOCK:
                if _ACTIVE.get(site_id) == run_id:
                    _ACTIVE.pop(site_id, None)

    threading.Thread(
        target=work,
        name=f"scene-crawl-{site_id}",
        daemon=True,
    ).start()
    return {"ok": True, "run_id": run_id, "site_id": site_id, "state": STATE_RUNNING}


def crawl_status(
    *,
    site_id: str,
    run_id: str | None = None,
    db_path: str | None = None,
) -> dict[str, Any]:
    _ensure_schema(db_path)
    with db.db_conn(db_path) as cx:
        if run_id:
            row = cx.execute(
                "SELECT * FROM scene_crawl_runs WHERE run_id = ? AND site_id = ?",
                (run_id, site_id),
            ).fetchone()
        else:
            row = cx.execute(
                """
                SELECT * FROM scene_crawl_runs
                WHERE site_id = ?
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (site_id,),
            ).fetchone()
    if not row:
        return {
            "ok": True,
            "site_id": site_id,
            "state": STATE_IDLE,
            "discovered": 0,
            "queued": 0,
            "pages_walked": 0,
            "zero_scenes_found": False,
            "scroll_settle_state": SETTLE_UNKNOWN,
        }
    result: dict[str, Any] = {}
    try:
        result = json.loads(row["result_json"] or "{}")
    except Exception:
        pass
    payload = {
        "ok": row["state"] != STATE_FAILED,
        "run_id": row["run_id"],
        "site_id": row["site_id"],
        "listing_url": row["listing_url"],
        "state": row["state"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "error": row["error"] or "",
        "discovered": 0,
        "queued": 0,
        "pages_walked": 0,
        "zero_scenes_found": False,
        "scroll_settle_state": SETTLE_UNKNOWN,
    }
    payload.update(result)
    return payload


__all__ = [
    "CrawlAlreadyRunning",
    "crawl_status",
    "crawl_with_page",
    "discovery_history",
    "start_background_crawl",
    "strip_repeated_title_templates",
]
