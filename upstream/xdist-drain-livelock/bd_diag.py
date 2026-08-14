"""Diagnostic plugin for the reproducer: makes the scheduler explain itself.

Wraps LoadScopeScheduling._reschedule and DSession.triggershutdown so the run
prints WHY dispatch stops, rather than leaving it to be inferred from a stack.
Writes to stderr with flush=True -- buffered stdout is precisely how this bug
hides (a wedged master never flushes its tail).
"""
import sys


def _log(msg):
    print("BD-DIAG %s" % msg, file=sys.stderr, flush=True)


def pytest_configure(config):
    from xdist.scheduler.loadscope import LoadScopeScheduling as S
    from xdist.dsession import DSession

    orig_resched = S._reschedule
    orig_trigger = DSession.triggershutdown

    def _reschedule(self, node):
        nid = getattr(node.gateway, "id", "?")
        latch = getattr(node, "shutting_down", "?")
        wq = len(self.workqueue)
        pend = self._pending_of(self.assigned_work.get(node, {}))
        if latch:
            _log("_reschedule(%s) REFUSED: node.shutting_down=True "
                 "-- workqueue=%d units stay UNDISPATCHED" % (nid, wq))
        else:
            _log("_reschedule(%s): latch=False workqueue=%d pending=%d" % (nid, wq, pend))
        return orig_resched(self, node)

    def triggershutdown(self):
        sched = getattr(self, "sched", None)
        tf = getattr(sched, "tests_finished", "?") if sched else "?"
        wq = len(sched.workqueue) if sched else "?"
        _log("triggershutdown(): tests_finished=%s workqueue=%s "
             "-- LATCHING every node now" % (tf, wq))
        return orig_trigger(self)

    orig_assign = S._assign_work_unit

    def _assign_work_unit(self, node):
        nid = getattr(node.gateway, "id", "?")
        known = node in self.registered_collections
        _log("_assign_work_unit(%s): registered_collection=%s workqueue=%d"
             % (nid, known, len(self.workqueue)))
        try:
            r = orig_assign(self, node)
        except Exception as e:
            _log("_assign_work_unit(%s) RAISED %r" % (nid, e))
            raise
        _log("_assign_work_unit(%s) done, workqueue now %d" % (nid, len(self.workqueue)))
        return r

    from xdist.workermanage import WorkerController as WC
    orig_shutdown = WC.shutdown

    def shutdown(self):
        _log("node.shutdown() -> %s  (latches it permanently)"
             % getattr(self.gateway, "id", "?"))
        return orig_shutdown(self)

    orig_addnode = DSession.worker_workerready

    def worker_workerready(self, node=None, workerinfo=None, **kw):
        _log("workerready: %s" % getattr(getattr(node, "gateway", None), "id", "?"))
        return orig_addnode(self, node=node, workerinfo=workerinfo, **kw)

    S._assign_work_unit = _assign_work_unit
    S._reschedule = _reschedule
    WC.shutdown = shutdown
    DSession.triggershutdown = triggershutdown
    DSession.worker_workerready = worker_workerready


def pytest_sessionstart(session):
    """Watchdog: if no scheduler event fires for 20s, dump the deadlock state.

    Self-contained so the reproducer needs no debugger: the interesting state
    is DSession/scheduler attributes, which a stack dump does not show.
    """
    import threading, time

    def _dump():
        time.sleep(20)
        import gc
        for o in gc.get_objects():
            if type(o).__name__ != "DSession":
                continue
            s = o.sched
            _log("=== WATCHDOG: state after 20s of no progress ===")
            _log("  DSession.shuttingdown   = %s" % o.shuttingdown)
            _log("  DSession.session_finished= %s" % o.session_finished)
            _log("  _active_nodes           = %s"
                 % [getattr(n.gateway, "id", "?") for n in o._active_nodes])
            _log("  sched.tests_finished    = %s" % s.tests_finished)
            _log("  sched.has_pending       = %s" % s.has_pending)
            _log("  sched.workqueue         = %d units: %s"
                 % (len(s.workqueue), list(s.workqueue)))
            for n, unit in s.assigned_work.items():
                nid = getattr(n.gateway, "id", "?")
                _log("  node %-4s latch=%-5s units=%s pending=%d"
                     % (nid, getattr(n, "shutting_down", "?"),
                        list(unit), s._pending_of(unit)))
            _log("=== the %d queued unit(s) above can never be dispatched: every "
                 "node is latched or gone ===" % len(s.workqueue))
            break

    t = threading.Thread(target=_dump, daemon=True)
    t.start()
