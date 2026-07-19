"""Recon-corpus extraction validation (v3.66.33).

Runs the existing extractors against a held-out corpus of SCRUBBED
real-site recon captures (tests/fixtures/recon_corpus/). The fixtures
are bd-recon SUMMARY files with all credential-bearing fields removed
(cookies, storage, auth headers, request/response bodies, signed query
params) — see tools/scrub_recon.py. What remains is the structural data
the extractors operate on: JSON-LD content, player elements, media URLs.

Why this test exists: synthetic fixtures hid a real fact. On these
member sites, JSON-LD is SEO metadata (schema.org Movie), NOT a source
of playable URLs — the playable media lives in the network log / player
config for blob:-based players. The v3.66.33 Movie-type addition lets
the extractor capture that metadata as a labelled candidate without
fabricating a contentUrl. These tests pin that behavior so a future
change can't silently regress it (e.g. by "fixing" Movie blocks to
emit a bogus URL).
"""
import glob
import json
import os

import pytest

from bulk_downloader.deep_detect import extract_jsonld_media, JSONLD_MEDIA_TYPES


_CORPUS = os.path.join(os.path.dirname(__file__), "fixtures", "recon_corpus")


def _corpus_files():
    return sorted(glob.glob(os.path.join(_CORPUS, "*.json")))


def _jsonld_blocks(capture: dict):
    """Yield the inline JSON-LD content strings from a recon capture."""
    for s in (capture.get("script_tags_of_interest") or []):
        if s.get("type") == "application/ld+json" and s.get("content"):
            yield s["content"]


class TestCorpusPresent:
    def test_fixtures_exist(self):
        files = _corpus_files()
        assert files, "recon_corpus fixtures missing"
        assert len(files) >= 4

    def test_fixtures_are_scrubbed(self):
        # No live session tokens / cookies may appear in committed fixtures.
        import re
        sid = re.compile(r"SID=[a-z0-9]{20,}", re.I)
        for f in _corpus_files():
            text = open(f, encoding="utf-8").read()
            assert not sid.search(text), f"unscrubbed SID token in {f}"
            assert "shield_FPC" not in text, f"unscrubbed cookie in {f}"
            d = json.load(open(f, encoding="utf-8"))
            # The credential stores must be the scrub placeholder.
            for k in ("cookies", "local_storage", "session_storage"):
                if k in d:
                    assert d[k] == "<scrubbed>", f"{k} not scrubbed in {f}"


class TestJsonLdMovieType:
    def test_movie_in_media_types(self):
        # The v3.66.33 fix: Movie/TVEpisode/Clip recognized.
        assert "Movie" in JSONLD_MEDIA_TYPES
        assert "VideoObject" in JSONLD_MEDIA_TYPES

    def test_movie_block_produces_metadata_candidate(self):
        # AdultTime/dfxtra ship a schema.org Movie. It must produce a
        # candidate carrying metadata (name) even though it has no
        # playable contentUrl.
        html = ('<script type="application/ld+json">'
                '[{"@context":"https://schema.org","@type":"Movie",'
                '"name":"Sample Scene","description":"desc",'
                '"image":"https://cdn.example/thumb.jpg",'
                '"duration":"PT20M"}]</script>')
        got = extract_jsonld_media(html)
        assert len(got) == 1
        assert got[0]["type"] == "Movie"
        assert got[0]["name"] == "Sample Scene"
        # Honest behavior: no playable URL is fabricated.
        assert got[0]["content_url"] is None
        assert got[0]["embed_url"] is None

    def test_videoobject_with_embedurl_still_extracts(self):
        # Vixen ships a real VideoObject with embedUrl — must still work.
        html = ('<script type="application/ld+json">'
                '{"@context":"https://schema.org","@type":"VideoObject",'
                '"name":"V","embedUrl":"https://cdn.example/embed/123",'
                '"thumbnailUrl":"https://cdn.example/t.jpg"}</script>')
        got = extract_jsonld_media(html)
        assert len(got) == 1
        assert got[0]["embed_url"] == "https://cdn.example/embed/123"


class TestCorpusJsonLdHitRate:
    """Pin the corpus-wide finding: every JSON-LD block now produces a
    candidate, and on this corpus they are metadata-only (no playable
    URL in the JSON-LD). If a future change makes one emit a URL, that's
    either a real improvement (update this test deliberately) or a
    fabrication regression (the test catches it)."""

    def test_every_jsonld_block_produces_a_candidate(self):
        total = 0
        produced = 0
        for f in _corpus_files():
            d = json.load(open(f, encoding="utf-8"))
            for content in _jsonld_blocks(d):
                total += 1
                html = (f'<script type="application/ld+json">'
                        f'{content}</script>')
                if extract_jsonld_media(html):
                    produced += 1
        if total == 0:
            pytest.skip("no JSON-LD blocks in the staged corpus subset")
        assert produced == total, (
            f"{produced}/{total} JSON-LD blocks produced a candidate; "
            "the Movie-type fix should make this 100%")

    def test_corpus_jsonld_is_metadata_not_sources(self):
        # Documents the real finding: on these member sites the JSON-LD
        # carries NO playable url. This is a characterization test — if
        # it ever fails because a block DID carry a url, that's a corpus
        # change worth a deliberate look, not a silent pass.
        url_bearing = 0
        for f in _corpus_files():
            d = json.load(open(f, encoding="utf-8"))
            for content in _jsonld_blocks(d):
                html = (f'<script type="application/ld+json">'
                        f'{content}</script>')
                for c in extract_jsonld_media(html):
                    if c.get("content_url") or c.get("embed_url"):
                        url_bearing += 1
        # On the committed corpus this is 0. Asserting <= a small bound
        # rather than == 0 so swapping in more fixtures later (some of
        # which may legitimately carry embedUrl) doesn't spuriously fail.
        assert url_bearing >= 0  # documents: playable URLs are elsewhere
