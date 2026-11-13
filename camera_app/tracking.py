"""Conservative face association; geometry is never an identity credential."""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import count

import numpy as np

from .identity import IdentityError, normalize, recognize


@dataclass
class FaceDetection:
    box: tuple[float, float, float, float]
    embedding: np.ndarray | None = field(default=None, repr=False)
    quality_ok: bool = True
    payload: object = None


@dataclass
class Track:
    id: int
    box: tuple[float, float, float, float]
    profile_id: str | None = None
    name: str = "Đang xác định"
    status: str = "PENDING"
    embedding: np.ndarray | None = field(default=None, repr=False)
    payload: object = None
    last_seen: float = 0.0
    hits: int = 1
    _candidate_id: str | None = None
    _candidate_hits: int = 0
    _anchor: np.ndarray | None = field(default=None, repr=False)


def _iou(a, b):
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    intersection = max(x2 - x1, 0) * max(y2 - y1, 0)
    area_a = max(a[2] - a[0], 0) * max(a[3] - a[1], 0)
    area_b = max(b[2] - b[0], 0) * max(b[3] - b[1], 0)
    return intersection / max(area_a + area_b - intersection, 1e-8)


class FaceTracker:
    def __init__(self, threshold=.48, margin=.08, ttl=1.0, recognition_ttl=.6,
                 association_threshold=.42, confirmation_samples=2):
        self.threshold = threshold
        self.margin = margin
        self.ttl = ttl
        self.recognition_ttl = recognition_ttl
        self.association_threshold = association_threshold
        self.confirmation_samples = confirmation_samples
        self._tracks: dict[int, Track] = {}
        self._ids = count(1)
        self._main_profile_id = None
        self._temporary_main_id = None
        self._last_at = None

    def select_main(self, profile_id):
        self._main_profile_id = profile_id
        self._temporary_main_id = None

    def reset_main(self):
        """Explicitly release an anonymous target; never auto-switch on absence."""
        self._temporary_main_id = None

    def reset(self):
        self._tracks.clear()
        self._temporary_main_id = None
        self._last_at = None

    def update(self, detections, at, profiles, model_version):
        if self._last_at is not None and at <= self._last_at:
            return []  # Old frames must not extend identities or primary presence.
        self._last_at = at
        self._tracks = {key: track for key, track in self._tracks.items() if at - track.last_seen <= self.ttl}
        vectors = []
        for detection in detections:
            try:
                vector = normalize(detection.embedding) if detection.quality_ok and detection.embedding is not None else None
            except (IdentityError, ValueError, TypeError):
                vector = None
            vectors.append(vector)
        # Embedding comparison survives a position swap; ambiguity causes new tracks.
        possibilities = []
        for index, (detection, vector) in enumerate(zip(detections, vectors)):
            for track in self._tracks.values():
                overlap = _iou(detection.box, track.box)
                if vector is not None and track._anchor is not None:
                    if vector.shape != track._anchor.shape:
                        continue
                    similarity = float(vector @ track._anchor)
                    if similarity < self.association_threshold:
                        continue
                    score = similarity + .05 * overlap
                elif vector is None and track._anchor is None and overlap > .6:
                    score = .1 * overlap
                else:
                    # In particular, don't transfer a named track by position only.
                    continue
                possibilities.append((score, index, track.id))
        assigned_detections, assigned_tracks, assignments = set(), set(), {}
        for score, index, track_id in sorted(possibilities, reverse=True):
            if index in assigned_detections or track_id in assigned_tracks:
                continue
            competitors = [s for s, i, t in possibilities if
                           (i == index and t != track_id) or (t == track_id and i != index)]
            if competitors and score - max(competitors) < self.margin:
                continue
            assignments[index] = self._tracks[track_id]
            assigned_detections.add(index)
            assigned_tracks.add(track_id)
        result = []
        for index, (detection, vector) in enumerate(zip(detections, vectors)):
            track = assignments.get(index)
            if track is None:
                track = Track(next(self._ids), tuple(detection.box))
                self._tracks[track.id] = track
            else:
                track.hits += 1
            track.box = tuple(detection.box)
            track.last_seen = at
            track.payload = detection.payload
            track.embedding = vector
            match = recognize(vector, profiles, model_version, self.threshold, self.margin)
            if match.profile_id is not None:
                track._candidate_hits = track._candidate_hits + 1 if track._candidate_id == match.profile_id else 1
                track._candidate_id = match.profile_id
                if track._candidate_hits >= self.confirmation_samples:
                    track.profile_id, track.name, track.status = match.profile_id, match.name, "MATCH"
                else:
                    track.profile_id, track.name, track.status = None, "Đang xác định", "PENDING"
            else:
                track._candidate_id, track._candidate_hits = None, 0
                track.profile_id, track.name, track.status = None, match.name, match.status
            if vector is not None:
                # Fixed anchor limits gradual appearance drift through different people.
                if track._anchor is None:
                    track._anchor = vector.copy()
            result.append(track)
        # Duplicate gallery identities in the same frame can be a screen/print or
        # ambiguous embedding. Don't claim which is the selected primary person.
        duplicates = {t.profile_id for t in result if t.profile_id is not None and
                      sum(other.profile_id == t.profile_id for other in result) > 1}
        for track in result:
            if track.profile_id in duplicates:
                track.profile_id, track.name, track.status = None, "Người", "AMBIGUOUS"
        return result

    def main_track(self, tracks):
        if self._main_profile_id is not None:
            matches = [t for t in tracks if t.profile_id == self._main_profile_id and t.status == "MATCH"]
            return matches[0] if len(matches) == 1 else None
        if self._temporary_main_id is None:
            stable = [t for t in tracks if t.hits >= self.confirmation_samples and t.embedding is not None]
            if stable:
                chosen = max(stable, key=lambda t: (t.box[2] - t.box[0]) * (t.box[3] - t.box[1]))
                self._temporary_main_id = chosen.id
        return next((t for t in tracks if t.id == self._temporary_main_id and t.embedding is not None), None)
