# Đối chiếu yêu cầu và bằng chứng

“Đã triển khai” ở đây mô tả mã/luồng chạy, không đồng nghĩa chất lượng mô hình đã đạt tiêu chí mục 5 tài liệu 03. Danh sách ca nghiệm thu riêng trong `reports/nghiem_thu.json` dùng PASS/FAIL/BLOCKED, với giới hạn bằng chứng ghi ngay từng ca.

| Mã | Tình trạng và bằng chứng |
|---|---|
| REQ-01 | Adapter backend xác nhận attempt/candidate; unique attempt + idempotency; test_create_unique_idempotency_and_body_collision. Chưa kết nối hệ thống thi thật. |
| REQ-02 | Hồ sơ do admin/service cung cấp, có source_ref/hạn/model hash; thiếu chuẩn INCONCLUSIVE, không tự đăng ký. Tests missing_profile, candidate_profile_replacement, real E2E. |
| REQ-03 | Quality brightness/sharpness/face size/count, version; real bad-image → clear-image precheck và test_quality_separates_dark_blur_small_and_multiple. Quyền webcam trình duyệt chưa thử được. |
| REQ-04 | JPEG/byte/pixel, seq/offset/hash/state, stale/drop; tests image/sequence/offset, queue, end. |
| FR-01 | SFace thật + cosine, 3 kết quả, version/threshold; test_true_models_face_pose_person_and_sface_pair. FAR/FRR BLOCKED do thiếu tập người/cặp đủ quy mô. |
| FR-02 | Ba lượt, manual retry/accept + reason/audit/history/idempotency, không giả MATCH; tests_three_eligible_no_matches, manual_decision, manual_retry. |
| FR-03 | Capability/challenge UNAVAILABLE rõ lý do. Chưa có nonce/random challenge/chống phát lại hoạt động; TC06 BLOCKED. |
| FR-04 | Chuỗi mẫu MediaPipe thật, median/stability/eyes/duration; real E2E, missing_eyes, camera_change. Dịch chuyển vật lý của cùng camera chưa phát hiện đầy đủ. |
| FR-05 | YuNet và NanoDet riêng, tín hiệu riêng, không cộng count; real one-person smoke + test_many_faces_never_implies_many_people. Video nhiều người/mặt trên điện thoại BLOCKED. |
| FR-06 | FACE_NOT_VISIBLE cần ảnh đủ chất lượng, 3 giây/5 mẫu; fake-clock boundary + real scene-without-face API event. Không gán nhãn rời chỗ. |
| FR-07 | Hộp phone, confidence và quy tắc 1 giây/3 mẫu. Logic PASS; chưa có positive phone qua webcam/ảnh mẫu truy cập được nên chất lượng BLOCKED. |
| FR-08 | Góc tương đối baseline, hysteresis, pose mất/không hữu hạn → UNKNOWN. Trả pose thật và test thời gian HEAD_AWAY; chưa đánh giá góc/chuỗi quay trên người. |
| FR-09 | Bốn lượt độc lập, mỗi lượt ≥500 ms, giữa lượt trung tính ≥500 ms, cửa sổ 60 giây; tests_single_twenty_second_turn / four_independent_turns / unknown_not_neutral. |
| FR-10 | Gaze ngang heuristic sau baseline/eyes/head neutral, mặc định tắt, có nhãn thử nghiệm. Chưa có đánh giá vùng nhìn; không làm điều kiện chặn thi. |
| FR-11 | Recheck theo lịch trên ảnh đạt, MATCH reset, INCONCLUSIVE không tăng, 2 NO_MATCH trong 180 giây; identity fake-clock tests. Chưa đánh giá session danh tính dài trên người. |
| FR-12 | UNKNOWN/max_gap, kỹ thuật riêng, queue/result quá hạn không kéo dài dấu hiệu; network_loss / stale_queued_result / object_failure / worker_supervises. |
| FR-13 | Sáu rule thời lượng có máy trạng thái, một event_id cập nhật, clear/cooldown; parameterized boundary/one-event/negative-clear tests. |
| FR-14 | Event UTC/type/span/reason/config/model/hash; UI nhóm khoảng giao nhau. Phần dữ liệu kiểm qua API; render UI BLOCKED. |
| FR-15 | Ảnh xác nhận/tối đa 3, encrypted/hash/expiry/private ACL; failure keeps event MISSING; real E2E và evidence tests. |
| FR-16 | Review 4 trạng thái + reason + expected_version + append-only history; 2-proctor CAS, retry, inference-versus-review race regression. |
| FR-17 | End ngừng nhận ảnh, đóng event, giữ timeline/review; repeat end/new-key không lặp notification. Real E2E và end_repeat. |
| NFR-01 | Phạm vi phiên/event/evidence/WS, URL ký vẫn cần Bearer, sanitized errors/audit; cross-session/expiry/revocation/WS/log tests. Không phải chứng nhận pentest. |
| NFR-02 | Fernet ảnh/vector thật, khóa riêng, không static path; HTTPS bắt buộc ngoài loopback ở chế độ không demo. Chưa triển khai/chứng nhận gateway HTTPS thật. |
| NFR-03 | Idempotency và receipt bền vững; create/review/end/manual retry tests. |
| NFR-04 | Config bất biến + snapshot + active pointer sau restart; model manifest/catalogue hash/license và event version. Config snapshot/restart tests. |
| NFR-05 | BLOCKED: không có máy Windows i7-10510U/MX130 hoặc webcam vật lý trong môi trường này. Wheel availability không phải benchmark. |
| NFR-06 | Model missing/checksum/inference error, per-module UNKNOWN/UNAVAILABLE; readiness 503 và degraded event tests. |
| NFR-07 | Có queue/inference/latency/drop/unknown/CPU/RAM metrics; chưa đo p95 thông báo trên target, BLOCKED SLA 2 giây. |
| NFR-08 | SQLite WAL/FULL, transactional outbox, child os._exit recovery, API replay/resume. Benchmark 60 phút và 10 phút quá tải thật còn BLOCKED. |

## Trạng thái từng mốc

| Mốc | Đã làm và giới hạn |
|---|---|
| A | Python 3.12, config, checksum, video reader 40 ảnh thật, camera source/UI. Không truy cập được webcam vật lý. |
| B | Bốn trọng số nạp CPU, mặt/pose/vector/người chạy thật, object phone class có adapter. Chưa có bộ kiểm nhận diện phone/multiple-person. |
| C | Event engine thuần, đồng hồ giả, SQLite/outbox, ảnh mã hóa/MISSING/expiry/review race được kiểm thử. |
| D | Xác thực hồ sơ riêng, manual flow, calibration thật và pose tương đối. Chưa đánh giá FAR/FRR và sai số góc. |
| E | API hợp đồng đầy đủ + OpenAPI, UI tối giản có source webcam/video/proctor/admin. HTTP thật PASS; thao tác UI trong browser BLOCKED. |
| F | Gaze interface thử nghiệm/off, anti-spoof unavailable, resume/error recovery, evaluation script. Dữ liệu đánh giá và target benchmark chưa có. |
