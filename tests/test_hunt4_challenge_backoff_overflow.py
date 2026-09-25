from bulk_downloader.challenge_circuit import calculate_backoff


def test_challenge_backoff_saturates_for_long_retry_history():
    assert calculate_backoff(1) == 30.0
    assert calculate_backoff(3) == 120.0
    assert calculate_backoff(1025) == 3600.0
