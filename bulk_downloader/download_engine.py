from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self
import httpx

from .network.http_client import (
    BackoffConfig,
    HttpClient,
    PoolConfig,
    TimeoutConfig,
    compute_full_jitter,
    parse_retry_after,
)
from .network.url_sanitizer import validate_url


@dataclasses.dataclass
class DownloadResult:
    url: str
    dest_path: str
    total_bytes: int = 0
    status_code: int = 0
    elapsed_s: float = 0.0
    retries_taken: int = 0
    success: bool = False
    error: str = ""

class DownloadEngine:

    def __init__(self, timeout_config: TimeoutConfig | None=None, pool_config: PoolConfig | None=None, backoff_config: BackoffConfig | None=None, sleep_fn: Callable[[float], None]=time.sleep, rng: Any=None):
        self.timeout_config = timeout_config or TimeoutConfig()
        self.pool_config = pool_config or PoolConfig()
        self.backoff_config = backoff_config or BackoffConfig()
        self._sleep_fn = sleep_fn
        self._rng = rng
        self._client = HttpClient(timeout_config=self.timeout_config, pool_config=self.pool_config, backoff_config=self.backoff_config, sleep_fn=self._sleep_fn, rng=self._rng)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def download_file(self, url: str, dest_path: str | Path, chunk_size: int=65536, allowed_subnets: list[str] | None=None) -> DownloadResult:
        t0 = time.time()
        retries_taken = 0
        clean_url = validate_url(url, allowed_subnets=allowed_subnets)
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part_path = dest.with_suffix(dest.suffix + f".part-{int(t0)}")
        attempt = 0
        while True:
            try:
                with self._client._client.stream("GET", clean_url, timeout=httpx.Timeout(connect=self.timeout_config.connect, read=self.timeout_config.read, write=self.timeout_config.write, pool=self.timeout_config.pool), follow_redirects=True) as resp:
                    if resp.status_code in self.backoff_config.retryable_codes and attempt < self.backoff_config.max_retries:
                        attempt += 1
                        retries_taken += 1
                        retry_after = parse_retry_after(resp.headers.get("Retry-After"))
                        delay = compute_full_jitter(attempt=attempt, base_delay=self.backoff_config.base_delay_s, max_delay=self.backoff_config.max_delay_s, factor=self.backoff_config.factor, retry_after=retry_after, rng=self._rng)
                        self._sleep_fn(delay)
                        continue
                    if resp.status_code >= 400:
                        return DownloadResult(url=clean_url, dest_path=str(dest), status_code=resp.status_code, elapsed_s=time.time() - t0, retries_taken=retries_taken, success=False, error=f"HTTP {resp.status_code}")
                    total_bytes = 0
                    with open(part_path, "wb") as f:
                        for chunk in resp.iter_bytes(chunk_size=chunk_size):
                            if chunk:
                                f.write(chunk)
                                total_bytes += len(chunk)
                    part_path.replace(dest)
                    return DownloadResult(url=clean_url, dest_path=str(dest), total_bytes=total_bytes, status_code=resp.status_code, elapsed_s=time.time() - t0, retries_taken=retries_taken, success=True)
            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as exc:
                if attempt < self.backoff_config.max_retries:
                    attempt += 1
                    retries_taken += 1
                    delay = compute_full_jitter(attempt=attempt, base_delay=self.backoff_config.base_delay_s, max_delay=self.backoff_config.max_delay_s, factor=self.backoff_config.factor, rng=self._rng)
                    self._sleep_fn(delay)
                    continue
                if part_path.exists():
                    part_path.unlink(missing_ok=True)
                return DownloadResult(url=clean_url, dest_path=str(dest), elapsed_s=time.time() - t0, retries_taken=retries_taken, success=False, error=f"Transport error after {attempt} retries: {exc}")
            except (OSError, RuntimeError, ValueError) as exc:
                if part_path.exists():
                    part_path.unlink(missing_ok=True)
                return DownloadResult(url=clean_url, dest_path=str(dest), elapsed_s=time.time() - t0, retries_taken=retries_taken, success=False, error=str(exc))

    def download_stream(self, url: str, chunk_size: int=65536, allowed_subnets: list[str] | None=None) -> Iterator[bytes]:
        clean_url = validate_url(url, allowed_subnets=allowed_subnets)
        with self._client._client.stream("GET", clean_url, timeout=httpx.Timeout(connect=self.timeout_config.connect, read=self.timeout_config.read, write=self.timeout_config.write, pool=self.timeout_config.pool), follow_redirects=True) as resp:
            resp.raise_for_status()
            yield from resp.iter_bytes(chunk_size=chunk_size)
