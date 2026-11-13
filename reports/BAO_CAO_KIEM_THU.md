# Báo cáo thực thi — Exam Watch 0.1.0

Ngày 13/11/2026. Build và dấu thời gian chính xác: `test-summary.json`. Phạm vi môi trường: Linux container, Python 3.12.14, CPU; không phải máy Windows i7-10510U/MX130. Chỉ sử dụng dữ liệu công khai cục bộ và fixture tổng hợp, không có thí sinh thật hoặc bộ dữ liệu thi được gán nhãn.

## Kết quả đã thực thi

| Lớp | Kết quả thực tế | Bằng chứng |
|---|---|---|
| Quy tắc/API/lưu trữ/phục hồi | 64 kiểm thử PASS: đồng hồ giả, biên thời gian/mẫu, UNKNOWN/gap/cooldown, chống trùng, quyền, CAS, race worker-review, fault injection, restart và native process crash | `all-tests.xml`, `logic-api.xml` (lọc từ cùng lần chạy) |
| Mô hình thật | 2 kiểm thử PASS: bốn trọng số nạp CPU, mặt/pose/person/vector SFace thật; cùng ảnh/khác ảnh; API end-to-end có reference riêng và calibration 12 mẫu | `real-models.xml`, `real-e2e.json`, `tests/test_real_models.py` |
| Toàn bộ pytest | **66 PASS, 0 FAIL, 0 SKIP** trong lần thực thi đã ghi | `all-tests.xml` và `test-summary.json` |
| HTTP qua TCP thật | PASS, năm route: live, ready, HTML, OpenAPI, JavaScript; readiness READY với mô hình thật, khởi động và dừng server | `http-smoke.json`, `scripts/http_smoke.py` |
| Video độc lập | PASS: video phát lại 8 giây, lấy 40 ảnh qua mô hình CPU, không tạo sự kiện trong đoạn tham chiếu này | `video-smoke.json`; đầu ra chi tiết cục bộ không đóng gói |
| Checksum/dependency/static | Bốn trọng số đúng SHA256; Google task CRC32C khớp; `pip check` không xung đột; Ruff và `node --check` qua | manifest, requirements, mã kiểm tra |
| Windows binary availability | Tải được wheel win_amd64/universal của 46 phiên bản khóa ban đầu cho CPython 3.12. Bổ sung khóa Colorama 0.4.6 (wheel universal) để dependency Windows không trôi phiên bản | `windows-compatibility.json`; không đóng gói wheel |

Một warning upstream trong test harness: Starlette báo `httpx` TestClient sẽ chuyển sang `httpx2`. Không có test lỗi vì warning này; giữ bộ phiên bản đã chạy, không tự đổi runtime chưa kiểm thử. MediaPipe in thông báo XNNPACK/feedback tensor khi khởi động, không chứa ảnh/vector/token.

Hai kiểm thử mô hình thật **không** là phép đo accuracy/FAR/FRR. Ảnh chuẩn và ảnh truy vấn cùng người dùng lại cùng ảnh công khai để kiểm tra đường dữ liệu; ảnh khác người chỉ một cặp. Không suy ra tỷ lệ từ các mẫu đó. Detector vật thể đã trả hộp person trên ảnh thật; chưa có test positive điện thoại, người thứ hai, kính lóa hoặc mặt trên điện thoại được nghiệm thu.

Luồng API thật đã chạy: backend adapter tạo phiên → consent → admin cấp hồ sơ ảnh riêng → ảnh kém không qua precheck → ảnh rõ qua → SFace MATCH → MediaPipe calibration → start → ảnh cảnh tổng hợp không mặt đủ chất lượng → một FACE_NOT_VISIBLE → ảnh lưu mã hóa → giám thị có quyền đọc và ghi xem xét → end chặn ảnh tiếp. Ảnh cảnh tổng hợp chỉ kiểm tra chức năng không phát hiện mặt, không đại diện cho hành vi thí sinh hoặc chất lượng trên lớp học.

Kiểm thử crash dùng tiến trình con kết thúc bằng `os._exit(23)` sau giao dịch event/outbox đã commit. Khi mở lại, event vẫn còn và đóng tại last_observed_at, có WORKER_INTERRUPTED, outbox còn đọc được, phiên yêu cầu resume. Các kiểm thử lỗi mô hình, kho ảnh và gửi thông báo dùng fault injection riêng, không tính vào chất lượng nhận diện.

## Đối chiếu ca nghiệm thu

`nghiem_thu.json` gồm đủ TC01–TC26, IT01–IT18, E2E01–E2E04 và SM01 bổ sung. Có 38 PASS trong phạm vi được ghi, 11 BLOCKED, 0 FAIL; PASS ở lớp ENGINE/API không phải PASS nhận diện trên người. Ca BLOCKED có `executed_at=null`, không giả làm ca đã chạy thành công.

Các ca BLOCKED:

| Ca | Nguyên nhân cụ thể |
|---|---|
| TC04 | Chưa có tập cặp danh tính đủ người/quy mô để nghiệm thu; smoke cùng/khác ảnh chỉ kiểm API/model wiring. |
| TC06 | Chống giả mạo/challenge nonce chưa triển khai và kiểm chứng. API trả UNAVAILABLE. |
| TC11 | Chưa có chuỗi 2 người thật được gán nhãn để xác nhận box + event + evidence. Rule thời gian đã qua unit test. |
| TC12 | Chưa có mẫu mặt trên điện thoại để đánh giá nhầm người/mặt. Hai tín hiệu tách riêng đã kiểm bằng dữ liệu chuẩn hóa. |
| TC14 | Chưa gọi model với phone positive thực đáp ứng ngưỡng mặc định. Rule 1 giây/3 mẫu và lưu ảnh được kiểm riêng. Nguồn ảnh phone công khai đã thử tải trả 403 nên không dùng. |
| TC19 | Không có video kính lóa và nhãn gaze; chức năng mặc định tắt, chỉ có interface/heuristic thử nghiệm. |
| IT11 | Chưa stress thực 10 phút; test giữ một pending/replaced/dropped/rate limit đã chạy. |
| E2E01–E2E04 | Browser cục bộ không được tạo socket process singleton; cloud browser localhost là endpoint khác, không vào được app của workspace. API/model subflows, storage/replay và history được thử riêng nhưng không thay thế thao tác UI. |

## Chưa có cơ sở nghiệm thu

- Không có webcam vật lý, laptop Windows mục tiêu, PowerShell hoặc GPU MX130 gắn với môi trường. Bộ cài Windows chưa được thực thi, dù các phiên bản chính có wheel tương thích. Không tuyên bố FPS hay số phiên đồng thời đạt SLA.
- Chưa có 12 người tình nguyện/36 phiên, split theo người, nhãn độc lập, normal hours hoặc ≥30 event/type như kế hoạch. Tất cả mục tiêu Precision/Recall/F1, FAR/FRR, false alerts/hour, UNKNOWN ≤10% và p95 alert ≤2 giây **chưa đo**.
- Chưa chạy ổn định 60 phút, đo nhiệt, VRAM, điều kiện nguồn điện, long-run memory hay tải tăng. Video smoke 8 giây không phải benchmark máy mục tiêu. Các timing trong JSON chỉ là thời gian xử lý cục bộ của đoạn smoke.
- Gaze chỉ ngang tương đối, không có model mắt chuyên dụng/nhãn gaze. Liveness chưa có kết quả thành công, không bảo đảm chống ảnh in hoặc video phát lại.
- Chưa tích hợp backend thi/SSO/gateway HTTPS/object store thật. Adapter và đăng nhập giả danh chỉ phục vụ localhost demo. Chưa tự phát hiện đầy đủ dịch chuyển vật lý của cùng webcam.

## Tái lập

Từ thư mục dự án, sau cài requirements và xác minh trọng số:

```powershell
.\.venv\Scripts\python.exe scripts\prepare_smoke_assets.py
.\.venv\Scripts\python.exe -m pytest -q --junitxml=reports\all-tests.xml
.\.venv\Scripts\python.exe scripts\http_smoke.py
```

Video smoke cần ffmpeg trên PATH để tạo fixture tùy chọn:

```powershell
.\.venv\Scripts\python.exe scripts\prepare_smoke_assets.py --videos
.\.venv\Scripts\python.exe scripts\analyze_source.py --video test_data\reference.webm --seconds 8 --output runtime\video-smoke-new.jsonl
```

Optional browser QA: cài Playwright/Chromium riêng cho QA, chuẩn bị `--videos`, chạy server mới với data directory thử riêng rồi `node scripts/ui_smoke.cjs`. Đây là ca chưa thực thi thành công ở môi trường hiện tại. Không chạy script xóa/ghi đè dữ liệu cũ để thử lại; dùng attempt/data directory mới.

Ảnh/video mẫu, vector thử, secret, token, DB, screenshots và runtime không nằm trong ZIP bàn giao. Chỉ đóng gói mã, bốn trọng số công khai, giấy phép, cấu hình, đặc tả, OpenAPI và kết quả kiểm thử không chứa dữ liệu nhạy cảm.
