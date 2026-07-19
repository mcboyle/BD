"""DRM / EME protection classifier -- DETECTION ONLY, never circumvention.

Classifies streaming protection from an EME key-system string, HLS/DASH manifest
text, or a URL marker into a coarse category, so the operator can tell DOWNLOADABLE
encrypted playback (HLS AES-128 / SAMPLE-AES with a fetchable key -- site-provided,
in scope, yt-dlp handles it natively) apart from CDM-DRM (Widevine / PlayReady /
FairPlay -- structurally un-downloadable).

This module NEVER decrypts, strips, extracts keys, requests a license, or drives a
CDM. It reads bytes/strings and returns a label. This is the charter's existing
"detect, never defeat" floor, restated in code. See DRM_EME_DETECTION_DECISION.md.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# Category vocabulary.
CAT_NONE = "none"
CAT_AES = "downloadable-aes"   # fetchable-key AES-128 / SAMPLE-AES: in scope, downloadable
CAT_CLEARKEY = "clearkey"      # EME Clear Key: keys in the clear, not a protection to defeat
CAT_CDM = "cdm-drm"            # Widevine / PlayReady / FairPlay via a CDM: un-circumventable

# key-system strings (EME) and keyformat / schemeIdUri identifiers -> system name.
_KS = {
    "com.widevine.alpha": "widevine",
    "com.microsoft.playready": "playready",
    "com.apple.fps": "fairplay",
    "com.apple.streamingkeydelivery": "fairplay",
    "org.w3.clearkey": "clearkey",
}
_UUID = {
    "edef8ba9-79d6-4ace-a3c8-27dcd51d21ed": "widevine",
    "9a04f079-9840-4286-ab92-e65be0885f95": "playready",
    "94ce86fb-07ff-4f43-adb8-93d2fa968ca2": "fairplay",
    "e2719d58-a985-b3c9-781a-b030af78d30e": "clearkey",
}
_URL_MARKER = re.compile(
    r"(widevine|playready|fairplay|streamingkeydelivery|drmtoday|licenseurl|"
    r"/license\b|\.wvm\b|cenc)", re.I)


def _sys_for(token: str) -> Optional[str]:
    t = (token or "").lower()
    for k, v in _KS.items():
        if k in t:
            return v
    for u, v in _UUID.items():
        if u in t:
            return v
    if "widevine" in t:
        return "widevine"
    if "playready" in t:
        return "playready"
    if "fairplay" in t or "streamingkeydelivery" in t:
        return "fairplay"
    if "clearkey" in t:
        return "clearkey"
    return None


def _category_for_system(system: Optional[str]) -> str:
    if system == "clearkey":
        return CAT_CLEARKEY
    if system in ("widevine", "playready", "fairplay"):
        return CAT_CDM
    return CAT_NONE


def _classify_hls(text: str, ev: List[str]) -> Dict[str, Any]:
    system: Optional[str] = None
    category = CAT_NONE
    downloadable_key = False
    for line in text.splitlines():
        s = line.strip()
        if not (s.startswith("#EXT-X-KEY") or s.startswith("#EXT-X-SESSION-KEY")):
            continue
        method = re.search(r"METHOD=([A-Za-z0-9\-]+)", s)
        keyformat = re.search(r'KEYFORMAT="([^"]+)"', s)
        m = (method.group(1) if method else "").upper()
        kf = keyformat.group(1) if keyformat else ""
        if m == "NONE":
            continue
        ev.append(s[:120])
        sysid = _sys_for(kf) if kf else None
        if sysid in ("widevine", "playready", "fairplay"):
            system = system or sysid
            category = CAT_CDM
        elif sysid == "clearkey":
            system = system or "clearkey"
            if category != CAT_CDM:
                category = CAT_CLEARKEY
        elif m in ("AES-128", "SAMPLE-AES") and (not kf or kf.lower() == "identity"):
            # AES with a fetchable key and no DRM keyformat -> downloadable.
            downloadable_key = True
            system = system or "aes-128"
    if category == CAT_NONE and downloadable_key:
        category = CAT_AES
    return {"system": system, "category": category, "evidence": ev}


def _classify_dash(text: str, ev: List[str]) -> Dict[str, Any]:
    system: Optional[str] = None
    category = CAT_NONE
    for m in re.finditer(r'<ContentProtection[^>]*schemeIdUri="([^"]+)"', text, re.I):
        scheme = m.group(1)
        ev.append(scheme[:120])
        sysid = _sys_for(scheme)
        if sysid in ("widevine", "playready", "fairplay"):
            system = system or sysid
            category = CAT_CDM
        elif sysid == "clearkey":
            system = system or "clearkey"
            if category != CAT_CDM:
                category = CAT_CLEARKEY
        elif "mp4protection" in scheme.lower() or "cenc" in scheme.lower():
            if category == CAT_NONE:
                category = CAT_CDM
    if category == CAT_NONE and re.search(r"<cenc:pssh|<pssh", text, re.I):
        ev.append("pssh")
        category = CAT_CDM
    return {"system": system, "category": category, "evidence": ev}


def classify_protection(*, url: Optional[str] = None, hls_text: Optional[str] = None,
                        dash_text: Optional[str] = None,
                        key_system: Optional[str] = None) -> Dict[str, Any]:
    """Return {"system", "category", "evidence"}; category in
    {none, downloadable-aes, clearkey, cdm-drm}. Detection only -- never decrypts,
    strips, extracts keys, requests a license, or drives a CDM."""
    ev: List[str] = []
    # An explicit EME key-system is the most authoritative signal.
    if key_system:
        sysid = _sys_for(key_system)
        ev.append(("eme:" + key_system)[:120])
        return {"system": sysid, "category": _category_for_system(sysid), "evidence": ev}
    if hls_text and ("#EXT-X-KEY" in hls_text or "#EXT-X-SESSION-KEY" in hls_text):
        return _classify_hls(hls_text, ev)
    if dash_text and ("ContentProtection" in dash_text or "pssh" in dash_text.lower()):
        return _classify_dash(dash_text, ev)
    if url and _URL_MARKER.search(url):
        sysid = _sys_for(url)
        ev.append(("url:" + url)[:120])
        cat = _category_for_system(sysid) if sysid else CAT_CDM
        if cat == CAT_NONE:
            cat = CAT_CDM
        return {"system": sysid, "category": cat, "evidence": ev}
    return {"system": None, "category": CAT_NONE, "evidence": ev}
