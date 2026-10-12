from __future__ import annotations

import json
import math
import os
import subprocess
from dataclasses import dataclass
from typing import Callable, Literal

import psutil

from crew.config import Resources


QUOTA_TIMEOUT_SECONDS = 3.0
QuotaStatus = Literal["available", "unavailable", "unknown"]


@dataclass(frozen=True)
class QuotaAvailability:
    status: QuotaStatus
    runway_seconds: float | None


@dataclass(frozen=True)
class ResourceReport:
    free_memory_gb: float | None
    load_per_core: float | None
    active_workers: int
    quota: QuotaAvailability
    ready: bool


def quota_availability(
    model: str,
    minimum_runway_seconds: float,
    *,
    runner: Callable = subprocess.run,
) -> QuotaAvailability:
    """Read one exact model's usable runway; report unknown on uncertainty."""
    unknown = QuotaAvailability("unknown", None)
    if not model or not math.isfinite(minimum_runway_seconds) or minimum_runway_seconds < 0:
        return unknown
    try:
        result = runner(
            ["quota-axi", "models", "--json"],
            capture_output=True,
            text=True,
            timeout=QUOTA_TIMEOUT_SECONDS,
            check=False,
        )
        if result.returncode != 0:
            return unknown
        payload = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return unknown

    records = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        return unknown
    matches = [record for record in records if isinstance(record, dict) and record.get("id") == model]
    if len(matches) != 1:
        return unknown
    record = matches[0]

    state = record.get("state")
    if not isinstance(state, dict) or state.get("status") != "fresh" or state.get("stale") is True:
        return unknown

    effective = record.get("effective")
    if not isinstance(effective, dict) or effective.get("status") != "known":
        return unknown

    runway_info = effective.get("runway")
    if not isinstance(runway_info, dict):
        return unknown
    runway_status = runway_info.get("status")
    if runway_status == "through_reset":
        return QuotaAvailability("available", None)
    if runway_status != "projected_exhaustion":
        return unknown

    runway = runway_info.get("usableRunwaySeconds")
    if isinstance(runway, bool) or not isinstance(runway, (int, float)):
        return unknown
    runway = float(runway)
    if not math.isfinite(runway) or runway < 0:
        return unknown
    status: QuotaStatus = "available" if runway >= minimum_runway_seconds else "unavailable"
    return QuotaAvailability(status, runway)


def check_resources(
    limits: Resources,
    model: str,
    *,
    active_workers: int = 0,
    psutil_api=psutil,
    quota_adapter: Callable[[str, float], QuotaAvailability] = quota_availability,
) -> ResourceReport:
    if active_workers < 0:
        raise ValueError("active_workers must be nonnegative")
    quota = quota_adapter(model, limits.min_quota_runway_seconds)
    try:
        free_memory_gb = psutil_api.virtual_memory().available / (1024**3)
        cpu_count = psutil_api.cpu_count() or os.cpu_count() or 0
        load_per_core = os.getloadavg()[0] / cpu_count if cpu_count else None
        if not math.isfinite(free_memory_gb) or load_per_core is None or not math.isfinite(load_per_core):
            free_memory_gb = load_per_core = None
    except (AttributeError, OSError, TypeError, ValueError, ZeroDivisionError):
        free_memory_gb = load_per_core = None
    ready = (
        free_memory_gb is not None
        and load_per_core is not None
        and free_memory_gb >= limits.min_free_mem_gb
        and load_per_core <= limits.max_load_per_core
        and active_workers < limits.max_workers
        and quota.status == "available"
    )
    return ResourceReport(free_memory_gb, load_per_core, active_workers, quota, ready)
