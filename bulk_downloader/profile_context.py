"""Row 1036: Ergonomic Multi-Environment Profile Context Switcher.

Provides structured, persistent management and context switching between
multiple operational environments (development, staging, production, test, local):
1. EnvironmentProfile dataclass modeling environment endpoints, database
   references, environment variables, and runtime configuration settings.
2. ProfileContextSwitcher managing profile persistence, switching, validation,
   cloning, diffing, and export/import. Every mutation is validated first and
   rolled back in memory if it cannot be persisted.
3. Scoped context switching via `with switcher.temporary_context(profile): ...`
   restoring the active profile and exactly the env vars the profile set.
4. Thread-safe operations with zero new `BD_` environment variables required.

Nothing touches the filesystem at import time: the default store path is
resolved from HOME when a switcher is built, and a missing store is only
created by the first mutation.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
from dataclasses import asdict, dataclass, field, fields, replace
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Dict, Generator, Iterator, List, Optional, Union
from urllib.parse import urlparse

log = logging.getLogger(__name__)

_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# Fields stamped by the switcher; callers may not rewrite them via update/clone.
_IMMUTABLE_FIELDS = frozenset({"name", "created_at", "updated_at"})


def default_profiles_file() -> Path:
    """Default store path, resolved from HOME at call time.

    It lives in the app's own ~/.config/bulk-downloader/ namespace (where
    widgets.json and vpn/ live on Linux/macOS), not in a namespace of its own.
    """
    return Path.home() / ".config" / "bulk-downloader" / "profiles.json"


class ProfileContextError(Exception):
    """Base exception for profile context operations."""


class ProfileNotFoundError(ProfileContextError):
    """Raised when a requested profile does not exist."""


class ProfileConflictError(ProfileContextError):
    """Raised when creating a profile with an existing name."""


@dataclass
class EnvironmentProfile:
    """Represents a named operational environment configuration."""

    name: str
    description: str = ""
    api_base_url: str = "http://localhost:8080"
    db_path: Optional[str] = None
    env_vars: Dict[str, str] = field(default_factory=dict)
    settings: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """Convert profile to serializable dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> EnvironmentProfile:
        """Instantiate and validate a profile from a dictionary."""
        if not isinstance(data, dict):
            raise ProfileContextError(f"Profile entry must be an object, got {type(data).__name__}")
        unknown = set(data) - _FIELD_NAMES
        if unknown:
            raise ProfileContextError(f"Unknown profile field(s): {', '.join(sorted(unknown))}")
        if "name" not in data:
            raise ProfileContextError("Profile entry is missing 'name'")
        prof = cls(**copy.deepcopy(data))
        prof.validate()
        return prof

    def validate(self) -> None:
        """Raise ProfileContextError unless every field has a usable value."""
        def fail(what: str) -> None:
            raise ProfileContextError(f"Invalid profile {self.name!r}: {what}")

        if not isinstance(self.name, str) or not _NAME_RE.fullmatch(self.name):
            fail("name must match [A-Za-z0-9][A-Za-z0-9._-]*")
        if not isinstance(self.description, str):
            fail("description must be a string")
        url = urlparse(self.api_base_url) if isinstance(self.api_base_url, str) else None
        if url is None or url.scheme not in ("http", "https") or not url.netloc:
            fail("api_base_url must be an http(s) URL with a host")
        if self.db_path is not None and not isinstance(self.db_path, str):
            fail("db_path must be a string or null")
        if not isinstance(self.env_vars, dict) or not all(
            isinstance(k, str) and k and isinstance(v, str) for k, v in self.env_vars.items()
        ):
            fail("env_vars must map non-empty strings to strings")
        if not isinstance(self.settings, dict) or not all(isinstance(k, str) for k in self.settings):
            fail("settings must be an object with string keys")
        if not isinstance(self.tags, list) or not all(isinstance(t, str) for t in self.tags):
            fail("tags must be a list of strings")
        for stamp in (self.created_at, self.updated_at):
            if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
                fail("created_at/updated_at must be numbers")


_FIELD_NAMES = frozenset(f.name for f in fields(EnvironmentProfile))


class ProfileContextSwitcher:
    """Manages multi-environment operational profiles and context transitions."""

    def __init__(
        self,
        storage_path: Optional[Union[str, Path]] = None,
        in_memory: bool = False,
    ):
        self._lock = threading.RLock()
        self.in_memory = in_memory
        self.storage_path = Path(storage_path) if storage_path else default_profiles_file()
        self._profiles: Dict[str, EnvironmentProfile] = {}
        # Persisted selection; temporary contexts overlay it without changing it.
        self._active_profile_name: str = "default"
        self._context_stack: List[tuple] = []

        self._initialize_default_profile()
        if not self.in_memory and self.storage_path.exists():
            self._load()

    def _initialize_default_profile(self) -> None:
        """Create canonical default profile if no profiles exist."""
        self._profiles["default"] = EnvironmentProfile(
            name="default",
            description="Default local runtime environment",
            api_base_url="http://localhost:8080",
            env_vars={},
            settings={"timeout_sec": 30, "retries": 3},
        )
        self._active_profile_name = "default"

    def _load(self) -> None:
        """Load profiles and active selection; a corrupt store raises and is left untouched."""
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            raise ProfileContextError(f"Cannot read profiles from {self.storage_path}: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("profiles", []), list):
            raise ProfileContextError(f"Malformed profile store {self.storage_path}")
        try:
            profiles = self._parse_entries(data.get("profiles", []))
        except ProfileContextError as exc:
            raise ProfileContextError(f"Malformed profile store {self.storage_path}: {exc}") from exc
        self._profiles.update(profiles)
        active = data.get("active", "default")
        self._active_profile_name = active if active in self._profiles else "default"

    @staticmethod
    def _parse_entries(entries: List[Any]) -> Dict[str, EnvironmentProfile]:
        """Validate every entry before any is used; duplicate names are an error."""
        parsed: Dict[str, EnvironmentProfile] = {}
        for entry in entries:
            prof = EnvironmentProfile.from_dict(entry)
            if prof.name in parsed:
                raise ProfileContextError(f"duplicate profile name {prof.name!r}")
            parsed[prof.name] = prof
        return parsed

    def _save(self) -> None:
        """Persist profiles and active selection atomically; raise on failure."""
        if self.in_memory:
            return
        payload = {
            "version": 1,
            "active": self._active_profile_name,
            "profiles": [p.to_dict() for p in self._profiles.values()],
        }
        tmp_file = self.storage_path.with_suffix(".tmp")
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            tmp_file.replace(self.storage_path)
        except (OSError, TypeError, ValueError) as exc:
            raise ProfileContextError(f"Failed to save profiles to {self.storage_path}: {exc}") from exc

    @contextmanager
    def _mutation(self) -> Iterator[None]:
        """Hold the lock, persist on success, restore memory if anything fails."""
        with self._lock:
            snapshot = (copy.deepcopy(self._profiles), self._active_profile_name)
            try:
                yield
                self._save()
            except BaseException:
                self._profiles, self._active_profile_name = snapshot
                raise

    def _require(self, name: str) -> EnvironmentProfile:
        prof = self._profiles.get(name)
        if prof is None:
            raise ProfileNotFoundError(f"Profile '{name}' does not exist")
        return prof

    def list_profiles(self) -> List[EnvironmentProfile]:
        """Return all registered environment profiles sorted by name."""
        with self._lock:
            return sorted(self._profiles.values(), key=lambda p: p.name)

    def get_profile(self, name: str) -> Optional[EnvironmentProfile]:
        """Retrieve a profile by name."""
        with self._lock:
            return self._profiles.get(name)

    def current_profile(self) -> EnvironmentProfile:
        """Return the effective profile: the innermost temporary context, else the active one."""
        with self._lock:
            if self._context_stack:
                return self._context_stack[-1][1]
            return self._profiles[self._active_profile_name]

    @property
    def current_profile_name(self) -> str:
        """Return name of the effective profile."""
        return self.current_profile().name

    def create_profile(
        self, profile: EnvironmentProfile, set_active: bool = False
    ) -> EnvironmentProfile:
        """Register a new environment profile."""
        profile.validate()
        with self._mutation():
            if profile.name in self._profiles:
                raise ProfileConflictError(f"Profile '{profile.name}' already exists")
            profile.updated_at = time.time()
            self._profiles[profile.name] = profile
            if set_active:
                self._active_profile_name = profile.name
        return profile

    def update_profile(self, name: str, /, **updates: Any) -> EnvironmentProfile:
        """Update mutable fields of an existing profile; bad fields change nothing."""
        bad = set(updates) - (_FIELD_NAMES - _IMMUTABLE_FIELDS)
        if bad:
            raise ProfileContextError(f"Cannot update field(s): {', '.join(sorted(bad))}")
        with self._mutation():
            updated = replace(self._require(name), **copy.deepcopy(updates), updated_at=time.time())
            updated.validate()
            self._profiles[name] = updated
        return updated

    def delete_profile(self, name: str) -> bool:
        """Delete an environment profile."""
        with self._mutation():
            self._require(name)
            if name == self._active_profile_name or any(p.name == name for _, p in self._context_stack):
                raise ProfileContextError(
                    f"Cannot delete active profile '{name}'. Switch to another profile first."
                )
            del self._profiles[name]
        return True

    def switch_profile(self, name: str) -> EnvironmentProfile:
        """Switch the persisted active profile."""
        with self._mutation():
            prof = self._require(name)
            self._active_profile_name = name
        return prof

    @contextmanager
    def temporary_context(
        self, name_or_profile: Union[str, EnvironmentProfile]
    ) -> Generator[EnvironmentProfile, None, None]:
        """Temporarily make a profile effective and apply its env vars.

        The lock is not held while the body runs, the persisted active profile
        is never changed, and on exit only the env keys this profile set are
        restored, so variables the body sets for other keys survive.
        """
        if isinstance(name_or_profile, str):
            with self._lock:
                target = self._require(name_or_profile)
        else:
            target = name_or_profile
            target.validate()

        token = object()
        with self._lock:
            self._context_stack.append((token, target))
            saved_env = {k: os.environ.get(k) for k in target.env_vars}
            os.environ.update(target.env_vars)
        try:
            yield target
        finally:
            with self._lock:
                self._context_stack = [e for e in self._context_stack if e[0] is not token]
                for k, prev_val in saved_env.items():
                    if prev_val is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = prev_val

    def clone_profile(
        self, source_name: str, target_name: str, /, **overrides: Any
    ) -> EnvironmentProfile:
        """Clone an existing profile under target_name with optional field overrides."""
        bad = set(overrides) - (_FIELD_NAMES - _IMMUTABLE_FIELDS)
        if bad:
            raise ProfileContextError(f"Cannot override field(s) on clone: {', '.join(sorted(bad))}")
        with self._mutation():
            src = self._require(source_name)
            if target_name in self._profiles:
                raise ProfileConflictError(f"Target profile '{target_name}' already exists")
            now = time.time()
            clone = replace(copy.deepcopy(src), **copy.deepcopy(overrides),
                            name=target_name, created_at=now, updated_at=now)
            clone.validate()
            self._profiles[target_name] = clone
        return clone

    def diff_profiles(self, name_a: str, name_b: str) -> Dict[str, Any]:
        """Compute structural differences between two profiles (timestamps ignored)."""
        with self._lock:
            dict_a = self._require(name_a).to_dict()
            dict_b = self._require(name_b).to_dict()

        changed = {}
        identical = {}
        for k in sorted((set(dict_a) | set(dict_b)) - {"created_at", "updated_at"}):
            val_a = dict_a.get(k)
            val_b = dict_b.get(k)
            if val_a != val_b:
                changed[k] = {"source": val_a, "target": val_b}
            else:
                identical[k] = val_a
        return {"source": name_a, "target": name_b, "changed": changed, "identical": identical}

    def export_profiles(self, dest_path: Union[str, Path]) -> None:
        """Export all profiles to a portable JSON file."""
        with self._lock:
            payload = {
                "version": 1,
                "exported_at": time.time(),
                "active": self._active_profile_name,
                "profiles": [p.to_dict() for p in self._profiles.values()],
            }
        path = Path(dest_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def import_profiles(
        self, src_path: Union[str, Path], overwrite: bool = False
    ) -> int:
        """Import a JSON bundle all-or-nothing. Existing names are kept unless overwrite.

        Returns the number of profiles added or replaced.
        """
        try:
            with open(src_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            raise ProfileContextError(f"Cannot read profile bundle {src_path}: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("profiles", []), list):
            raise ProfileContextError(f"Malformed profile bundle {src_path}")
        incoming = self._parse_entries(data.get("profiles", []))

        with self._mutation():
            accepted = {n: p for n, p in incoming.items() if overwrite or n not in self._profiles}
            self._profiles.update(accepted)
        return len(accepted)


_GLOBAL_SWITCHER: Optional[ProfileContextSwitcher] = None
_GLOBAL_LOCK = threading.Lock()


def get_profile_switcher() -> ProfileContextSwitcher:
    """Get the process-wide ProfileContextSwitcher, built on first use."""
    global _GLOBAL_SWITCHER
    with _GLOBAL_LOCK:
        if _GLOBAL_SWITCHER is None:
            _GLOBAL_SWITCHER = ProfileContextSwitcher()
        return _GLOBAL_SWITCHER
