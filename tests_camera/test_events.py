from camera_app.events import EventEngine, Signal


def samples(engine, kind, start, end, step=.25, value="POSITIVE", **kwargs):
    at = start
    while at <= end + 1e-8:
        engine.observe(round(at, 8), {kind: value}, **kwargs)
        at += step


def test_continuous_episode_has_one_stable_alert_and_unknown_adds_no_time():
    engine = EventEngine()
    samples(engine, "HEAD_AWAY", 0, 4)
    assert len(engine.alerts) == 1
    first_id = engine.alerts[0].id
    engine.observe(4.5, {"HEAD_AWAY": "UNKNOWN"})
    engine.observe(5, {"HEAD_AWAY": "POSITIVE"})
    assert engine.snapshot()["head_away_seconds"] == 4
    samples(engine, "HEAD_AWAY", 5.25, 7)
    assert engine.alerts[0].id == first_id
    assert engine.snapshot()["head_away_seconds"] == 6
    assert engine.snapshot()["turn_count"] == 1
    engine.observe(7.25, {"HEAD_AWAY": "NEGATIVE"})
    assert engine.snapshot()["head_away_seconds"] == 0
    assert engine.snapshot()["last_turn_seconds"] == 6


def test_phone_needs_duration_and_three_fresh_samples():
    engine = EventEngine()
    engine.observe(0, {"PHONE_VISIBLE": Signal("POSITIVE", 0, 1.5)})
    engine.observe(.5, {"PHONE_VISIBLE": Signal("POSITIVE", 0, 1.5)})
    engine.observe(1, {"PHONE_VISIBLE": Signal("POSITIVE", 1, 2.5)})
    assert not engine.alerts
    engine.observe(1.1, {"PHONE_VISIBLE": Signal("POSITIVE", 1.1, 2.6)})
    assert len(engine.alerts) == 1
    assert engine.alerts[0].samples == 3


def test_camera_failure_and_stale_data_cannot_turn_into_absence():
    engine = EventEngine()
    samples(engine, "FACE_NOT_VISIBLE", 0, 2)
    engine.observe(2.5, {"FACE_NOT_VISIBLE": "UNKNOWN", "HEAD_AWAY": "UNKNOWN"})
    engine.observe(3, {"FACE_NOT_VISIBLE": "UNKNOWN"})
    assert engine.snapshot()["absence_seconds"] == 2
    engine.tick(4)
    assert engine.snapshot()["absence_seconds"] == 0
    assert not engine.alerts
    engine.observe(5, {"FACE_NOT_VISIBLE": Signal("POSITIVE", 1, 2.5)})
    assert engine.snapshot()["absence_seconds"] == 0


def test_suspension_and_primary_change_reset_person_counters():
    engine = EventEngine()
    samples(engine, "HEAD_AWAY", 0, 4, primary_key="alice")
    assert engine.alerts
    engine.observe(4.1, {"HEAD_AWAY": "POSITIVE"}, suspended=True, primary_key="alice")
    assert not engine.alerts
    assert engine.snapshot()["turn_count"] == 0
    assert engine.snapshot()["head_away_seconds"] == 0
    samples(engine, "HEAD_AWAY", 5, 9, primary_key="alice")
    engine.observe(9.1, {"HEAD_AWAY": "POSITIVE"}, primary_key="bob")
    assert not engine.alerts
    assert engine.snapshot()["head_away_seconds"] == 0


def test_four_independent_turns_require_neutral_and_one_long_is_one_turn():
    engine = EventEngine()
    samples(engine, "HEAD_AWAY", 0, 7)
    assert engine.snapshot()["turn_count"] == 1
    for start in (8, 11, 14):
        samples(engine, "HEAD_AWAY", start, start + 1, value="NEGATIVE")
        samples(engine, "HEAD_AWAY", start + 1.25, start + 2.5)
    assert engine.snapshot()["turn_count"] == 4
    assert [a.kind for a in engine.alerts] == ["HEAD_TURN_FREQUENT"]
    alert_id = engine.alerts[0].id
    samples(engine, "HEAD_AWAY", 16.75, 18)
    assert any(a.id == alert_id for a in engine.alerts)
    engine.tick(80)
    assert engine.snapshot()["turn_count"] == 0


def test_people_and_faces_are_independent_and_clear_is_debounced():
    engine = EventEngine()
    for at in (0, .5, 1, 1.5, 2):
        engine.observe(at, {"MULTIPLE_PEOPLE": "NEGATIVE", "MULTIPLE_FACES": "POSITIVE"})
    assert [a.kind for a in engine.alerts] == ["MULTIPLE_FACES"]
    engine.observe(2.1, {"MULTIPLE_FACES": "NEGATIVE"})
    assert engine.alerts
    engine.observe(2.4, {"MULTIPLE_FACES": "POSITIVE"})
    assert len(engine.alerts) == 1
    engine.observe(2.6, {"MULTIPLE_FACES": "NEGATIVE"})
    engine.observe(3.2, {"MULTIPLE_FACES": "NEGATIVE"})
    assert not engine.alerts


def test_unknown_break_excluded_from_candidate_duration():
    engine = EventEngine()
    samples(engine, "HEAD_AWAY", 0, 2)
    engine.observe(2.5, {"HEAD_AWAY": "UNKNOWN"})
    engine.observe(3, {"HEAD_AWAY": "POSITIVE"})
    assert engine.snapshot()["head_away_seconds"] == 2
    samples(engine, "HEAD_AWAY", 3.25, 4.75)
    assert not engine.alerts
    engine.observe(5, {"HEAD_AWAY": "POSITIVE"})
    assert engine.alerts


def test_long_gap_does_not_count_new_turn_without_observed_neutral():
    engine = EventEngine()
    samples(engine, "HEAD_AWAY", 0, 1)
    samples(engine, "HEAD_AWAY", 4, 5)
    assert engine.snapshot()["turn_count"] == 1
    samples(engine, "HEAD_AWAY", 5.25, 6, value="NEGATIVE")
    samples(engine, "HEAD_AWAY", 6.25, 7)
    assert engine.snapshot()["turn_count"] == 2
