import cv2
import numpy as np

from camera_app.pose import HeadGazeCalibrator, PoseEstimator, PoseMeasurement


def calibrated():
    calibrator = HeadGazeCalibrator({"calibration_samples": 3, "calibration_seconds": .5})
    for at in (0, .25, .5):
        state = calibrator.update(PoseMeasurement(0, 0, 0, True, .5, 60), at, "alice")
    assert state["calibrated"]
    return calibrator


def test_separate_head_and_gaze_and_missing_eyes_are_unknown():
    c = calibrated()
    state = c.update(PoseMeasurement(-30, 0, 0, True, .5), .75, "alice")
    assert state["head_direction"] == "Quay trái"
    assert state["head_signal"] == "POSITIVE"
    assert state["gaze_signal"] == "UNKNOWN"
    state = c.update(PoseMeasurement(0, 0, 0, True, .75), 1, "alice")
    assert state["head_direction"] == "Thẳng"
    assert state["gaze_direction"] == "Trái (ước lượng)"
    assert state["gaze_signal"] == "POSITIVE"
    state = c.update(PoseMeasurement(0, 0, 0, False, .5), 1.25, "alice")
    assert state["gaze_direction"] == "Chưa xác định"
    assert state["gaze_signal"] == "UNKNOWN"


def test_missing_pose_not_calibrating_forever_after_success_and_subject_reset():
    c = calibrated()
    state = c.update(None, 1, "alice")
    assert state["calibrated"] and not state["calibrating"]
    assert state["head_signal"] == "UNKNOWN"
    state = c.update(PoseMeasurement(0, 0, 0), 2, "bob")
    assert not state["calibrated"] and state["calibrating"]


def test_calibration_rejects_turned_and_unstable_head_and_never_creates_identity():
    c = HeadGazeCalibrator({"calibration_samples": 3, "calibration_seconds": .5})
    for at in (0, .25, .5, .75):
        state = c.update(PoseMeasurement(40, 0, 0), at, "temporary:1")
    assert not state["calibrated"]
    for at, yaw in ((1, 0), (1.25, 19), (1.5, -19), (1.75, 19)):
        state = c.update(PoseMeasurement(yaw, 0, 0), at, "temporary:1")
    assert not state["calibrated"]


def synthetic_landmarks(yaw=0, pitch=0):
    angles = np.radians((pitch, yaw, 0))
    rx = cv2.Rodrigues(np.array((angles[0], 0, 0)))[0]
    ry = cv2.Rodrigues(np.array((0, angles[1], 0)))[0]
    rotation = ry @ rx
    camera = np.array(((640., 0, 320), (0, 640., 240), (0, 0, 1)))
    projected = cv2.projectPoints(PoseEstimator.MODEL, cv2.Rodrigues(rotation)[0],
                                 np.array((0., 0., 1400)), camera, None)[0].reshape(-1, 2)
    xy = np.zeros((478, 2))
    xy[list(PoseEstimator.INDICES)] = projected / (640, 480)
    return xy


def test_solvepnp_sign_convention_and_five_directions():
    estimator = PoseEstimator()
    c = calibrated()
    expected = ((30, 0, "Quay phải"), (-30, 0, "Quay trái"),
                (0, 25, "Cúi"), (0, -25, "Ngẩng"), (0, 0, "Thẳng"))
    for i, (yaw, pitch, direction) in enumerate(expected):
        measured = estimator.measure(synthetic_landmarks(yaw, pitch), 640, 480)
        assert measured is not None
        assert abs(measured.yaw - yaw) < 1e-3
        assert abs(measured.pitch - pitch) < 1e-3
        state = c.update(measured, 1 + i, "alice")
        assert state["head_direction"] == direction


def test_mediapipe_basis_conversion_same_direction_and_no_quality_no_pose():
    estimator = PoseEstimator()
    mp_rotation = cv2.Rodrigues(np.array((0., np.radians(30), 0.)))[0]
    transform = np.eye(4)
    transform[:3, :3] = mp_rotation
    measured = estimator.measure(synthetic_landmarks(), 640, 480, transformation_matrix=transform)
    assert measured.yaw < -29
    assert estimator.measure(synthetic_landmarks(), 640, 480, quality_ok=False) is None


def test_eye_gate_small_closed_or_disagreeing_iris():
    estimator = PoseEstimator()
    xy = np.zeros((478, 2))
    for a, b, upper, lower, iris, start in ((33, 133, 159, 145, 468, 100),
                                           (362, 263, 386, 374, 473, 180)):
        xy[a] = (start, 100)
        xy[b] = (start + 25, 100)
        xy[upper] = (start + 12, 97)
        xy[lower] = (start + 12, 103)
        xy[iris] = (start + 12.5, 100)
    assert estimator._eyes(xy)[0]
    xy[473] = (180 + 22.5, 100)
    assert not estimator._eyes(xy)[0]
    xy[473] = (192.5, 100)
    xy[145] = xy[159]
    assert not estimator._eyes(xy)[0]


def test_gaze_can_be_disabled_in_versioned_configuration():
    c = calibrated()
    c.config["gaze_enabled"] = False
    state = c.update(PoseMeasurement(0, 0, 0, True, .75), 1, "alice")
    assert state["head_signal"] == "NEGATIVE"
    assert state["gaze_signal"] == "UNKNOWN"
    assert state["gaze_direction"] == "Chưa xác định"
    assert "đang tắt" in state["status"]


def test_actual_eye_regions_reject_featureless_dark_or_blurred_image():
    estimator = PoseEstimator()
    xy = np.zeros((478, 2))
    xy[33], xy[133] = (50, 50), (80, 50)
    xy[362], xy[263] = (120, 50), (150, 50)
    rng = np.random.default_rng(10)
    sharp = rng.integers(45, 220, (100, 200), dtype=np.uint8)
    assert estimator._eye_image_quality(sharp, xy)
    assert not estimator._eye_image_quality(np.full((100, 200), 100, np.uint8), xy)
    assert not estimator._eye_image_quality(np.full((100, 200), 10, np.uint8), xy)
    assert not estimator._eye_image_quality(cv2.GaussianBlur(sharp, (21, 21), 8), xy)
