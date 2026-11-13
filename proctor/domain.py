from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import uuid4


def uid():
    return str(uuid4())


def utcnow():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def utc_add(value: str, seconds: float):
    return (
        (datetime.fromisoformat(value.replace("Z", "+00:00")) + timedelta(seconds=seconds))
        .isoformat()
        .replace("+00:00", "Z")
    )


class Verdict(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Signal:
    value: Verdict
    observed_at: float
    valid_until: float
    reason: str | None = None

    def at(self, now: float):
        if self.observed_at > now or now > self.valid_until:
            return Verdict.UNKNOWN
        return self.value


@dataclass
class Observation:
    at: float
    utc: str
    seq: int
    signals: dict[str, Signal] = field(default_factory=dict)
    identity: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class EngineEvent:
    id: str
    type: str
    start: float
    confirmed: float
    start_at: str
    confirmed_at: str
    last_positive: float
    last_observed_at: str
    end_at: str | None = None
    close_reason: str | None = None


@dataclass
class Change:
    action: str
    event: EngineEvent
