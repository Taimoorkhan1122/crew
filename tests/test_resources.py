import copy
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from crew.config import Resources
from crew.resources import QUOTA_TIMEOUT_SECONDS, QuotaAvailability, check_resources, quota_availability


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "quota-axi-0.1.60-models.json"

THROUGH_RESET_MODEL = "claude-haiku-5-5"
PROJECTED_MODEL = "gpt-5.1-codex"
UNAUTHENTICATED_MODEL = "grok-4"


def _load_models() -> list[dict]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == 1
    return copy.deepcopy(payload["models"])


def _record(models: list[dict], model_id: str) -> dict:
    matches = [record for record in models if record["id"] == model_id]
    assert len(matches) == 1
    return matches[0]


def _runner(models: list[dict]):
    def run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, json.dumps({"models": models}), "")

    return run


def test_quota_adapter_uses_exact_model_command_and_bounded_timeout() -> None:
    calls: list = []
    models = _load_models()

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, json.dumps({"models": models}), "")

    result = quota_availability(THROUGH_RESET_MODEL, 60, runner=run)

    assert result == QuotaAvailability("available", None)
    assert calls[0][0] == ["quota-axi", "models", "--json"]
    assert calls[0][1]["timeout"] == QUOTA_TIMEOUT_SECONDS
    assert QUOTA_TIMEOUT_SECONDS <= 5


@pytest.mark.parametrize("minimum", [0, 60, 10**9])
def test_quota_adapter_through_reset_is_available_at_every_minimum(minimum: float) -> None:
    models = _load_models()

    result = quota_availability(THROUGH_RESET_MODEL, minimum, runner=_runner(models))

    assert result == QuotaAvailability("available", None)


def test_quota_adapter_through_reset_is_available_when_stale_key_is_missing() -> None:
    models = _load_models()
    del _record(models, THROUGH_RESET_MODEL)["state"]["stale"]

    result = quota_availability(THROUGH_RESET_MODEL, 60, runner=_runner(models))

    assert result == QuotaAvailability("available", None)


def test_quota_adapter_projected_exhaustion_compares_runway_to_minimum() -> None:
    models = _load_models()
    runway = float(_record(models, PROJECTED_MODEL)["effective"]["runway"]["usableRunwaySeconds"])
    assert runway > 0

    above = quota_availability(PROJECTED_MODEL, runway - 1, runner=_runner(models))
    equal = quota_availability(PROJECTED_MODEL, runway, runner=_runner(models))
    below = quota_availability(PROJECTED_MODEL, runway + 1, runner=_runner(models))

    assert above == QuotaAvailability("available", runway)
    assert equal == QuotaAvailability("available", runway)
    assert below == QuotaAvailability("unavailable", runway)


def test_quota_adapter_requires_one_exact_model_match() -> None:
    models = _load_models()

    missing = quota_availability("missing/model", 60, runner=_runner(models))

    models.append(copy.deepcopy(_record(models, PROJECTED_MODEL)))
    duplicated = quota_availability(PROJECTED_MODEL, 60, runner=_runner(models))

    assert missing == QuotaAvailability("unknown", None)
    assert duplicated == QuotaAvailability("unknown", None)


def test_quota_adapter_returns_unknown_for_unauthenticated_state() -> None:
    models = _load_models()
    assert _record(models, UNAUTHENTICATED_MODEL)["state"]["status"] == "auth_required"

    result = quota_availability(UNAUTHENTICATED_MODEL, 60, runner=_runner(models))

    assert result == QuotaAvailability("unknown", None)


def test_quota_adapter_returns_unknown_when_state_is_stale() -> None:
    models = _load_models()
    _record(models, PROJECTED_MODEL)["state"]["stale"] = True

    result = quota_availability(PROJECTED_MODEL, 60, runner=_runner(models))

    assert result == QuotaAvailability("unknown", None)


def test_quota_adapter_returns_unknown_when_state_status_is_not_fresh() -> None:
    models = _load_models()
    _record(models, PROJECTED_MODEL)["state"]["status"] = "unavailable"

    result = quota_availability(PROJECTED_MODEL, 60, runner=_runner(models))

    assert result == QuotaAvailability("unknown", None)


def test_quota_adapter_returns_unknown_for_missing_effective() -> None:
    models = _load_models()
    del _record(models, PROJECTED_MODEL)["effective"]

    result = quota_availability(PROJECTED_MODEL, 60, runner=_runner(models))

    assert result == QuotaAvailability("unknown", None)


def test_quota_adapter_returns_unknown_for_unknown_effective_status() -> None:
    models = _load_models()
    _record(models, PROJECTED_MODEL)["effective"]["status"] = "unknown"

    result = quota_availability(PROJECTED_MODEL, 60, runner=_runner(models))

    assert result == QuotaAvailability("unknown", None)


def test_quota_adapter_returns_unknown_for_unknown_runway_status() -> None:
    models = _load_models()
    runway = _record(models, PROJECTED_MODEL)["effective"]["runway"]
    runway["status"] = "unknown"
    runway.pop("usableRunwaySeconds", None)

    result = quota_availability(PROJECTED_MODEL, 60, runner=_runner(models))

    assert result == QuotaAvailability("unknown", None)


def test_quota_adapter_returns_unknown_for_missing_runway_seconds() -> None:
    models = _load_models()
    del _record(models, PROJECTED_MODEL)["effective"]["runway"]["usableRunwaySeconds"]

    result = quota_availability(PROJECTED_MODEL, 60, runner=_runner(models))

    assert result == QuotaAvailability("unknown", None)


@pytest.mark.parametrize(
    "value",
    [True, -1, float("nan"), float("inf")],
    ids=["bool", "negative", "nan", "infinite"],
)
def test_quota_adapter_returns_unknown_for_invalid_runway_seconds(value: float) -> None:
    models = _load_models()
    _record(models, PROJECTED_MODEL)["effective"]["runway"]["usableRunwaySeconds"] = value

    result = quota_availability(PROJECTED_MODEL, 60, runner=_runner(models))

    assert result == QuotaAvailability("unknown", None)


def test_quota_adapter_returns_unknown_for_timeout() -> None:
    def timeout(args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    result = quota_availability(THROUGH_RESET_MODEL, 60, runner=timeout)

    assert result == QuotaAvailability("unknown", None)


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

    report = check_resources(limits, PROJECTED_MODEL, psutil_api=psutil_api, quota_adapter=quota)

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
        limits, PROJECTED_MODEL, active_workers=2,
        psutil_api=psutil_api, quota_adapter=quota,
    )

    assert report.active_workers == 2
    assert report.ready is False
