"""O1826 C39: reachability builds the call-graph adjacency once per artifact."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterator, Mapping
from itertools import pairwise
from pathlib import Path

from tools.code_intelligence import reachability_service as service

BD_GATE_SCOPE = "module"

HEX64 = "a" * 64
ROUTES = 200
ENDPOINTS = (
    "endpoint",
    "inner",
    "endpoint::inner",
    ":endpoint",
    "m.py::endpoint",
    "wide",
    "missing",
)


class _CountingList(list):
    """A JSON list that records how many times the graph walker scans it."""

    def __init__(self, items: list[object]) -> None:
        super().__init__(items)
        self.scans = 0

    def __iter__(self) -> Iterator[object]:
        self.scans += 1
        return super().__iter__()


def _base_call_paths(
    call_graph: Mapping[str, object],
    endpoint: str,
) -> tuple[tuple[str, ...], ...]:
    """The origin/main 91158531 `_call_paths`, verbatim, as the output oracle."""
    nodes = call_graph["nodes"]
    edges = call_graph["edges"]
    assert isinstance(nodes, list)
    assert isinstance(edges, list)
    starts = sorted(
        node
        for node in nodes
        if isinstance(node, str) and node.endswith(f"::{endpoint}")
    )
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        adjacency[edge["from"]].append(edge["to"])
    for targets in adjacency.values():
        targets.sort()
    paths: list[tuple[str, ...]] = []
    for start in starts[:100]:
        frontier: deque[tuple[str, ...]] = deque([(start,)])
        visited = {start}
        while frontier and len(paths) < 500:
            path = frontier.popleft()
            for target in adjacency.get(path[-1], []):
                candidate = (*path, target)
                if len(candidate) > 8:
                    continue
                paths.append(candidate)
                if target not in visited and len(candidate) < 8:
                    visited.add(target)
                    frontier.append(candidate)
    return tuple(sorted(paths))


def _fixture_graph() -> dict[str, object]:
    chain = [f"chain.py::step_{index}" for index in range(12)]
    wide = [f"s{index:03d}.py::wide" for index in range(120)]
    fan = [f"fan.py::leaf_{index}" for index in range(30)]
    nodes = [
        "m.py::endpoint",
        "pkg::m.py::endpoint",
        "a:::endpoint",
        "x.py::my_endpoint",
        "x.py::endpoint::inner",
        "endpoint",
        "q.py:endpoint",
        *chain,
        *wide,
        *fan,
    ]
    pairs = [
        ("m.py::endpoint", chain[0]),
        ("pkg::m.py::endpoint", "m.py::endpoint"),
        ("m.py::endpoint", "pkg::m.py::endpoint"),
        ("a:::endpoint", "x.py::endpoint::inner"),
        ("x.py::endpoint::inner", "a:::endpoint"),
        ("x.py::endpoint::inner", chain[3]),
        ("x.py::my_endpoint", "m.py::endpoint"),
        ("q.py:endpoint", chain[0]),
        *pairwise(chain),
        (chain[5], chain[1]),
        *((source, target) for source in reversed(wide) for target in fan[:5]),
        *((fan[0], target) for target in reversed(fan[1:])),
        (fan[0], fan[0]),
        (fan[0], fan[0]),
    ]
    return {
        "source_sha": HEX64,
        "nodes": _CountingList(nodes),
        "edges": _CountingList([{"from": a, "to": b} for a, b in pairs]),
    }


def _json_input(value: dict[str, object]) -> service._JsonInput:
    return service._JsonInput(Path("fixture.json"), value, HEX64, (0, 0, 0, 0, 0, 0))


def _payload() -> dict[str, object]:
    rows = [
        {
            "rule": f"/route/{index:03d}",
            "endpoint": ENDPOINTS[index % len(ENDPOINTS)],
            "method": "GET",
            "unauthenticated": {"status": 200, "location": None, "exception": None},
            "authenticated": None,
        }
        for index in range(ROUTES)
    ]
    return {
        "status": "ok",
        "rows": rows,
        "adapter_status": {
            "operator_wiring": "unavailable",
            "navigation": "unavailable",
        },
        "operator_wiring": {},
        "navigation": {},
    }


def _build(call_graph: dict[str, object]) -> dict[str, object]:
    return service._build_artifact(
        app_target="fixture_app:app",
        authenticated_fixture=None,
        security=_json_input({"source_sha": HEX64, "auth_gates": []}),
        call_graph=_json_input(call_graph),
        deferrals=None,
        deferral_summaries=[],
        app_source=service._SourceInput(
            Path("fixture_app.py"), HEX64, (0, 0, 0, 0, 0, 0)
        ),
        fixture_source=None,
        tracked_source_sha=HEX64,
        payload=_payload(),
    )


def test_artifact_scans_call_graph_once_for_all_route_rows() -> None:
    call_graph = _fixture_graph()

    artifact = _build(call_graph)

    assert len(artifact["routes"]) == ROUTES
    assert (call_graph["nodes"].scans, call_graph["edges"].scans) == (1, 1), (
        "C39: call-graph adjacency rebuilt per route row: "
        f"nodes scanned {call_graph['nodes'].scans}x, "
        f"edges scanned {call_graph['edges'].scans}x for {ROUTES} rows"
    )


def test_artifact_call_paths_match_base_walker_for_every_row() -> None:
    call_graph = _fixture_graph()
    expected = {
        endpoint: sorted(list(path) for path in _base_call_paths(call_graph, endpoint))
        for endpoint in ENDPOINTS
    }

    artifact = _build(call_graph)

    by_rule = {row["rule"]: row["evidence"]["call_paths"] for row in artifact["routes"]}
    for index in range(ROUTES):
        endpoint = ENDPOINTS[index % len(ENDPOINTS)]
        assert by_rule[f"/route/{index:03d}"] == expected[endpoint], endpoint
    assert all(expected[endpoint] for endpoint in ENDPOINTS if endpoint != "missing")
    assert expected["missing"] == []


def test_call_paths_match_base_walker_on_fixture_graph() -> None:
    call_graph = _fixture_graph()

    for endpoint in ENDPOINTS:
        assert service._call_paths(call_graph, endpoint) == _base_call_paths(
            call_graph, endpoint
        ), endpoint
    assert len(_base_call_paths(call_graph, "wide")) >= 500


def _capped_fan_graph() -> dict[str, object]:
    # The 500-path cap stops the BFS after 12 of the 30 hubs are expanded, so
    # which hubs contribute leaves depends on the adjacency sort alone.
    hubs = [f"h{index:02d}" for index in range(30)]
    leaves = [f"l{index:03d}" for index in range(40)]
    pairs = [
        *(("m::e", hub) for hub in reversed(hubs)),
        *((hub, leaf) for hub in reversed(hubs) for leaf in reversed(leaves)),
    ]
    return {
        "source_sha": HEX64,
        "nodes": ["m::e", *hubs, *leaves],
        "edges": [{"from": a, "to": b} for a, b in pairs],
    }


def _many_starts_graph() -> dict[str, object]:
    # 150 starts listed in reverse order; only the first 100 sorted starts walk.
    starts = [f"s{index:03d}::e" for index in reversed(range(150))]
    return {
        "source_sha": HEX64,
        "nodes": [*starts, "t"],
        "edges": [{"from": start, "to": "t"} for start in starts],
    }


def test_call_paths_keep_base_selection_when_path_cap_cuts_bfs() -> None:
    call_graph = _capped_fan_graph()
    index = service._call_graph_index(call_graph)

    paths = service._call_paths(call_graph, "e", index)

    expanded = sorted({path[1] for path in paths if len(path) == 3})
    assert expanded == [f"h{index:02d}" for index in range(12)], (
        "C39: adjacency targets not sorted; capped BFS expanded hubs "
        f"{expanded[:3]}..{expanded[-3:]} instead of h00..h11"
    )
    assert len(paths) == 510
    assert paths == _base_call_paths(call_graph, "e")
    assert service._call_paths(call_graph, "e") == paths


def test_call_paths_walk_first_hundred_sorted_starts() -> None:
    call_graph = _many_starts_graph()
    index = service._call_graph_index(call_graph)

    paths = service._call_paths(call_graph, "e", index)

    assert (paths[0], paths[-1]) == (("s000::e", "t"), ("s099::e", "t")), (
        "C39: starts not sorted before the [:100] window; walked "
        f"{paths[0][0]}..{paths[-1][0]} instead of s000::e..s099::e"
    )
    assert len(paths) == 100
    assert paths == _base_call_paths(call_graph, "e")
    assert service._call_paths(call_graph, "e") == paths


def test_artifact_builds_fresh_index_for_each_call_graph() -> None:
    nodes = ["m.py::endpoint", "n.py::f"]
    empty = {"source_sha": HEX64, "nodes": list(nodes), "edges": []}
    linked = {
        "source_sha": HEX64,
        "nodes": list(nodes),
        "edges": [{"from": "m.py::endpoint", "to": "n.py::f"}],
    }

    first = _build(empty)
    second = _build(linked)

    def endpoint_paths(artifact: dict[str, object]) -> list[object]:
        by_rule = {
            row["rule"]: row["evidence"]["call_paths"] for row in artifact["routes"]
        }
        return [
            by_rule[f"/route/{index:03d}"]
            for index in range(ROUTES)
            if ENDPOINTS[index % len(ENDPOINTS)] == "endpoint"
        ]

    stale = "C39: call-graph index reused across artifacts"
    assert endpoint_paths(first)
    assert all(p == [] for p in endpoint_paths(first)), f"{stale}; edgeless graph"
    assert all(p == [["m.py::endpoint", "n.py::f"]] for p in endpoint_paths(second)), (
        f"{stale}; second graph's edge lost"
    )
