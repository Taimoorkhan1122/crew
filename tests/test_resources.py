import json
import subprocess
from types import SimpleNamespace

from crew.config import Resources
from crew.resources import QUOTA_TIMEOUT_SECONDS, QuotaAvailability, check_resources, quota_availability


_QUOTA_FIXTURE = {
    "models": [{
        "provider": "anthropic",
        "id": "anthropic/claude-haiku",
        "status": "ready",
        "effective": {"runway": {"status": "available"}},
        "usableRunwaySeconds": 180,
    }],
}


def test_quota_adapter_uses_inspected_model_schema_and_bounded_timeout() -> None:
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, json.dumps(_QUOTA_FIXTURE), "")

    result = quota_availability("anthropic/claude-haiku", 60, runner=run)

    assert result == QuotaAvailability("available", 180)
    assert calls[0][0] == ["quota-axi", "models", "--json"]
    assert calls[0][1]["timeout"] == QUOTA_TIMEOUT_SECONDS
    assert QUOTA_TIMEOUT_SECONDS <= 5


def test_quota_adapter_returns_unavailable_below_configured_runway() -> None:
    fixture = {"models": [{**_QUOTA_FIXTURE["models"][0], "usableRunwaySeconds": 30}]}
    result = quota_availability(
        "anthropic/claude-haiku", 60,
        runner=lambda args, **kwargs: subprocess.CompletedProcess(args, 0, json.dumps(fixture), ""),
    )

    assert result == QuotaAvailability("unavailable", 30)


def test_quota_adapter_returns_unknown_for_timeout_or_ambiguous_data() -> None:
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    timed_out = quota_availability("anthropic/claude-haiku", 60, runner=timeout)
    missing_model = quota_availability(
        "different/model", 60,
        runner=lambda args, **kwargs: subprocess.CompletedProcess(args, 0, json.dumps(_QUOTA_FIXTURE), ""),
    )

    assert timed_out == QuotaAvailability("unknown", None)
    assert missing_model == QuotaAvailability("unknown", None)


def test_resource_check_requires_memory_load_and_known_quota(monkeypatch) -> None:
    from crew import resources

    monkeypatch.setattr(resources.os, "getloadavg", lambda: (2.0, 0.0, 0.0))
    psutil_api = SimpleNamespace(
        virtual_memory=lambda: SimpleNamespace(available=4 * 1024**3),
        cpu_count=lambda: 4,
    )
    quota = lambda model, minimum: QuotaAvailability("unknown", None)
    limits = Resources(
        min_free_mem_gb=2,
        max_load_per_core=1.5,
        max_workers=2,
        min_quota_runway_seconds=60,
    )

    report = check_resources(limits, "anthropic/claude-haiku", psutil_api=psutil_api, quota_adapter=quota)

    assert report.free_memory_gb == 4
    assert report.load_per_core == 0.5
    assert report.quota.status == "unknown"
    assert report.ready is False


def test_resource_check_respects_worker_limit(monkeypatch) -> None:
    from crew import resources

    monkeypatch.setattr(resources.os, "getloadavg", lambda: (0.0, 0.0, 0.0))
    psutil_api = SimpleNamespace(
        virtual_memory=lambda: SimpleNamespace(available=4 * 1024**3),
        cpu_count=lambda: 4,
    )
    quota = lambda model, minimum: QuotaAvailability("available", 180)
    limits = Resources(
        min_free_mem_gb=2,
        max_load_per_core=1.5,
        max_workers=2,
        min_quota_runway_seconds=60,
    )

    report = check_resources(
        limits, "anthropic/claude-haiku", active_workers=2,
        psutil_api=psutil_api, quota_adapter=quota,
    )

    assert report.active_workers == 2
    assert report.ready is False


def test_quota_adapter_keeps_unknown_or_conflicting_status_conservative() -> None:
    base = _QUOTA_FIXTURE["models"][0]
    unknown_state = {"models": [{**base, "status": "unknown"}]}
    unknown_runway = {"models": [{
        **base, "effective": {"runway": {"status": "unknown"}},
    }]}

    for fixture in (unknown_state, unknown_runway):
        result = quota_availability(
            "anthropic/claude-haiku", 60,
            runner=lambda args, **kwargs: subprocess.CompletedProcess(args, 0, json.dumps(fixture), ""),
        )
        assert result == QuotaAvailability("unknown", None)
