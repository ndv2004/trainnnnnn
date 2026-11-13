"""Logic tests use synthetic vectors, never evidence of recognition accuracy."""
from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from camera_app.identity import (
    EnrollmentCollector,
    IdentityError,
    IdentityStore,
    Profile,
    normalize,
    recognize,
)
from camera_app.tracking import FaceDetection, FaceTracker


def vector(index=0):
    value = np.zeros(128, np.float32)
    value[index] = 1
    return value


def profile(name, value, key):
    return Profile(key, name, normalize(value), "sface-test", "2026-11-13T00:00:00Z")


def test_unicode_encrypted_restart_and_main(tmp_path):
    with_store = IdentityStore(tmp_path)
    saved = with_store.add("Nguyễn Duy Việt", vector(), "sface-test", make_main=True)
    encrypted = (tmp_path / "profiles.enc").read_bytes()
    assert "Nguyễn Duy Việt".encode() not in encrypted
    assert b'"embedding"' not in encrypted
    with_store.close()
    reopened = IdentityStore(tmp_path)
    assert reopened.main_profile_id == saved.id
    assert reopened.profiles()[0].name == "Nguyễn Duy Việt"
    assert recognize(vector(), reopened.profiles(), "sface-test").profile_id == saved.id
    reopened.delete(saved.id)
    assert reopened.main_profile_id is None
    assert reopened.profiles() == []
    reopened.close()


def test_process_lock_and_failed_write_preserve_saved_profiles(tmp_path):
    store = IdentityStore(tmp_path)
    first = store.add("A", vector(), "sface-test")
    with pytest.raises(IdentityError):
        IdentityStore(tmp_path)
    old_file = (tmp_path / "profiles.enc").read_bytes()
    with patch("camera_app.identity.os.replace", side_effect=OSError("mock failure")):
        with pytest.raises(IdentityError):
            store.add("B", vector(1), "sface-test")
    assert store.profiles()[0].id == first.id
    assert len(store.profiles()) == 1
    assert (tmp_path / "profiles.enc").read_bytes() == old_file
    store.close()


def test_corruption_never_reinitializes_or_overwrites(tmp_path):
    path = tmp_path / "profiles.enc"
    path.write_bytes(b"corrupt data")
    with pytest.raises(IdentityError):
        IdentityStore(tmp_path)
    assert path.read_bytes() == b"corrupt data"


def test_recognition_refuses_weak_ambiguous_and_other_model():
    p = profile("A", vector(), "a")
    assert recognize(vector(1), [p], "sface-test").profile_id is None
    assert recognize(vector(), [p], "other-model").profile_id is None
    duplicate = profile("B", normalize(vector() + .02 * vector(1)), "b")
    assert recognize(vector(), [p, duplicate], "sface-test").status == "AMBIGUOUS"
    assert recognize(None, [p], "sface-test").status == "INSUFFICIENT"
    assert recognize(vector(), [], "sface-test").name == "Người"


def test_enrollment_requires_diversity_quality_and_same_identity():
    collector = EnrollmentCollector(target_samples=10, min_samples=10)
    assert not collector.offer(vector(), False, 1, 0)[0]
    assert not collector.offer(vector(), True, 2, 0)[0]
    assert collector.offer(vector(), True, 1, 0, pose={"yaw": 0, "pitch": 0})[0]
    assert not collector.offer(vector(), True, 1, 1, pose={"yaw": 0, "pitch": 0})[0]
    assert not collector.offer(vector(1), True, 1, 1)[0]
    assert collector.count == 1
    for index in range(1, 10):
        # Sufficient appearance variation with consistent identity.
        sample = normalize(vector() + .13 * vector(index))
        assert collector.offer(sample, True, 1, index)[0]
    assert collector.ready
    assert float(collector.embedding() @ vector()) > .99
    collector.clear()
    assert collector.count == 0
    with pytest.raises(IdentityError):
        collector.embedding()


def detection(value, x=0, quality=True):
    return FaceDetection((x, 0, x + 100, 100), value, quality)


def test_two_people_swap_positions_without_swapping_ids_or_main():
    gallery = [profile("A", vector(), "a"), profile("B", vector(1), "b")]
    tracker = FaceTracker()
    tracker.select_main("a")
    tracker.update([detection(vector()), detection(vector(1), 200)], 0, gallery, "sface-test")
    tracks = tracker.update([detection(vector()), detection(vector(1), 200)], .3, gallery, "sface-test")
    first_ids = {t.name: t.id for t in tracks}
    assert tracker.main_track(tracks).name == "A"
    swapped = tracker.update([detection(vector(1)), detection(vector(), 200)], .6, gallery, "sface-test")
    assert {t.name: t.id for t in swapped} == first_ids
    assert tracker.main_track(swapped).box[0] == 200
    absent = tracker.update([detection(vector(1), 200)], .9, gallery, "sface-test")
    assert tracker.main_track(absent) is None


def test_unknown_at_old_position_never_inherits_name_or_anonymous_main():
    tracker = FaceTracker()
    gallery = [profile("A", vector(), "a")]
    tracker.update([detection(vector())], 0, gallery, "sface-test")
    tracks = tracker.update([detection(vector())], .3, gallery, "sface-test")
    old = tracker.main_track(tracks).id
    other = tracker.update([detection(vector(1))], .6, gallery, "sface-test")
    assert other[0].id != old
    assert other[0].name == "Người"
    assert tracker.main_track(other) is None
    tracker.reset_main()
    again = tracker.update([detection(vector(1))], .9, gallery, "sface-test")
    assert tracker.main_track(again).name == "Người"


def test_blur_and_duplicate_identity_never_display_stale_names():
    tracker = FaceTracker()
    gallery = [profile("A", vector(), "a")]
    tracker.select_main("a")
    tracker.update([detection(vector())], 0, gallery, "sface-test")
    assert tracker.update([detection(vector())], .3, gallery, "sface-test")[0].name == "A"
    blurred = tracker.update([detection(None, quality=False)], .6, gallery, "sface-test")
    assert blurred[0].profile_id is None
    assert blurred[0].name == "Chưa đủ dữ liệu"
    assert tracker.main_track(blurred) is None
    tracker.reset()
    tracker.update([detection(vector()), detection(vector(), 200)], 1, gallery, "sface-test")
    copies = tracker.update([detection(vector()), detection(vector(), 200)], 1.3, gallery, "sface-test")
    assert all(t.profile_id is None for t in copies)
    assert tracker.main_track(copies) is None


def test_stale_frames_and_expired_tracks_do_not_restore_identity():
    tracker = FaceTracker(ttl=1)
    gallery = [profile("A", vector(), "a")]
    first = tracker.update([detection(vector())], 1, gallery, "sface-test")[0]
    assert tracker.update([detection(vector())], .8, gallery, "sface-test") == []
    after = tracker.update([detection(vector())], 3, gallery, "sface-test")[0]
    assert after.id != first.id
    assert after.name == "Đang xác định"
