"""Targeted Media Server Library Refresh Dispatcher (Row 836).

Provides targeted library update notifications for media management platforms
(Plex, Jellyfin, and Stash) post-download completion. Translates local
filesystem paths to server mount paths and dispatches targeted update requests
non-blockingly within 500ms of file completion.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Outcome of a media server targeted refresh request."""

    server_type: str
    server_url: str
    path: str
    ok: bool
    status_code: int = 0
    error: str = ""
    duration_ms: float = 0.0


@dataclass
class MediaServerConfig:
    """Configuration descriptor for a target media server."""

    server_type: str  # 'plex', 'jellyfin', or 'stash'
    url: str
    token: str = ""
    section_id: str = "1"
    path_mappings: dict[str, str] | list[dict[str, str]] | None = None
    timeout: float = 5.0
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MediaServerConfig:
        return cls(
            server_type=str(data.get("server_type", "")).strip().lower(),
            url=str(data.get("url", "")).strip(),
            token=str(data.get("token") or data.get("api_key") or "").strip(),
            section_id=str(data.get("section_id", "1")).strip(),
            path_mappings=data.get("path_mappings"),
            timeout=float(data.get("timeout", 5.0)),
            enabled=bool(data.get("enabled", True)),
        )


class PathMapper:
    """Translates local directory/file paths to server mount paths."""

    def __init__(
        self,
        mappings: dict[str, str] | list[dict[str, str]] | None = None,
    ) -> None:
        self._rules: list[tuple[str, str]] = []
        if mappings:
            self.load_mappings(mappings)

    def load_mappings(
        self,
        mappings: dict[str, str] | list[dict[str, str]],
    ) -> None:
        rules: list[tuple[str, str]] = []
        if isinstance(mappings, dict):
            for local_p, server_p in mappings.items():
                norm_local = self._normalize_prefix(local_p)
                norm_server = self._normalize_prefix(server_p)
                if norm_local:
                    rules.append((norm_local, norm_server))
        elif isinstance(mappings, list):
            for item in mappings:
                if isinstance(item, dict):
                    local_p = item.get("local") or item.get("from") or ""
                    server_p = item.get("server") or item.get("to") or ""
                    norm_local = self._normalize_prefix(local_p)
                    norm_server = self._normalize_prefix(server_p)
                    if norm_local:
                        rules.append((norm_local, norm_server))

        # Longest prefix matches first
        self._rules = sorted(rules, key=lambda r: len(r[0]), reverse=True)

    @staticmethod
    def _normalize_prefix(path_str: str) -> str:
        if not path_str:
            return ""
        # Convert backslashes to posix separators; strip trailing slashes but
        # keep a bare root ("/" is the server root, not "nothing" -- row836 E2).
        posix = path_str.replace("\\", "/")
        stripped = posix.rstrip("/")
        return stripped if stripped else ("/" if posix.startswith("/") else "")

    def translate(self, local_path: str) -> str:
        """Translate a local filesystem path to the corresponding server path."""
        if not local_path or not self._rules:
            return local_path

        posix_local = local_path.replace("\\", "/")
        for local_prefix, server_prefix in self._rules:
            if posix_local == local_prefix:
                return server_prefix
            sep = "" if local_prefix.endswith("/") else "/"
            if posix_local.startswith(local_prefix + sep):
                remainder = posix_local[len(local_prefix) + len(sep) :]
                if server_prefix.endswith("/"):
                    return f"{server_prefix}{remainder}"
                return f"{server_prefix}/{remainder}"

        return local_path


def _open(req: urllib.request.Request, timeout: float):
    """row836 fixer (E1 HIGH): every hop of a media-server request goes
    through the hooks' pinned opener (row 728: resolve once, open only the
    vetted address, and vet every redirect target with the same policy
    ``_validate_url`` applied to the base URL). A bare
    ``urllib.request.urlopen`` follows 301/302 to anywhere -- the metadata
    service, loopback admin ports -- after the base URL passed."""
    try:
        from .hooks import _hook_urlopen  # lazy import
    except (ImportError, AttributeError):
        from .hooks import _HookRedirectHandler  # lazy import
        return urllib.request.build_opener(_HookRedirectHandler()).open(req, timeout=timeout)
    return _hook_urlopen(req, timeout=timeout)


def _validate_url(url: str) -> tuple[bool, str]:
    """Validate server URL safety against SSRF policies."""
    try:
        from .hooks import _validate_webhook_url  # lazy import

        return _validate_webhook_url(url)
    except (ImportError, AttributeError, ValueError):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False, f"unsupported scheme: {parsed.scheme}"
        if not parsed.hostname:
            return False, "missing hostname"
        return True, "ok"


class BaseMediaServerClient:
    """Base client for targeted media server refresh requests."""

    def __init__(self, config: MediaServerConfig) -> None:
        self.config = config
        self.path_mapper = PathMapper(config.path_mappings)

    def refresh(self, path: str) -> SyncResult:
        raise NotImplementedError


class PlexClient(BaseMediaServerClient):
    """Plex media server targeted refresh client."""

    def __init__(
        self,
        base_url: str,
        token: str,
        section_id: str = "1",
        path_mappings: dict[str, str] | list[dict[str, str]] | None = None,
        timeout: float = 5.0,
    ) -> None:
        cfg = MediaServerConfig(
            server_type="plex",
            url=base_url,
            token=token,
            section_id=section_id,
            path_mappings=path_mappings,
            timeout=timeout,
        )
        super().__init__(cfg)

    def refresh(self, path: str) -> SyncResult:
        start_t = time.time()
        server_path = self.path_mapper.translate(path)
        base_url = self.config.url.rstrip("/")

        ok, msg = _validate_url(base_url)
        if not ok:
            return SyncResult(
                server_type="plex",
                server_url=base_url,
                path=server_path,
                ok=False,
                error=f"url validation failed: {msg}",
                duration_ms=(time.time() - start_t) * 1000,
            )

        section = self.config.section_id or "1"
        endpoint = f"{base_url}/library/sections/{section}/refresh"
        query_params = {}
        if server_path:
            query_params["path"] = server_path
        if self.config.token:
            query_params["X-Plex-Token"] = self.config.token

        url_with_query = f"{endpoint}?{urllib.parse.urlencode(query_params)}"
        headers = {
            "Accept": "application/xml",
            "User-Agent": "BulkDownloader-MediaServerSync/1.0",
        }
        if self.config.token:
            headers["X-Plex-Token"] = self.config.token

        try:
            req = urllib.request.Request(url_with_query, method="GET", headers=headers)
            with _open(req, self.config.timeout) as resp:
                status = resp.status
                return SyncResult(
                    server_type="plex",
                    server_url=base_url,
                    path=server_path,
                    ok=200 <= status < 300,
                    status_code=status,
                    duration_ms=(time.time() - start_t) * 1000,
                )
        except urllib.error.HTTPError as e:
            return SyncResult(
                server_type="plex",
                server_url=base_url,
                path=server_path,
                ok=False,
                status_code=e.code,
                error=f"HTTP {e.code}: {e.reason}",
                duration_ms=(time.time() - start_t) * 1000,
            )
        except Exception as e:  # noqa: BLE001
            return SyncResult(
                server_type="plex",
                server_url=base_url,
                path=server_path,
                ok=False,
                error=str(e),
                duration_ms=(time.time() - start_t) * 1000,
            )


class JellyfinClient(BaseMediaServerClient):
    """Jellyfin media server targeted refresh client."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        path_mappings: dict[str, str] | list[dict[str, str]] | None = None,
        timeout: float = 5.0,
    ) -> None:
        cfg = MediaServerConfig(
            server_type="jellyfin",
            url=base_url,
            token=api_key,
            path_mappings=path_mappings,
            timeout=timeout,
        )
        super().__init__(cfg)

    def refresh(self, path: str) -> SyncResult:
        start_t = time.time()
        server_path = self.path_mapper.translate(path)
        base_url = self.config.url.rstrip("/")

        ok, msg = _validate_url(base_url)
        if not ok:
            return SyncResult(
                server_type="jellyfin",
                server_url=base_url,
                path=server_path,
                ok=False,
                error=f"url validation failed: {msg}",
                duration_ms=(time.time() - start_t) * 1000,
            )

        endpoint = f"{base_url}/Library/Media/Updated"
        payload = {
            "Updates": [
                {
                    "Path": server_path,
                    "UpdateType": "Created",
                }
            ]
        }
        body_bytes = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "BulkDownloader-MediaServerSync/1.0",
        }
        if self.config.token:
            headers["X-Emby-Token"] = self.config.token

        try:
            req = urllib.request.Request(
                endpoint, data=body_bytes, method="POST", headers=headers
            )
            with _open(req, self.config.timeout) as resp:
                status = resp.status
                return SyncResult(
                    server_type="jellyfin",
                    server_url=base_url,
                    path=server_path,
                    ok=200 <= status < 300,
                    status_code=status,
                    duration_ms=(time.time() - start_t) * 1000,
                )
        except urllib.error.HTTPError as e:
            return SyncResult(
                server_type="jellyfin",
                server_url=base_url,
                path=server_path,
                ok=False,
                status_code=e.code,
                error=f"HTTP {e.code}: {e.reason}",
                duration_ms=(time.time() - start_t) * 1000,
            )
        except Exception as e:  # noqa: BLE001
            return SyncResult(
                server_type="jellyfin",
                server_url=base_url,
                path=server_path,
                ok=False,
                error=str(e),
                duration_ms=(time.time() - start_t) * 1000,
            )


class StashClient(BaseMediaServerClient):
    """Stash media server targeted metadata scan client."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        path_mappings: dict[str, str] | list[dict[str, str]] | None = None,
        timeout: float = 5.0,
    ) -> None:
        cfg = MediaServerConfig(
            server_type="stash",
            url=base_url,
            token=api_key,
            path_mappings=path_mappings,
            timeout=timeout,
        )
        super().__init__(cfg)

    def refresh(self, path: str) -> SyncResult:
        start_t = time.time()
        server_path = self.path_mapper.translate(path)
        base_url = self.config.url.rstrip("/")

        ok, msg = _validate_url(base_url)
        if not ok:
            return SyncResult(
                server_type="stash",
                server_url=base_url,
                path=server_path,
                ok=False,
                error=f"url validation failed: {msg}",
                duration_ms=(time.time() - start_t) * 1000,
            )

        endpoint = f"{base_url}/graphql"
        query = """
        mutation MetadataScan($input: ScanMetadataInput!) {
            metadataScan(input: $input)
        }
        """
        payload = {
            "query": query,
            "variables": {
                "input": {
                    "paths": [server_path] if server_path else [],
                }
            },
        }
        body_bytes = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "BulkDownloader-MediaServerSync/1.0",
        }
        if self.config.token:
            headers["ApiKey"] = self.config.token

        try:
            req = urllib.request.Request(
                endpoint, data=body_bytes, method="POST", headers=headers
            )
            with _open(req, self.config.timeout) as resp:
                status = resp.status
                body = resp.read().decode("utf-8", "replace")
                try:
                    data = json.loads(body)
                    if data.get("errors"):
                        err_msg = data["errors"][0].get("message", "GraphQL error")
                        return SyncResult(
                            server_type="stash",
                            server_url=base_url,
                            path=server_path,
                            ok=False,
                            status_code=status,
                            error=f"Stash GraphQL error: {err_msg}",
                            duration_ms=(time.time() - start_t) * 1000,
                        )
                except (json.JSONDecodeError, ValueError, KeyError):
                    pass

                return SyncResult(
                    server_type="stash",
                    server_url=base_url,
                    path=server_path,
                    ok=200 <= status < 300,
                    status_code=status,
                    duration_ms=(time.time() - start_t) * 1000,
                )
        except urllib.error.HTTPError as e:
            return SyncResult(
                server_type="stash",
                server_url=base_url,
                path=server_path,
                ok=False,
                status_code=e.code,
                error=f"HTTP {e.code}: {e.reason}",
                duration_ms=(time.time() - start_t) * 1000,
            )
        except Exception as e:  # noqa: BLE001
            return SyncResult(
                server_type="stash",
                server_url=base_url,
                path=server_path,
                ok=False,
                error=str(e),
                duration_ms=(time.time() - start_t) * 1000,
            )


def create_client(config: MediaServerConfig) -> BaseMediaServerClient | None:
    """Factory creating appropriate client instance for given configuration."""
    stype = config.server_type.lower()
    if stype == "plex":
        return PlexClient(
            base_url=config.url,
            token=config.token,
            section_id=config.section_id,
            path_mappings=config.path_mappings,
            timeout=config.timeout,
        )
    elif stype in ("jellyfin", "emby"):
        return JellyfinClient(
            base_url=config.url,
            api_key=config.token,
            path_mappings=config.path_mappings,
            timeout=config.timeout,
        )
    elif stype == "stash":
        return StashClient(
            base_url=config.url,
            api_key=config.token,
            path_mappings=config.path_mappings,
            timeout=config.timeout,
        )
    return None


class MediaServerDispatcher:
    """Non-blocking targeted refresh dispatcher for media servers."""

    _executor: concurrent.futures.ThreadPoolExecutor | None = None

    def __init__(
        self,
        servers: list[MediaServerConfig | dict[str, Any]] | None = None,
    ) -> None:
        self.servers: list[MediaServerConfig] = []
        if servers:
            for s in servers:
                if isinstance(s, dict):
                    self.servers.append(MediaServerConfig.from_dict(s))
                elif isinstance(s, MediaServerConfig):
                    self.servers.append(s)

    @classmethod
    def get_executor(cls) -> concurrent.futures.ThreadPoolExecutor:
        if cls._executor is None:
            cls._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=8, thread_name_prefix="media_server_sync"
            )
        return cls._executor

    def dispatch(
        self,
        local_path: str,
        server_configs: list[MediaServerConfig | dict[str, Any]] | None = None,
        sync: bool = False,
    ) -> list[SyncResult]:
        """Dispatch targeted refresh requests to all configured servers.

        When sync=False, executes asynchronously in a background thread and returns
        immediately (<500ms) without blocking download completion.
        """
        active_configs: list[MediaServerConfig] = []
        target_list = server_configs if server_configs is not None else self.servers

        for item in target_list:
            cfg = MediaServerConfig.from_dict(item) if isinstance(item, dict) else item
            if cfg.enabled:
                active_configs.append(cfg)

        if not active_configs or not local_path:
            return []

        def _execute_sync() -> list[SyncResult]:
            results: list[SyncResult] = []
            for cfg in active_configs:
                client = create_client(cfg)
                if client:
                    try:
                        res = client.refresh(local_path)
                        results.append(res)
                    except Exception as e:  # noqa: BLE001
                        results.append(
                            SyncResult(
                                server_type=cfg.server_type,
                                server_url=cfg.url,
                                path=local_path,
                                ok=False,
                                error=f"dispatch exception: {e}",
                            )
                        )
            return results

        if sync:
            return _execute_sync()

        # Non-blocking async fire-and-forget: returns immediately (<500ms)
        try:
            executor = self.get_executor()
            executor.submit(_execute_sync)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to submit media server sync task: %s", e)

        return []

    def dispatch_event(
        self,
        event_name: str,
        payload: dict[str, Any],
        sync: bool = False,
    ) -> list[SyncResult]:
        """Handle download event payload and dispatch targeted refresh."""
        if event_name not in ("download.done", "download_done"):
            return []

        path = payload.get("path") or ""
        if not path:
            dl_dir = payload.get("download_dir") or ""
            filename = payload.get("filename") or ""
            if dl_dir and filename:
                path = os.path.join(dl_dir, filename)

        if not path:
            return []

        return self.dispatch(local_path=path, sync=sync)


def dispatch_media_server_refresh(
    file_path: str,
    servers: list[MediaServerConfig | dict[str, Any]] | None = None,
    sync: bool = False,
) -> list[SyncResult]:
    """Convenience entry point for targeted media server library refresh."""
    dispatcher = MediaServerDispatcher(servers=servers)
    return dispatcher.dispatch(local_path=file_path, sync=sync)
