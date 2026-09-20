"""Row 844 -- Docker local registry mirror configuration for satellite containers.

WHY THIS GATE EXISTS.
Satellite AI containers (TEI, Ollama, Langfuse) pulled over public registries
during node reprovisioning take 3-7 minutes per node and risk rate limits.
A local Docker Registry Mirror running at http://127.0.0.1:5000 accelerates
container launch times to <15s over 25GbE LAN fabric.

WHAT THIS GATE ASSERTS.
1. Docker daemon config generation in toolchain/bin/bd-docker-registry configures
   registry-mirrors ["http://127.0.0.1:5000"] and preserves existing daemon keys.
2. Malformed existing daemon.json is refused (raising ValueError) while preserving
   original file bytes on disk untouched.
3. Image reference parsing correctly preserves registry domains and ports (e.g.
   localhost:5000/team/image) and defaults missing tags to "latest".
4. Image pull from local mirror requires valid manifest and blob storage evidence,
   rejecting non-manifest text payloads.
5. Image pull executes in <15s on 25GbE LAN.
6. Fail-open fallback to Docker Hub when mirror is unpopulated or unreachable,
   with real error propagation when upstream fails.
7. Pre-population routine verifies satellite AI container manifest list on boot.
"""
from __future__ import annotations

import hashlib
import http.server
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
from typing import Any
from unittest import mock
import urllib.error

BD_GATE_SCOPE = "module"

REPO = pathlib.Path(__file__).resolve().parents[1]
TOOL = REPO / "toolchain" / "bin" / "bd-docker-registry"


def _load_tool() -> Any:
    """Import extensionless toolchain/bin/bd-docker-registry dynamically."""
    assert TOOL.is_file(), f"toolchain/bin/bd-docker-registry does not exist at {TOOL}"
    name = f"bd_docker_registry_under_test_{os.getpid()}"
    spec = importlib.util.spec_from_loader(
        name, importlib.machinery.SourceFileLoader(name, str(TOOL))
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_tool_exists_and_is_executable():
    """RED assertion: toolchain/bin/bd-docker-registry must exist and be executable."""
    assert TOOL.is_file(), f"Missing required executable tool: {TOOL}"
    assert os.access(TOOL, os.X_OK), f"Tool {TOOL} is not marked executable (chmod +x)"


def test_daemon_config_generation_with_local_mirror():
    """Verifies Docker daemon config generation configuring registry-mirrors."""
    mod = _load_tool()
    assert hasattr(mod, "generate_daemon_config"), "missing generate_daemon_config function"

    # 1. Fresh config generation
    cfg = mod.generate_daemon_config(mirrors=["http://127.0.0.1:5000"])
    assert "registry-mirrors" in cfg, "registry-mirrors key missing from generated daemon config"
    assert "http://127.0.0.1:5000" in cfg["registry-mirrors"]

    # 2. Existing config merge preservation
    existing = {
        "insecure-registries": ["10.0.70.182:5000"],
        "log-driver": "json-file",
        "registry-mirrors": ["http://10.0.70.182:5001"],
    }
    merged = mod.generate_daemon_config(
        mirrors=["http://127.0.0.1:5000"],
        existing_config=existing,
    )
    assert merged["log-driver"] == "json-file", "existing keys must be preserved"
    assert "10.0.70.182:5000" in merged["insecure-registries"]
    assert "http://127.0.0.1:5000" in merged["registry-mirrors"]
    assert "http://10.0.70.182:5001" in merged["registry-mirrors"]

    # 3. Idempotent merge (no duplicate entries)
    merged_again = mod.generate_daemon_config(
        mirrors=["http://127.0.0.1:5000"],
        existing_config=merged,
    )
    assert len(merged_again["registry-mirrors"]) == len(set(merged_again["registry-mirrors"])), (
        "mirrors must not contain duplicate entries on repeated configuration"
    )


def test_daemon_config_refuses_malformed_existing_file(tmp_path: pathlib.Path):
    """Verifies write_daemon_config refuses malformed existing files and preserves disk bytes."""
    mod = _load_tool()
    assert hasattr(mod, "write_daemon_config"), "missing write_daemon_config function"

    cfg_file = tmp_path / "daemon-invalid.json"
    raw_invalid = '{"log-driver":"journald",}'  # trailing comma is invalid JSON
    cfg_file.write_text(raw_invalid, encoding="utf-8")

    # Attempting to write config over a malformed existing file must raise ValueError
    try:
        mod.write_daemon_config(cfg_file, mirrors=["http://127.0.0.1:5000"])
        assert False, "write_daemon_config should have raised ValueError on malformed JSON"
    except ValueError:
        pass

    # Assert original bytes preserved untouched
    assert cfg_file.read_text(encoding="utf-8") == raw_invalid, (
        "malformed file bytes must be preserved untouched"
    )


def test_daemon_config_cli_invocation(tmp_path: pathlib.Path):
    """Verifies CLI execution of bd-docker-registry config command."""
    _load_tool()
    out_file = tmp_path / "daemon.json"

    cmd = [
        sys.executable,
        str(TOOL),
        "config",
        "--output",
        str(out_file),
        "--mirror",
        "http://127.0.0.1:5000",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    assert proc.returncode == 0, f"CLI exited with {proc.returncode}: {proc.stderr}"
    assert out_file.exists(), "Target daemon.json was not written"

    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert "http://127.0.0.1:5000" in data.get("registry-mirrors", [])


def test_image_reference_parsing_and_normalization():
    """Verifies image reference parsing preserving ports and defaulting tags."""
    mod = _load_tool()
    assert hasattr(mod, "_normalize_image_name"), "missing _normalize_image_name function"

    # Port in domain must NOT be split as a tag
    repo, tag = mod._normalize_image_name("localhost:5000/team/image")
    assert repo == "localhost:5000/team/image"
    assert tag == "latest"

    # Port in domain with explicit tag
    repo, tag = mod._normalize_image_name("localhost:5000/team/image:v1")
    assert repo == "localhost:5000/team/image"
    assert tag == "v1"

    # Digest reference
    repo, tag = mod._normalize_image_name("ubuntu@sha256:abc123")
    assert repo == "library/ubuntu"
    assert tag == "sha256:abc123"

    # Standard short name
    repo, tag = mod._normalize_image_name("busybox:latest")
    assert repo == "library/busybox"
    assert tag == "latest"

    # Standard short name without tag
    repo, tag = mod._normalize_image_name("busybox")
    assert repo == "library/busybox"
    assert tag == "latest"


def test_image_pull_from_local_mirror_with_manifest_and_blobs(tmp_path: pathlib.Path):
    """Verifies local mirror pull validates manifest, fetches blobs, and executes in <15s."""
    mod = _load_tool()
    assert hasattr(mod, "pull_image"), "missing pull_image function"

    # Create dummy layer and config blob
    config_data = b'{"architecture":"amd64","os":"linux"}'
    config_digest = f"sha256:{hashlib.sha256(config_data).hexdigest()}"

    layer_data = b"mock-layer-rootfs-content"
    layer_digest = f"sha256:{hashlib.sha256(layer_data).hexdigest()}"

    manifest_obj = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
        "config": {
            "mediaType": "application/vnd.docker.container.image.v1+json",
            "size": len(config_data),
            "digest": config_digest,
        },
        "layers": [
            {
                "mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",
                "size": len(layer_data),
                "digest": layer_digest,
            }
        ],
    }
    manifest_bytes = json.dumps(manifest_obj).encode("utf-8")

    requests_received: list[str] = []

    class MockMirrorHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests_received.append(self.path)
            if "/manifests/" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.docker.distribution.manifest.v2+json")
                self.end_headers()
                self.wfile.write(manifest_bytes)
            elif f"/blobs/{config_digest}" in self.path:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(config_data)
            elif f"/blobs/{layer_digest}" in self.path:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(layer_data)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), MockMirrorHandler)
    port = server.server_port
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()

    storage_dir = tmp_path / "docker-storage"
    try:
        with mock.patch.dict(os.environ, {"BD_DOCKER_STORAGE_DIR": str(storage_dir)}):
            t0 = time.monotonic()
            res = mod.pull_image("library/testimage:latest", mirror_url=f"http://127.0.0.1:{port}", timeout_s=15.0)
            elapsed = time.monotonic() - t0

            assert res.success is True
            assert res.source == "mirror"
            assert res.fallback_used is False
            assert res.duration_s < 15.0
            assert elapsed < 15.0

            # Verified both manifest and blob endpoints were fetched
            assert any("/manifests/" in r for r in requests_received)
            assert any(config_digest in r for r in requests_received)
            assert any(layer_digest in r for r in requests_received)

            # Usable-image evidence: blobs and manifest persisted to disk
            blob_store = storage_dir / "blobs"
            assert (blob_store / config_digest.replace(":", "_")).exists()
            assert (blob_store / layer_digest.replace(":", "_")).exists()
    finally:
        server.shutdown()
        server.server_close()
        th.join(timeout=3)


def test_image_pull_rejects_non_manifest_payload():
    """Verifies that HTTP 200 non-manifest text is rejected and NOT treated as success."""
    mod = _load_tool()

    class NonManifestHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"not an image manifest")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), NonManifestHandler)
    port = server.server_port
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()

    try:
        # Mock upstream to fail so fallback cannot falsely mask the mirror rejection
        with mock.patch.object(mod, "_fetch_from_upstream", side_effect=RuntimeError("upstream unreachable")):
            res = mod.pull_image("library/garbage:latest", mirror_url=f"http://127.0.0.1:{port}", timeout_s=3.0)
            # The mirror pull MUST fail because the payload is not a valid manifest
            assert res.success is False, "HTTP 200 non-manifest text must not yield successful pull"
            assert res.source == "upstream"  # fell back after mirror rejected corrupt data
    finally:
        server.shutdown()
        server.server_close()
        th.join(timeout=3)


def test_fail_open_fallback_to_upstream_when_mirror_unpopulated():
    """Acceptance (3): Fail-open fallback to Docker Hub when mirror is unpopulated."""
    mod = _load_tool()

    # Case A: Mirror returns 404 (unpopulated) -> fallback to upstream succeeds
    with (
        mock.patch.object(mod, "_fetch_from_mirror", side_effect=mod.MirrorUnpopulatedError("404 unpopulated")),
        mock.patch.object(mod, "_fetch_from_upstream") as mock_upstream,
    ):
        mock_upstream.return_value = {"status": "downloaded", "source": "upstream_docker_hub", "duration_s": 2.5}
        res = mod.pull_image("library/python:3.12-slim", mirror_url="http://127.0.0.1:5000")
        assert res.success is True
        assert res.source == "upstream"
        assert res.fallback_used is True
        mock_upstream.assert_called_once()

    # Case B: Mirror connection error (unreachable) -> fallback to upstream succeeds
    with (
        mock.patch.object(mod, "_fetch_from_mirror", side_effect=mod.MirrorUnreachableError("connection refused")),
        mock.patch.object(mod, "_fetch_from_upstream") as mock_upstream,
    ):
        mock_upstream.return_value = {"status": "downloaded", "source": "upstream_docker_hub", "duration_s": 3.1}
        res = mod.pull_image("library/python:3.12-slim", mirror_url="http://127.0.0.1:5000")
        assert res.success is True
        assert res.source == "upstream"
        assert res.fallback_used is True


def test_upstream_fallback_propagates_failure_when_upstream_unreachable():
    """Verifies that upstream failures (e.g. nonexistent image) are propagated as failure."""
    mod = _load_tool()

    with (
        mock.patch.object(mod, "_fetch_from_mirror", side_effect=mod.MirrorUnpopulatedError("404 unpopulated")),
        mock.patch.object(mod, "_fetch_from_upstream", side_effect=urllib.error.URLError("connection refused")),
    ):
        res = mod.pull_image("nonexistent-image:latest", mirror_url="http://127.0.0.1:5000")
        assert res.success is False, "Failure must be propagated when upstream also fails"
        assert res.fallback_used is True
        assert "upstream failed" in res.detail


def test_satellite_ai_images_list_and_prepopulation():
    """Verifies default satellite AI images and boot pre-population check."""
    mod = _load_tool()
    assert hasattr(mod, "SATELLITE_AI_IMAGES"), "missing SATELLITE_AI_IMAGES declaration"
    assert len(mod.SATELLITE_AI_IMAGES) >= 3, "expected at least 3 satellite AI images in roster"

    roster_text = " ".join(mod.SATELLITE_AI_IMAGES)
    assert "text-embeddings-inference" in roster_text or "tei" in roster_text
    assert "langfuse" in roster_text
    assert "coredns" in roster_text

    with mock.patch.object(mod, "pull_image") as mock_pull:
        mock_pull.return_value = mod.PullResult(image="dummy", success=True, source="mirror", duration_s=0.5)
        results = mod.prepopulate_satellite_images(mirror_url="http://127.0.0.1:5000")
        assert len(results) == len(mod.SATELLITE_AI_IMAGES)
        assert all(r.success for r in results.values())


def test_positive_control_mirror_catalog_check():
    """Positive control: probe against local mirror returns structured status."""
    mod = _load_tool()
    assert hasattr(mod, "check_mirror_health"), "missing check_mirror_health function"

    with mock.patch.object(mod, "_query_registry_catalog") as mock_cat:
        mock_cat.return_value = {"repositories": ["library/busybox", "coredns/coredns"]}
        status = mod.check_mirror_health("http://127.0.0.1:5000")
        assert status["healthy"] is True
        assert "library/busybox" in status["repositories"]


def test_negative_control_invalid_configurations_and_exact_counts():
    """Negative control: exact counts and error assertions across edge conditions."""
    mod = _load_tool()

    # Exact count assertion: exactly 4 core satellite images defined in default roster
    assert len(mod.SATELLITE_AI_IMAGES) == 4, (
        f"Expected exactly 4 satellite AI images, found {len(mod.SATELLITE_AI_IMAGES)}"
    )

    # Negative control: malformed JSON parsing raises MirrorError
    try:
        mod._validate_manifest(b"corrupt non-json content", "test:latest")
        assert False, "_validate_manifest should have raised MirrorError"
    except mod.MirrorError as exc:
        assert "not valid JSON" in str(exc)

    # Negative control: missing schemaVersion raises MirrorError
    try:
        mod._validate_manifest(b'{"name":"no-schema"}', "test:latest")
        assert False, "_validate_manifest should have raised MirrorError for missing schemaVersion"
    except mod.MirrorError as exc:
        assert "missing schemaVersion" in str(exc)
