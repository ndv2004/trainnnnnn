import pytest

from proctor.config import Config
from proctor.domain import Observation, Signal, Verdict, utc_add
from proctor.event_engine import EventEngine

P, N, U = Verdict.POSITIVE, Verdict.NEGATIVE, Verdict.UNKNOWN


class Clock:
    def __init__(self):
        self.seq = 0

    def observation(self, t, kind="FACE_NOT_VISIBLE", value=P, identity=None, reason=None, observed_at=None):
        self.seq += 1
        stamp = t if observed_at is None else observed_at
        return Observation(
            t,
            utc_add("2026-11-13T00:00:00Z", t),
            self.seq,
            {kind: Signal(value, stamp, stamp + 1.5, reason)},
            identity,
        )


def created(changes, kind=None):
    return [c for c in changes if c.action == "create" and (kind is None or c.event.type == kind)]


@pytest.mark.parametrize(
    "kind,opening,samples",
    [
        ("FACE_NOT_VISIBLE", 3, 5),
        ("MULTIPLE_PEOPLE", 2, 3),
        ("MULTIPLE_FACES", 2, 3),
        ("PHONE_VISIBLE", 1, 3),
        ("HEAD_AWAY", 4, 5),
        ("GAZE_AWAY", 5, 5),
    ],
)
def test_exact_open_boundary_and_one_event(kind, opening, samples):
    e, c = EventEngine(Config()), Clock()
    all_changes = []
    for i in range(opening * 10):
        changes = e.observe(c.observation(i / 10, kind))
        all_changes += changes
        assert not created(changes, kind)
    all_changes += e.observe(c.observation(opening, kind))
    assert len(created(all_changes, kind)) == 1
    assert created(all_changes, kind)[0].event.start == 0
    for i in range(opening * 10 + 1, 301):
        all_changes += e.observe(c.observation(i / 10, kind))
    assert len(created(all_changes, kind)) == 1
    ids = {x.event.id for x in all_changes if x.event.type == kind}
    assert len(ids) == 1


def test_duration_alone_cannot_replace_minimum_samples():
    e, c = EventEngine(Config()), Clock()
    changes = []
    for t in (0, 1, 2, 3):
        changes += e.observe(c.observation(t))
    assert not created(changes)


def test_phone_single_frame_does_not_open():
    e, c = EventEngine(Config()), Clock()
    assert not created(e.observe(c.observation(0, "PHONE_VISIBLE")))
    assert not created(e.observe(c.observation(0.5, "PHONE_VISIBLE", N)))


def test_unknown_resets_candidate_and_never_bridges():
    e, c = EventEngine(Config()), Clock()
    for t in (0, 0.5, 1, 1.5, 2, 2.9):
        assert not created(e.observe(c.observation(t)))
    e.observe(c.observation(3, value=U))
    for t in (3.1, 3.6, 4.1, 4.6, 5.1, 5.6, 6):
        assert not created(e.observe(c.observation(t)))
    assert len(created(e.observe(c.observation(6.1)))) == 1


def test_gap_over_1500ms_breaks_candidate():
    e, c = EventEngine(Config()), Clock()
    for t in (0, 0.5, 1, 1.5, 2):
        e.observe(c.observation(t))
    assert not created(e.observe(c.observation(3.501)))
    assert e.tracks["FACE_NOT_VISIBLE"].start == 3.501


def test_active_unknown_closes_at_last_positive():
    e, c = EventEngine(Config()), Clock()
    changes = []
    for t in (0, 0.5, 1, 1.5, 2, 2.5, 3):
        changes += e.observe(c.observation(t))
    event = created(changes)[0].event
    e.observe(c.observation(3.2, value=U))
    closing = e.tick(4.501)
    assert len(closing) == 1
    assert closing[0].event.end_at == event.last_observed_at
    assert closing[0].event.close_reason == "DATA_GAP"


def test_negative_clear_cooldown_and_session_end():
    e, c = EventEngine(Config()), Clock()
    for t in (0, 0.5, 1, 1.5, 2, 2.5, 3):
        e.observe(c.observation(t))
    e.observe(c.observation(3.5, value=N))
    assert e.observe(c.observation(4, value=N)) == []
    closed = e.observe(c.observation(4.5, value=N))
    assert closed[0].event.end_at == utc_add("2026-11-13T00:00:00Z", 3)
    for t in (5, 5.5, 6, 6.5, 7):
        assert not created(e.observe(c.observation(t)))
    e.observe(c.observation(7.5))
    assert e.tracks["FACE_NOT_VISIBLE"].state == "CANDIDATE"
    e.gap(8, "SESSION_ENDED")
    assert e.tracks["FACE_NOT_VISIBLE"].state == "NORMAL"


def test_old_model_result_does_not_supply_new_samples():
    e, c = EventEngine(Config()), Clock()
    e.observe(c.observation(0, "PHONE_VISIBLE"))
    for t in (0.5, 1, 1.5, 2):
        assert not created(e.observe(c.observation(t, "PHONE_VISIBLE", observed_at=0)))


def test_not_scheduled_neither_extends_nor_resets_fresh_module():
    e, c = EventEngine(Config()), Clock()
    e.observe(c.observation(0, "PHONE_VISIBLE"))
    e.observe(c.observation(0.2, "PHONE_VISIBLE", U, reason="NOT_SCHEDULED"))
    e.observe(c.observation(0.5, "PHONE_VISIBLE"))
    e.observe(c.observation(0.7, "PHONE_VISIBLE", U, reason="NOT_SCHEDULED"))
    assert len(created(e.observe(c.observation(1, "PHONE_VISIBLE")))) == 1
    assert e.tick(2.501)[0].event.close_reason == "DATA_GAP"


def test_single_twenty_second_turn_is_one_turn():
    e, c = EventEngine(Config()), Clock()
    changes = []
    for i in range(41):
        changes += e.observe(c.observation(i * 0.5, "HEAD_AWAY"))
    assert len(e.turns) == 1
    assert not created(changes, "HEAD_TURN_FREQUENT")
    assert len(created(changes, "HEAD_AWAY")) == 1


def test_four_independent_turns_and_neutral_requirement():
    e, c = EventEngine(Config()), Clock()
    changes = []
    for t, v in (
        (0, N),
        (0.5, N),
        (1, P),
        (1.5, P),
        (2, N),
        (2.5, N),
        (3, P),
        (3.5, P),
        (4, N),
        (4.5, N),
        (5, P),
        (5.5, P),
        (6, N),
        (6.5, N),
        (7, P),
        (7.5, P),
        (8, P),
    ):
        changes += e.observe(c.observation(t, "HEAD_AWAY", v))
    assert len(created(changes, "HEAD_TURN_FREQUENT")) == 1
    assert not created(changes, "HEAD_AWAY")


def test_unknown_does_not_count_as_neutral_return():
    e, c = EventEngine(Config()), Clock()
    for t, v in ((0, P), (0.5, P), (1, U), (1.5, U), (2, P), (2.5, P)):
        e.observe(c.observation(t, "HEAD_AWAY", v))
    assert len(e.turns) == 1


@pytest.mark.parametrize(
    "results,expected",
    [(["NO_MATCH", "INCONCLUSIVE", "NO_MATCH"], 1), (["NO_MATCH", "MATCH", "NO_MATCH"], 0)],
)
def test_identity_only_eligible_consecutive_results(results, expected):
    e, c = EventEngine(Config()), Clock()
    changes = []
    for t, result in zip((0, 60, 120), results):
        changes += e.observe(c.observation(t, value=U, identity=result))
    assert len(created(changes, "IDENTITY_REVIEW_REQUIRED")) == expected


def test_identity_window_expires():
    e, c = EventEngine(Config()), Clock()
    e.observe(c.observation(0, value=U, identity="NO_MATCH"))
    assert not created(e.observe(c.observation(181, value=U, identity="NO_MATCH")))


def test_duplicate_and_out_of_order_observation_ignored():
    e, c = EventEngine(Config()), Clock()
    o = c.observation(1)
    e.observe(o)
    assert e.observe(o) == []
    assert e.observe(c.observation(0.5)) == []
    assert e.tracks["FACE_NOT_VISIBLE"].samples == 1


def test_many_faces_never_implies_many_people():
    e, c = EventEngine(Config()), Clock()
    changes = []
    for t in (0, 0.5, 1, 1.5, 2):
        o = c.observation(t, "MULTIPLE_FACES")
        o.signals["MULTIPLE_PEOPLE"] = Signal(N, t, t + 1.5)
        changes += e.observe(o)
    assert len(created(changes, "MULTIPLE_FACES")) == 1
    assert not created(changes, "MULTIPLE_PEOPLE")
