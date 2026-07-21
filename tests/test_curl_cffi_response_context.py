from bulk_downloader import runner_transport


class _CloseableResponse:
    def __init__(self):
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


def test_closeable_response_context_yields_a_non_context_manager_response():
    response = _CloseableResponse()

    with runner_transport._closeable_response_context(response) as entered:
        assert entered is response
        assert response.close_calls == 0

    assert response.close_calls == 1


def test_closeable_response_context_closes_on_exception():
    response = _CloseableResponse()

    try:
        with runner_transport._closeable_response_context(response):
            raise RuntimeError("stream processing failed")
    except RuntimeError as exc:
        assert str(exc) == "stream processing failed"
    else:
        raise AssertionError("the streaming exception must propagate")

    assert response.close_calls == 1


if __name__ == "__main__":
    test_closeable_response_context_yields_a_non_context_manager_response()
    test_closeable_response_context_closes_on_exception()
