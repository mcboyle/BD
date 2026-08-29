"""body_contract_fixtures -- REAL entities for the body-contract gate (v3.66.729).

WHY THIS EXISTS
---------------
bd-body-contract replays the body the frontend actually sends against the real
Flask app. That is sound, but it replays against a world with nothing in it: a
site id of "_probe", a task_id of "x", a filename that does not exist. So the
endpoint answers 400 "unknown site_id" or 404 "no such item", and the tool --
correctly, honestly -- says UNKNOWN. It cannot tell "this control is broken"
from "our placeholder is not a real id".

That is not a defect in the tool. It is the ceiling of replay analysis, and the
tool's own docstring says so: judging these needs REAL FIXTURES.

This module is those fixtures. It stands up a world:
  - a real SITE (s_cfg/s_meta/runner) with a real download dir
  - a real QUEUE JOB, so task_id resolves
  - real HISTORY rows, so item_id/log ids resolve
  - real FILES on disk, so `file`/`path` bodies point at something
  - real rows for the resource-404 families (library, schedules, saved searches,
    macros, rights, scheduled exports)

VALUES ARE RESOLVED BY KEY, NOT GUESSED
---------------------------------------
The replay substitutes a real value for a body key by NAME (task_id -> the real
queued job's id). A key we have no fixture for is NOT silently filled with "x"
and judged -- it is reported as UNRESOLVED, which keeps UNKNOWN an honest third
state instead of laundering a guess into a verdict.
"""
from __future__ import annotations

import os
import uuid

# Every attribute the stub had to invent. Reported, never silent.
_FABRICATED = set()


class _StubRunner:
    """A runner the app can actually SERIALIZE.

    The first fixture used MagicMock. That is fine until an endpoint puts a
    runner-derived value into a response: api_bulk_pause returns
    {"paused": n} and MagicMock's n is not JSON-serializable, so the endpoint
    500s and the harness records a failure the product never had. A mock that
    answers every question with a mock will eventually be asked one whose answer
    escapes into the response. Return REAL values.
    """

    def __init__(self, site_id):
        import threading
        self.site_id = site_id
        # Mirror the REAL runner's shape (bulk_downloader/runner.py:395-396):
        #     self.urls=[]; self.jobs={}; self._lock=threading.Lock()
        # The stub previously invented these via __getattr__, which handed the app
        # a FUNCTION where it expected a dict -- producing
        #   "argument of type 'function' is not iterable"   (bulk_delete)
        #   "'function' object has no attribute 'get'"      (queue/v2/cancel)
        # and the harness logged all of it as product 500s. A stub that answers
        # every question WILL eventually answer one wrongly; the fix is to answer
        # only what the real object answers, in the real shape.
        self.urls = []
        self.jobs = {}
        self._lock = threading.RLock()
        self.queue = []
        self.paused = False
        self.stopped = False

    def seed(self, jobs):
        """Populate the in-memory job map the bulk_* endpoints mutate."""
        self.jobs = {j["url"]: dict(j) for j in jobs}
        self.urls = list(self.jobs)
        self.queue = list(self.jobs.values())

    def is_running(self):
        return False

    def is_paused(self):
        return self.paused

    def pause(self, *a, **k):
        self.paused = True
        return 0

    def resume(self, *a, **k):
        self.paused = False
        return 0

    def stop(self, *a, **k):
        self.stopped = True
        return 0

    def _stop_auto_retry(self, *a, **k):
        return None

    def load_urls(self, urls, dedupe=True, folder_scan=False):
        # The REAL runner (runner_queue.py load_urls) returns the 3-tuple
        # (added, dupes, skipped_on_disk); /api/route_urls unpacks it
        #     added, dupes, *rest = runners[sid].load_urls(site_urls)
        # The __getattr__ no-op returned 0, and unpacking an int is a
        # TypeError -> a 500 the product never had (mechanism #5, again --
        # surfaced the moment the body-contract extractor's denominator was
        # fixed at v3.66.743 and /api/route_urls entered the scanned set).
        # Mirror the real shape: everything queued, nothing duped or skipped.
        return (len(list(urls)), 0, 0)

    def __getattr__(self, name):
        # THE LINE, drawn where the evidence put it:
        #
        #   DATA attributes must be REAL. Fabricating `jobs` as a callable handed the
        #   app a function where it expected a dict -> "'function' object is not
        #   iterable" -> a phantom 500. Those live in __init__ and are never invented.
        #
        #   METHODS may no-op. `bulk_pause()` returning 0 is harmless and serializes.
        #   Refusing them outright was ALSO wrong: the app catches AttributeError and
        #   remaps it to 400 "request body must be a JSON object", so an honest
        #   AttributeError came back disguised as a BODY defect and manufactured six
        #   phantom DEAD controls. A truthful error the caller mistranslates is still
        #   a lie in the report.
        #
        # So: no-op, but RECORD it. A fabrication nobody counted is how this harness
        # went wrong four separate times; a fabrication we can list is a fixture to-do.
        if name.startswith("__"):
            raise AttributeError(name)
        _FABRICATED.add(name)

        def _f(*a, **k):
            return 0
        return _f


class Fixtures:
    """A real world for the replay to run against. Values are resolved by key."""

    def __init__(self, app_mod, client, home):
        self.A = app_mod
        self.c = client
        self.home = home
        self.site_id = None
        self.task_id = None
        self.file_rel = None
        self.values = {}
        self.unresolved = set()
        self._cfg0 = None

    # ---------------------------------------------------------------- build
    def build(self):
        # v3.66.750 -- the fixture world includes GLOBAL config, not just
        # sites. path_allowlist must cover the scratch home or every
        # download_dir-bearing body 400s on validation; and the whole
        # _app_cfg is snapshotted here so ensure() can restore it after a
        # config-mutating probe poisons it (the real channel behind the
        # setup_site OK->UNKNOWN flap: a replayed settings call left
        # path_allowlist = ["x"] in the module dict).
        cfg = getattr(self.A, "_app_cfg", None)
        if cfg is not None:
            cfg["path_allowlist"] = [self.home]
            self._cfg0 = dict(cfg)
        else:
            self._cfg0 = None
        self._site()
        self._files()
        self._queue_job()
        self._history()
        self._resources()
        self._knowledge()
        self._api_resources()
        self._value_map()
        return self

    def ensure(self):  # noqa: D401
        """Rebuild the world. MUST run before EVERY probe.

        The harness replays every MUTATING call site -- and that list includes
        apiDelete("/api/sites/${}"). The first version replayed all 126 against ONE
        shared world, so the moment the delete fired, the fixture site was gone and
        every later /api/sites/<sid>/* call answered 404. The harness was destroying
        its own fixtures and then recording the wreckage as UNKNOWN. Verdicts became
        a function of REPLAY ORDER, which means none of them were evidence.

        Re-establishing the world before each probe makes every call site independent
        -- which is the only way a per-call verdict means anything.

        v3.66.750 -- "the world" includes two things the first version missed:
        GLOBAL config (a replayed settings probe poisons _app_cfg -- restore the
        build()-time baseline, allowlist included) and SITES OTHER PROBES CREATED
        (setup_site leaves a generated 8-hex site behind; a later differential
        must not see a world an earlier probe built). Both restores are what the
        docstring above always promised and only s_cfg[fx_site] delivered.
        """
        cfg = getattr(self.A, "_app_cfg", None)
        if cfg is not None and self._cfg0 is not None:
            cfg.clear()
            cfg.update(self._cfg0)
        for k in [k for k in list(self.A.s_cfg) if k != "fx_site"]:
            self.A.s_cfg.pop(k, None)
            self.A.s_meta.pop(k, None)
            self.A.runners.pop(k, None)
        self._site()
        self._files()
        self._queue_job()
        self._history()
        self._resources()
        self._knowledge()
        self._value_map()
        return self

    def _site(self):
        sid = "fx_site"
        dl = os.path.join(self.home, "dl_" + sid)
        os.makedirs(dl, exist_ok=True)
        self.A.s_cfg[sid] = {
            "url": "https://example.com/gallery",
            "name": "Fixture Site",
            "download_dir": dl,
            "login_url": "https://example.com/login",
            # v3.66.743 -- /api/cookie_clipboard/save/<sid> refuses ANY body
            # for a site without a cookie_file, so its refusal was identical
            # to {} and the differential rule pronounced the control DEAD.
            # It is not: the refusal was about OUR site, not their body. A
            # real cookie_file lets the endpoint reach body validation.
            "cookie_file": os.path.join(self.home, "fx_cookies.txt"),
        }
        self.A.s_meta[sid] = {"status": "idle"}
        self.A.runners[sid] = _StubRunner(sid)
        self.site_id = sid
        self.download_dir = dl

    def _files(self):
        """A real file on disk: `file`/`path`/`capture` bodies must point at one."""
        rel = "fixture_asset.mp4"
        p = os.path.join(self.download_dir, rel)
        with open(p, "wb") as fh:
            fh.write(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
        self.file_rel = rel
        self.file_abs = p

    def _queue_job(self):
        """A REAL queued job -- written to the actual `queue` table.

        The first version of this set runner.queue to a MagicMock and called it a
        fixture. It was not one: the endpoints do not read runner.queue, they read
        the sqlite `queue` table (PRIMARY KEY(site_id, url)). So /api/sites/<sid>/
        priority answered 404 "no such job" and the harness recorded that as
        UNKNOWN -- our fixture's fault, blamed on the product. A fixture that the
        app cannot see is not a fixture.
        """
        from bulk_downloader.db import db_conn
        self.job_url = "https://example.com/gallery/item/1"
        rows = [
            (self.site_id, self.job_url, "pending"),
            (self.site_id, "https://example.com/gallery/item/2", "error"),
            (self.site_id, "https://example.com/gallery/item/3", "done"),
        ]
        try:
            with db_conn() as cx:
                for sid, url, st in rows:
                    cx.execute(
                        "INSERT OR REPLACE INTO queue(site_id,url,status,filename)"
                        " VALUES(?,?,?,?)",
                        (sid, url, st, self.file_rel or ""))
        except Exception as e:
            self.unresolved.add("queue:%s" % type(e).__name__)
        # The bulk_* endpoints mutate the runner's IN-MEMORY job map, not the DB.
        # Seed it from the same rows so the two views of the queue agree -- a
        # fixture whose DB says one thing and whose runner says another is not a
        # world, it is a contradiction, and the endpoint will pick one and look broken.
        self.A.runners[self.site_id].seed(
            [{"url": u, "status": s, "filename": self.file_rel or ""}
             for _sid, u, s in rows])
        # task_id: many cockpit/capture endpoints key on a task id, not a url.
        self.task_id = uuid.uuid4().hex[:12]

    def _history(self):
        """Real history rows -> real item_id / log ids."""
        try:
            from bulk_downloader.db import db_log
            db_log(self.site_id, "Fixture Site",
                   "https://example.com/gallery/item/1", "done",
                   filename=self.file_rel, file_size=72, message="fixture")
        except Exception:
            pass

    def _resources(self):
        """Rows for the resource-404 families.

        TYPES MATTER. /api/library/<int:lid> takes an INT. Substituting the string
        "fx_site" into that slot fails werkzeug's int converter, so the request 404s
        in ROUTING and never reaches the view -- and the harness recorded that as
        "resource missing", blaming the product for a URL we built wrong. A fixture
        id of the wrong TYPE is not a thin world; it is a broken probe.
        """
        from bulk_downloader.db import db_conn
        self.res = {}
        try:
            with db_conn() as cx:
                # history row -> stream/token/<int:hid>
                cur = cx.execute(
                    "INSERT INTO history(site_id,site_name,url,status,filename,file_size)"
                    " VALUES(?,?,?,?,?,?)",
                    (self.site_id, "Fixture Site", self.job_url, "done",
                     self.file_rel, 72))
                self.history_id = cur.lastrowid
                # library row -> library/<int:lid>
                cur = cx.execute(
                    "INSERT OR REPLACE INTO library(history_id,site_id,file_path,"
                    "file_exists,file_size) VALUES(?,?,?,1,?)",
                    (self.history_id, self.site_id, self.file_abs, 72))
                self.library_id = cur.lastrowid
                # library tag -> library/tags/<int:tag_id>. library_tags is a JOIN
                # table (library_id, tag_id); the tag itself lives in `tags`. The
                # first version inserted a `tag` column that does not exist, the
                # insert threw, and the harness reported the resulting 404 as a
                # product mystery. Read the schema; do not assume it.
                try:
                    cur = cx.execute(
                        "INSERT OR IGNORE INTO tags(name) VALUES(?)", ("fixture",))
                    row = cx.execute(
                        "SELECT id FROM tags WHERE name=?", ("fixture",)).fetchone()
                    self.tag_id = row[0] if row else 1
                    cx.execute(
                        "INSERT OR IGNORE INTO library_tags(library_id,tag_id)"
                        " VALUES(?,?)", (self.library_id, self.tag_id))
                except Exception:
                    self.tag_id = 1
        except Exception as e:
            self.unresolved.add("resources:%s" % type(e).__name__)
            self.history_id = self.library_id = self.tag_id = 1
        self.res = {"history_id": self.history_id, "library_id": self.library_id,
                    "tag_id": self.tag_id}

    def _knowledge(self):
        """A real knowledge note -> a real <int:nid> for the DELETE probe.

        v3.66.751 -- the notes cluster got wired. Without a live row,
        path_id_for falls back to the site id, werkzeug's int converter
        rejects it, and the 404 happens in ROUTING -- a broken probe blamed
        on the product (the exact wrong-TYPE fixture bug _resources
        documents). Goes through knowledge.add_note (the real code path),
        re-created per ensure() because the DELETE probe consumes it."""
        try:
            from bulk_downloader import knowledge as _kn
            nid = _kn.add_note(site_id=self.site_id, kind="failure",
                               pattern="cloudflare challenge",
                               resolution="rotate the fingerprint and retry")
            self.knowledge_note_id = nid or 1
        except Exception as e:
            self.unresolved.add("knowledge:%s" % type(e).__name__)
            self.knowledge_note_id = 1

    # Path family -> the fixture id that belongs in ITS <slot>. Substituting the
    # site id into every slot was the bug: each family has its own key space, and
    # several are typed (int).
    def path_id_for(self, path):
        import re as _re
        table = (
            (r"^/api/library/tags/", lambda: self.tag_id),
            (r"^/api/library/", lambda: self.library_id),
            (r"^/api/stream/token/", lambda: self.history_id),
            (r"^/api/knowledge/notes/", lambda: self.knowledge_note_id),
            (r"^/api/user_templates/", lambda: self.user_template_id),
            (r"^/api/vpn/tunnels/", lambda: self.tunnel_id),
        )
        for pat, fn in table:
            if _re.match(pat, path):
                return str(fn())
        return self.site_id

    def csrf(self):
        self.c.get("/")
        return (self.c.get("/api/csrf").get_json() or {}).get("csrf_token") or ""

    # ------------------------------------------------------------ resolution
    def _value_map(self):
        """Body key -> a REAL value. Anything not here is UNRESOLVED, not guessed.

        A fixture value must satisfy the endpoint's SEMANTICS, not merely its TYPE.
        `text` for /api/import/start is not free prose -- it is the pasted URL list
        the operator types. Filling it with "fixture text" made the endpoint answer
        "no valid URLs", identically to the empty body, and the differential rule
        duly pronounced the control DEAD. It is not: it was FIXED at v3.66.726, and
        the source says so three lines above the call. A type-correct, meaning-wrong
        fixture is just a slower way of making things up.
        """
        u = "https://example.com/gallery/item/1"
        self.values = {
            "site_id": self.site_id,
            "site_ids": [self.site_id],
            "sid": self.site_id,
            "task_id": self.task_id,
            "task_ids": [self.task_id],
            "job_id": self.task_id,
            "id": self.task_id,
            "item_id": self.task_id,
            "url": u,
            "urls": [u],
            "action_url": u,
            "login_url": "https://example.com/login",
            # Row 374: these two fields ride the general site-create/update
            # surface.  A bare string placeholder is no longer semantic once
            # the listing URL is validated as an absolute members-area URL.
            "crawler_listing_url": "https://example.com/gallery",
            "crawler_newest_n": 1,
            # SEMANTIC, not merely typed: these are URL-bearing text blobs.
            "text": u + "\n" + "https://example.com/gallery/item/2",
            "html": '<a href="%s">x</a>' % u,
            "file": self.file_rel,
            "path": self.file_rel,
            "filename": self.file_rel,
            "capture": self.file_rel,
            "download_dir": self.download_dir,
            "target_dir": self.download_dir,
            "name": "fixture",
            "query": "fixture",
            "priority": "high",
            "status": "pending",
            "dry_run": True,
            "enable": True,
            "all": False,
            "ack": True,
            "discard": False,
            "accept_api": True,
        }

    # v3.66.743 -- SEMANTIC, PER-PATH values. `text` is not one thing: for
    # /api/route_urls and /api/import/start it is the operator's pasted URL
    # list; for /api/cookie_clipboard/* it is a pasted COOKIE JAR, which the
    # endpoint re-parses. The URL-list value parses to zero cookies, the
    # refusal matches {}, and the differential rule manufactures a DEAD out
    # of our own type-correct, meaning-wrong fixture (mechanism #4, again).
    PATH_VALUES = {
        "/api/cookie_clipboard/": {
            "text": "session=fxtoken123; Domain=.example.com; Path=/",
        },
        # v3.66.771 -- /api/scrape_listing is the one external-FETCH mutating
        # endpoint: it fetches the posted `url` server-side (SSRF-guarded, so a
        # loopback/private host is refused before the fetch). The shared `url`
        # fixture (example.com/gallery) 404s, and a well-behaved external-fetch
        # endpoint returns 502 on an unreachable upstream -- correct behaviour the
        # harness mis-read as a 5xx robustness fault. Point it at the public root
        # (example.com/, a 200) so the endpoint fetches cleanly and returns 200
        # {found:[]}; the body contract (it accepts {url}) is what we are judging.
        "/api/scrape_listing": {
            "url": "https://example.com/",
        },
        # v3.66.751 -- knowledge/notes. `pattern` here is a failure-message
        # substring and `kind` is the derived {failure|login|rate_limit}
        # vocabulary; BOTH names exist on other endpoints with different
        # semantics (rights/block_url patterns are URL globs, interop kinds
        # are a different enum), so these stay per-path, never global.
        "/api/knowledge/notes": {
            "pattern": "cloudflare challenge",
            "resolution": "rotate the fingerprint and retry",
            "kind": "failure",
        },
        # Row 374: one bounded crawl request.  These values exercise body
        # acceptance without launching an unbounded traversal or using an
        # invalid placeholder URL.
        "/api/discovery/scenes/start": {
            "listing_url": "https://example.com/gallery",
            "newest_n": 1,
            "max_pages": 1,
            "max_scrolls": 1,
            "delay_s": 0.1,
            "title_fetch_limit": 1,
        },
    }

    # Fixture probes must never launch detached operator workloads. These
    # values are both ADDED to empty comparator bodies and OVERRIDE generated
    # samples. Keep them separate from PATH_VALUES, whose entries only replace
    # keys already present in a type-directed sample.
    PROBE_SAFETY_VALUES = {
        "/api/sites/${}/template_onboard": {
            "run": False,
        },
    }

    def resolve(self, sample, path=""):
        """Fill a type-directed sample body with REAL values where we have them.

        Returns (body, unresolved_keys). A key we cannot resolve is REPORTED --
        the caller must treat the result as UNKNOWN rather than judging a body we
        partly made up. `path` selects per-path semantic overrides (PATH_VALUES).
        """
        if not isinstance(sample, dict):
            return sample, set()
        over = {}
        for prefix, vals in self.PATH_VALUES.items():
            if path.startswith(prefix):
                over.update(vals)
        safety = {}
        for prefix, vals in self.PROBE_SAFETY_VALUES.items():
            if path.startswith(prefix):
                safety.update(vals)
        body, missing = dict(safety), set()
        for k, v in sample.items():
            if k in safety:
                body[k] = safety[k]
            elif k in over:
                body[k] = over[k]
            elif k in self.values:
                body[k] = self.values[k]
            else:
                body[k] = v          # keep the type-directed placeholder
                missing.add(k)
        return body, missing

    def _api_resources(self):
        """Created through the app's OWN API where one exists -- a row inserted
        behind the app's back can have a shape the app would never produce, and
        then the replay is testing our fiction, not the product."""
        tok = self.csrf()
        hdr = {"X-CSRFToken": tok, "X-CSRF-Token": tok,
               "Content-Type": "application/json"}
        self.user_template_id = "fx_tpl"
        self.tunnel_id = "fx_tunnel"
        try:
            r = self.c.post("/api/vpn/tunnels",
                            json={"name": "fx_tunnel", "location": "eu",
                                  "provider": "wireguard", "backend": "wireguard",
                                  "config": "[Interface]\nPrivateKey=x\n"},
                            headers=hdr)
            j = r.get_json(silent=True) or {}
            if r.status_code < 300:
                self.tunnel_id = (j.get("id") or j.get("tunnel_id")
                                  or self.tunnel_id)
            else:
                self.unresolved.add("tunnel:%s" % (j.get("error") or r.status_code))
        except Exception:
            pass

    def probe_path(self, path):
        """Substitute the RIGHT id for THIS family into the URL slot."""
        rid = self.path_id_for(path)
        return path.replace("${}", rid).replace("_probe", rid)
