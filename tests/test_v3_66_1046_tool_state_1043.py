"""1046 tool-state shard: measurement and fleet tools."""

import pytest

from test_v3_66_1046_gates_for_this_sessions_shapes import (
    _run_tool_state_shard,
    _tool_state_shard_timeout,
)


BD_GATE_SCOPE = "repo-wide"


@pytest.mark.timeout(_tool_state_shard_timeout(__file__))
def test_tool_suite_does_not_write_real_tool_state():
    _run_tool_state_shard(__file__)
