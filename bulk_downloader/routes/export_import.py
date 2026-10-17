from __future__ import annotations

import ipaddress
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request

from ..network.url_sanitizer import (
    SSRFBlocked,
    _classify_ip,
    sanitize_url,
    validate_url,
)
from ..urllib_ssrf import PinnedUrlOpener

export_import_bp = Blueprint("export_import", __name__)

def run_argv_command(argv: list[str] | tuple[str, ...], cwd: str | Path | None=None, timeout: float=30.0, env: dict[str, str] | None=None) -> subprocess.CompletedProcess:
    if isinstance(argv, str):
        raise TypeError("Structured parameter invocation violation: command must be an argv list, not a string")
    if not isinstance(argv, (list, tuple)) or not argv:
        raise ValueError("argv must be a non-empty sequence of arguments")
    for i, arg in enumerate(argv):
        if not isinstance(arg, str):
            raise TypeError(f"argv element {i} must be str, got {type(arg).__name__}")
    return subprocess.run(list(argv), shell=False, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout, cwd=str(cwd) if cwd else None, env=env, check=False)

def safe_import_from_url(url: str, allowed_subnets: list[str] | None=None, timeout: float=30.0) -> dict[str, Any]:
    clean_url = sanitize_url(url, allowed_subnets=allowed_subnets)
    req = urllib.request.Request(clean_url, headers={"User-Agent": "BulkDownloader-Import/1.0"})
    opener = PinnedUrlOpener(lambda addr, host: is_safe_ip(addr, allowed_subnets=allowed_subnets))
    with opener.open(req, timeout=timeout) as resp:
        content_type = resp.headers.get("Content-Type", "")
        data = resp.read()
        return {"ok": True, "url": clean_url, "status_code": resp.status, "content_type": content_type, "bytes_read": len(data), "payload": data.decode("utf-8", errors="replace")}

def is_safe_ip(addr: str, allowed_subnets: list[str] | None=None) -> bool:
    try:
        ip_obj = ipaddress.ip_address(addr)
        allowed, _ = _classify_ip(ip_obj, allowed_subnets=allowed_subnets)
        return allowed
    except ValueError:
        return False

@export_import_bp.route("/api/export_import/validate_url", methods=["POST"])
def api_validate_url():
    body = request.json or {}
    url = body.get("url", "")
    allowed_subnets = body.get("allowed_subnets")
    if not url:
        return (jsonify({"ok": False, "error": "url is required"}), 400)
    try:
        clean = validate_url(url, allowed_subnets=allowed_subnets)
        return jsonify({"ok": True, "validated_url": clean})
    except SSRFBlocked as exc:
        return (jsonify({"ok": False, "error": str(exc)}), 400)
    except (OSError, RuntimeError, ValueError) as exc:
        return (jsonify({"ok": False, "error": str(exc)}), 500)

@export_import_bp.route("/api/export_import/import_url", methods=["POST"])
def api_import_url():
    body = request.json or {}
    url = body.get("url", "")
    allowed_subnets = body.get("allowed_subnets")
    if not url:
        return (jsonify({"ok": False, "error": "url is required"}), 400)
    try:
        result = safe_import_from_url(url, allowed_subnets=allowed_subnets)
        return jsonify(result)
    except SSRFBlocked as exc:
        return (jsonify({"ok": False, "error": f"Import blocked by SSRF policy: {exc}"}), 400)
    except (OSError, RuntimeError, ValueError) as exc:
        return (jsonify({"ok": False, "error": str(exc)}), 500)

@export_import_bp.route("/api/export_import/run_conversion", methods=["POST"])
def api_run_conversion():
    body = request.json or {}
    argv = body.get("argv")
    timeout = float(body.get("timeout", 30.0))
    if not argv or not isinstance(argv, list):
        return (jsonify({"ok": False, "error": "argv list of string arguments required"}), 400)
    try:
        proc = run_argv_command(argv, timeout=timeout)
        return jsonify({"ok": proc.returncode == 0, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr})
    except (TypeError, ValueError) as exc:
        return (jsonify({"ok": False, "error": str(exc)}), 400)
    except subprocess.TimeoutExpired:
        return (jsonify({"ok": False, "error": f"Command timed out after {timeout}s"}), 504)
    except (OSError, RuntimeError) as exc:
        return (jsonify({"ok": False, "error": str(exc)}), 500)
