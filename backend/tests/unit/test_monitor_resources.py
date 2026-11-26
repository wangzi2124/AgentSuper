from observability import monitor


def test_get_stats_exposes_service_resources():
    stats = monitor.get_stats()
    service = stats["service"]

    assert service["name"] == "agentsuper-backend"
    assert service["pid"] > 0
    assert isinstance(service["available"], bool)
    for key in (
        "uptime_seconds", "cpu_percent", "memory_percent",
        "host_cpu_percent", "host_memory_percent",
    ):
        assert isinstance(service[key], (int, float))
        assert service[key] >= 0
    for key in (
        "memory_rss_bytes", "memory_vms_bytes",
        "host_memory_total_bytes", "host_memory_used_bytes",
    ):
        assert isinstance(service[key], int)
        assert service[key] >= 0
