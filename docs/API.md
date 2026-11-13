# Hợp đồng API triển khai

Tiền tố `/api/v1/proctoring`. Hợp đồng máy đọc: `openapi.json`; khi chạy, xem `/docs`. JSON snake_case, UUID, UTC. Mỗi phản hồi JSON có `request_id`; mọi phản hồi có `X-Request-ID`. Lỗi: `{"error":{"code":"...","message":"...","details":{}},"request_id":"..."}`. Không có stack trace hoặc ảnh/vector trong lỗi.

## Danh tính và quyền

`Authorization: Bearer <token>`. Token HS256 kiểm issuer, audience, exp, jti, purpose. User token dùng nghiệp vụ; frames token chỉ đúng phiên và mục đích thu ảnh, TTL 900 giây, generation thu hồi khi resume. Token đã thu hồi không được resume kể cả hết hạn; token cũ hết hạn nhưng chưa thu hồi có thể được chủ phiên dùng làm bằng chứng luồng khi resume với user token còn hạn.

| Vai trò | Phạm vi |
|---|---|
| candidate | Phiên của chính mình: tạo từ attempt đã đăng ký, consent/precheck/verify/calibration/resume, đọc trạng thái/events, dừng tham gia với reason PARTICIPANT_STOPPED. Không đọc ảnh minh chứng, sửa review, đăng ký hồ sơ hoặc gọi start chính. |
| proctor | Phiên có tên trong phân công server: đọc, notifications/WS, evidence, review và quyết định thủ công. |
| service/admin | Thao tác backend trên adapter độc lập, đăng ký attempt/hồ sơ, start/end, quản trị phù hợp. Config chỉ admin. |

Tạo session chỉ nhận `attempt_id`; backend adapter tra candidate/exam/proctors. Tạo attempt mới qua endpoint quản trị. Không có API cho client tự gán quyền. Ngoài demo cần gateway/adapter xác thực với hệ thống thi thật; workspace không có hệ thống đó.

## Các endpoint theo tài liệu 02

| Method + path (sau tiền tố) | Đầu vào | Kết quả / ràng buộc |
|---|---|---|
| POST `/sessions` | `{attempt_id}`, Idempotency-Key | 201 mới / 200 đã có, attempt duy nhất |
| GET `/sessions/{id}` | Bearer có quyền | State, health, latest observation; hết hạn trả UNKNOWN |
| POST `/sessions/{id}/precheck` | multipart `image` JPEG | quality riêng từng nguyên nhân, PRECHECK/VERIFYING |
| POST `/sessions/{id}/verify` | multipart `image` | MATCH/NO_MATCH/INCONCLUSIVE, score null khi chưa chạy được |
| POST `/sessions/{id}/calibration/samples` | image, seq, capture_offset_ms, camera_id (mặc định default) | 202 mẫu, count và calibration_id; có thể accepted=false do chất lượng |
| POST `/sessions/{id}/calibration/complete` | `{calibration_id}` | PASSED → READY hoặc RETRY_REQUIRED, xóa bộ mẫu lỗi để thử lại |
| POST `/sessions/{id}/start` | Backend/admin token | Kiểm verified + consent + baseline + module bắt buộc, cấp frames_token và stream_id |
| POST `/sessions/{id}/frames` | multipart image, seq, capture_offset_ms, camera_id; frames token | 202 QUEUED / 200 DUPLICATE; kết quả suy luận đọc riêng |
| POST `/sessions/{id}/resume` | `{old_token, stream_id: UUID mới, camera_changed}` + user token | Token/generation mới, bỏ pending; có thể CALIBRATING |
| POST `/sessions/{id}/end` | `{reason}`, Idempotency-Key | ENDED bền vững, lặp không đóng/phát thông báo trùng |
| GET `/sessions/{id}/events` | cursor, limit≤100, type, review_status | items + next_cursor; thứ tự `(start_at,event_id)` |
| PATCH `/events/{id}/review` | `{status,reason,expected_version}`, Idempotency-Key | 200 hoặc 409 VERSION_CONFLICT; audit + ReviewAction + outbox cùng giao dịch |
| GET `/evidence/{id}/access` | Token giám thị đúng phân công | URL ký 60 giây; nội dung còn yêu cầu Bearer của chính người xin URL |
| POST `/configs` | `{config,reason}`, admin | Phiên bản bất biến; active bền vững, chỉ phiên mới nhận thay đổi |

JPEG thực tối đa 1 MiB, tối đa 1920×1080. Header ảnh được kiểm trước giải mã pixel. Body multipart tổng được chặn ở 1 MiB + 64 KiB kể cả chunked. Không nhận URL ảnh. Giới hạn 5 request ảnh/giây/phiên; trả Retry-After khi 429/503.

seq tăng dần theo stream; offset bắt đầu gần 0 và tăng, lệch không quá 1500 ms so với monotonic server. Cùng seq, hash ảnh và metadata giống nhau trả DUPLICATE, không suy luận lại. Cùng seq khác nội dung trả FRAME_CONFLICT; thấp hơn/offset cũ trả STALE_FRAME. Offset nhảy trả INVALID_CAPTURE_TIME. Stream_id đã dùng trả STREAM_REPLAY trước khi thay đổi trạng thái. 202 chỉ tiếp nhận, không cam kết đã suy luận.

Idempotency-Key có phạm vi actor + thao tác + phiên/event; cùng khóa khác nội dung trả 409. Khóa được giữ lâu hơn tối thiểu suốt phiên +24 giờ (bản này chưa dọn bảng khóa). expected_version là version hiện đang xem, không được tự tăng để bỏ kiểm tra xung đột.

## Bổ sung cho bản chạy độc lập

- GET `/sessions/{id}/verification-history`: quality precheck, kết quả xác thực và quyết định thủ công kèm lý do, chỉ giám thị được phân công/backend.
- GET `/sessions`: danh sách trong phạm vi quyền. GET `/events/{id}/reviews`: lịch sử xem xét chỉ thêm.
- POST `/sessions/{id}/consent`: `{accepted}` trước thu ảnh; không dùng consent để đổi danh tính.
- POST `/sessions/{id}/manual-decision`: `{decision:ALLOW_RETRY|ACCEPT_MANUALLY,reason,expected_version}` + Idempotency-Key; lưu lịch sử/lý do. Không tạo MATCH giả.
- POST `/sessions/{id}/frame-token`: gia hạn token khi user còn quyền, không reset bộ đếm; không cấp khi cần resume.
- POST `/sessions/{id}/client-health`: CAMERA_PERMISSION_LOST / CAMERA_STOPPED / CLIENT_TRANSPORT_ERROR; chỉ ghi kỹ thuật và yêu cầu resume.
- GET `/capabilities`, POST `/sessions/{id}/liveness/challenge`: chống giả mạo UNAVAILABLE; không có thử thách thành công giả.
- POST `/profiles/{candidate_id}`: multipart image/source_ref/expires_at, chỉ backend/admin; DELETE tương ứng xóa vector. Hồ sơ lấy từ nguồn người tổ chức đã đối soát.
- POST `/admin/attempts`: attempt_id/candidate_id/exam_id/proctor_ids từ backend. Không thay danh tính của attempt cũ.
- POST `/admin/tokens/revoke`, GET `/admin/metrics`, POST `/admin/outbox/retry`, POST `/admin/purge`: quản trị có quyền.
- `/demo/info`, `/demo/login`, `/demo/sessions/{id}/start` nằm ngoài tiền tố, chỉ tồn tại hiệu lực khi PROCTOR_DEMO=1 và localhost. Đây là tiện ích thử, không phải SSO.

## Thông báo và phục hồi

WebSocket `/sessions/{id}/ws`. Kết nối rồi gửi JSON đầu tiên `{"token":"<user token>"}` trong 5 giây; không đặt token vào query URL. Máy chủ kiểm phân công trước đăng ký, kiểm lại định kỳ. Loại: proctor.event.created/updated, proctor.health.changed, proctor.session.ended. Envelope: message_id, session_id, event_id, version, server_time, type, payload.

Outbox ghi cùng event/review. Không có subscriber thì giữ pending; gửi lỗi retry lùi tới 60 giây, 10 lần vào dead queue, admin có thể retry. Mạng có thể nhận trùng; client loại bằng message_id/version. Không có xác nhận ứng dụng cho từng gói: sau reconnect **luôn** đọc lại REST events hoặc `/sessions/{id}/notifications?after_id=...`, kể cả thông báo đã đánh dấu sent. Không coi WebSocket là kho dữ liệu.

Health: `/health/live` chỉ tiến trình; `/health/ready` kiểm model bắt buộc đã nạp đúng checksum và DB truy vấn được. Lỗi ghi minh chứng xảy ra trong khi chạy được thể hiện trên Evidence, không biến thành READY giả. Readiness chưa thay thế phép đo I/O/độ trễ/benchmark dài.

Các mã chính: 400 INVALID_REQUEST, 401 INVALID_TOKEN/TOKEN_EXPIRED/TOKEN_REVOKED, 403 ACCESS_DENIED, 404 NOT_FOUND ngoài quyền, 409 INVALID_STATE/SESSION_ENDED/VERSION_CONFLICT/STALE_FRAME/RESUME_REQUIRED, 413 IMAGE_TOO_LARGE, 415 UNSUPPORTED_IMAGE, 422 IMAGE_UNDECODABLE, 429 RATE_LIMITED, 503 MODEL_UNAVAILABLE/SERVICE_OVERLOADED. Ảnh kém nhưng giải mã được trả quality với HTTP 200; NO_MATCH không dùng 401.
