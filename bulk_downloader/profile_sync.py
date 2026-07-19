"""profile_sync — propagate a completed manual-login browser session into the
app-managed runtime profiles.

Manual login persists to ``profiles/<sid>/manual``; the runtime uses separate
Chromium profile dirs — ``profiles/<sid>/main`` (default / first download
worker), ``profiles/<sid>/w<N>`` (extra workers), and
``profiles/<sid>/keepalive_<N>`` (session keepers). Before this, a successful
manual login was not reused when downloads or keepalive started: those profiles
kept their own (logged-out) session. This copies the login-continuity browser
state from the manual profile into each runtime profile so they share the same
session.

Only the state needed for login continuity is copied — NOT the whole profile
(caches, GPU state, History, etc. are large and worker-specific):

  Cookies, Cookies-journal, Local Storage, Session Storage, IndexedDB, WebStorage

Chromium keeps these under the profile's data subdir (``Default/``); the source
layout is mirrored into each destination. This must only run when no browser
holds the source or target profiles open — `runner.finish_manual_login` calls
it after the manual browser is closed (so the source is flushed to disk) and
while the session keepers are paused.
"""
from __future__ import annotations

import contextlib
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List

# The browser state required for login continuity. Order is not significant.
LOGIN_CONTINUITY_ITEMS = (
    "Cookies",            # SQLite cookie store
    "Cookies-journal",    # its rollback journal (absent under WAL / clean close)
    "Local Storage",      # leveldb dir
    "Session Storage",    # leveldb dir
    "IndexedDB",          # dir
    "WebStorage",         # dir
)

# Runtime profile dir names this app manages, besides "manual" (the source):
#   main, w<N> (download workers), keepalive_<N> (session keepers).
_RUNTIME_RE = re.compile(r"^(?:main|w\d+|keepalive_\d+)$")
_KEEPALIVE_RE = re.compile(r"^keepalive_(\d+)$")

# Local Storage / Session Storage / IndexedDB / WebStorage are leveldb-style
# dirs guarded by a process-held ``LOCK`` file. Never copy it: a stale LOCK
# confuses (or is rejected by) the Chromium that later opens the destination
# profile. Matched at every nesting level by copytree's ``ignore`` callback.
_IGNORE_DIR_ENTRIES = shutil.ignore_patterns("LOCK")


def _data_subdir(profile: Path) -> Path:
    """Chromium stores Cookies / Local Storage / etc. under the profile's
    ``Default`` subdir. Fall back to the profile root for layouts without one."""
    default = profile / "Default"
    if default.is_dir():
        return default
    return profile


def _copy_item(src_item: Path, dst_item: Path, backup_dir: Path | None = None) -> bool:
    """Copy one file or directory, replacing the destination. Returns True iff
    the source existed and something was copied.

    Before overwriting an existing destination (an auth/session item), the old
    copy is moved into ``backup_dir`` (timestamped, set by the caller) so a bad
    sync is recoverable. Directory items skip the leveldb ``LOCK`` file."""
    if not src_item.exists():
        return False
    dst_item.parent.mkdir(parents=True, exist_ok=True)
    # Back up (and thereby clear) any existing destination before replacing it.
    if dst_item.exists() or dst_item.is_symlink():
        backed_up = False
        if backup_dir is not None:
            try:
                backup_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dst_item), str(backup_dir / dst_item.name))
                backed_up = True
            except Exception:
                backed_up = False
        if not backed_up:
            if dst_item.is_dir() and not dst_item.is_symlink():
                shutil.rmtree(dst_item, ignore_errors=True)
            else:
                try:
                    dst_item.unlink()
                except Exception:
                    pass
    if src_item.is_dir():
        shutil.copytree(src_item, dst_item, ignore=_IGNORE_DIR_ENTRIES)
    else:
        shutil.copy2(src_item, dst_item)
    return True


def sync_profile(src, dst,
                 items: Iterable[str] = LOGIN_CONTINUITY_ITEMS,
                 *, backup: bool = True) -> List[str]:
    """Copy the login-continuity items from profile dir ``src`` to ``dst``,
    mirroring the source's ``Default``-subdir layout. Returns the names of the
    items actually copied (a subset — some, e.g. Cookies-journal, may be absent).

    When ``backup`` is set, any existing destination item is moved into
    ``<dst>/.sync_backups/<timestamp>/`` (at the profile root, outside
    ``Default/`` so Chromium ignores it) before being replaced.
    """
    src, dst = Path(src), Path(dst)
    src_data = _data_subdir(src)
    rel = src_data.name if src_data != src else ""   # "Default" or ""
    dst_data = (dst / rel) if rel else dst
    dst_data.mkdir(parents=True, exist_ok=True)
    backup_dir = None
    if backup:
        backup_dir = dst / ".sync_backups" / time.strftime("%Y%m%d-%H%M%S")
    copied: List[str] = []
    for name in items:
        try:
            if _copy_item(src_data / name, dst_data / name, backup_dir=backup_dir):
                copied.append(name)
        except Exception:
            # Best-effort per item; a single failure must not abort the rest.
            pass
    return copied


def runtime_profile_dirs(site_id: str, profiles_root="profiles") -> List[Path]:
    """Existing app-managed runtime profile dirs for a site (main, w<N>,
    keepalive_<N>). Excludes the ``manual`` login source profile."""
    base = Path(profiles_root) / site_id
    if not base.is_dir():
        return []
    return sorted(d for d in base.iterdir()
                  if d.is_dir() and _RUNTIME_RE.match(d.name))


def _get_takeover_lock(site_id: str, account_idx: int):
    """Indirection over ``session_keeper.get_takeover_lock`` (lazy import to
    avoid an import cycle; monkeypatchable in tests). Returns the lock, or
    ``None`` if the keeper module is unavailable."""
    try:
        from .session_keeper import get_takeover_lock
        return get_takeover_lock(site_id, account_idx)
    except Exception:
        return None


@contextlib.contextmanager
def _keepalive_guard(site_id: str, account_idx: int):
    """Hold the keepalive takeover lock for ``(site_id, account_idx)`` for the
    duration of the block so the session keeper cannot relaunch / reopen that
    profile mid-sync (the keeper acquires the same lock non-blocking before it
    launches, and defers if it's held).

    Yields ``True`` when it is safe to sync (lock held, or the lock mechanism is
    unavailable → best-effort) and ``False`` when the keeper currently holds the
    lock (it is actively using the profile — the caller should skip rather than
    clobber a live profile)."""
    lock = _get_takeover_lock(site_id, account_idx)
    if lock is None:
        yield True
        return
    if not lock.acquire(blocking=False):
        yield False
        return
    try:
        yield True
    finally:
        try:
            lock.release()
        except Exception:
            pass


def sync_manual_to_runtime(site_id: str, *, profiles_root="profiles",
                           ensure: Iterable[str] = ("main",),
                           items: Iterable[str] = LOGIN_CONTINUITY_ITEMS,
                           backup: bool = True) -> Dict:
    """Propagate the manual-login session into every runtime profile for a site.

    Syncs into all existing runtime profiles and additionally creates + seeds
    the dirs named in ``ensure`` even if they don't exist yet, so the first
    download (``main``) / keepalive after a fresh manual login is logged in.
    Best-effort per target — a failure on one profile is recorded, not raised.

    A profile is reported under ``synced`` only if at least one item was
    actually copied; targets where nothing was copied (or that were skipped
    because a keeper held the profile) are recorded under ``skipped``. Keepalive
    profiles are guarded by the takeover lock so the keeper cannot reopen them
    while the copy is in flight. Copied item names + target names are logged.

    Returns ``{"source", "synced": {name: [items]}, "errors": {name: str},
    "skipped": {name: reason}, "skipped_reason": str|None}``.
    """
    base = Path(profiles_root) / site_id
    manual = base / "manual"
    summary: Dict = {"source": str(manual), "synced": {}, "errors": {},
                     "skipped": {}, "skipped_reason": None}
    if not manual.is_dir():
        summary["skipped_reason"] = "no manual profile to sync from"
        return summary

    targets: Dict[str, Path] = {
        d.name: d for d in runtime_profile_dirs(site_id, profiles_root)}
    for name in ensure:
        targets.setdefault(name, base / name)

    for name, dst in sorted(targets.items()):
        ka = _KEEPALIVE_RE.match(name)
        try:
            if ka:
                with _keepalive_guard(site_id, int(ka.group(1))) as safe:
                    if not safe:
                        summary["skipped"][name] = "profile in use by keepalive"
                        sys.stderr.write(
                            f"  profile_sync[{site_id}]: {name} skipped "
                            f"(in use by keepalive)\n")
                        continue
                    copied = sync_profile(manual, dst, items=items, backup=backup)
            else:
                copied = sync_profile(manual, dst, items=items, backup=backup)
        except Exception as e:  # pragma: no cover - defensive
            summary["errors"][name] = str(e)
            sys.stderr.write(
                f"  profile_sync[{site_id}]: {name} ERROR: {e}\n")
            continue
        if copied:
            summary["synced"][name] = copied
            sys.stderr.write(
                f"  profile_sync[{site_id}]: {name} <- manual: "
                f"copied {', '.join(copied)}\n")
        else:
            summary["skipped"][name] = "no continuity items copied"
            sys.stderr.write(
                f"  profile_sync[{site_id}]: {name}: nothing to copy\n")
    return summary


_CLOAK_SUFFIX = "-cloak"


def _slug(s: str) -> str:
    """Mirror tools/onboard_site_template.slug so cloak dir names resolve the
    same way the capture flow wrote them (lowercase, strip leading www., map
    runs of non [a-z0-9._-] to '-')."""
    s = (s or "").strip().lower()
    s = re.sub(r"^www\.", "", s)
    s = re.sub(r"[^a-z0-9._-]+", "-", s)
    return s.strip("-")


def _profile_has_session(profile: Path) -> bool:
    """A profile carries a reusable session iff its data subdir has a Cookies
    store (matches profile_storage_status's has_session signal)."""
    return (_data_subdir(Path(profile)) / "Cookies").exists()


def onboarding_cloak_profiles(site_id: str, profiles_root="profiles") -> List[Path]:
    """The site's onboarding capture profile dirs (profiles/<slug>-<host>-cloak),
    newest first. The onboarding/teach capture writes the authenticated session
    here (login + cf_clearance); the worker opens profiles/<sid>/main and never
    reads it — this enumerates the candidates to reuse from."""
    root = Path(profiles_root)
    if not root.is_dir():
        return []
    pref = _slug(site_id) + "-"
    cands = [d for d in root.iterdir()
             if d.is_dir() and d.name.startswith(pref)
             and d.name.endswith(_CLOAK_SUFFIX)]
    return sorted(cands, key=lambda d: d.stat().st_mtime, reverse=True)


def _cloak_host(site_id: str, cloak_dir: Path) -> str | None:
    """Recover the <host> embedded in a <slug>-<host>-cloak dir name."""
    name = cloak_dir.name
    if not name.endswith(_CLOAK_SUFFIX):
        return None
    mid = name[: -len(_CLOAK_SUFFIX)]            # <slug>-<host>
    pref = _slug(site_id) + "-"
    return mid[len(pref):] if mid.startswith(pref) else None


def sync_onboarding_to_runtime(site_id: str, *, cloak_dir=None,
                               profiles_root="profiles",
                               ensure: Iterable[str] = ("main",),
                               items: Iterable[str] = LOGIN_CONTINUITY_ITEMS,
                               backup: bool = True) -> Dict:
    """Reuse the authenticated ONBOARDING session for downloads.

    Copies the login-continuity browser state (Cookies incl. cf_clearance,
    Local/Session Storage, IndexedDB, WebStorage) from the site's most recent
    onboarding capture profile (profiles/<slug>-<host>-cloak) into every runtime
    download profile (main / w<N> / keepalive_<N>), creating + seeding the
    ``ensure`` dirs even if absent. This is session REUSE — it transplants a
    session the operator already established by hand during onboarding — NOT
    challenge-solving: no challenge is defeated, only existing local browser
    state is copied. Mirrors sync_manual_to_runtime (manual-login source) but
    sources the onboarding cloak profile, which the worker otherwise can't see.

    Keepalive targets are guarded by the takeover lock; copies are
    backup-before-overwrite. Returns ``{"source", "host", "synced": {name:
    [items]}, "errors": {name: str}, "skipped": {name: reason},
    "skipped_reason": str|None}`` — caller surfaces a value-free summary.
    """
    base = Path(profiles_root) / site_id
    if cloak_dir is not None:
        src = Path(cloak_dir)
    else:
        src = next((d for d in onboarding_cloak_profiles(site_id, profiles_root)
                    if _profile_has_session(d)), None)
    summary: Dict = {"source": str(src) if src else None, "host": None,
                     "synced": {}, "errors": {}, "skipped": {},
                     "skipped_reason": None}
    if src is None or not src.is_dir():
        summary["skipped_reason"] = "no onboarding session profile to reuse"
        return summary
    if not _profile_has_session(src):
        summary["skipped_reason"] = "onboarding profile has no session (no Cookies)"
        return summary
    summary["host"] = _cloak_host(site_id, src)

    targets: Dict[str, Path] = {
        d.name: d for d in runtime_profile_dirs(site_id, profiles_root)}
    for name in ensure:
        targets.setdefault(name, base / name)

    for name, dst in sorted(targets.items()):
        ka = _KEEPALIVE_RE.match(name)
        try:
            if ka:
                with _keepalive_guard(site_id, int(ka.group(1))) as safe:
                    if not safe:
                        summary["skipped"][name] = "profile in use by keepalive"
                        sys.stderr.write(
                            f"  profile_sync[{site_id}]: {name} skipped "
                            f"(in use by keepalive)\n")
                        continue
                    copied = sync_profile(src, dst, items=items, backup=backup)
            else:
                copied = sync_profile(src, dst, items=items, backup=backup)
        except Exception as e:  # pragma: no cover - defensive
            summary["errors"][name] = str(e)
            sys.stderr.write(
                f"  profile_sync[{site_id}]: {name} ERROR: {e}\n")
            continue
        if copied:
            summary["synced"][name] = copied
            sys.stderr.write(
                f"  profile_sync[{site_id}]: {name} <- onboarding: "
                f"copied {', '.join(copied)}\n")
        else:
            summary["skipped"][name] = "no continuity items copied"
            sys.stderr.write(
                f"  profile_sync[{site_id}]: {name}: nothing to copy\n")
    return summary


def _path_size(p: Path) -> int:
    """Byte size of a file, or the total of a directory tree (storage items
    like Local Storage / IndexedDB are leveldb dirs). Never reads contents."""
    try:
        if p.is_file():
            return p.stat().st_size
        if p.is_dir():
            return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except Exception:
        pass
    return 0


def _iso(ts: float) -> str:
    try:
        import datetime as _dt
        return _dt.datetime.fromtimestamp(ts).isoformat(timespec="seconds")
    except Exception:
        return ""


def profile_storage_status(site_id: str | None = None, *,
                           profiles_root="profiles",
                           items: Iterable[str] = LOGIN_CONTINUITY_ITEMS
                           ) -> Dict:
    """Read-only per-profile, per-storage-item status for a site's manual +
    runtime (main / worker / keeper) profiles. Reports EXISTENCE, byte size,
    and modified time only — it never reads or returns cookie / token / storage
    VALUES, only metadata. Pure filesystem inspection; launches nothing.

    Returns ``{"profiles_root", "present", "sites": [{"site", "profiles":
    [{"profile", "kind", "present", "items": [{"name", "present", "bytes",
    "mtime"}]}]}]}``.
    """
    root = Path(profiles_root)
    out: Dict = {"profiles_root": str(root), "present": root.is_dir(),
                 "sites": []}
    if not root.is_dir():
        return out
    site_dirs = ([root / site_id] if site_id else
                 sorted(d for d in root.iterdir() if d.is_dir()))
    for sd in site_dirs:
        if not sd.is_dir():
            continue
        profiles: List[Dict] = []
        for d in sorted(sd.iterdir()):
            if not d.is_dir():
                continue
            name = d.name
            if name == "manual":
                kind = "manual"
            elif _RUNTIME_RE.match(name):
                kind = ("keeper" if name.startswith("keepalive_")
                        else "worker" if name.startswith("w") else "main")
            else:
                continue
            data = _data_subdir(d)
            rows: List[Dict] = []
            for n in items:
                p = data / n
                present = p.exists()
                rows.append({
                    "name": n,
                    "present": present,
                    "bytes": _path_size(p) if present else 0,
                    "mtime": _iso(p.stat().st_mtime) if present else None,
                })
            profiles.append({
                "profile": name, "kind": kind,
                "present": any(r["present"] for r in rows),
                "items": rows,
            })
        out["sites"].append({"site": sd.name, "profiles": profiles})
    return out


def handoff_status(site_id: str | None = None, *, profiles_root="profiles",
                   items: Iterable[str] = LOGIN_CONTINUITY_ITEMS) -> Dict:
    """Read-only snapshot of the manual-login -> runtime profile handoff, for
    status surfaces. Reports, per site, whether a ``manual`` profile exists and
    which runtime profiles (main / w<N> / keepalive_<N>) currently carry
    login-continuity state (i.e. received a handoff), plus any sync backups.

    Pure filesystem inspection — never launches a browser or copies anything.
    Returns ``{"profiles_root", "present", "sites": [...]}``; ``sites`` is empty
    when the profiles root does not exist.
    """
    root = Path(profiles_root)
    out: Dict = {"profiles_root": str(root), "present": root.is_dir(),
                 "sites": []}
    if not root.is_dir():
        return out
    site_dirs = ([root / site_id] if site_id else
                 sorted(d for d in root.iterdir() if d.is_dir()))
    for sd in site_dirs:
        if not sd.is_dir():
            continue
        runtimes: List[Dict] = []
        for d in sorted(sd.iterdir()):
            if not d.is_dir() or not _RUNTIME_RE.match(d.name):
                continue
            data = _data_subdir(d)
            backups_dir = d / ".sync_backups"
            backups = (sorted(p.name for p in backups_dir.iterdir() if p.is_dir())
                       if backups_dir.is_dir() else [])
            runtimes.append({
                "profile": d.name,
                "has_session": (data / "Cookies").exists(),
                "has_continuity_items": any((data / n).exists() for n in items),
                "backup_count": len(backups),
                "last_backup": backups[-1] if backups else None,
            })
        out["sites"].append({
            "site": sd.name,
            "manual_present": (sd / "manual").is_dir(),
            "runtime_profiles": runtimes,
            "handed_off": any(r["has_session"] for r in runtimes),
        })
    return out
