# Thiết kế kỹ thuật dữ liệu và API AI giám sát thi

Phiên bản 1.0 | Ngày 13 tháng 11 năm 2026 | Liên quan tài liệu yêu cầu 01

Tài liệu quy định cấu trúc dịch vụ AI, cách tổng hợp sự kiện, hợp đồng API và dữ liệu lưu trữ. Các lựa chọn dưới đây là thiết kế đề xuất để lập trình thống nhất. Không mô tả một API đã được triển khai sẵn.

## 1 Ranh giới thành phần

Trình duyệt thu ảnh webcam và hiển thị hướng dẫn. Backend hệ thống thi xác nhận tài khoản, lượt làm bài và phân công giám thị. Dịch vụ AI Python nhận ảnh, chạy mô hình và tổng hợp sự kiện. Cơ sở dữ liệu lưu phiên, cấu hình và sự kiện; kho đối tượng lưu ảnh minh chứng ngoài webroot.

Luồng tích hợp là trình duyệt gửi ảnh qua backend hoặc qua cổng thu ảnh chuyên dụng do backend cấp token có thời hạn. Bản thử nghiệm dùng backend chuyển tiếp để đơn giản hóa quyền. Không công khai dịch vụ AI nội bộ ra Internet mà không có cổng xác thực.

Các mô-đun Python gồm ingestion, quality, face_detection, face_verification, head_pose, gaze, object_detection, event_engine, evidence và adapters. event_engine chỉ nhận quan sát chuẩn hóa; không gọi trực tiếp camera hoặc mô hình, nhờ đó kiểm thử được bằng chuỗi dữ liệu xác định.

## 2 Mô hình và lịch xử lý

OpenCV phục vụ đọc và chuẩn hóa ảnh. MediaPipe Face Landmarker cung cấp điểm mốc và ma trận biến đổi để xây dựng hướng đầu. Mô hình phát hiện vật thể loại nhỏ dùng để nhận diện người và điện thoại. Mô hình tạo vector khuôn mặt được chọn sau khi kiểm tra giấy phép và đánh giá. Không dùng điểm mốc MediaPipe làm bộ xác thực danh tính.

| Công việc | Cấu hình thử nghiệm | Khi không đáp ứng |
|---|---|---|
| Ảnh thu nhận | JPEG 640 x 480 khoảng 5 ảnh mỗi giây | Giảm nhịp có thông báo, ghi khoảng trống |
| Chất lượng và mặt | Trên ảnh được chọn xử lý | Trả UNKNOWN khi không đủ dữ liệu |
| Người và điện thoại | Khoảng 2 lần mỗi giây | Không giữ kết quả cũ quá thời hạn |
| Hướng đầu và mắt | Tối đa theo nhịp ảnh hợp lệ | Đánh dấu không khả dụng từng mô-đun |
| Kiểm tra danh tính | Khoảng 60 giây một lần | Hoãn tới ảnh rõ, không coi hoãn là không khớp |

Mỗi mô-đun có observed_at và valid_until. Kết quả quá 1,5 giây ở cấu hình đầu trở thành UNKNOWN; không kéo dài quan sát cũ để bù nhịp xử lý thấp. Các thông số trên là đầu vào benchmark, không bảo đảm hiệu năng.

Mỗi phiên chỉ giữ một ảnh chờ mới nhất. Ảnh đang xử lý được hoàn tất; ảnh chờ cũ được thay bằng ảnh mới và tăng dropped_frames. Sự kiện đã ghi không bị bỏ. Bộ chia lịch phải ngăn một phiên chiếm hết worker; giới hạn tải theo benchmark.

## 3 Chuẩn hóa thời gian và khôi phục

Lưu UTC cho các mốc nghiệp vụ. Trong một tiến trình, tính khoảng thời gian bằng đồng hồ đơn điệu. Frame có seq tăng dần, capture_offset_ms kể từ lúc bắt đầu luồng và received_at do máy chủ gán. Máy chủ kiểm tra độ lệch với thời gian đã trôi phía máy chủ, loại dữ liệu âm, nhảy quá mức hoặc phát lại.

Không tin thời gian client để chứng minh webcam là trực tiếp. HTTPS, token phiên và kiểm tra phát lại làm giảm rủi ro nhưng không chứng minh tuyệt đối nguồn ảnh. Đây là giới hạn cần thể hiện trong đánh giá chống giả mạo.

Sau khởi động lại, tải lại phiên và sự kiện ACTIVE đã lưu. Đóng khoảng quan sát cũ với lý do WORKER_INTERRUPTED ở mốc quan sát cuối, ghi gián đoạn, xóa các CANDIDATE chưa xác nhận và yêu cầu resume_token mới. Không cộng thời gian chết vào hành vi. Backend thi đối soát phiên chưa kết thúc khi dịch vụ trở lại.

## 4 Máy trạng thái của phiên

| Trạng thái | Điều kiện vào | Chuyển tiếp hợp lệ |
|---|---|---|
| CREATED | Backend tạo phiên hợp lệ | PRECHECK hoặc ENDED |
| PRECHECK | Đang kiểm tra camera | VERIFYING hoặc ENDED |
| VERIFYING | Đủ ảnh chuẩn và ảnh camera | CALIBRATING hoặc chờ giám thị |
| CALIBRATING | Xác thực được chấp nhận | READY hoặc quay lại PRECHECK |
| READY | Đủ điều kiện bắt đầu | MONITORING theo lệnh backend |
| MONITORING | Lượt thi đang diễn ra | DEGRADED hoặc ENDED |
| DEGRADED | Mất dữ liệu hoặc thành phần bắt buộc lỗi | MONITORING sau phục hồi hoặc ENDED |
| ENDED | Backend kết thúc hoặc thu hồi | Không nhận ảnh mới |

Lưu manual_override riêng, không tạo trạng thái giả MATCH. Tắt mô-đun hướng nhìn không nhất thiết làm cả phiên DEGRADED; health của từng mô-đun vẫn phải được trả về. Danh sách mô-đun bắt buộc được đóng băng trong cấu hình phiên.

## 5 Quy tắc sự kiện theo thời gian

Với mỗi loại dấu hiệu, một quan sát là POSITIVE, NEGATIVE hoặc UNKNOWN. Chỉ quan sát đủ chất lượng mới được phân thành POSITIVE hoặc NEGATIVE. Khoảng cách hai ảnh hợp lệ vượt max_gap_ms thì ngắt chuỗi liên tục. Không cộng các khoảng thiếu dữ liệu.

NORMAL chuyển CANDIDATE khi có dấu hiệu. CANDIDATE trở ACTIVE khi đủ thời lượng và số lần quan sát tối thiểu. ACTIVE trở COOLDOWN sau clear_ms quan sát âm liên tục; sự kiện được đóng tại mốc dương cuối cùng. UNKNOWN kéo dài quá max_gap_ms đóng sự kiện với lý do DATA_GAP. COOLDOWN ngăn sinh lại ngay, nhưng vẫn theo dõi quan sát; dấu hiệu còn tồn tại phải hình thành ứng viên mới sau thời gian nghỉ.

| Loại | Điều kiện đầu vào | Thời gian mở | Số mẫu tối thiểu | Thời gian xóa |
|---|---|---|---|---|
| FACE_NOT_VISIBLE | Không mặt và ảnh đạt chất lượng | 3000 ms | 5 | 1000 ms |
| MULTIPLE_PEOPLE | Có ít nhất 2 hộp người vượt ngưỡng | 2000 ms | 3 | 1000 ms |
| MULTIPLE_FACES | Có ít nhất 2 mặt vượt ngưỡng | 2000 ms | 3 | 1000 ms |
| PHONE_VISIBLE | Có điện thoại vượt ngưỡng | 1000 ms | 3 | 1000 ms |
| HEAD_AWAY | Góc lệch mốc vượt ngưỡng | 4000 ms | 5 | 1000 ms |
| GAZE_AWAY | Mắt rõ và nằm ngoài vùng hiệu chỉnh | 5000 ms | 5 | 1000 ms |

Cấu hình ban đầu: max_gap_ms=1500, cooldown_ms=3000, confidence_person=0.60 và confidence_phone=0.65. Góc đầu mở khi độ lệch yaw tuyệt đối trên 25 độ hoặc pitch trên 20 độ; vùng trở lại dùng yaw dưới 15 độ và pitch dưới 12 độ để tránh dao động biên. Đây đều là giá trị cần điều chỉnh; gaze chưa có ngưỡng số cố định trước khi chọn mô hình và hiệu chỉnh.

HEAD_TURN_FREQUENT mở khi có ít nhất 4 lượt quay độc lập trong 60 giây. Mỗi lượt phải vượt ngưỡng tối thiểu 500 ms và trở về trung tính ít nhất 500 ms trước lượt kế tiếp. Sau khi mở, chỉ tạo lại sự kiện khi số lượt trong cửa sổ giảm dưới 4 rồi tăng trở lại, đồng thời hết cooldown.

IDENTITY_REVIEW_REQUIRED dùng hai lần NO_MATCH hợp lệ liên tiếp. MATCH đặt lại bộ đếm; INCONCLUSIVE không tăng bộ đếm. Hai lần cách nhau quá 180 giây thì không được coi là liên tiếp. Điểm tương đồng và ngưỡng phụ thuộc đúng phiên bản mô hình xác thực.

CAMERA_INTERRUPTED được ghi sau 3 giây không có ảnh mới, nhưng khoảng không quan sát được bắt đầu từ khi ảnh cuối hết hạn. Chính sách kỹ thuật này khác với FACE_NOT_VISIBLE. Sự kiện kỹ thuật không được cộng vào điểm hành vi.

Không xây dựng một phần trăm gian lận. Mức ưu tiên xem xét có thể dựa trên loại sự kiện và thời lượng, nhưng phải hiển thị lý do và không cộng dồn mù quáng các dấu hiệu tương quan.

## 6 Mô hình dữ liệu

Mã định danh dùng UUID; thời gian dùng kiểu UTC tương đương datetimeoffset hoặc timestamptz. Kiểu dưới đây là kiểu logic, không khóa hệ quản trị. Ràng buộc quyền và tổ chức phải áp dụng cho mọi truy vấn.

| Thực thể | Trường chính | Ràng buộc |
|---|---|---|
| ProctorSession | id, attempt_id, candidate_id, state, config_id, started_at, ended_at | attempt_id duy nhất; candidate_id lấy từ backend |
| FaceProfile | id, candidate_id, model_version, encrypted_embedding_ref, source_ref, expires_at | Không trộn vector từ hai mô hình; theo dõi nguồn chuẩn |
| VerificationAttempt | id, session_id, score, threshold, result, quality, created_at | score có thể null khi INCONCLUSIVE |
| Calibration | id, session_id, baseline, quality, camera_signature, created_at | Có trạng thái hiệu lực; chỉ một bản đang dùng |
| ModelVersion | id, name, weights_hash, runtime, license_ref, metrics_ref | Hash và tên trọng số bất biến |
| ConfigVersion | id, schema_version, payload, hash, created_by, created_at | Bất biến sau kích hoạt |
| ProctorEvent | id, session_id, type, start_at, confirmed_at, end_at, close_reason, review_status, version | Một sự kiện mở mỗi loại trong một phiên |
| Evidence | id, event_id, object_key, sha256, captured_at, status, expires_at | Đường dẫn nội bộ; trạng thái READY MISSING DELETED |
| ReviewAction | id, event_id, actor_id, before_state, after_state, reason, created_at | Chỉ thêm; không sửa lịch sử |
| SessionHealth | session_id, module, status, last_observed_at, error_code | Duy nhất theo phiên và mô-đun |
| Outbox | id, aggregate_id, event_type, payload, sent_at | Cùng giao dịch với ProctorEvent |
| AuditLog | id, actor_id, action, target_id, at, request_id | Không chứa ảnh, vector hoặc token |

Quan hệ: ProctorSession có nhiều VerificationAttempt, Calibration và ProctorEvent; ProctorEvent có nhiều Evidence và ReviewAction. Phiên tham chiếu cấu hình cố định; sự kiện tham chiếu bộ phiên bản mô hình thực sự dùng khi quan sát. Mỗi lần đổi mô hình đang chạy phải tạo mốc phiên bản mới, không sửa lịch sử.

Chỉ mục cần có: ProctorEvent(session_id,start_at), ProctorEvent(review_status,confirmed_at), Evidence(expires_at,status), Outbox(sent_at,id). Khóa duy nhất sự kiện mở có thể dùng chỉ mục lọc hoặc bảng khóa tùy DB. Ghi sự kiện và Outbox trong cùng giao dịch để tránh lưu xong nhưng mất thông báo.

## 7 Hợp đồng API chung

Tiền tố công khai đề xuất /api/v1/proctoring; backend chuyển lệnh nội bộ sang dịch vụ AI. JSON dùng snake_case, UUID dạng chuỗi, thời gian RFC3339 UTC. Kết quả có request_id; lỗi có error.code, error.message và error.details. Không trả stack trace.

Token người dùng được backend kiểm tra. Token thu ảnh chỉ cho đúng session_id, mục đích frames, có thời hạn ngắn và thu hồi được. Giám thị chỉ đọc các phiên thuộc phân công. Tác vụ dịch vụ nội bộ dùng danh tính dịch vụ riêng. Mọi endpoint phải kiểm tra quyền trên đối tượng, không chỉ kiểm tra đã đăng nhập.

| Phương thức và đường dẫn | Đầu vào chính | Thành công |
|---|---|---|
| POST /sessions | attempt_id và Idempotency-Key | 201 phiên mới hoặc 200 bản đã có |
| GET /sessions/{id} | Quyền xem phiên | 200 trạng thái và health |
| POST /sessions/{id}/precheck | image multipart | 200 quality và lý do |
| POST /sessions/{id}/verify | image multipart | 200 MATCH NO_MATCH INCONCLUSIVE |
| POST /sessions/{id}/calibration/samples | image, seq, capture_offset_ms | 202 mẫu đã nhận |
| POST /sessions/{id}/calibration/complete | calibration_id | 200 đạt hoặc cần làm lại |
| POST /sessions/{id}/start | Lệnh backend có thẩm quyền | 200 MONITORING |
| POST /sessions/{id}/frames | image, seq, capture_offset_ms | 202 tiếp nhận, không bảo đảm đã suy luận |
| POST /sessions/{id}/resume | Token cũ và mã luồng mới | 200 token thay thế và yêu cầu hiệu chỉnh |
| POST /sessions/{id}/end | reason và Idempotency-Key | 200 ENDED |
| GET /sessions/{id}/events | cursor, limit, type, review_status | 200 danh sách theo cursor |
| PATCH /events/{id}/review | status, reason, expected_version | 200 bản mới |
| GET /evidence/{id}/access | Quyền xem minh chứng | 200 URL ký hạn ngắn |
| POST /configs | Cấu hình và lý do thay đổi | 201 phiên bản mới, chỉ quản trị |

POST /sessions chỉ nhận attempt_id; backend tra candidate_id và exam_id. Idempotency-Key được ràng buộc người gọi và hash nội dung; cùng khóa khác nội dung trả 409. Lưu khóa tối thiểu suốt phiên và 24 giờ sau kết thúc ở cấu hình thử nghiệm.

Giới hạn ảnh đề xuất: JPEG, tối đa 1 MiB và 1920 x 1080 sau giải mã; từ chối ảnh không đúng định dạng thực tế dù phần mở rộng hợp lệ. Khung hình gợi ý 640 x 480. Tối đa 5 request ảnh mỗi giây mỗi phiên ở cấu hình đầu, có giới hạn chung theo tải. Không tự tải URL ảnh do client cung cấp.

## 8 Ví dụ dữ liệu giao tiếp

Yêu cầu tạo phiên:

```json
{"attempt_id":"6dfc187c-ea30-4f71-82ef-c95a321f2d96"}
```

Phản hồi tạo phiên:

```json
{"session_id":"cab05a93-770e-4a0f-9e01-805b15c782fb","state":"CREATED","config_version":"cfg-1","request_id":"req-001"}
```

Phản hồi tiếp nhận ảnh:

```json
{"accepted_seq":128,"processing_status":"QUEUED","replaced_pending_seq":127,"request_id":"req-002"}
```

replaced_pending_seq có thể null. HTTP 202 chỉ xác nhận tiếp nhận; kết quả phân tích xem qua health, sự kiện hoặc kênh thông báo. Dùng lại seq và cùng hash trả 200 DUPLICATE; cùng seq khác nội dung trả 409. Ảnh quá cũ hoặc sai thứ tự trả 409 STALE_FRAME. Sau end trả 409 SESSION_ENDED.

Ví dụ sự kiện:

```json
{"event_id":"700b09b6-fd3b-4166-8416-7b2aba2606db","type":"PHONE_VISIBLE","start_at":"2026-11-13T01:00:00Z","confirmed_at":"2026-11-13T01:00:01Z","end_at":null,"review_status":"UNREVIEWED","evidence_status":"READY","model_version":"detector-1","config_version":"cfg-1","version":1}
```

Ví dụ lỗi:

```json
{"error":{"code":"VERSION_CONFLICT","message":"Sự kiện đã được cập nhật","details":{"current_version":3}},"request_id":"req-003"}
```

| HTTP | Mã nghiệp vụ tiêu biểu | Client xử lý |
|---|---|---|
| 400 | INVALID_REQUEST | Sửa cấu trúc hoặc trường thiếu |
| 401 | TOKEN_EXPIRED | Xin token mới qua backend |
| 403 | ACCESS_DENIED | Không tự thử lại bằng đối tượng khác |
| 404 | NOT_FOUND | Không tiết lộ đối tượng ngoài quyền |
| 409 | INVALID_STATE VERSION_CONFLICT STALE_FRAME | Đồng bộ trạng thái hoặc bản ghi |
| 413 | IMAGE_TOO_LARGE | Giảm ảnh |
| 415 | UNSUPPORTED_IMAGE | Chuyển định dạng hợp lệ |
| 422 | IMAGE_UNDECODABLE | Thu ảnh mới |
| 429 | RATE_LIMITED | Theo Retry-After, bỏ ảnh cũ |
| 503 | MODEL_UNAVAILABLE SERVICE_OVERLOADED | Báo suy giảm, thử lại có giãn cách |

Ảnh chất lượng kém nhưng giải mã được là kết quả quality, không phải lỗi 422. Một lần NO_MATCH cũng là HTTP 200 với kết quả nghiệp vụ, không dùng 401.

## 9 Kênh thông báo và nhất quán

WebSocket do backend quản lý phát proctor.event.created, proctor.event.updated, proctor.health.changed và proctor.session.ended. Envelope có message_id, session_id, event_id nếu có, version, server_time và payload. Client chỉ đăng ký các phiên được cấp quyền.

Truyền ít nhất một lần: client loại trùng bằng message_id và bỏ cập nhật có version thấp hơn. Sau mất kết nối, client đọc lại danh sách sự kiện bằng API; WebSocket không phải nguồn lưu duy nhất. Outbox thử lại có giãn cách và hàng đợi lỗi; không xóa sự kiện gốc khi phát thất bại.

Review dùng khóa lạc quan expected_version. Chỉ khi cập nhật thành công mới ghi ReviewAction và Outbox. Yêu cầu lặp dùng request_id hoặc Idempotency-Key để không tạo nhiều lịch sử giống nhau.

## 10 Minh chứng vận hành và bảo vệ dữ liệu

Mặc định lưu một ảnh ở lúc xác nhận và tối đa ba ảnh đại diện cho một sự kiện trong bản thử nghiệm. Không ghi liên tục toàn bộ webcam. Nếu bổ sung clip trước sau sự kiện, phải có đặc tả bộ đệm và chính sách riêng, không coi clip là tính năng đã có.

Ảnh lưu theo object_key ngẫu nhiên, kèm SHA256, quyền riêng và mã hóa. Hash phát hiện thay đổi byte khi đối chiếu bản đã ghi; hash không chứng minh ảnh phản ánh đúng thực tế. URL đọc ký trong 60 giây, không ghi vào audit. Tác vụ xóa theo expires_at xóa cả byte, thumbnail và vector hết hạn; giữ metadata tối thiểu theo chính sách đã chốt.

Đo queue_age, inference_ms từng mô-đun, end_to_end_latency, dropped_frames, unknown_ratio, CPU, RAM và số phiên. Liveness chỉ xác nhận tiến trình; readiness yêu cầu tải được các mô hình bắt buộc và kết nối nơi lưu dữ liệu. Mô hình tùy chọn lỗi được trả riêng. Khởi chạy phải kiểm tra checksum trọng số và phiên bản cấu hình.

## 11 Tham chiếu kỹ thuật

Google AI Edge MediaPipe Face Landmarker: https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker

Ultralytics Object Detection: https://docs.ultralytics.com/tasks/detect/

Ultralytics Model Export: https://docs.ultralytics.com/modes/export/

ONNX Runtime Execution Providers: https://onnxruntime.ai/docs/execution-providers/

Các nguồn trên hỗ trợ lựa chọn thành phần kỹ thuật. Thông số thời gian, API, cấu trúc dữ liệu và tiêu chí nghiệm thu trong bộ tài liệu là đề xuất riêng của thiết kế này. Phiên bản thư viện và trọng số chỉ được khóa sau khi cài đặt và chạy thử.
