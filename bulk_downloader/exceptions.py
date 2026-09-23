"""Row 1023: Unified Error Hierarchy & Enterprise Exception Taxonomy Standardization (ExceptionTaxonomy).

Provides a centralized, standardized enterprise error hierarchy, structured error codes,
rich metadata serialization, and automatic exception classification.

Live path: ``SiteRunner._publish_worker_exception`` (every exception that escapes a
worker attempt, except a config-selector SyntaxError, which goes to needs_review)
renders ``friendly_error.format_standardized_error``, which classifies through
``classify_exception``, so the published ``worker error: [<code>] <message>`` carries
the taxonomy code. ``retryable`` is classification metadata; job retry scheduling is
still decided by ``TelemetryMixin._classify_error`` on the message text.
"""
from __future__ import annotations

import socket
import sqlite3
from typing import Any


class BulkDownloaderError(Exception):
    """Enterprise root base exception for all BulkDownloader domain errors."""

    default_code: str = "BD-GEN-000"
    default_status: int = 500
    default_retryable: bool = False

    def __init__(
        self,
        message: str,
        error_code: str | None = None,
        retryable: bool | None = None,
        details: dict[str, Any] | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code or self.default_code
        self.retryable = self.default_retryable if retryable is None else retryable
        self.details = details or {}
        self.http_status = self.default_status if http_status is None else http_status

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_type": self.__class__.__name__,
            "error_code": self.error_code,
            "message": self.message,
            "retryable": self.retryable,
            "http_status": self.http_status,
            "details": self.details,
        }


# Network Category
class NetworkError(BulkDownloaderError):
    default_code = "BD-NET-001"
    default_status = 502
    default_retryable = True


class ConnectionTimeoutError(NetworkError):
    default_code = "BD-NET-002"
    default_status = 504


class DNSResolutionError(NetworkError):
    default_code = "BD-NET-003"
    default_status = 502


class CircuitBreakerOpenError(NetworkError):
    default_code = "BD-NET-004"
    default_status = 503
    default_retryable = False


# Authentication Category
class AuthenticationError(BulkDownloaderError):
    default_code = "BD-AUTH-001"
    default_status = 401
    default_retryable = False


class InvalidCredentialsError(AuthenticationError):
    default_code = "BD-AUTH-002"
    default_status = 401


class SessionExpiredError(AuthenticationError):
    default_code = "BD-AUTH-003"
    default_status = 401
    default_retryable = True


class BotDetectionChallengeError(AuthenticationError):
    default_code = "BD-AUTH-004"
    default_status = 403


# Storage Category
class StorageError(BulkDownloaderError):
    default_code = "BD-STORE-001"
    default_status = 500
    default_retryable = False


class ObjectNotFoundError(StorageError):
    default_code = "BD-STORE-002"
    default_status = 404


class StorageQuotaExceededError(StorageError):
    default_code = "BD-STORE-003"
    default_status = 507


class IntegrityVerificationError(StorageError):
    default_code = "BD-STORE-004"
    default_status = 500


# Extraction Category
class ExtractionError(BulkDownloaderError):
    default_code = "BD-EXTR-001"
    default_status = 422
    default_retryable = False


class UnsupportedFormatError(ExtractionError):
    default_code = "BD-EXTR-002"


class MediaUnavailableError(ExtractionError):
    default_code = "BD-EXTR-003"
    default_status = 404


# Configuration Category
class ConfigurationError(BulkDownloaderError):
    default_code = "BD-CFG-001"
    default_status = 400
    default_retryable = False


class ValidationError(ConfigurationError):
    default_code = "BD-CFG-002"


# SQLite primary result codes for lock contention: SQLITE_BUSY (5), another
# connection's write lock held past the busy timeout, and SQLITE_LOCKED (6), a
# conflicting lock inside the same connection. Both clear when the holder is done,
# so the same operation can succeed when retried; every other SQLite failure (no
# such table, constraint, corrupt file, disk I/O) is not transient.
_SQLITE_LOCK_CODES = frozenset({5, 6})

# Fallback for interpreters that do not populate sqlite_errorcode (and for errors
# built by hand): SQLite's own wording for exactly those two codes.
_SQLITE_LOCK_TEXT = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
)


def _is_sqlite_lock(exc: sqlite3.Error) -> bool:
    """Is this a transient SQLite lock (busy/locked) rather than a lasting fault?"""
    code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(code, int):
        # Extended result codes carry the primary code in the low 8 bits
        # (SQLITE_BUSY_SNAPSHOT is 517, not 5).
        return (code & 0xFF) in _SQLITE_LOCK_CODES
    text = str(exc).lower()
    return any(sig in text for sig in _SQLITE_LOCK_TEXT)


def classify_exception(exc: Exception) -> BulkDownloaderError:
    """Classify an exception into the BulkDownloaderError hierarchy.

    A taxonomy instance is returned unchanged: its code, message and caller
    overrides are the classification. Otherwise the exception TYPE decides;
    message words are only a last-resort hint once every type branch has
    failed, and an exception nothing models stays generic (BD-GEN-000) and
    non-retryable.
    """
    if isinstance(exc, BulkDownloaderError):
        return exc

    msg = str(exc)
    details = {"original_type": type(exc).__name__}
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return ConfigurationError(msg, details=details)
    if isinstance(exc, socket.gaierror):
        return DNSResolutionError(msg, details=details)
    if isinstance(exc, TimeoutError):
        return ConnectionTimeoutError(msg, details=details)
    if isinstance(exc, ConnectionRefusedError):
        return NetworkError(msg, details=details)
    if isinstance(exc, sqlite3.OperationalError) and _is_sqlite_lock(exc):
        return StorageError(msg, retryable=True, details=details)
    if isinstance(exc, (FileNotFoundError, PermissionError, sqlite3.Error)):
        return StorageError(msg, details=details)

    if "timeout" in msg.lower():
        return ConnectionTimeoutError(msg, details=details)
    if "connection refused" in msg.lower():
        return NetworkError(msg, details=details)
    return BulkDownloaderError(msg, details=details)
