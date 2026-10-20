"""Deterministic traces, trial order, and evidence formats (no service access)."""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICIES = ("equal", "severity")
CONDITIONS = ("healthy", "broker_outage")
LIMITS = {
    "app": {"cpus": 1.0, "mem_limit": "768m"},
    "migrate": {"cpus": 1.0, "mem_limit": "512m"},
    "postgres": {"cpus": 1.0, "mem_limit": "512m"},
    "redis": {"cpus": 0.5, "mem_limit": "256m"},
    "rabbitmq": {"cpus": 1.0, "mem_limit": "768m"},
}


@dataclass(frozen=True)
class Settings:
    reports: int = 160
    interval_ms: float = 10
    dispatch_delay_ms: float = 0
    concurrency: int = 16
    drain_seconds: float = 120
    outage_hold_seconds: float = 3
    database_sample_seconds: float = 0.25
    service_sample_seconds: float = 2
    order_seed: int = 20261020

    def __post_init__(self):
        for key, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError(f"Invalid numeric setting: {key}")
            if not math.isfinite(value):
                raise ValueError(f"Nonfinite setting: {key}")
        if not isinstance(self.reports, int) or not 40 <= self.reports <= 400:
            raise ValueError("Reports must be an integer between 40 and 400")
        if self.reports % 4:
            raise ValueError("Reports must be divisible by four (25% CRITICAL)")
        if not 0 <= self.interval_ms <= 100:
            raise ValueError("Interval must be between 0 and 100 milliseconds")
        if not 0 <= self.dispatch_delay_ms <= 200:
            raise ValueError("Artificial delay must be between 0 and 200 milliseconds")
        if self.concurrency != 16 or not isinstance(self.concurrency, int):
            raise ValueError("This protocol fixes client concurrency at sixteen")
        if (self.drain_seconds, self.outage_hold_seconds) != (120, 3):
            raise ValueError("This protocol fixes drain at 120s and outage hold at 3s")
        if (self.database_sample_seconds, self.service_sample_seconds) != (0.25, 2):
            raise ValueError("This protocol fixes database/service sampling at .25s/2s")
        if not isinstance(self.order_seed, int) or not 0 <= self.order_seed < 2**32:
            raise ValueError("Invalid trial-order seed")


def write_json(path: Path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, default=str, allow_nan=False)
        stream.write("\n")


def append_json(path: Path, value):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, default=str, allow_nan=False) + "\n")


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def fingerprint() -> dict[str, str]:
    paths = {
        ROOT / "docker-compose.integration.yml",
        ROOT / "integration_tests/stack.py",
    }
    paths.update((ROOT / "app").rglob("*.py"))
    paths.update((ROOT / "experiments").rglob("*.py"))
    paths.update(ROOT / "app" / name for name in ("Dockerfile", "requirements.txt"))
    paths.add(ROOT / "experiments/requirements.txt")
    return {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(paths)
        if "__pycache__" not in path.parts
    }


def trace(settings: Settings, seed: int, *, warmup=False) -> list[dict]:
    count = 8 if warmup else settings.reports
    severities = ["CRITICAL"] * (count // 4) + ["STANDARD"] * (3 * count // 4)
    random.Random(seed).shuffle(severities)
    return [
        {
            "index": i,
            "identity": f"study:{seed}:{i}",
            "scheduled_seconds": i * settings.interval_ms / 1000,
            "body": {
                "emergency_type": "FIRE",
                "severity": severity,
                "latitude": 5.555,
                "longitude": -0.1969,
                "description": "Synthetic scheduling study",
            },
        }
        for i, severity in enumerate(severities)
    ]


def evidence_hashes(directory: Path) -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.iterdir())
        if path.is_file() and path.name != "result.json"
    }


def verify_evidence(directory: Path, recorded):
    hashes = recorded.get("evidence_sha256")
    if not hashes or hashes != evidence_hashes(directory):
        raise ValueError("Retained trial evidence changed or is incomplete")


def trials(settings: Settings, *, calibration: bool) -> list[dict]:
    rng = random.Random(settings.order_seed)
    result = []
    for condition in CONDITIONS:
        for seed in [0] if calibration else range(1, 11):
            order = list(POLICIES)
            rng.shuffle(order)
            for policy in order:
                result.append(
                    {
                        "trial": f"{len(result)+1:02d}-{condition}-{seed}-{policy}",
                        "seed": seed,
                        "condition": condition,
                        "policy": policy,
                        "trace_sha256": digest(trace(settings, seed)),
                    }
                )
    return result


def plan(settings: Settings, *, calibration: bool) -> dict:
    return {
        "schema": 1,
        "phase": "calibration" if calibration else "measured",
        "settings": asdict(settings),
        "service_limits": LIMITS,
        "prefetch": 1,
        "trials": trials(settings, calibration=calibration),
        "source": fingerprint(),
    }
