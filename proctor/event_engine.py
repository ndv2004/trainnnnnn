"""Deterministic temporal rules. No I/O, model calls, wall clock or camera dependencies."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass

from .config import Config
from .domain import Change, EngineEvent, Observation, Verdict, uid


@dataclass
class Track:
    state: str = "NORMAL"
    start: float | None = None
    start_utc: str | None = None
    samples: int = 0
    last_known: float | None = None
    negative_since: float | None = None
    cooldown_until: float = -1
    event: EngineEvent | None = None


class EventEngine:
    def __init__(self, config: Config):
        self.cfg = config
        self.tracks = {k: Track() for k in config.rules}
        self.last_at = -1.0
        self.last_seq = -1
        self.turn_start: float | None = None
        self.neutral_start: float | None = None
        self.turn_armed = True
        self.turns: deque[float] = deque()
        self.frequent_latched = False
        self.frequent_cooldown = -1.0
        self.identity_no_matches: deque[float] = deque(maxlen=2)
        self.identity_latched = False

    def _close(self, track: Track, reason: str, now: float) -> list[Change]:
        changes = []
        if track.event:
            track.event.end_at = track.event.last_observed_at
            track.event.close_reason = reason
            changes.append(Change("close", deepcopy(track.event)))
        track.state = "COOLDOWN" if track.event else "NORMAL"
        track.cooldown_until = now + self.cfg.cooldown_ms / 1000 if track.event else -1
        track.event = None
        track.start = track.start_utc = track.negative_since = None
        track.samples = 0
        track.last_known = None
        return changes

    def gap(self, now: float, reason: str = "DATA_GAP") -> list[Change]:
        changes = []
        for track in self.tracks.values():
            changes += self._close(track, reason, now)
        self.turn_start = self.neutral_start = None
        self.turn_armed = False  # a new, observed neutral interval must re-arm counting
        return changes

    def tick(self, now: float) -> list[Change]:
        changes = []
        for track in self.tracks.values():
            if track.last_known is not None and now - track.last_known > self.cfg.max_gap_ms / 1000:
                changes += self._close(track, "DATA_GAP", now)
        return changes

    def _pulse(self, kind: str, o: Observation) -> Change:
        event = EngineEvent(uid(), kind, o.at, o.at, o.utc, o.utc, o.at, o.utc, o.utc, "RULE_CONFIRMED")
        return Change("create", event)

    def observe(self, o: Observation) -> list[Change]:
        if o.at <= self.last_at or o.seq <= self.last_seq:
            return []
        global_gap = self.last_at >= 0 and o.at - self.last_at > self.cfg.max_gap_ms / 1000
        changes = self.gap(o.at) if global_gap else self.tick(o.at)
        self.last_at, self.last_seq = o.at, o.seq
        for kind, rule in self.cfg.rules.items():
            track = self.tracks[kind]
            signal = o.signals.get(kind)
            if signal and signal.reason == "NOT_SCHEDULED":
                continue  # no new evidence; tick() still expires this module independently
            value = signal.at(o.at) if signal else Verdict.UNKNOWN
            # Cached observations must not provide extra samples or time to any rule.
            if signal and abs(signal.observed_at - o.at) > 1e-6:
                value = Verdict.UNKNOWN
            if value == Verdict.UNKNOWN:
                track.negative_since = None
                if track.state == "CANDIDATE":
                    changes += self._close(track, "DATA_GAP", o.at)
                continue
            track.last_known = o.at
            if track.state == "COOLDOWN":
                if o.at < track.cooldown_until:
                    continue
                track.state = "NORMAL"
            if value == Verdict.POSITIVE:
                track.negative_since = None
                if track.state == "NORMAL":
                    track.state, track.start, track.start_utc, track.samples = "CANDIDATE", o.at, o.utc, 1
                elif track.state == "CANDIDATE":
                    track.samples += 1
                if (
                    track.state == "CANDIDATE"
                    and (o.at - track.start) * 1000 >= rule.open_ms - 1e-6
                    and track.samples >= rule.samples
                ):
                    track.state = "ACTIVE"
                    track.event = EngineEvent(
                        uid(), kind, track.start, o.at, track.start_utc, o.utc, o.at, o.utc
                    )
                    changes.append(Change("create", deepcopy(track.event)))
                elif track.state == "ACTIVE":
                    track.event.last_positive = o.at
                    track.event.last_observed_at = o.utc
                    changes.append(Change("update", deepcopy(track.event)))
            elif track.state == "CANDIDATE":
                changes += self._close(track, "CLEARED", o.at)
            elif track.state == "ACTIVE":
                if track.negative_since is None:
                    track.negative_since = o.at
                if (o.at - track.negative_since) * 1000 >= rule.clear_ms - 1e-6:
                    changes += self._close(track, "CLEARED", o.at)
        changes += self._frequent(o)
        changes += self._identity(o)
        return changes

    def _frequent(self, o: Observation) -> list[Change]:
        cutoff = o.at - self.cfg.turn_window_ms / 1000
        while self.turns and self.turns[0] < cutoff:
            self.turns.popleft()
        if len(self.turns) < self.cfg.turn_count:
            self.frequent_latched = False
        signal = o.signals.get("HEAD_AWAY")
        value = signal.at(o.at) if signal and signal.observed_at == o.at else Verdict.UNKNOWN
        # HEAD_AWAY NEGATIVE is emitted only in the neutral hysteresis region.
        if value == Verdict.UNKNOWN:
            self.turn_start = self.neutral_start = None
            self.turn_armed = False
        elif value == Verdict.NEGATIVE:
            self.turn_start = None
            if self.neutral_start is None:
                self.neutral_start = o.at
            if (o.at - self.neutral_start) * 1000 >= self.cfg.neutral_min_ms - 1e-6:
                self.turn_armed = True
        else:
            self.neutral_start = None
            if self.turn_start is None:
                self.turn_start = o.at
            if self.turn_armed and (o.at - self.turn_start) * 1000 >= self.cfg.turn_min_ms - 1e-6:
                self.turns.append(o.at)
                self.turn_armed = False
        if (
            len(self.turns) >= self.cfg.turn_count
            and not self.frequent_latched
            and o.at >= self.frequent_cooldown
        ):
            self.frequent_latched = True
            self.frequent_cooldown = o.at + self.cfg.cooldown_ms / 1000
            return [self._pulse("HEAD_TURN_FREQUENT", o)]
        return []

    def _identity(self, o: Observation) -> list[Change]:
        if o.identity == "MATCH":
            self.identity_no_matches.clear()
            self.identity_latched = False
        elif o.identity == "NO_MATCH":
            if (
                self.identity_no_matches
                and (o.at - self.identity_no_matches[-1]) * 1000 > self.cfg.identity_window_ms
            ):
                self.identity_no_matches.clear()
                self.identity_latched = False
            self.identity_no_matches.append(o.at)
            if len(self.identity_no_matches) == 2 and not self.identity_latched:
                self.identity_latched = True
                return [self._pulse("IDENTITY_REVIEW_REQUIRED", o)]
        return []
