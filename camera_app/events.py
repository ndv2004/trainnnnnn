"""Camera-independent temporal observations; times are monotonic seconds.

Only pairs of fresh, adjacent POSITIVE samples contribute elapsed time. UNKNOWN
pauses an episode and a data gap expires it, so a frozen frame is never evidence.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from enum import Enum
from uuid import uuid4


class Verdict(str, Enum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Signal:
    value: Verdict | str
    observed_at: float
    valid_until: float


@dataclass
class Alert:
    id: str
    kind: str
    message: str
    started_at: float
    observed_at: float
    duration: float
    samples: int


MESSAGES = {
    "FACE_NOT_VISIBLE": "Không thấy khuôn mặt người chính",
    "MULTIPLE_PEOPLE": "Có nhiều người được phát hiện",
    "MULTIPLE_FACES": "Có nhiều khuôn mặt được phát hiện",
    "PHONE_VISIBLE": "Điện thoại xuất hiện",
    "HEAD_AWAY": "Quay đầu kéo dài",
    "HEAD_TURN_FREQUENT": "Quay đầu nhiều lượt",
    "GAZE_AWAY": "Nhìn lệch kéo dài (ước lượng)",
}

DEFAULT_RULES = {
    "FACE_NOT_VISIBLE": {"open_seconds": 3.0, "samples": 2, "clear_seconds": 0.5},
    "MULTIPLE_PEOPLE": {"open_seconds": 2.0, "samples": 2, "clear_seconds": 0.5},
    "MULTIPLE_FACES": {"open_seconds": 2.0, "samples": 2, "clear_seconds": 0.5},
    "PHONE_VISIBLE": {"open_seconds": 1.0, "samples": 3, "clear_seconds": 0.7},
    "HEAD_AWAY": {"open_seconds": 4.0, "samples": 2, "clear_seconds": 0.3},
    "GAZE_AWAY": {"open_seconds": 5.0, "samples": 2, "clear_seconds": 0.5},
}


@dataclass
class _Episode:
    start: float | None = None
    duration: float = 0.0
    samples: int = 0
    last_sample: float | None = None
    last_value: Verdict = Verdict.UNKNOWN
    last_known: float | None = None
    clear_duration: float = 0.0
    alert: Alert | None = None
    cooldown_until: float = -1.0


class EventEngine:
    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.max_gap = float(self.config.get("max_gap_seconds", 1.5))
        self.cooldown = float(self.config.get("cooldown_seconds", 2.0))
        self.rules = {kind: dict(rule) for kind, rule in DEFAULT_RULES.items()}
        for kind, rule in self.config.get("rules", {}).items():
            if kind in self.rules:
                self.rules[kind].update(rule)
        self.tracks = {kind: _Episode() for kind in self.rules}
        self.last_at = float("-inf")
        self.primary_key: str | None = None
        self.suspended = False
        self.turns: deque[float] = deque()
        self.turn_armed = True
        self.turn_counted = False
        self.neutral_duration = 0.0
        self.turn_last_at: float | None = None
        self.turn_last_value = Verdict.UNKNOWN
        self.last_turn_seconds = 0.0
        self.frequent_alert: Alert | None = None

    def _reset_track(self, kind: str, cooldown_at: float | None = None):
        old = self.tracks[kind]
        until = old.cooldown_until
        if old.alert is not None and cooldown_at is not None:
            until = cooldown_at + self.cooldown
        self.tracks[kind] = _Episode(cooldown_until=until)

    def reset(self):
        """Clear behavioral evidence without changing the monotonic clock."""
        self.tracks = {kind: _Episode() for kind in self.rules}
        self.turns.clear()
        self.turn_armed = True
        self.turn_counted = False
        self.turn_last_at = None
        self.turn_last_value = Verdict.UNKNOWN
        self.neutral_duration = self.last_turn_seconds = 0.0
        self.frequent_alert = None

    def _reset_primary(self):
        for kind in ("FACE_NOT_VISIBLE", "HEAD_AWAY", "GAZE_AWAY"):
            self.tracks[kind] = _Episode()
        self.turns.clear()
        self.turn_armed = True
        self.turn_counted = False
        self.turn_last_at = None
        self.turn_last_value = Verdict.UNKNOWN
        self.neutral_duration = self.last_turn_seconds = 0.0
        self.frequent_alert = None

    def _signal(self, kind: str, signals: dict, now: float) -> tuple[Verdict, float]:
        supplied = signals.get(kind, Verdict.UNKNOWN)
        if isinstance(supplied, Signal):
            if supplied.observed_at > now or now > supplied.valid_until:
                return Verdict.UNKNOWN, now
            if now - supplied.observed_at > self.max_gap:
                return Verdict.UNKNOWN, now
            try:
                return Verdict(supplied.value), supplied.observed_at
            except ValueError:
                return Verdict.UNKNOWN, now
        try:
            return Verdict(supplied), now
        except (ValueError, TypeError):
            return Verdict.UNKNOWN, now

    def tick(self, now: float) -> list[Alert]:
        """Expire stale evidence even while the camera/model produces no output."""
        for kind, track in list(self.tracks.items()):
            if track.last_known is not None and now - track.last_known > self.max_gap:
                self._reset_track(kind)
                if kind == "HEAD_AWAY":
                    self.turn_armed = False
                    self.turn_counted = False
                    self.neutral_duration = 0.0
                    self.turn_last_value = Verdict.UNKNOWN
        while self.turns and self.turns[0] < now - float(self.config.get("turn_window_seconds", 60.0)):
            self.turns.popleft()
        if len(self.turns) < int(self.config.get("turn_count", 4)):
            self.frequent_alert = None
        return self.alerts

    @property
    def alerts(self) -> list[Alert]:
        if self.suspended:
            return []
        result = [track.alert for track in self.tracks.values() if track.alert is not None]
        if self.frequent_alert is not None:
            result.append(self.frequent_alert)
        return result

    def observe(self, now: float, signals: dict, suspended: bool = False,
                primary_key: str | None = None) -> list[Alert]:
        if now <= self.last_at:
            return self.alerts
        self.last_at = now
        if primary_key != self.primary_key:
            self._reset_primary()
            self.primary_key = primary_key
        if suspended:
            self.reset()
            self.suspended = True
            return []
        self.suspended = False
        self.tick(now)
        head_value, head_at = self._signal("HEAD_AWAY", signals, now)
        # Count a completed duration before NEGATIVE clears the current turn.
        previous_head_duration = self.tracks["HEAD_AWAY"].duration
        head_fresh = (self.tracks["HEAD_AWAY"].last_sample is None
                      or head_at > self.tracks["HEAD_AWAY"].last_sample)
        for kind, rule in self.rules.items():
            value, at = self._signal(kind, signals, now)
            track = self.tracks[kind]
            # Repeated cached observations neither extend nor interrupt an episode.
            if track.last_sample is not None and at <= track.last_sample:
                continue
            dt = 0.0 if track.last_sample is None else at - track.last_sample
            adjacent = 0 <= dt <= self.max_gap
            if value == Verdict.UNKNOWN:
                track.last_value = value
                track.last_sample = at
                track.clear_duration = 0.0
                continue
            track.last_known = at
            if value == Verdict.POSITIVE:
                if track.start is None:
                    track.start = at
                if adjacent and track.last_value == Verdict.POSITIVE:
                    track.duration += dt
                track.samples += 1
                track.clear_duration = 0.0
                if (track.alert is None and track.duration + 1e-8 >= float(rule["open_seconds"])
                        and track.samples >= int(rule["samples"]) and at >= track.cooldown_until):
                    track.alert = Alert(uuid4().hex, kind, MESSAGES[kind], track.start,
                                        at, track.duration, track.samples)
                elif track.alert is not None:
                    track.alert.observed_at = at
                    track.alert.duration = track.duration
                    track.alert.samples = track.samples
            else:
                if adjacent and track.last_value == Verdict.NEGATIVE:
                    track.clear_duration += dt
                else:
                    track.clear_duration = 0.0
                if kind in ("HEAD_AWAY", "FACE_NOT_VISIBLE"):
                    # Current timers reset immediately on a valid return/presence;
                    # alert clearing can still wait for its debounce interval.
                    track.duration = 0.0
                if track.alert is None or track.clear_duration + 1e-8 >= float(rule["clear_seconds"]):
                    self._reset_track(kind, at)
                    track = self.tracks[kind]
                    track.last_known = at
            track.last_value, track.last_sample = value, at
        if head_fresh:
            self._turns(head_value, head_at, previous_head_duration)
        return self.alerts

    def _turns(self, value: Verdict, at: float, previous_duration: float):
        dt = 0.0 if self.turn_last_at is None else at - self.turn_last_at
        if dt > self.max_gap:
            self.turn_armed = False
            self.neutral_duration = 0.0
        if value == Verdict.NEGATIVE:
            if previous_duration > 0:
                self.last_turn_seconds = previous_duration
            if self.turn_last_value == Verdict.NEGATIVE and dt <= self.max_gap:
                self.neutral_duration += dt
            else:
                self.neutral_duration = 0.0
            if self.neutral_duration + 1e-8 >= float(self.config.get("neutral_min_seconds", 0.5)):
                self.turn_armed = True
                self.turn_counted = False
        elif value == Verdict.POSITIVE:
            self.neutral_duration = 0.0
            if (self.turn_armed and not self.turn_counted
                    and self.tracks["HEAD_AWAY"].duration + 1e-8
                    >= float(self.config.get("turn_min_seconds", 0.5))):
                self.turns.append(at)
                self.turn_counted = True
                self.turn_armed = False
        else:
            self.neutral_duration = 0.0
        self.turn_last_value, self.turn_last_at = value, at
        count = int(self.config.get("turn_count", 4))
        if len(self.turns) >= count and self.frequent_alert is None:
            self.frequent_alert = Alert(uuid4().hex, "HEAD_TURN_FREQUENT",
                                        MESSAGES["HEAD_TURN_FREQUENT"], self.turns[0], at,
                                        at - self.turns[0], len(self.turns))
        elif self.frequent_alert is not None:
            self.frequent_alert.observed_at = at
            self.frequent_alert.samples = len(self.turns)

    def snapshot(self) -> dict:
        return {
            "head_away_seconds": self.tracks["HEAD_AWAY"].duration,
            "absence_seconds": self.tracks["FACE_NOT_VISIBLE"].duration,
            "last_turn_seconds": self.last_turn_seconds,
            "turn_count": len(self.turns),
            "alerts": [asdict(alert) for alert in self.alerts],
            "suspended": self.suspended,
        }
