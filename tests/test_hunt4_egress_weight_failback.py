from bulk_downloader.multi_homed_egress import FailoverPolicy, MultiHomedEgressRouter


def test_higher_weight_route_becomes_active_after_damping():
    now = [10.0]
    router = MultiHomedEgressRouter(clock=lambda: now[0])
    router.register_interface("eth0", "192.0.2.10", "192.0.2.1", priority=10, weight=1)
    router.register_interface("eth1", "192.0.2.11", "192.0.2.1", priority=10, weight=10)
    assert router.get_active_interface().name == "eth0"

    now[0] = 16.0
    router.record_probe_result("eth1", success=True)
    assert router.get_active_interface().name == "eth1"


def test_weight_does_not_override_disabled_failback():
    now = [10.0]
    router = MultiHomedEgressRouter(
        failover_policy=FailoverPolicy(auto_failback=False), clock=lambda: now[0]
    )
    router.register_interface("eth0", "192.0.2.10", "192.0.2.1", priority=10, weight=1)
    router.register_interface("eth1", "192.0.2.11", "192.0.2.1", priority=10, weight=10)
    now[0] = 16.0
    router.record_probe_result("eth1", success=True)
    assert router.get_active_interface().name == "eth0"


def test_equal_route_does_not_displace_healthy_current():
    # lens (B1): "fail back only to a BETTER interface" -- an interface with the
    # same priority and weight is not better, so a healthy current route stays
    # even if its name sorts later.
    now = [10.0]
    router = MultiHomedEgressRouter(clock=lambda: now[0])
    router.register_interface("eth1", "192.0.2.11", "192.0.2.1", priority=10, weight=5)
    router.register_interface("eth0", "192.0.2.10", "192.0.2.1", priority=10, weight=5)
    assert router.get_active_interface().name == "eth1"
    now[0] = 16.0
    router.record_probe_result("eth0", success=True)
    assert router.get_active_interface().name == "eth1"
