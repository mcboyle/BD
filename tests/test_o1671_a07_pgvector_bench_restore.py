import pytest
from bulk_downloader import pg_vector

BD_GATE_SCOPE = "module"


class Connection:
    def __init__(self, failure=None):
        self.failure = failure
        self.error = RuntimeError("BENCHMARK_EXACT_QUERY_FAILED")
        self.flags = {"enable_indexscan": "on", "enable_bitmapscan": "on"}
        self.committed = self.flags.copy()
        self.aborted = False
        self.statements = []
        self.commits = 0
        self.rollbacks = 0
        self.exact_queries = 0

    def cursor(self):
        return Cursor(self)

    def commit(self):
        assert not self.aborted, "ABORTED_TRANSACTION_REQUIRES_ROLLBACK"
        self.commits += 1
        self.committed = self.flags.copy()

    def rollback(self):
        self.rollbacks += 1
        self.aborted = False
        self.flags = self.committed.copy()

    def fail(self):
        self.aborted = True
        raise self.error


class Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.exact = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None, **kwargs):
        conn = self.conn
        assert not conn.aborted, "ABORTED_TRANSACTION_REQUIRES_ROLLBACK"
        conn.statements.append((sql, params, kwargs))
        if sql == "SET enable_bitmapscan = off" and conn.failure == "disable":
            conn.fail()
        if sql.startswith("SET enable_"):
            _, flag, _, value = sql.split()
            conn.flags[flag] = value
        if sql.startswith("SELECT"):
            self.exact = conn.flags["enable_indexscan"] == "off"
            if self.exact:
                conn.exact_queries += 1
                if conn.failure == "execute":
                    conn.fail()

    def fetchall(self):
        if self.exact and self.conn.failure == "fetch":
            self.conn.fail()
        return [(1,), (2,)]


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
@pytest.mark.parametrize("failure", ["execute", "fetch", "disable"])
def test_failed_exact_scan_restores_planner_and_propagates_original_error(failure, error_type):
    conn = Connection(failure)
    conn.error = error_type("BENCHMARK_EXACT_QUERY_FAILED")
    with pytest.raises(error_type, match="BENCHMARK_EXACT_QUERY_FAILED") as caught:
        pg_vector.benchmark(conn, "vectors", [[0.1, 0.2]], k=2)
    assert caught.value is conn.error
    assert ("SET enable_indexscan = off", None, {}) in conn.statements
    if failure != "disable":
        assert conn.exact_queries == 1
    assert conn.flags == {"enable_indexscan": "on", "enable_bitmapscan": "on"}, "PLANNER_FLAGS_NOT_RESTORED"
    assert conn.statements[-2:] == [
        ("SET enable_indexscan = on", None, {}),
        ("SET enable_bitmapscan = on", None, {}),
    ]
    assert conn.rollbacks == 1 and not conn.aborted
    assert conn.committed == conn.flags


def test_success_preserves_sql_order_commits_and_result(monkeypatch):
    conn = Connection()
    ticks = iter([0, 0.001, 0.002, 0.004])
    monkeypatch.setattr(pg_vector.time, "perf_counter", lambda: next(ticks))
    result = pg_vector.benchmark(conn, "vectors", [[0.1, 0.2], [0.3, 0.4]], k=2)
    sql = 'SELECT id FROM "row875vec"."vectors" ORDER BY embedding <-> %s::vector LIMIT %s'
    queries = [(sql, ("[0.1,0.2]", 2), {"prepare": True}), (sql, ("[0.3,0.4]", 2), {"prepare": True})]
    assert conn.statements == [
        ("SET hnsw.ef_search = 100", None, {}),
        ("SET enable_indexscan = on", None, {}),
        ("SET enable_bitmapscan = on", None, {}),
        *queries,
        ("SET enable_indexscan = off", None, {}),
        ("SET enable_bitmapscan = off", None, {}),
        *queries,
        ("SET enable_indexscan = on", None, {}),
        ("SET enable_bitmapscan = on", None, {}),
    ]
    assert conn.commits == 4 and conn.rollbacks == 0 and conn.exact_queries == 2
    assert result.n_queries == 2 and result.k == 2
    assert result.mean_latency_ms == pytest.approx(1.5)
    assert result.p99_latency_ms == pytest.approx(2.0)
    assert result.recall_at_k == 1.0
