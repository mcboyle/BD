"""Tests for v3.46.3 Phase 185 worker-affinity improvement.

The original Phase 185 had a degenerate case: when no worker can
take a given URL (untagged URL + all workers have affinities), the
URL spun in the queue forever, each worker requeueing it 200ms
later. The fix added the "any other worker?" check that lets the
URL be processed by SOMEONE rather than nobody.

These tests verify the source-level wiring + simulate the decision
logic on a stub since the real path requires a Playwright context.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


def _isolated_bd():
    tmp = tempfile.mkdtemp(prefix="bd-185-test-")
    # v3.66.9: snapshot env + modules so teardown_module can
    # restore them. Without this, BD_INSTALL_DIR leaks to
    # subsequent test files that use chdir-only isolation.
    if not _ISOLATED_BD_SAVED:
        _ISOLATED_BD_SAVED['env'] = {
            k: os.environ.get(k)
            for k in ('BD_INSTALL_DIR', 'BD_DISABLE_KEEPALIVE')
        }
        _ISOLATED_BD_SAVED['modules'] = {
            k: v for k, v in sys.modules.items()
            if k.startswith('bulk_downloader')
        }
    os.environ["BD_INSTALL_DIR"] = tmp
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    for m in list(sys.modules):
        if m.startswith("bulk_downloader"):
            del sys.modules[m]
    return tmp


_ISOLATED_BD_SAVED = {}


def teardown_module(module):
    """v3.66.9: pytest hook — restore env + sys.modules after
    this file's tests run, so the leaked BD_INSTALL_DIR doesn't
    taint downstream tests."""
    if not _ISOLATED_BD_SAVED:
        return
    for k, v in _ISOLATED_BD_SAVED['env'].items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    for m in [k for k in list(sys.modules)
              if k.startswith('bulk_downloader')]:
        del sys.modules[m]
    sys.modules.update(_ISOLATED_BD_SAVED['modules'])
    _ISOLATED_BD_SAVED.clear()


# ─── Source-level wiring checks ────────────────────────────────────────


class TestAffinitySourceWiring(unittest.TestCase):
    """Confirms the Phase 185 logic lives where the worker pickup
    loop expects it. Cheap to run — no test setup needed."""

    def setup_method(self, method=None):
        _isolated_bd()
        import bulk_downloader
        self.runner_src = (Path(bulk_downloader.__file__).parent
                           / "runner.py").read_text(encoding="utf-8")

    setUp = setup_method

    def test_worker_affinity_check_present(self):
        self.assertIn("worker_affinity", self.runner_src)
        # The improved check must consider whether any OTHER worker
        # can take the URL — without this, untagged URLs spin
        self.assertIn("other_can_take", self.runner_src,
            "improved affinity check should look at other workers")

    def test_unrestricted_worker_check_present(self):
        """A worker without an affinity entry takes any URL. The
        improved check accounts for these so a mixed config
        (some workers bound, some free) doesn't deadlock."""
        self.assertIn("unrestricted_exists", self.runner_src)

    def test_check_inside_worker_loop(self):
        """The affinity check must live AFTER the queue.get() so
        we have a URL to inspect, but BEFORE the global-semaphore
        acquire so we don't waste a slot on a URL we'll requeue."""
        get_pos = self.runner_src.find("url=self._url_queue.get")
        affinity_pos = self.runner_src.find("worker_affinity = ")
        sem_pos = self.runner_src.find("_global_sem is not None",
                                       affinity_pos if affinity_pos > 0
                                       else 0)
        self.assertGreater(get_pos, 0)
        # Should be near the worker loop, not in some unrelated function
        affinity_in_loop = self.runner_src.find("affinity_map = ")
        self.assertGreater(affinity_in_loop, get_pos,
            "affinity check should come AFTER queue.get()")
        # And before the global semaphore acquire on this same iteration
        self.assertLess(affinity_in_loop, sem_pos,
            "affinity check should come BEFORE _global_sem acquire")

    def test_fail_open_on_tag_module_error(self):
        """If tags module raises, the worker should still process the
        URL rather than getting stuck in an infinite exception loop."""
        # Find the affinity block + check it has a try/except around
        # the tags module call
        affinity_pos = self.runner_src.find("affinity_map = ")
        block = self.runner_src[affinity_pos:affinity_pos + 3000]
        self.assertIn("try:", block)
        self.assertIn("except Exception", block)
        self.assertIn("Fail-open", block,
            "fail-open comment should document the safety property")


# ─── Decision-logic simulation ─────────────────────────────────────────
#
# The real worker loop spins inside a Playwright session that's hard
# to instantiate in unit tests. Simulate just the affinity decision
# logic — same conditions, same branches — to verify the truth table.


def _should_requeue(*, my_idx, my_affinity, url_tags,
                   affinity_map, max_concurrent):
    """Reimplementation of the runner's affinity decision branch.
    Returns True if the current worker should requeue + skip,
    False if it should process the URL itself.

    Mirrors the inline logic from runner.py — keep these in sync.
    If you change one, change the other."""
    if not my_affinity:
        return False  # no affinity → take anything
    if my_affinity in url_tags:
        return False  # URL is mine
    # URL isn't mine. Does any OTHER worker have affinity matching?
    other_can_take = any(
        aff in url_tags
        for w, aff in affinity_map.items()
        if str(w) != str(my_idx))
    unrestricted_exists = len(affinity_map) < max_concurrent
    if other_can_take or unrestricted_exists:
        return True   # someone else can take it; requeue
    return False      # nobody else will claim it; take it ourselves


class TestAffinityDecisionLogic(unittest.TestCase):

    def test_worker_without_affinity_takes_anything(self):
        self.assertFalse(_should_requeue(
            my_idx=0, my_affinity=None,
            url_tags={"hi-res"},
            affinity_map={1: "hi-res", 2: "mobile"},
            max_concurrent=3))

    def test_worker_takes_matching_url(self):
        self.assertFalse(_should_requeue(
            my_idx=0, my_affinity="hi-res",
            url_tags={"hi-res", "exclusive"},
            affinity_map={0: "hi-res", 1: "mobile"},
            max_concurrent=2))

    def test_worker_requeues_when_another_matches(self):
        """The URL is tagged 'mobile', worker 0 has affinity 'hi-res',
        worker 1 has affinity 'mobile' — worker 0 should requeue so
        worker 1 picks it up."""
        self.assertTrue(_should_requeue(
            my_idx=0, my_affinity="hi-res",
            url_tags={"mobile"},
            affinity_map={0: "hi-res", 1: "mobile"},
            max_concurrent=2))

    def test_worker_processes_when_nobody_else_matches(self):
        """URL tagged 'exclusive'. No worker has 'exclusive' affinity.
        All workers have affinities (no unrestricted). Without this
        fix, the URL spins forever — every worker requeues every time
        they see it.

        After the fix: the first worker to dequeue it takes it,
        because the 'any other can take it' check returns False."""
        self.assertFalse(_should_requeue(
            my_idx=0, my_affinity="hi-res",
            url_tags={"exclusive"},  # nobody's affinity
            affinity_map={0: "hi-res", 1: "mobile", 2: "audio"},
            max_concurrent=3))

    def test_worker_requeues_when_unrestricted_exists(self):
        """3 workers, only 1 has an affinity. The unrestricted ones
        should take URLs that don't match any specific affinity."""
        self.assertTrue(_should_requeue(
            my_idx=0, my_affinity="hi-res",
            url_tags={"mobile"},  # no worker has 'mobile' explicitly
            affinity_map={0: "hi-res"},  # only worker 0 is bound
            max_concurrent=3))  # workers 1, 2 are unrestricted

    def test_untagged_url_processed_when_no_unrestricted_workers(self):
        """URL has no tags (e.g. newly added, not yet hashed). All
        workers have affinities. None of them match (empty intersection).
        Without the fix: spin forever. With the fix: worker 0 takes it."""
        self.assertFalse(_should_requeue(
            my_idx=0, my_affinity="hi-res",
            url_tags=set(),
            affinity_map={0: "hi-res", 1: "mobile"},
            max_concurrent=2))

    def test_string_keyed_affinity_map_works(self):
        """Config from JSON loads as string keys. The decision logic
        must handle this — string keys for indices."""
        self.assertTrue(_should_requeue(
            my_idx=0, my_affinity="hi-res",
            url_tags={"mobile"},
            affinity_map={"0": "hi-res", "1": "mobile"},  # string keys
            max_concurrent=2))


if __name__ == "__main__":
    unittest.main()
