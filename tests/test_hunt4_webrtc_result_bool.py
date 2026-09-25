"""External WebRTC probe results must preserve the boolean verdict."""

from bulk_downloader import vpn_leak_tests

BD_GATE_SCOPE = "module"


def test_external_webrtc_passed_requires_boolean_true():
    tunnel_id = "hunt4-webrtc-result-bool"
    probe_id = vpn_leak_tests.ProbeId.WEBRTC.value
    try:
        vpn_leak_tests.record_external_probe_result(
            tunnel_id, probe_id, {"passed": True}
        )
        assert vpn_leak_tests._probe_webrtc(tunnel_id).passed is True

        vpn_leak_tests.record_external_probe_result(
            tunnel_id, probe_id, {"passed": False}
        )
        assert vpn_leak_tests._probe_webrtc(tunnel_id).passed is False

        vpn_leak_tests.record_external_probe_result(
            tunnel_id, probe_id, {"passed": "false"}
        )
        assert vpn_leak_tests._probe_webrtc(tunnel_id).passed is False

        # lens (B1): only the JSON literal true passes a CRITICAL probe --
        # neither the truthy integer 1 nor the string "true".
        for lenient in (1, "true"):
            vpn_leak_tests.record_external_probe_result(
                tunnel_id, probe_id, {"passed": lenient}
            )
            assert vpn_leak_tests._probe_webrtc(tunnel_id).passed is False, lenient
    finally:
        with vpn_leak_tests._external_lock:
            vpn_leak_tests._external_results.pop(tunnel_id, None)
