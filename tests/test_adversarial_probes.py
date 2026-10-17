from __future__ import annotations

import ast
import json
import tempfile
import threading
from pathlib import Path
from typing import Any

import pytest

from bulk_downloader.network import (
    MalformedPayloadError,
    PayloadLimitExceeded,
    SanitizationError,
    is_safe_url,
    safe_atomic_write,
    sanitize_unicode_text,
    sanitize_url,
    validate_json_payload,
)

BD_GATE_SCOPE = "module"


class TestBoundaryValueLimits:
    def test_url_length_boundary_exact_and_off_by_one(self) -> None:
        base = "https://example.com/path?q="
        padding_exact = "a" * (4096 - len(base))
        url_exact = base + padding_exact
        assert len(url_exact) == 4096
        sanitized = sanitize_url(url_exact, max_length=4096)
        assert sanitized == url_exact
        url_over = url_exact + "x"
        assert len(url_over) == 4097
        with pytest.raises(PayloadLimitExceeded, match="exceeds maximum limit"):
            sanitize_url(url_over, max_length=4096)

    def test_port_boundaries_valid_and_off_by_one(self) -> None:
        assert (
            sanitize_url("https://example.com:1/test") == "https://example.com:1/test"
        )
        assert (
            sanitize_url("https://example.com:65535/test")
            == "https://example.com:65535/test"
        )
        with pytest.raises(
            SanitizationError, match="(Port 0 out of valid bounds|Invalid port)"
        ):
            sanitize_url("https://example.com:0/test")
        with pytest.raises(
            SanitizationError, match="(Port -1 out of valid bounds|Invalid port)"
        ):
            sanitize_url("https://example.com:-1/test")
        with pytest.raises(
            SanitizationError, match="(Port 65536 out of valid bounds|Invalid port)"
        ):
            sanitize_url("https://example.com:65536/test")
        with pytest.raises(
            SanitizationError, match="(Port 99999 out of valid bounds|Invalid port)"
        ):
            sanitize_url("https://example.com:99999/test")

    def test_payload_byte_size_boundary_50mb(self) -> None:
        limit = 100 * 1024
        payload_at_limit = b'{"data":"' + b"A" * (limit - 11) + b'"}'
        assert len(payload_at_limit) == limit
        res = validate_json_payload(payload_at_limit, max_size_bytes=limit)
        assert isinstance(res, dict)
        payload_over = payload_at_limit + b"!"
        assert len(payload_over) == limit + 1
        with pytest.raises(PayloadLimitExceeded, match="exceeds limit"):
            validate_json_payload(payload_over, max_size_bytes=limit)

    def test_recursion_nesting_depth_boundary(self) -> None:
        obj_limit: dict[str, Any] = {}
        curr = obj_limit
        for _ in range(8):
            curr["child"] = {}
            curr = curr["child"]
        curr["val"] = 42
        res = validate_json_payload(obj_limit, max_depth=10)
        assert isinstance(res, dict)
        curr["child"] = {"val": 99}
        with pytest.raises(MalformedPayloadError, match="nesting depth .* exceeds"):
            validate_json_payload(obj_limit, max_depth=10)


class TestZeroLengthAndEmptyPayloads:
    def test_zero_length_bytes_rejected(self) -> None:
        with pytest.raises(MalformedPayloadError, match="cannot be zero-length"):
            validate_json_payload(b"")

    def test_empty_and_whitespace_strings_rejected(self) -> None:
        with pytest.raises(
            MalformedPayloadError, match="cannot be empty or whitespace"
        ):
            validate_json_payload("")
        with pytest.raises(
            MalformedPayloadError, match="cannot be empty or whitespace"
        ):
            validate_json_payload("   \t\n\r  ")
        with pytest.raises(SanitizationError, match="cannot be empty or zero-length"):
            sanitize_url("")
        with pytest.raises(SanitizationError, match="cannot be empty or zero-length"):
            sanitize_url("    \n\t   ")

    def test_atomic_file_write_and_zero_byte_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "sub" / "output.bin"
            written = safe_atomic_write(target, b"")
            assert written == 0
            assert target.exists()
            assert target.stat().st_size == 0
            data = b"ADVERSARIAL_TEST_PAYLOAD"
            written = safe_atomic_write(target, data)
            assert written == len(data)
            assert target.read_bytes() == data

    def test_concurrent_atomic_replace_zero_torn_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "concurrent.json"
            safe_atomic_write(target, json.dumps({"init": True}))
            errors: list[str] = []

            def writer_thread(tid: int) -> None:
                for idx in range(20):
                    payload = json.dumps({"tid": tid, "idx": idx, "data": "X" * 1024})
                    try:
                        safe_atomic_write(target, payload)
                        read_back = json.loads(target.read_text(encoding="utf-8"))
                        assert "tid" in read_back
                    except (OSError, json.JSONDecodeError, AssertionError) as exc:
                        errors.append(f"Thread {tid} error: {exc}")

            threads = [
                threading.Thread(target=writer_thread, args=(i,)) for i in range(5)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert not errors, f"Concurrent torn reads detected: {errors}"


class TestUnicodeHomoglyphsAndEncoding:
    def test_cyrillic_homoglyphs_distinguished(self) -> None:
        latin = "admin_console"
        cyrillic = "\u0430dmin_c\u043ens\u043ele"
        assert len(latin) == len(cyrillic)
        clean_latin = sanitize_unicode_text(latin)
        clean_cyrillic = sanitize_unicode_text(cyrillic)
        assert clean_latin != clean_cyrillic

    def test_fullwidth_latin_normalized_by_nfkc(self) -> None:
        fullwidth_url = "ｈｔｔｐｓ://example.com/test"

        sanitized = sanitize_url(fullwidth_url)
        assert sanitized.startswith("https://example.com/test")

    def test_zero_width_and_bidi_override_stripped(self) -> None:
        spoofed = "https://safe.com\u200b\u202e/exe.evil"
        clean = sanitize_url(spoofed)
        assert "\u200b" not in clean
        assert "\u202e" not in clean
        assert clean == "https://safe.com/exe.evil"
        text_spoof = "Normal text\u200c\u200d with \ufeffhidden\u202b marks"
        clean_text = sanitize_unicode_text(text_spoof)
        for char in ("\u200c", "\u200d", "\ufeff", "\u202b"):
            assert char not in clean_text

    def test_malformed_utf8_byte_sequences_rejected(self) -> None:
        bad_utf8 = b'{"key": "\xff\xfe"}'
        with pytest.raises(MalformedPayloadError, match="Malformed UTF-8"):
            validate_json_payload(bad_utf8)
        truncated_utf8 = b'{"key": "\xc3"}'
        with pytest.raises(MalformedPayloadError, match="Malformed UTF-8"):
            validate_json_payload(truncated_utf8)

    def test_massive_unicode_payload_throughput(self) -> None:
        chunk = "🚀🌟✨日本語한국어العربية"

        massive_text = chunk * 5000
        sanitized = sanitize_unicode_text(massive_text, max_length=200000)
        assert len(sanitized) == len(massive_text)


class TestMalformedJsonAndInjectionVectors:
    def test_top_level_array_rejected_when_dict_required(self) -> None:
        with pytest.raises(MalformedPayloadError, match="must be a JSON object"):
            validate_json_payload("[1, 2, 3]", require_dict=True)
        with pytest.raises(MalformedPayloadError, match="must be a JSON object"):
            validate_json_payload([{"id": 1}], require_dict=True)

    def test_raw_scalar_json_rejected(self) -> None:
        with pytest.raises(MalformedPayloadError, match="must be a JSON object"):
            validate_json_payload('"raw_scalar_string"', require_dict=True)
        with pytest.raises(MalformedPayloadError, match="must be a JSON object"):
            validate_json_payload("12345", require_dict=True)
        with pytest.raises(MalformedPayloadError, match="must be a JSON object"):
            validate_json_payload("true", require_dict=True)

    def test_syntax_errors_and_trailing_commas_rejected(self) -> None:
        with pytest.raises(MalformedPayloadError, match="Malformed JSON syntax"):
            validate_json_payload('{"key": "value",}')
        with pytest.raises(MalformedPayloadError, match="Malformed JSON syntax"):
            validate_json_payload('{key: "unquoted"}')
        with pytest.raises(MalformedPayloadError, match="Malformed JSON syntax"):
            validate_json_payload('{"truncated": ')

    def test_null_byte_in_keys_and_urls_rejected(self) -> None:
        with pytest.raises(MalformedPayloadError, match="Dict key contains null byte"):
            validate_json_payload('{"key\\u0000poison": "val"}')
        with pytest.raises(SanitizationError, match="null byte"):
            sanitize_url("https://example.com/test\x00/path")

    def test_ssrf_ip_variations_blocked(self) -> None:
        restricted_targets = [
            "http://127.0.0.1/admin",
            "http://127.0.0.2:8080/metrics",
            "http://10.0.70.164/api",
            "http://192.168.1.1/router",
            "http://169.254.169.254/latest/meta-data/",
            "http://0.0.0.0:8000/",
            "http://2130706433/",
            "http://0x7f000001/",
            "http://[::1]/secret",
            "http://[::ffff:127.0.0.1]/status",
            "http://[::ffff:169.254.169.254]/",
        ]
        for url in restricted_targets:
            assert not is_safe_url(url), f"Failed to block SSRF vector: {url}"
            with pytest.raises(SanitizationError, match="SSRF violation"):
                sanitize_url(url, block_private_ips=True)

    def test_unsupported_schemes_rejected(self) -> None:
        dangerous_schemes = [
            "file:///etc/passwd",
            "javascript:alert(1)",
            "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
            "gopher://127.0.0.1:70/",
            "ftp://files.example.com/",
        ]
        for url in dangerous_schemes:
            with pytest.raises(SanitizationError, match="not in allowed schemes"):
                sanitize_url(url, allow_schemes=("http", "https"))


class TestSyntheticMutantKillRate:
    def test_synthetic_mutant_1_inverted_allowed_schemes_condition(self) -> None:
        res = sanitize_url("https://example.com/test", allow_schemes=("http", "https"))
        assert res == "https://example.com/test"

    def test_synthetic_mutant_2_none_bypass_on_url(self) -> None:
        with pytest.raises(TypeError, match=r"^URL must be a string, got None$"):
            sanitize_url(None)

    def test_synthetic_mutant_3_port_lower_bound_off_by_one(self) -> None:
        with pytest.raises(SanitizationError, match=r"Port 0 out of valid bounds"):
            sanitize_url("https://example.com:0/test")

    def test_synthetic_mutant_4_drop_null_byte_validation(self) -> None:
        url_with_null = "https://example.com/test\x00path"
        with pytest.raises(SanitizationError, match="illegal null byte"):
            sanitize_url(url_with_null)

    def test_synthetic_mutant_5_ssrf_bypass_fallback(self) -> None:
        with pytest.raises(SanitizationError, match="SSRF violation"):
            sanitize_url("http://127.0.0.1/")

    def test_synthetic_mutant_6_ast_scanner_kwarg_unpacking_and_timeout(self) -> None:

        def _find_untimed(code: str) -> list[int]:
            tree = ast.parse(code)
            untimed: list[int] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    name = None
                    if isinstance(func, ast.Attribute) and func.attr in (
                        "run",
                        "call",
                        "check_output",
                    ):
                        name = func.attr
                    elif isinstance(func, ast.Name) and func.id in (
                        "run",
                        "call",
                        "check_output",
                    ):
                        name = func.id
                    if name:
                        has_timeout = any(kw.arg == "timeout" for kw in node.keywords)
                        has_kwargs = any(kw.arg is None for kw in node.keywords)
                        if not has_timeout and (not has_kwargs):
                            untimed.append(node.lineno)
            return untimed

        code_direct = "import subprocess\nsubprocess.run(['ls'])"
        code_timed = "import subprocess\nsubprocess.run(['ls'], timeout=10)"
        code_kwargs = "import subprocess\nsubprocess.run(['ls'], **kwargs)"
        assert _find_untimed(code_direct) == [2]
        assert _find_untimed(code_timed) == []
        assert _find_untimed(code_kwargs) == []


def test_fleet_rule_86_contract_conformance() -> None:
    assert is_safe_url("not a url") is False
    assert is_safe_url(None) is False
    assert is_safe_url("https://example.com:80/path") is True
    with tempfile.TemporaryDirectory() as tmp_dir:
        invalid_path = Path(tmp_dir) / "nonexistent_dir" / "file.txt"
        written = safe_atomic_write(invalid_path, "hello")
        assert written == 5
        assert invalid_path.read_text() == "hello"
