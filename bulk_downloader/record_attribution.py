"""Canonical Record Attribution & Metadata Normalization.

Maps noisy, heterogeneous upstream media metadata (creator, publisher, series,
title, release date, tags) to canonical identities and structured catalog paths,
stripping web boilerplates and preserving attribution provenance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Union

from bulk_downloader.metadata_normalizer import (
    AliasTable,
    to_filesystem_component,
)


class AttributionRole(str, Enum):
    """Semantic role of an attributed entity in a media record."""
    CREATOR = "creator"
    PUBLISHER = "publisher"
    SERIES = "series"
    CONTRIBUTOR = "contributor"
    PLATFORM = "platform"


@dataclass
class CanonicalAttribution:
    """A normalized entity attribution with provenance tracking."""
    canonical_name: str
    original_name: str
    role: AttributionRole
    matched_alias: bool = False
    fuzzy: bool = False
    confidence: float = 1.0
    fs_safe_name: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "canonical_name": self.canonical_name,
            "original_name": self.original_name,
            "role": self.role.value if isinstance(self.role, AttributionRole) else str(self.role),
            "matched_alias": self.matched_alias,
            "fuzzy": self.fuzzy,
            "confidence": round(self.confidence, 3),
            "fs_safe_name": self.fs_safe_name,
            "metadata": self.metadata,
        }


@dataclass
class RecordMetadata:
    """Input record descriptor."""
    title: str = ""
    creator: Optional[str] = None
    publisher: Optional[str] = None
    series: Optional[str] = None
    date: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedRecord:
    """Complete canonically normalized media record."""
    canonical_title: str
    attributions: List[CanonicalAttribution] = field(default_factory=list)
    primary_attribution: Optional[CanonicalAttribution] = None
    clean_date: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    catalog_path: str = ""
    provenance: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "canonical_title": self.canonical_title,
            "attributions": [a.to_dict() for a in self.attributions],
            "primary_attribution": self.primary_attribution.to_dict() if self.primary_attribution else None,
            "clean_date": self.clean_date,
            "tags": self.tags,
            "catalog_path": self.catalog_path,
            "provenance": self.provenance,
        }


_NOISE_PREFIX_RE = re.compile(
    r"^(by\s+|uploaded\s+by\s+|official\s+channel\s+of\s+)",
    re.IGNORECASE,
)
_NOISE_SUFFIX_RE = re.compile(
    r"(\s+official|\s+channel|\s+vevo)$",
    re.IGNORECASE,
)

_TITLE_JUNK_PATTERNS = [
    re.compile(r"\s*\[(?:official|4k|60fps|hd|hq|video|audio|music\s+video|lyric\s+video|visualizer|1080p|720p).*?\]", re.IGNORECASE),
    re.compile(r"\s*\((?:official|4k|60fps|hd|hq|video|audio|music\s+video|lyric\s+video|visualizer|1080p|720p).*?\)", re.IGNORECASE),
]

_DATE_ISO_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def strip_attribution_noise(text: str) -> str:
    """Strip common boilerplate prefixes/suffixes from creator/publisher names."""
    if not text:
        return ""
    cleaned = unicodedata.normalize("NFKC", text).strip()
    cleaned = _NOISE_PREFIX_RE.sub("", cleaned).strip()
    cleaned = _NOISE_SUFFIX_RE.sub("", cleaned).strip()
    return cleaned


def clean_title(title: str) -> str:
    """Strip video format noise, resolution tags, and bracketed junk from titles."""
    if not title:
        return ""
    t = unicodedata.normalize("NFKC", title).strip()
    for pattern in _TITLE_JUNK_PATTERNS:
        t = pattern.sub("", t)
    return t.strip()


def extract_clean_date(date_str: Optional[str]) -> Optional[str]:
    """Extract standard YYYY-MM-DD date string from arbitrary timestamp."""
    if not date_str:
        return None
    m = _DATE_ISO_RE.search(date_str)
    return m.group(1) if m else None


class RecordAttributionNormalizer:
    """Engine for normalizing record attributions and structuring canonical paths."""

    def __init__(self, alias_tables: Optional[Dict[AttributionRole, AliasTable]] = None):
        self._role_mappings: Dict[AttributionRole, Dict[str, str]] = {
            role: {} for role in AttributionRole
        }
        self._alias_tables: Dict[AttributionRole, AliasTable] = (
            dict(alias_tables) if alias_tables is not None else {}
        )

    def register_alias(
        self,
        variant: str,
        canonical: str,
        role: AttributionRole = AttributionRole.CREATOR,
    ) -> None:
        """Register a known alias variant mapping to a canonical identity."""
        if role not in self._role_mappings:
            self._role_mappings[role] = {}
        self._role_mappings[role][variant] = canonical
        # Invalidate compiled AliasTable for this role
        self._alias_tables.pop(role, None)

    def _get_alias_table(self, role: AttributionRole) -> AliasTable:
        if role not in self._alias_tables:
            mapping = self._role_mappings.get(role, {})
            self._alias_tables[role] = AliasTable(mapping, fuzzy_max_distance=2)
        return self._alias_tables[role]

    def normalize_attribution(
        self,
        name: str,
        role: AttributionRole = AttributionRole.CREATOR,
    ) -> CanonicalAttribution:
        """Normalize a raw creator/publisher/series string to a CanonicalAttribution."""
        raw = name or ""
        stripped = strip_attribution_noise(raw)
        if not stripped:
            return CanonicalAttribution(
                canonical_name="unknown",
                original_name=raw,
                role=role,
                matched_alias=False,
                fuzzy=False,
                confidence=0.0,
                fs_safe_name="unknown",
            )

        table = self._get_alias_table(role)
        res = table.lookup(stripped)

        # res.canonical is already guaranteed filesystem-safe by AliasTable.lookup
        fs_safe = res.canonical
        confidence = 1.0 if (res.matched_alias and not res.fuzzy) else (0.85 if res.fuzzy else 0.75)

        return CanonicalAttribution(
            canonical_name=res.canonical,
            original_name=raw,
            role=role,
            matched_alias=res.matched_alias,
            fuzzy=res.fuzzy,
            confidence=confidence,
            fs_safe_name=fs_safe,
            metadata={"distance": res.distance, "stripped_input": stripped},
        )

    def normalize_record(
        self,
        record: Union[Dict[str, Any], RecordMetadata],
    ) -> NormalizedRecord:
        """Normalize complete record metadata, synthesizing clean attributions and catalog path."""
        if isinstance(record, RecordMetadata):
            raw_title = record.title
            raw_creator = record.creator
            raw_publisher = record.publisher
            raw_series = record.series
            raw_date = record.date
            raw_tags = record.tags
        elif isinstance(record, dict):
            raw_title = record.get("title", "")
            raw_creator = record.get("creator") or record.get("author") or record.get("artist") or record.get("uploader")
            raw_publisher = record.get("publisher")
            raw_series = record.get("series") or record.get("album") or record.get("show")
            raw_date = record.get("date") or record.get("release_date") or record.get("upload_date")
            raw_tags = record.get("tags") or []
        else:
            raise TypeError(f"Unsupported record type: {type(record).__name__}")

        canonical_title = clean_title(raw_title) or "untitled"
        clean_date = extract_clean_date(raw_date)

        attributions: List[CanonicalAttribution] = []
        primary_attr: Optional[CanonicalAttribution] = None

        if raw_creator:
            creator_attr = self.normalize_attribution(raw_creator, role=AttributionRole.CREATOR)
            attributions.append(creator_attr)
            primary_attr = creator_attr

        if raw_publisher:
            pub_attr = self.normalize_attribution(raw_publisher, role=AttributionRole.PUBLISHER)
            attributions.append(pub_attr)
            if not primary_attr:
                primary_attr = pub_attr

        series_attr: Optional[CanonicalAttribution] = None
        if raw_series:
            series_attr = self.normalize_attribution(raw_series, role=AttributionRole.SERIES)
            attributions.append(series_attr)

        # Clean tags
        clean_tags = sorted(list({t.strip().lower() for t in raw_tags if t and t.strip()}))

        # Synthesize catalog path: <Creator>/[<Series>/]<Title>
        path_components: List[str] = []
        if primary_attr:
            path_components.append(primary_attr.fs_safe_name)
        if series_attr:
            path_components.append(series_attr.fs_safe_name)
        path_components.append(to_filesystem_component(canonical_title))

        catalog_path = "/".join(path_components)

        provenance = {
            "raw_title": raw_title,
            "raw_creator": raw_creator,
            "raw_publisher": raw_publisher,
            "raw_series": raw_series,
            "raw_date": raw_date,
        }

        return NormalizedRecord(
            canonical_title=canonical_title,
            attributions=attributions,
            primary_attribution=primary_attr,
            clean_date=clean_date,
            tags=clean_tags,
            catalog_path=catalog_path,
            provenance=provenance,
        )


def normalize_record_attribution(
    record_or_name: Any,
    *,
    role: AttributionRole = AttributionRole.CREATOR,
    normalizer: Optional[RecordAttributionNormalizer] = None,
) -> Union[NormalizedRecord, CanonicalAttribution]:
    """Unified functional dispatcher for string attributions or full record metadata."""
    norm = normalizer or RecordAttributionNormalizer()
    if isinstance(record_or_name, str):
        return norm.normalize_attribution(record_or_name, role=role)
    if isinstance(record_or_name, (dict, RecordMetadata)):
        return norm.normalize_record(record_or_name)
    raise TypeError(f"Cannot normalize attribution for type {type(record_or_name).__name__}")
