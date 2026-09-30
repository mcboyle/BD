"""fx-takeover-plain-browser (O1567, operator 22:5xZ): a Cloudflare check a human passes in a PLAIN browser.

Live on test3 (.80): the takeover window for freetour.adulttime.com is the cloakbrowser chromium driven by
Playwright over --remote-debugging-pipe, with a throwaway /tmp profile. Cloudflare's Turnstile refused even a
human click there, but the same box passed in a plain google-chrome on the same VM and IP. Operator requirement:
the operator's ONLY action is the checkbox click on the display; the app does the rest.

This module is that plain browser: the same cloakbrowser binary and stealth flags (UA and fingerprint parity),
with NO CDP attach (no --remote-debugging-*, no --enable-automation), on the site's persistent manual profile
(profiles/<sid>/manual). The pass is read from the profile's own cookie store: a cf_clearance for the site's
registrable domain written after the browser started.

The login then runs INSIDE that same browser session. Measured live on test3 (results/test3/
FINDING-cf-clearance-handoff.md): the clearance is bound to the session that earned it -- carried into a new cloak
browser, a persistent context on the same profile, or even the same plain browser relaunched, Cloudflare challenged
again. So the plain browser opens a loopback --remote-debugging-port that NO client attaches to while the human is on
the challenge; once the pass is on disk, submit.do_login attaches over CDP (config["_human_clearance"]["cdp_url"])
and fills the form on the page that passed. The browser is closed (SIGTERM: Chromium flushes its cookies on a clean
exit) only after that login.

Agents never click the checkbox; nothing here clicks, types, or solves anything.
"""
from __future__ import annotations

import ipaddress
import os
import random
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from .registrable_domain import registrable_domain

PASS_COOKIE = "cf_clearance"
DEFAULT_TIMEOUT_S = 900.0
POLL_S = 3.0
_FORBIDDEN_FLAGS = ("--remote-debugging", "--enable-automation")
# Chromium's cookie timestamps count microseconds from 1601-01-01.
_CHROME_EPOCH_OFFSET_S = 11644473600


def enabled(config: dict) -> bool:
    """The plain challenge browser is on unless the site config turns it off."""
    return bool((config or {}).get("human_challenge_plain_browser", True))


def new_seed() -> int:
    return random.randint(10000, 99999)


def domain_of(url: str) -> str:
    host = (urlparse(url or "").hostname or "").lower()
    try:
        ipaddress.ip_address(host)
        return host          # an IP host is its own cookie domain
    except ValueError:
        return registrable_domain(host)


def plain_binary() -> str:
    """The cloakbrowser chromium already on disk (never downloads)."""
    from cloakbrowser import config as _cbc
    path = Path(_cbc.get_binary_path())
    if not path.exists():
        raise FileNotFoundError(f"cloakbrowser binary not installed at {path}")
    return str(path)


def free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def plain_browser_argv(binary: str, user_data_dir, url: str, *, seed: int,
                       user_agent: str | None = None, headless: bool = False,
                       debug_port: int | None = None) -> list[str]:
    """cloakbrowser's own stealth flags with a pinned fingerprint seed, the password store Playwright uses, and NO
    automation control channel. *debug_port* adds only a loopback CDP listener for the attach after the pass; no
    client is connected while the human is on the challenge."""
    try:
        from cloakbrowser.browser import build_args
        args = list(build_args(True, [f"--fingerprint={int(seed)}"], headless=headless))
    except Exception:  # noqa: BLE001 -- an older cloakbrowser: its documented defaults
        args = ["--no-sandbox", f"--fingerprint={int(seed)}", "--fingerprint-platform=windows"]
    args += ["--password-store=basic", "--use-mock-keychain", "--no-first-run",
             "--no-default-browser-check", f"--user-data-dir={user_data_dir}"]
    if headless and not any(a.startswith("--headless") for a in args):
        args.append("--headless=new")
    if user_agent:
        args.append(f"--user-agent={user_agent}")
    args = [a for a in args if not a.startswith(_FORBIDDEN_FLAGS)]
    if debug_port:
        args += [f"--remote-debugging-port={int(debug_port)}", "--remote-debugging-address=127.0.0.1"]
    return [binary, *args, url]


def _host_matches(host_key: str, domain: str) -> bool:
    h = (host_key or "").lstrip(".").lower()
    return bool(domain) and (h == domain or h.endswith("." + domain))


def cookies_on_disk(user_data_dir, domain: str) -> list[tuple[str, str, int]]:
    """(host_key, name, last-update-unix-s) of the profile's stored cookies for *domain*. The store is copied first:
    the running browser holds it open."""
    db = Path(user_data_dir) / "Default" / "Cookies"
    if not db.exists():
        return []
    with tempfile.TemporaryDirectory(prefix="bd-hc-") as tmp:
        copy = Path(tmp) / "Cookies"
        shutil.copyfile(db, copy)
        con = sqlite3.connect(str(copy))
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info(cookies)")}
            ts = "last_update_utc" if "last_update_utc" in cols else "creation_utc"
            rows = con.execute(f"SELECT host_key, name, {ts} FROM cookies").fetchall()
        finally:
            con.close()
    return [(h, n, int(t) // 1_000_000 - _CHROME_EPOCH_OFFSET_S)
            for h, n, t in rows if _host_matches(h, domain)]


class PlainChallengeSession:
    """One plain challenge browser: start, wait for the human's pass, close."""

    def __init__(self, url: str, user_data_dir, *, seed: int | None = None,
                 user_agent: str | None = None, headless: bool = False, env: dict | None = None,
                 attach: bool = True):
        self.url = url
        self.domain = domain_of(url)
        self.user_data_dir = Path(user_data_dir)
        self.seed = int(seed if seed is not None else new_seed())
        self.user_agent = user_agent
        self.headless = headless
        self.env = env
        self.debug_port = free_loopback_port() if attach else None
        self.argv: list[str] = []
        self.proc: subprocess.Popen | None = None
        self.started_at = 0.0
        self.cancelled = threading.Event()

    def prepare(self) -> list[str]:
        """Resolve the binary and the command line; raises when no plain browser can be launched."""
        if not self.domain:
            raise ValueError(f"no registrable domain in {self.url!r}")
        self.argv = plain_browser_argv(plain_binary(), self.user_data_dir, self.url, seed=self.seed,
                                       user_agent=self.user_agent, headless=self.headless,
                                       debug_port=self.debug_port)
        return self.argv

    @property
    def cdp_url(self) -> str | None:
        """Where do_login attaches after the pass; None once the browser has exited."""
        if not self.debug_port or self.proc is None or self.proc.poll() is not None:
            return None
        return f"http://127.0.0.1:{self.debug_port}"

    def start(self) -> None:
        if not self.argv:
            self.prepare()
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ if self.env is None else self.env)
        env.setdefault("DISPLAY", ":99")
        self.started_at = time.time()
        self.proc = subprocess.Popen(self.argv, env=env, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=True)

    def passed(self) -> bool:
        """A cf_clearance for the site's domain written after this browser started."""
        since = int(self.started_at) - 1
        return any(name == PASS_COOKIE and ts >= since
                   for _host, name, ts in cookies_on_disk(self.user_data_dir, self.domain))

    def wait_for_pass(self, timeout: float = DEFAULT_TIMEOUT_S, poll: float = POLL_S) -> tuple[bool, str]:
        """(True, "") on a pass; (False, why) on a timeout, a closed window or a cancel. Chromium commits its cookie
        store about every 30 s while running, so a pass is read within ~30 s of the click."""
        end = time.time() + timeout
        while time.time() < end:
            if self.cancelled.is_set():
                return False, "cancelled"
            try:
                if self.passed():
                    return True, ""
            except (OSError, sqlite3.Error) as exc:
                sys.stderr.write(f"  human check: cookie store not readable yet ({type(exc).__name__})\n")
            if self.proc is not None and self.proc.poll() is not None:
                # the window was closed: the exit flushed the store, so read it once more
                if self.passed():
                    return True, ""
                return False, f"the challenge window was closed (exit {self.proc.returncode}) before a pass"
            time.sleep(poll)
        return False, f"no pass within {int(timeout)}s"

    def close(self, timeout: float = 20.0) -> int | None:
        """SIGTERM (a clean Chromium exit writes its cookie store), then SIGKILL if it hangs."""
        p = self.proc
        if p is None or p.poll() is not None:
            return None if p is None else p.returncode
        try:
            p.send_signal(signal.SIGTERM)
            return p.wait(timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            return p.wait(5)
