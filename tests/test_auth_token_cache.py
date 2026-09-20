"""Alias acceptance test for Row 894: tests/test_auth_token_cache.py."""
from test_row894 import (  # noqa: F401
    test_401_during_in_flight_fetch_is_not_republished,
    test_cache_invalidated_on_401,
    test_distinct_keys_do_not_share_tokens,
    test_token_auto_renews_on_expiration,
    test_token_reused_across_successive_sessions,
    test_ttl_is_elapsed_time_not_wall_clock,
)

# the CI shard gate (test_v3_66_939) classifies a file by a module-level
# ASSIGNMENT, not an imported name
BD_GATE_SCOPE = "module"
