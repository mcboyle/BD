import importlib

import pytest
from test_row870_work_stealing import HermeticRESPDaemon

BD_GATE_SCOPE = "module"


@pytest.fixture
def queue_client():
    server = HermeticRESPDaemon()
    client = server.create_client()
    try:
        yield client
    finally:
        client.close()
        server.stop()


def test_push_and_steal_are_fifo(queue_client):
    ws = importlib.import_module("bulk_downloader.work_stealing")
    coord = ws.WorkStealingCoordinator(queue_client, worker_id="fifo-worker")
    for depth, job_id in enumerate(("a", "b", "c"), 1):
        assert coord.push_job("fifo", job_id, payload=job_id) == depth
    assert coord.queue_length("fifo") == 3

    jobs = [coord.steal("fifo") for _ in range(3)]
    assert all(job is not None for job in jobs)
    assert [job.job_id for job in jobs] == ["a", "b", "c"], "O1713_FIFO"
    assert [job.payload for job in jobs] == ["a", "b", "c"]
    assert queue_client.llen(ws.processing_key("fifo", "fifo-worker")) == 3
    assert coord.queue_length("fifo") == 0
    assert coord.steal("fifo") is None


def test_abandoned_job_is_next_despite_new_pushes(queue_client):
    ws = importlib.import_module("bulk_downloader.work_stealing")
    coord = ws.WorkStealingCoordinator(queue_client, worker_id="retry-worker")
    assert coord.push_job("retry", "returned", payload="original") == 1
    returned = coord.steal("retry")
    assert returned is not None and returned.job_id == "returned"
    assert queue_client.llen(ws.processing_key("retry", "retry-worker")) == 1
    assert queue_client.get(ws.lease_key("retry", "returned")) == b"retry-worker"
    assert coord.push_job("retry", "before-return") == 1

    coord.abandon(returned)
    assert queue_client.llen(ws.processing_key("retry", "retry-worker")) == 0
    assert queue_client.get(ws.lease_key("retry", "returned")) is None
    assert coord.queue_length("retry") == 2
    assert coord.push_job("retry", "after-return") == 3

    jobs = [coord.steal("retry") for _ in range(3)]
    assert all(job is not None for job in jobs)
    assert [job.job_id for job in jobs] == [
        "returned", "before-return", "after-return"
    ], "O1713_ABANDON_PRIORITY"
    assert jobs[0].raw_bytes == returned.raw_bytes
    assert jobs[0].payload == "original"
    assert coord.queue_length("retry") == 0


def test_raw_single_job_control_is_byte_identical(queue_client):
    ws = importlib.import_module("bulk_downloader.work_stealing")
    coord = ws.WorkStealingCoordinator(queue_client, worker_id="raw-worker")
    raw = b"legacy-job-\xff"
    assert coord.steal("raw") is None
    assert queue_client.rpush(ws.queue_key("raw"), raw) == 1
    job = coord.steal("raw")
    assert job is not None
    assert (job.job_id, job.payload, job.raw_bytes, job.worker_id) == (
        "legacy-job-\ufffd", "legacy-job-\ufffd", raw, "raw-worker"
    ), "O1713_RAW_CONTROL"
    assert queue_client.lrange(ws.processing_key("raw", "raw-worker"), 0, -1) == [raw]
    assert queue_client.get(ws.lease_key("raw", job.job_id)) == b"raw-worker"
    coord.complete(job)
    assert queue_client.lrange(ws.processing_key("raw", "raw-worker"), 0, -1) == []
    assert queue_client.get(ws.lease_key("raw", job.job_id)) is None
    assert coord.steal("raw") is None
