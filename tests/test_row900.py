"""Row 900 -- MULTI-SEGMENT-MEDIA-KEY-FETCHING-AND-PAYLOAD-ASSEMBLER.

Segmented HLS media whose manifest declares a per-segment (or per-range)
`#EXT-X-KEY` needs the key fetched and each segment decrypted before the
segments can be concatenated into one continuous playable file. Without
that, BD's segmented downloader has the ciphertext but nothing that turns
it into the plaintext transport stream.

CHARTER SCOPE (project-knowledge/DRM_EME_DETECTION_DECISION.md): this is
the SAME "downloadable-aes" playback yt-dlp already downloads natively --
a site-provided, fetchable AES-128/SAMPLE-AES key, not DRM. The decision's
hard line is specifically CDM-DRM (Widevine/PlayReady/FairPlay) key
extraction/decryption, which stays permanently out of scope. Every test
below with a CDM keyformat asserts hls.py REFUSES before ever calling the
fetcher for that key -- the negative control that proves the scope gate
actually gates, not just that it happens to agree with a green path.

ACCEPTANCE (row900 register text):
  (1) key descriptor extraction from manifest
  (2) authenticated key acquisition
  (3) byte-level output file integrity
"""
from __future__ import annotations

import os

import pytest

from bulk_downloader import hls

BD_GATE_SCOPE = "module"


MANIFEST_AES = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-KEY:METHOD=AES-128,URI="https://example.test/key1",IV=0x00000000000000000000000000000001
#EXTINF:6.0,
seg0.ts
#EXTINF:6.0,
seg1.ts
#EXT-X-ENDLIST
"""

MANIFEST_UNENCRYPTED = """#EXTM3U
#EXT-X-VERSION:3
#EXTINF:6.0,
plain0.ts
#EXT-X-ENDLIST
"""

MANIFEST_FAIRPLAY = """#EXTM3U
#EXT-X-VERSION:7
#EXT-X-KEY:METHOD=SAMPLE-AES,KEYFORMAT="com.apple.streamingkeydelivery",URI="skd://fp1"
#EXTINF:6.0,
seg0.ts
#EXT-X-ENDLIST
"""

MANIFEST_WIDEVINE = """#EXTM3U
#EXT-X-VERSION:7
#EXT-X-KEY:METHOD=SAMPLE-AES-CTR,KEYFORMAT="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed",URI="data:..."
#EXTINF:6.0,
seg0.ts
#EXT-X-ENDLIST
"""


# ---- (1) key descriptor extraction ---------------------------------------

class TestKeyDescriptorExtraction:
    def test_extracts_method_uri_keyformat_and_iv(self):
        descs = hls.parse_key_descriptors(MANIFEST_AES)
        assert len(descs) == 1
        d = descs[0]
        assert d.method == "AES-128"
        assert d.uri == "https://example.test/key1"
        assert d.keyformat is None
        assert d.iv == bytes.fromhex("00000000000000000000000000000001")

    def test_method_none_yields_no_descriptor(self):
        assert hls.parse_key_descriptors(MANIFEST_UNENCRYPTED) == []

    def test_no_key_tag_at_all_yields_no_descriptor(self):
        assert hls.parse_key_descriptors("#EXTM3U\nseg.ts\n") == []

    def test_cdm_keyformat_is_still_extracted_as_a_descriptor(self):
        """Extraction itself must see a CDM descriptor -- the refusal lives
        at the fetch/assemble boundary (_assert_downloadable), not by
        pretending the tag was never there."""
        descs = hls.parse_key_descriptors(MANIFEST_FAIRPLAY)
        assert len(descs) == 1
        assert descs[0].keyformat == "com.apple.streamingkeydelivery"


# ---- (2) authenticated key acquisition -----------------------------------

class TestAuthenticatedKeyAcquisition:
    def test_fetch_key_bytes_calls_the_callers_authenticated_fetcher(self):
        """The caller owns the session/auth (cookies, headers) -- this
        module never opens its own connection or performs a login (Fleet
        Rule 21: 0 site logins touched)."""
        calls = []

        def fetch(uri):
            calls.append(uri)
            return b"\x11" * 16

        desc = hls.parse_key_descriptors(MANIFEST_AES)[0]
        key = hls.fetch_key_bytes(desc, fetch)
        assert key == b"\x11" * 16
        assert calls == ["https://example.test/key1"]

    def test_fetch_key_bytes_refuses_fairplay_before_calling_fetcher(self):
        calls = []
        desc = hls.parse_key_descriptors(MANIFEST_FAIRPLAY)[0]
        with pytest.raises(hls.DrmScopeError):
            hls.fetch_key_bytes(desc, lambda uri: calls.append(uri))
        assert calls == [], "fetcher was called for a CDM-DRM key descriptor"

    def test_fetch_key_bytes_refuses_widevine_before_calling_fetcher(self):
        calls = []
        desc = hls.parse_key_descriptors(MANIFEST_WIDEVINE)[0]
        with pytest.raises(hls.DrmScopeError):
            hls.fetch_key_bytes(desc, lambda uri: calls.append(uri))
        assert calls == [], "fetcher was called for a CDM-DRM key descriptor"

    def test_fetch_key_bytes_requires_a_uri(self):
        desc = hls.KeyDescriptor(method="AES-128", uri=None, keyformat=None,
                                  iv=b"\x00" * 16)
        with pytest.raises(ValueError):
            hls.fetch_key_bytes(desc, lambda uri: b"")


# ---- segment planning (KEY tag applies to following segments) -----------

class TestSegmentPlanning:
    def test_key_applies_to_segments_until_superseded(self):
        manifest = (
            "#EXTM3U\n"
            "#EXT-X-KEY:METHOD=AES-128,URI=\"k1\",IV=0x00000000000000000000000000000001\n"
            "#EXTINF:6.0,\nseg0.ts\n"
            "#EXTINF:6.0,\nseg1.ts\n"
            "#EXT-X-KEY:METHOD=NONE\n"
            "#EXTINF:6.0,\nseg2.ts\n"
        )
        plans = hls.plan_segments(manifest)
        assert [p.uri for p in plans] == ["seg0.ts", "seg1.ts", "seg2.ts"]
        assert plans[0].key is not None and plans[1].key is not None
        assert plans[0].key.uri == "k1" and plans[1].key.uri == "k1"
        assert plans[2].key is None

    def test_missing_iv_derives_from_media_sequence(self):
        manifest = (
            "#EXTM3U\n"
            "#EXT-X-MEDIA-SEQUENCE:5\n"
            "#EXT-X-KEY:METHOD=AES-128,URI=\"k1\"\n"
            "#EXTINF:6.0,\nseg0.ts\n"
            "#EXTINF:6.0,\nseg1.ts\n"
        )
        plans = hls.plan_segments(manifest)
        assert plans[0].iv == hls.default_iv_for_sequence(5)
        assert plans[1].iv == hls.default_iv_for_sequence(6)


# ---- (3) byte-level output file integrity --------------------------------

def _aes128_cbc_encrypt(plaintext: bytes, key: bytes, iv: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    pad_len = 16 - (len(plaintext) % 16)
    padded = plaintext + bytes([pad_len]) * pad_len
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return enc.update(padded) + enc.finalize()


class TestByteLevelAssembly:
    def test_decrypt_segment_recovers_exact_plaintext(self):
        key, iv = b"\x02" * 16, b"\x03" * 16
        plaintext = b"transport-stream-bytes-000111222333"
        ct = _aes128_cbc_encrypt(plaintext, key, iv)
        assert hls.decrypt_segment(ct, key, iv) == plaintext

    def test_decrypt_segment_rejects_bad_padding(self):
        key, iv = b"\x02" * 16, b"\x03" * 16
        garbage = os.urandom(32)
        # Overwhelmingly likely to have invalid PKCS7 padding; on the rare
        # chance it doesn't, this simply proves nothing that run -- pin a
        # deterministic bad case too:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        bad = enc.update(b"\x00" * 16) + enc.finalize()  # pad byte = 0x00, invalid
        with pytest.raises(ValueError):
            hls.decrypt_segment(bad, key, iv)
        del garbage

    def test_assemble_payload_concatenates_encrypted_and_plain_segments(self):
        key, iv0, iv1 = b"\x05" * 16, b"\x06" * 16, b"\x07" * 16
        p0, p1, p2 = b"AAAA-plaintext-zero", b"BBBB-plaintext-one!", b"CCCC-unencrypted"
        segs = [
            _aes128_cbc_encrypt(p0, key, iv0),
            _aes128_cbc_encrypt(p1, key, iv1),
            p2,
        ]
        out = hls.assemble_payload(segs, [key, key, None], [iv0, iv1, None])
        assert out == p0 + p1 + p2

    def test_assemble_payload_rejects_mismatched_lengths(self):
        with pytest.raises(ValueError):
            hls.assemble_payload([b"x"], [b"\x00" * 16], [])

    def test_write_payload_round_trips_exact_bytes(self, tmp_path):
        payload = bytes(range(256)) * 4
        out_path = tmp_path / "assembled.ts"
        hls.write_payload(str(out_path), payload)
        assert out_path.read_bytes() == payload


class TestEndToEndAssembleFromManifest:
    def test_full_pipeline_reconstructs_exact_output(self):
        key = b"\x09" * 16
        manifest = (
            "#EXTM3U\n"
            "#EXT-X-KEY:METHOD=AES-128,URI=\"https://example.test/key1\","
            "IV=0x0000000000000000000000000000000a\n"
            "#EXTINF:6.0,\nseg0.ts\n"
            "#EXTINF:6.0,\nseg1.ts\n"
        )
        iv = bytes.fromhex("0000000000000000000000000000000a")
        plains = {"seg0.ts": b"segment-zero-plaintext",
                  "seg1.ts": b"segment-one--plaintext"}
        ciphers = {uri: _aes128_cbc_encrypt(p, key, iv)
                   for uri, p in plains.items()}

        def fetch_segment(uri):
            return ciphers[uri]

        def fetch_key(uri):
            assert uri == "https://example.test/key1"
            return key

        out = hls.assemble_from_manifest(
            manifest, fetch_segment=fetch_segment, fetch_key=fetch_key)
        assert out == plains["seg0.ts"] + plains["seg1.ts"]

    def test_full_pipeline_refuses_widevine_manifest_before_any_fetch(self):
        calls = []

        def fetch_segment(uri):
            calls.append(("segment", uri))
            return b""

        def fetch_key(uri):
            calls.append(("key", uri))
            return b""

        with pytest.raises(hls.DrmScopeError):
            hls.assemble_from_manifest(
                MANIFEST_WIDEVINE, fetch_segment=fetch_segment,
                fetch_key=fetch_key)
        assert calls == [], (
            "a fetcher was called for a manifest containing a Widevine "
            "key descriptor -- the scope gate did not run before fetching")


# ---- fixer (O928): correctness REFUTE E1-E4 ----------------------------------

KEY16 = bytes(range(16))
IV_ONE = (1).to_bytes(16, "big")


class TestFixerRound2:
    def test_e1_short_iv_spellings_are_the_same_128_bit_iv(self):
        """RFC 8216: the IV is a 128-bit hexadecimal-sequence; fewer digits
        are the same value (0x1 == 0x01 == the zero-padded spelling)."""
        for spelling in ("0x1", "0x01", "0x0001", "0x" + "0" * 31 + "1"):
            [d] = hls.parse_key_descriptors(f'#EXT-X-KEY:METHOD=AES-128,URI="k",IV={spelling}')
            assert d.iv == IV_ONE, spelling
        ct = _aes128_cbc_encrypt(b"hello world", KEY16, IV_ONE)
        manifest = '#EXT-X-KEY:METHOD=AES-128,URI="k",IV=0x1\nseg0.ts\n'
        assert hls.assemble_from_manifest(manifest, fetch_segment=lambda u: ct, fetch_key=lambda u: KEY16) == b"hello world"
        for bad in ("0x" + "1" * 33, "0xzz", "abcd", "0x"):
            with pytest.raises(ValueError):
                hls.parse_key_descriptors(f'#EXT-X-KEY:METHOD=AES-128,URI="k",IV={bad}')

    def test_e2_unsupported_methods_are_refused_not_substituted(self):
        """SAMPLE-AES / an unknown METHOD is not whole-segment AES-128-CBC:
        refused before any fetch, never run through the wrong cipher."""
        calls = []
        for method in ("SAMPLE-AES", "AES-256", "UNKNOWN"):
            manifest = f'#EXT-X-KEY:METHOD={method},URI="k"\nseg0.ts\n'
            with pytest.raises(hls.UnsupportedHlsError):
                hls.assemble_from_manifest(manifest, fetch_segment=lambda u: calls.append(("seg", u)) or b"x" * 16,
                                           fetch_key=lambda u: calls.append(("key", u)) or KEY16)
            [d] = hls.parse_key_descriptors(manifest)
            with pytest.raises(hls.UnsupportedHlsError):
                hls.fetch_key_bytes(d, lambda u: calls.append(("key", u)) or KEY16)
        assert calls == []                                # positive control: AES-128 still fetches
        [d] = hls.parse_key_descriptors('#EXT-X-KEY:METHOD=AES-128,URI="k"')
        assert hls.fetch_key_bytes(d, lambda u: KEY16) == KEY16

    def test_e2_a_key_that_is_not_16_bytes_is_refused(self):
        """AES-128 means a 128-bit key; a 32-byte key would silently select
        AES-256 (a different cipher from the one the manifest declares)."""
        [d] = hls.parse_key_descriptors('#EXT-X-KEY:METHOD=AES-128,URI="k"')
        for key in (bytes(32), bytes(15), b""):
            with pytest.raises(ValueError):
                hls.fetch_key_bytes(d, lambda u: key)
            with pytest.raises(ValueError):
                hls.decrypt_segment(bytes(16), key, IV_ONE)
        with pytest.raises(ValueError):
            hls.decrypt_segment(bytes(16), KEY16, b"\x00" * 8)          # and the IV is 16 bytes

    def test_e3_byterange_sub_ranges_are_sliced_and_map_is_refused(self):
        """BYTERANGE names a sub-range of one resource: 4@0 then 4 (continuing)
        of 'AAAABBBBCCCC' yield AAAA and BBBB, not the whole resource twice;
        an EXT-X-MAP initialization section is refused explicitly."""
        resource = b"AAAABBBBCCCC"
        manifest = "#EXT-X-BYTERANGE:4@0\nres.bin\n#EXT-X-BYTERANGE:4\nres.bin\n#EXT-X-BYTERANGE:4@8\nres.bin\n"
        plans = hls.plan_segments(manifest)
        assert [p.byterange for p in plans] == [(4, 0), (4, 4), (4, 8)]
        fetched = []
        out = hls.assemble_from_manifest(manifest, fetch_segment=lambda u: fetched.append(u) or resource, fetch_key=lambda u: KEY16)
        assert out == b"AAAABBBBCCCC" and fetched == ["res.bin"] * 3
        assert hls.assemble_from_manifest("#EXT-X-BYTERANGE:4@4\nres.bin\n", fetch_segment=lambda u: resource,
                                          fetch_key=lambda u: KEY16) == b"BBBB"
        with pytest.raises(ValueError):                 # a range past the end of the resource
            hls.assemble_from_manifest("#EXT-X-BYTERANGE:8@8\nres.bin\n", fetch_segment=lambda u: resource, fetch_key=lambda u: KEY16)
        with pytest.raises(ValueError):                 # no @offset and nothing to continue from
            hls.plan_segments("#EXT-X-BYTERANGE:4\nres.bin\n")
        with pytest.raises(hls.UnsupportedHlsError):
            hls.plan_segments('#EXT-X-MAP:URI="init.mp4"\nseg0.m4s\n')
        # positive control: a manifest without BYTERANGE still yields whole segments
        assert hls.assemble_from_manifest("seg0.ts\nseg1.ts\n", fetch_segment=lambda u: u.encode(), fetch_key=lambda u: KEY16) == b"seg0.tsseg1.ts"

    def test_e4_a_key_without_a_uri_is_refused_before_any_fetch(self):
        """The pipeline and fetch_key_bytes agree: METHOD=AES-128 with no URI
        is refused before fetch_key is called -- never fetch_key(None) and
        never a plaintext pass-through."""
        calls = []
        manifest = "#EXT-X-KEY:METHOD=AES-128\nseg0.ts\n"
        with pytest.raises(ValueError):
            hls.assemble_from_manifest(manifest, fetch_segment=lambda u: calls.append(("seg", u)) or b"x" * 16,
                                       fetch_key=lambda u: calls.append(("key", u)) or KEY16)
        assert calls == []
