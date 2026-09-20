#!/usr/bin/env python3
"""tools/local_wheelhouse.py -- Local Wheelhouse & Package Cache Harness.

Row 861: Provide an approved local wheelhouse/cache endpoint for offline-capable virtualenv creation.
- Strict isolation: Do not add runtime public-CDN dependency.
- Rejects remote HTTP/HTTPS package indices and public CDN endpoints.
- Supports instant sub-second wheel installation into isolated virtualenvs.
- Employs SocketCensus to verify and assert zero external network connections.
"""
from __future__ import annotations

import argparse
import base64
import dataclasses
import glob
import hashlib
import ipaddress
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import venv
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

# Common public CDN / repository domains that must NEVER be used at runtime
FORBIDDEN_CDN_PATTERNS = [
    r"pypi\.org",
    r"pythonhosted\.org",
    r"jsdelivr\.net",
    r"unpkg\.com",
    r"cdnjs\.cloudflare\.com",
    r"cloudflare\.com",
    r"fastly\.net",
    r"akamaihd\.net",
    r"githubusercontent\.com",
    r"aws\.amazon\.com",
    r"azureedge\.net",
]

_WHEEL_FILENAME_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)-(?P<ver>[A-Za-z0-9_.]+)(?:-(?P<build>\d[A-Za-z0-9_.]*))?"
    r"-(?P<py>[A-Za-z0-9_.]+)-(?P<abi>[A-Za-z0-9_.]+)-(?P<plat>[A-Za-z0-9_.]+)\.whl$"
)


def normalize_package_name(name: str) -> str:
    """Normalize package name per PEP 503."""
    return re.sub(r"[-_.]+", "-", name).lower()


class WheelhouseError(Exception):
    """Base exception for local wheelhouse errors."""
    pass


class SecurityViolationError(WheelhouseError):
    """Raised when an unapproved external or public-CDN endpoint is targeted."""
    pass


class PackageNotFoundError(WheelhouseError):
    """Raised when a requested wheel package is not present in the local cache."""
    pass


class IntegrityError(WheelhouseError):
    """Raised when a wheel archive fails checksum or zip validation."""
    pass


class NetworkLeakError(WheelhouseError):
    """Raised when an external non-loopback connection attempt is detected."""
    pass


@dataclass
class WheelInfo:
    """Metadata describing a cached wheel in the local wheelhouse."""
    name: str                # normalized package name
    raw_name: str            # raw name from filename
    version: str             # package version
    filename: str            # wheel filename
    path: Path               # absolute path to .whl
    sha256: str              # hex sha256 checksum
    size_bytes: int          # file size in bytes

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "raw_name": self.raw_name,
            "version": self.version,
            "filename": self.filename,
            "path": str(self.path),
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass
class VirtualenvResult:
    """Outcome of creating and provisioning an isolated virtual environment."""
    venv_path: Path
    python_path: Path
    installed_packages: List[str]
    duration_seconds: float
    returncode: int
    stdout: str
    stderr: str

    @property
    def is_success(self) -> bool:
        return self.returncode == 0


class SocketCensus:
    """Context manager intercepting socket.socket.connect to record and enforce network isolation."""

    def __init__(self, block_external: bool = True) -> None:
        self.block_external = block_external
        self.external_connections: List[Tuple[str, int]] = []
        self.loopback_connections: List[Tuple[str, int]] = []
        self._orig_connect = None

    def __enter__(self) -> SocketCensus:
        self.external_connections.clear()
        self.loopback_connections.clear()
        self._orig_connect = socket.socket.connect

        def hooked_connect(sock_self: socket.socket, address: Any) -> Any:
            host = ""
            port = 0
            if isinstance(address, tuple) and len(address) >= 2:
                host = str(address[0])
                port = int(address[1])
            else:
                host = str(address)

            is_loopback = False
            try:
                ip_obj = ipaddress.ip_address(host)
                is_loopback = ip_obj.is_loopback
            except ValueError:
                if host.lower() in ("localhost", "127.0.0.1", "::1"):
                    is_loopback = True

            if is_loopback:
                self.loopback_connections.append((host, port))
                return self._orig_connect(sock_self, address)
            else:
                self.external_connections.append((host, port))
                if self.block_external:
                    raise NetworkLeakError(
                        f"Zero-network invariant violated: outbound connection to {host}:{port} blocked by SocketCensus"
                    )
                return self._orig_connect(sock_self, address)

        socket.socket.connect = hooked_connect
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._orig_connect is not None:
            socket.socket.connect = self._orig_connect
            self._orig_connect = None

    @property
    def has_external_connections(self) -> bool:
        return len(self.external_connections) > 0


class LocalWheelhouse:
    """Local wheelhouse & package cache harness for hermetic, offline-capable package installs."""

    def __init__(self, root: Optional[Union[str, Path]] = None) -> None:
        if root is None:
            configured = os.environ.get("BD_WHEELHOUSE_DIR")
            if configured:
                self.root = Path(configured).resolve()
            else:
                self.root = Path(tempfile.gettempdir()) / "bd_local_wheelhouse"
        else:
            self.root = Path(root).resolve()

        self.root.mkdir(parents=True, exist_ok=True)
        self._index: Dict[str, List[WheelInfo]] = {}
        self.refresh_index()

    @property
    def endpoint_url(self) -> str:
        """Return the canonical approved file:// endpoint for this wheelhouse."""
        return f"file://{self.root.resolve()}"

    @property
    def find_links_arg(self) -> str:
        """Return pip-compatible --find-links argument."""
        return f"--find-links {self.root.resolve()}"

    @staticmethod
    def validate_endpoint(endpoint: str) -> None:
        """Ensure endpoint is strictly local and never targets a public CDN or remote index."""
        if not endpoint:
            raise SecurityViolationError("Endpoint URL cannot be empty")

        parsed = urllib.parse.urlparse(endpoint)
        if parsed.scheme in ("http", "https", "ftp"):
            raise SecurityViolationError(
                f"Public/remote schemes ({parsed.scheme}://) are forbidden. Endpoint must be local file:// or path: {endpoint}"
            )

        for pattern in FORBIDDEN_CDN_PATTERNS:
            if re.search(pattern, endpoint, re.IGNORECASE):
                raise SecurityViolationError(
                    f"Forbidden public CDN dependency detected ({pattern}): {endpoint}"
                )

        if parsed.scheme == "file":
            local_path = Path(urllib.parse.unquote(parsed.path))
            if not local_path.is_absolute():
                raise SecurityViolationError(f"file:// endpoint must use an absolute path: {endpoint}")
        elif parsed.scheme == "":
            pass
        else:
            raise SecurityViolationError(f"Unsupported endpoint scheme '{parsed.scheme}': {endpoint}")

    def refresh_index(self) -> Dict[str, List[WheelInfo]]:
        """Index all .whl files present in the wheelhouse root directory."""
        self._index.clear()
        if not self.root.is_dir():
            return self._index

        for whl_path in sorted(self.root.glob("*.whl")):
            info = self._parse_wheel_file(whl_path)
            if info:
                self._index.setdefault(info.name, []).append(info)

        return self._index

    def _parse_wheel_file(self, path: Path) -> Optional[WheelInfo]:
        """Inspect and parse a wheel file, validating zip integrity and PEP 427 conventions."""
        filename = path.name
        m = _WHEEL_FILENAME_RE.match(filename)
        if not m:
            return None

        raw_name = m.group("name")
        version = m.group("ver")
        norm_name = normalize_package_name(raw_name)

        try:
            with zipfile.ZipFile(path, "r") as zf:
                if zf.testzip() is not None:
                    return None
        except Exception:
            return None

        # Compute sha256 and size
        hasher = hashlib.sha256()
        try:
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    hasher.update(chunk)
            sha256 = hasher.hexdigest()
            size = path.stat().st_size
        except OSError:
            return None

        return WheelInfo(
            name=norm_name,
            raw_name=raw_name,
            version=version,
            filename=filename,
            path=path,
            sha256=sha256,
            size_bytes=size,
        )

    def find_wheel(self, name: str, version: Optional[str] = None) -> Optional[WheelInfo]:
        """Find a cached wheel by package name and optional version."""
        norm_name = normalize_package_name(name)
        wheels = self._index.get(norm_name, [])
        if not wheels:
            self.refresh_index()
            wheels = self._index.get(norm_name, [])
            if not wheels:
                return None

        if version is not None:
            for w in wheels:
                if w.version == version:
                    return w
            return None

        # Return latest / highest version (last in sorted list)
        return wheels[-1]

    def add_wheel(self, wheel_path: Union[str, Path], verify_hash: bool = True) -> WheelInfo:
        """Add an existing wheel file to the wheelhouse."""
        src = Path(wheel_path).resolve()
        if not src.is_file():
            raise FileNotFoundError(f"Source wheel not found: {src}")

        dest = self.root / src.name
        if src != dest:
            shutil.copy2(src, dest)

        info = self._parse_wheel_file(dest)
        if not info:
            if dest.exists():
                dest.unlink()
            raise IntegrityError(f"Corrupt or invalid wheel archive: {src.name}")

        self._index.setdefault(info.name, []).append(info)
        return info

    def create_fixture_wheel(
        self,
        name: str,
        version: str = "1.0.0",
        py_content: Optional[str] = None,
        dest_dir: Optional[Path] = None,
    ) -> WheelInfo:
        """Create a valid PEP 427 pure-python wheel without invoking external build tools."""
        norm_name = normalize_package_name(name)
        raw_name = name.replace("-", "_")
        filename = f"{raw_name}-{version}-py3-none-any.whl"
        target_dir = dest_dir if dest_dir is not None else self.root
        target_dir.mkdir(parents=True, exist_ok=True)
        whl_path = target_dir / filename

        dist_info = f"{raw_name}-{version}.dist-info"
        if py_content is None:
            py_content = f'"""Synthetic offline package {name}."""\nVERSION = "{version}"\ndef get_status():\n    return "offline-ok"\n'

        py_bytes = py_content.encode("utf-8")
        meta_bytes = (
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\nSummary: Synthetic offline fixture wheel\n"
        ).encode("utf-8")
        wheel_bytes = b"Wheel-Version: 1.0\nGenerator: local_wheelhouse\nRoot-Is-Purelib: true\nTag: py3-none-any\n"

        def _b64_hash(b: bytes) -> str:
            h = hashlib.sha256(b).digest()
            return "sha256=" + base64.urlsafe_b64encode(h).decode("ascii").rstrip("=")

        record_lines = [
            f"{raw_name}.py,{_b64_hash(py_bytes)},{len(py_bytes)}",
            f"{dist_info}/METADATA,{_b64_hash(meta_bytes)},{len(meta_bytes)}",
            f"{dist_info}/WHEEL,{_b64_hash(wheel_bytes)},{len(wheel_bytes)}",
            f"{dist_info}/RECORD,,",
        ]
        record_bytes = "\n".join(record_lines).encode("utf-8")

        with zipfile.ZipFile(whl_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{raw_name}.py", py_bytes)
            zf.writestr(f"{dist_info}/METADATA", meta_bytes)
            zf.writestr(f"{dist_info}/WHEEL", wheel_bytes)
            zf.writestr(f"{dist_info}/RECORD", record_bytes)

        info = self._parse_wheel_file(whl_path)
        if not info:
            raise IntegrityError(f"Failed to verify generated fixture wheel: {whl_path}")

        if target_dir == self.root:
            self._index.setdefault(info.name, []).append(info)
        return info

    def install_wheel_direct(self, wheel: Union[WheelInfo, Path, str], site_packages_dir: Union[Path, str]) -> float:
        """Extract a wheel directly into site-packages in sub-second time without network."""
        t0 = time.perf_counter()
        whl_path = wheel.path if isinstance(wheel, WheelInfo) else Path(wheel)
        sp = Path(site_packages_dir)
        if not sp.is_dir():
            raise FileNotFoundError(f"Target site-packages directory does not exist: {sp}")

        with zipfile.ZipFile(whl_path, "r") as zf:
            zf.extractall(sp)

        duration = time.perf_counter() - t0
        return duration

    def create_isolated_virtualenv(
        self,
        venv_path: Union[str, Path],
        packages: Optional[List[str]] = None,
        install_method: str = "direct",
        internet_disabled: bool = True,
        with_pip: bool = False,
    ) -> VirtualenvResult:
        """Create a hermetic virtualenv and install cached packages under strict network isolation."""
        target_path = Path(venv_path).resolve()
        packages = packages or []
        t0 = time.perf_counter()

        # Enforce zero-network invariant
        census = SocketCensus(block_external=internet_disabled)
        with census:
            # 1. Create virtualenv
            builder = venv.EnvBuilder(with_pip=with_pip, clear=True, symlinks=(os.name != "nt"))
            builder.create(target_path)

            py_executable = target_path / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
            if not py_executable.exists():
                raise FileNotFoundError(f"Virtualenv python executable missing: {py_executable}")

            # Locate site-packages
            sp_matches = list(target_path.glob("lib/python*/site-packages"))
            if not sp_matches:
                # Windows fallback
                sp_matches = list(target_path.glob("Lib/site-packages"))
            if not sp_matches:
                raise FileNotFoundError(f"Could not locate site-packages in created venv: {target_path}")
            site_packages = sp_matches[0]

            installed: List[str] = []
            stdout_acc: List[str] = []
            stderr_acc: List[str] = []
            returncode = 0

            # 2. Install packages
            if packages:
                if install_method == "direct":
                    for pkg in packages:
                        w = self.find_wheel(pkg)
                        if not w:
                            raise PackageNotFoundError(
                                f"Package '{pkg}' not found in local wheelhouse cache at {self.root}"
                            )
                        self.install_wheel_direct(w, site_packages)
                        installed.append(f"{w.name}=={w.version}")
                        stdout_acc.append(f"Successfully installed {w.name}-{w.version} from {w.filename} (direct)")
                elif install_method == "pip":
                    if not with_pip:
                        raise ValueError("install_method='pip' requires with_pip=True")

                    pip_exe = target_path / ("Scripts" if os.name == "nt" else "bin") / ("pip.exe" if os.name == "nt" else "pip")
                    env = dict(os.environ)
                    if internet_disabled:
                        env["PIP_NO_INDEX"] = "1"
                        env["PIP_FIND_LINKS"] = str(self.root)
                        env["http_proxy"] = "http://127.0.0.1:0"
                        env["https_proxy"] = "http://127.0.0.1:0"
                        env["HTTP_PROXY"] = "http://127.0.0.1:0"
                        env["HTTPS_PROXY"] = "http://127.0.0.1:0"

                    cmd = [
                        str(pip_exe),
                        "install",
                        "--no-index",
                        "--find-links",
                        str(self.root),
                        *packages,
                    ]
                    run = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120)
                    stdout_acc.append(run.stdout)
                    stderr_acc.append(run.stderr)
                    returncode = run.returncode
                    if returncode == 0:
                        installed.extend(packages)
                else:
                    raise ValueError(f"Unknown install_method: {install_method}")

        duration = time.perf_counter() - t0
        return VirtualenvResult(
            venv_path=target_path,
            python_path=py_executable,
            installed_packages=installed,
            duration_seconds=duration,
            returncode=returncode,
            stdout="\n".join(stdout_acc),
            stderr="\n".join(stderr_acc),
        )


PackageCacheHarness = LocalWheelhouse


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Local wheelhouse & package cache harness (Row 861)."
    )
    parser.add_argument("--root", help="Path to wheelhouse root directory.")
    parser.add_argument("--endpoint", action="store_true", help="Print approved local endpoint URL.")
    parser.add_argument("--check-endpoint", help="Validate a candidate endpoint for security.")
    parser.add_argument("--list", action="store_true", help="List all cached wheels.")
    parser.add_argument("--create-venv", help="Create an offline virtualenv at path.")
    parser.add_argument("--install", nargs="+", help="Package names to install into created venv.")
    parser.add_argument("--method", choices=["direct", "pip"], default="direct", help="Install method.")

    args = parser.parse_args(argv)

    if args.check_endpoint:
        try:
            LocalWheelhouse.validate_endpoint(args.check_endpoint)
            print(f"APPROVED: {args.check_endpoint}")
            return 0
        except SecurityViolationError as e:
            print(f"REJECTED: {e}", file=sys.stderr)
            return 1

    wheelhouse = LocalWheelhouse(root=args.root)

    if args.endpoint:
        print(wheelhouse.endpoint_url)
        return 0

    if args.list:
        index = wheelhouse.refresh_index()
        total_wheels = sum(len(v) for v in index.values())
        print(f"Local Wheelhouse at {wheelhouse.root} ({total_wheels} wheel(s), {len(index)} package(s)):")
        for pkg, wheels in sorted(index.items()):
            for w in wheels:
                print(f"  {w.name}=={w.version} -> {w.filename} ({w.size_bytes} bytes, sha256={w.sha256[:12]}...)")
        return 0

    if args.create_venv:
        res = wheelhouse.create_isolated_virtualenv(
            venv_path=args.create_venv,
            packages=args.install or [],
            install_method=args.method,
            internet_disabled=True,
            with_pip=(args.method == "pip"),
        )
        print(f"VENV CREATED in {res.duration_seconds:.4f}s: {res.venv_path}")
        print(f"Installed: {res.installed_packages}")
        return res.returncode

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
