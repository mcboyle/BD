#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent

# When this file is executed as tools/onboard_site_template.py,
# Python puts tools/ on sys.path, not the project root. Add ROOT so
# imports like bulk_downloader.template_registry work.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CAPTURE_DIR = ROOT / "captures" / "template_onboarding"
DRAFT_DIR = ROOT / "templates" / "drafts"


def slug(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"^www\.", "", s)
    s = re.sub(r"[^a-z0-9._-]+", "-", s)
    return s.strip("-") or "unknown"


def best_url_from_site(site: dict) -> str:
    for key in (
        "login_url",
        "start_url",
        "base_url",
        "url",
        "homepage",
        "member_url",
        "site_url",
    ):
        val = (site.get(key) or "").strip()
        if val.startswith(("http://", "https://")):
            return val
    return ""


def host_for_url(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def enabled_template_exists(url: str) -> bool:
    try:
        from bulk_downloader.template_registry import find_template_for_url
        t = find_template_for_url(url)
        return bool(t and t.get("status") == "enabled")
    except Exception as e:
        print(f"[onboard] template check failed: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def plan_site(site: dict) -> dict:
    """Pure classification: the config-key updates onboarding would apply to
    ``site`` — no capture launch, no disk write. Returns {} when the site has
    no usable URL. ``auto_teach_first_run`` is forced off in both branches so a
    reviewed/approved (or to-be-captured) site never pops the first-run teach
    window. Used by main() and exercised directly by the regression tests."""
    url = best_url_from_site(site)
    if not url:
        return {}
    if enabled_template_exists(url):
        return {
            "template_onboarding": "approved_template_found",
            "auto_teach_first_run": False,
            "template_auto_detect_mode": "reviewed",
        }
    return {
        "template_onboarding": "capture_required",
        "auto_teach_first_run": False,
        "template_auto_detect_mode": "capture_then_review",
    }


def site_items(config):
    """Yield (site_id, site_dict) across common config shapes."""
    if isinstance(config, dict):
        # Shape: {"sites": {"id": {...}}}
        if isinstance(config.get("sites"), dict):
            for sid, site in config["sites"].items():
                if isinstance(site, dict):
                    yield str(sid), site
            return

        # Shape: {"sites": [{...}]}
        if isinstance(config.get("sites"), list):
            for i, site in enumerate(config["sites"]):
                if isinstance(site, dict):
                    yield str(site.get("id") or site.get("site_id") or i), site
            return

        # Shape: {"id": {...}, "id2": {...}}
        for sid, site in config.items():
            if isinstance(site, dict):
                yield str(sid), site


def save_config(path: Path, data):
    bak = path.with_suffix(path.suffix + ".bak_onboard")
    if not bak.exists():
        bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def build_capture_command(site_id: str, url: str, display: str):
    host = slug(host_for_url(url))
    safe_id = slug(site_id)
    # Second-resolution ts + a short random token, so two launches of the SAME
    # site within one second still get distinct wacz/sentinel stems (the stem is
    # what scopes the per-capture FINISH/CANCEL sentinels below).
    ts = time.strftime("%Y%m%d_%H%M%S") + "_" + os.urandom(2).hex()

    profile_dir = ROOT / "profiles" / f"{safe_id}-{host}-cloak"
    out_wacz = CAPTURE_DIR / f"{host}_{safe_id}_{ts}.wacz"
    out_draft = DRAFT_DIR / f"{host}.template-draft.json"
    # Per-capture finish sentinel: onboarding dumps every WACZ in one flat
    # CAPTURE_DIR, so a single shared CAPTURE_DIR/FINISH would close EVERY
    # concurrent onboarding capture at once (and race their [2/2] builds). Key
    # the sentinel to this capture's wacz stem so each finishes independently;
    # capture_session derives the matching .CANCEL sibling.
    finish_file = out_wacz.with_suffix(".FINISH")

    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    DRAFT_DIR.mkdir(parents=True, exist_ok=True)
    profile_dir.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(ROOT / "venv" / "bin" / "python"),
        str(ROOT / "tools" / "capture_session.py"),
        "--profile-dir",
        str(profile_dir.relative_to(ROOT)),
        "--url",
        url,
        "--out",
        str(out_wacz.relative_to(ROOT)),
        "--finish-file",
        str(finish_file.relative_to(ROOT)),
    ]

    draft_cmd = [
        str(ROOT / "venv" / "bin" / "python"),
        str(ROOT / "tools" / "build_template_from_wacz.py"),
        str(out_wacz.relative_to(ROOT)),
        "--out",
        str(out_draft.relative_to(ROOT)),
    ]

    return {
        "host": host,
        "profile_dir": str(profile_dir),
        "wacz": str(out_wacz),
        "draft": str(out_draft),
        "finish_file": str(finish_file),
        "display": display,
        "capture_cmd": cmd,
        "draft_cmd": draft_cmd,
    }


def run_capture_flow(info, *, run: bool):
    shell_script = CAPTURE_DIR / f"run_capture_{info['host']}.sh"

    script = f"""#!/usr/bin/env bash
set -euo pipefail
cd {shlex.quote(str(ROOT))}
export DISPLAY={shlex.quote(info['display'])}
export BD_BROWSER_BACKEND=cloakbrowser
export BD_USE_CLOAKBROWSER=1

echo "[1/2] Capture WACZ"
{" ".join(shlex.quote(x) for x in info["capture_cmd"])}

echo
echo "[F0.3] scrub-on-capture: share-ready twin (non-fatal)"
{shlex.quote(info["capture_cmd"][0])} -m bulk_downloader.capture_scrub_hook {shlex.quote(info["wacz"])} || echo "[scrub] skipped (non-fatal)"

echo
echo "[2/2] Build draft template"
{" ".join(shlex.quote(x) for x in info["draft_cmd"])}

echo
echo "Draft written: {info['draft']}"
echo "Review it, clean it, then promote with tools/promote_template.py"
"""
    shell_script.write_text(script, encoding="utf-8")
    shell_script.chmod(0o755)

    print(f"[onboard] capture script: {shell_script}")
    print(f"[onboard] wacz: {info['wacz']}")
    print(f"[onboard] draft: {info['draft']}")

    if not run:
        return None

    print("[onboard] running capture now")
    # Return the wrapper pid so the caller can record it in the in-flight marker.
    # The bash wrapper runs capture -> scrub -> draft-build sequentially, so its
    # liveness is a faithful proxy for "the capture flow is still in flight" (B1
    # self-heal). It exits when the draft is built OR when the flow dies.
    proc = subprocess.Popen(["bash", str(shell_script)])
    return proc.pid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites-config", default="sites_config.json")
    ap.add_argument("--site-id", default="")
    ap.add_argument("--url", default="")
    ap.add_argument("--display", default=os.environ.get("DISPLAY", ":99"))
    ap.add_argument("--run", action="store_true", help="actually launch the capture flow")
    args = ap.parse_args()

    cfg_path = ROOT / args.sites_config
    if not cfg_path.exists() and not args.url:
        raise SystemExit(f"missing {cfg_path}")

    targets = []

    if args.url:
        targets.append((args.site_id or slug(host_for_url(args.url)), {"login_url": args.url}))
        cfg = None
    else:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        for sid, site in site_items(cfg):
            if args.site_id and sid != args.site_id:
                continue
            url = best_url_from_site(site)
            if url:
                targets.append((sid, site))

    if not targets:
        raise SystemExit("no site/url targets found")

    changed = False

    for sid, site in targets:
        url = best_url_from_site(site)
        host = host_for_url(url)
        print(f"[onboard] site={sid} host={host} url={url}")

        plan = plan_site(site)
        site.update(plan)
        changed = True

        if plan.get("template_onboarding") == "approved_template_found":
            print(f"[onboard] enabled template exists for {host}; no capture needed")
            continue

        print(f"[onboard] no enabled template for {host}; creating capture flow")

        info = build_capture_command(sid, url, args.display)
        site["template_capture"] = {
            "profile_dir": info["profile_dir"],
            "wacz": info["wacz"],
            "draft": info["draft"],
            "display": info["display"],
        }
        run_capture_flow(info, run=args.run)

    if cfg is not None and changed:
        save_config(cfg_path, cfg)
        print(f"[onboard] updated {cfg_path}")


if __name__ == "__main__":
    main()
