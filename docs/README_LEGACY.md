# Exam Watch — AI hỗ trợ giám sát thi

Bản thử nghiệm **chạy CPU**, xây dựng trực tiếp từ ba đặc tả trong `specs/`. Có mô hình thật, API FastAPI, bộ tổng hợp sự kiện, SQLite, minh chứng mã hóa và màn hình thí sinh/giám thị. AI chỉ ghi nhận dấu hiệu để người có thẩm quyền xem xét.

**Chưa phải bản đã nghiệm thu chất lượng nhận diện hoặc sẵn sàng triển khai kỳ thi thật.** Kết quả chạy thực tế và ca còn bị chặn nằm trong `reports/BAO_CAO_KIEM_THU.md`. Không có số liệu accuracy, FAR, FRR hay năng lực nhiều phiên khi chưa đo.

## 1. Cài trên Windows từ môi trường mới

Cài **Python 3.12 64-bit**, gồm Python Launcher `py`. Không dùng chung môi trường Python 3.14 của dự án khác. Giải nén rồi mở PowerShell **trong thư mục `ai_proctor`**:

```powershell
py -3.12 --version
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
.\.venv\Scripts\python.exe scripts\run.py
```

Mở **http://127.0.0.1:8000/**. OpenAPI tương tác ở **http://127.0.0.1:8000/docs**, JSON ở `/openapi.json`. `Ctrl+C` để dừng. Không phải mở file HTML trực tiếp.

Lệnh tương đương nếu không chạy PowerShell script:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe scripts\download_models.py
.\.venv\Scripts\python.exe scripts\run.py --port 8000
```

Trọng số đã được đóng kèm; downloader kiểm tra SHA256 và bỏ qua file đúng. Nếu bản tải chỉ có mã nguồn, cùng lệnh sẽ tải trọng số từ URL đã khóa trong `models/manifest.json`. File hiện có sai checksum sẽ bị từ chối, không âm thầm ghi đè. `--repair` giữ bản cũ thành file backup rồi thay bằng bản đã xác minh. Không cần CUDA, PyTorch hay ONNX Runtime; ONNX dùng OpenCV DNN CPU.

Bộ dependencies đã cài/chạy thử trên Linux Python 3.12.14. Đã kiểm tra có wheel Windows cho các phiên bản được khóa; **chưa chạy bộ cài trên Windows hoặc webcam của máy HP**. `requirements.txt` có cả công cụ kiểm thử. Script không đổi cấu hình hệ thống, không cài GPU và không xóa dữ liệu.

## 2. Chạy webcam và xem cảnh báo

1. Mở mục **Hồ sơ chuẩn**. Người quản trị chọn một ảnh JPEG được đối soát riêng, nhập mã nguồn xác minh và đăng ký cho **Thí sinh A**. Không lấy tự động ảnh webcam làm hồ sơ. Đăng ký tạo vector SFace mã hóa; ảnh chuẩn không được lưu lại.
2. Sang **Thí sinh**, mở webcam, đọc thông báo dữ liệu, đánh dấu đồng ý rồi tạo phiên. Trình duyệt cần cho phép camera tại localhost.
3. Chọn **Kiểm tra camera → Xác thực khuôn mặt → Thu mẫu hiệu chỉnh**. Nhìn bình thường vào màn hình, giữ đầu tương đối ổn định; mẫu cần một mặt đủ lớn và vùng mắt thấy được. Chỉ khi đạt `READY` mới bắt đầu giám sát được.
4. Chọn **Bắt đầu giám sát**. Bản demo gọi adapter backend cục bộ để thử luồng; đây không phải kết nối với hệ thống thi của bạn.
5. Mở **Giám thị**, chọn Giám thị A, tải danh sách rồi chọn phiên. Có thể mở trang thứ hai để giữ màn hình thí sinh riêng. Cảnh báo đi qua WebSocket; REST là nguồn dữ liệu bền vững.
6. Giám thị đọc khoảng thời gian, xem ảnh minh chứng rồi chọn kết quả và nhập lý do. `CONFIRMED_OBSERVATION` chỉ xác nhận dấu hiệu quan sát được. Sự kiện đang cập nhật có thể gây xung đột phiên bản; tải lại rồi xem xét bản mới.
7. **Dừng tham gia** kết thúc phiên và đóng sự kiện đang mở. Một `attempt_id` chỉ có một phiên, kể cả khi đã kết thúc; để chạy lượt thử mới, backend phải đăng ký một `attempt_id` mới. Khởi động lại chương trình không xóa phiên cũ.

Ba lần xác thực tự động chưa thành công sẽ cần giám thị cho thử lại hoặc chấp nhận thủ công với lý do. Chấp nhận thủ công lưu riêng `manual_override`, không biến kết quả thành `MATCH`. Thiếu ảnh chuẩn trả `INCONCLUSIVE / REFERENCE_PROFILE_MISSING`.

Để quan sát quy tắc, thử điện thoại xuất hiện liên tục, người thứ hai đứng cạnh, quay đầu đủ lâu hoặc bốn lượt quay có trở về trung tính. Ngưỡng trong `config/cpu-v1.json` là ngưỡng thử nghiệm; mô hình có thể bỏ sót/báo nhầm. Ảnh tối, mờ và mô hình lỗi cho `UNKNOWN`. Không có ảnh mới tạo gián đoạn kỹ thuật, không suy ra thí sinh rời chỗ.

Nếu camera/mạng gián đoạn, mở lại nguồn ảnh rồi chọn **Khôi phục luồng**. Mã luồng và token mới vô hiệu hóa luồng trước; ảnh chờ cũ bị bỏ. Thay thiết bị hoặc kích thước ảnh bắt buộc hiệu chỉnh lại. Hệ thống chưa chứng minh tự nhận ra mọi lần dịch chuyển vật lý của cùng webcam; người vận hành phải khai báo đổi camera bằng `resume(camera_changed=true)` khi cần.

## 3. Chạy video khi không có webcam

Trên màn hình Thí sinh, chọn **Chọn video** thay vì mở webcam; các bước xác thực, hiệu chỉnh, bắt đầu và xem xét giữ nguyên. Video cần được sử dụng hợp lệ và có hồ sơ chuẩn riêng. Nguồn hiển thị rõ là video phát lại; giao thức không chứng minh nguồn ảnh là webcam trực tiếp.

Có chế độ phân tích nguồn độc lập, ghi JSONL quan sát và sự kiện, không cần backend/API:

```powershell
.\.venv\Scripts\python.exe scripts\analyze_source.py --video "C:\DuLieuThu\phien_01.mp4" --seconds 60 --output "C:\DuLieuThu\ket_qua_01.jsonl"
.\.venv\Scripts\python.exe scripts\analyze_source.py --camera 0 --seconds 60 --preview --output "C:\DuLieuThu\webcam_01.jsonl"
```

Chế độ độc lập không tự tạo danh tính chuẩn hoặc tự hiệu chỉnh từ người đầu tiên trong video. Khi thiếu `--baseline`, đầu/mắt là `UNKNOWN`; xác thực ghi `NOT_EVALUATED`. Dùng giao diện/API nếu cần thử đủ vòng đời, minh chứng mã hóa và luồng giám thị. File đầu ra đã có sẽ không bị ghi đè.

## 4. Kiến trúc và quy tắc

| Thành phần | File / nhiệm vụ |
|---|---|
| Nhập ảnh, quality | `images.py`: xác minh JPEG, byte/pixel, độ sáng/độ rõ |
| Mô hình CPU | `models.py`: YuNet, SFace, NanoDet, MediaPipe Tasks; lỗi từng mô-đun |
| Quan sát | `domain.py`: POSITIVE / NEGATIVE / UNKNOWN, dấu thời gian và hạn dùng |
| Quy tắc thuần | `event_engine.py`: thời lượng, số mẫu, clear/cooldown, lượt quay, danh tính |
| Điều phối | `service.py`: một ảnh đang xử lý + tối đa một ảnh chờ mới nhất mỗi phiên; quay vòng phiên |
| Lưu trữ | `store.py`: SQLite WAL, giao dịch event + outbox, review CAS, idempotency |
| Minh chứng | `evidence.py`: Fernet, SHA256, URL hạn 60 giây + quyền người đọc, dọn quá hạn |
| Quyền / tích hợp | `security.py`: JWT có mục đích, phạm vi phiên, thu hồi; adapter độc lập |
| API và UI | `api.py`, `web/`: API theo đặc tả 02, giao diện không cần npm build |

Đồng hồ đơn điệu của máy chủ quyết định thời lượng trong một tiến trình; mốc nghiệp vụ lưu UTC. Offset client chỉ dùng kiểm tra độ mới/tính hợp lý, không dùng để làm hành vi đủ thời lượng. Sau restart, sự kiện mở đóng tại quan sát cuối với `WORKER_INTERRUPTED`, bỏ ứng viên và yêu cầu resume. Không cộng thời gian dừng máy.

Mô-đun vật thể chạy theo lịch riêng. Lượt không đến lịch không tăng số mẫu hoặc thời lượng; kết quả hết hạn không được giữ thành kết quả mới. Nhiều mặt và nhiều người là hai tín hiệu riêng. Khuôn mặt trên điện thoại/ảnh in vẫn có thể đánh lừa mô hình; không có nhãn “người thật” chỉ từ landmark.

Các tham số, checksum và quyết định xử lý những chỗ đặc tả còn mở được ghi trong `docs/QUYET_DINH.md`; danh mục mô hình/giấy phép ở `docs/MO_HINH.md`. File `config/cpu-v1.json` là cấu hình phiên bản bất biến; API thay cấu hình chỉ áp dụng phiên mới.

## 5. Dữ liệu và chế độ triển khai

Mặc định `PROCTOR_DEMO=1`, bind `127.0.0.1`. Demo sử dụng tài khoản giả danh; người dùng trên chính máy có thể chọn vai trò để thử. **Không dùng cơ chế đăng nhập demo để tổ chức thi thật.** Middleware chặn host/peer ngoài loopback và Origin khác nguồn. Không mở CORS đại trà.

`runtime/` chứa DB, khóa tự sinh và ảnh mã hóa ngoài webroot. Không chia sẻ thư mục này, không đưa vào Git hoặc ZIP nguồn. Trên Windows, chọn thư mục NTFS riêng chỉ tài khoản chạy dịch vụ được đọc; khóa không dựa vào TPM/DPAPI. Giữ nguyên khóa khi khôi phục DB, nếu mất khóa thì không giải mã được dữ liệu cũ.

Ảnh minh chứng tối đa 3/sự kiện, mặc định 30 ngày; không có clip hay lưu toàn bộ webcam. Hồ sơ chuẩn có hạn riêng, có API xóa. Dọn dữ liệu chạy mỗi phút và có lệnh quản trị. Metadata sự kiện/audit được giữ để truy vết, chưa có chính sách lưu lâu dài của đơn vị tổ chức. Gói bàn giao không chứa ảnh/video khuôn mặt thử, hồ sơ, token hoặc khóa.

Khi xây cổng tích hợp riêng, đặt `PROCTOR_DEMO=0`, dùng HTTPS khi ra mạng, giữ một worker cho adapter SQLite này và để backend xác nhận lượt thi/phân công. `POST /admin/attempts` chỉ nhận lệnh từ danh tính backend/quản trị; không nhận candidate_id do trình duyệt tự khai để tạo phiên. Hệ thống thi thật chưa có trong workspace, nên chưa có adapter SSO hay kết nối DB hệ thống thi của bạn.

Cấp token cho backend cục bộ bằng công cụ quản trị, không in token ra log:

```powershell
$env:PROCTOR_DEMO="0"
.\.venv\Scripts\python.exe scripts\provision_token.py --actor exam-backend --role service --output .\runtime\backend-token.txt
```

Gửi token bằng `Authorization: Bearer ...`; tài liệu `docs/API.md` mô tả các quyền. Chạy **một tiến trình / một data directory**; không dùng `uvicorn --workers 2`. Khóa tiến trình từ chối worker thứ hai, thay vì âm thầm tạo hai bộ đếm khác nhau.

## 6. Tái lập kiểm thử

```powershell
.\.venv\Scripts\python.exe -m pytest -q -m "not real"
.\.venv\Scripts\python.exe scripts\prepare_smoke_assets.py
.\.venv\Scripts\python.exe -m pytest -q -m real
.\.venv\Scripts\python.exe scripts\http_smoke.py
.\.venv\Scripts\python.exe -m ruff check proctor scripts tests
```

`prepare_smoke_assets.py` tải ảnh mẫu công khai vào `test_data/`, kiểm checksum; ảnh không được đóng kèm bộ cài. Nếu chưa có dữ liệu, test mô hình thật được ghi `SKIPPED`, không phải PASS. Các fixture giả chỉ kiểm tra logic/API. Hai kiểm thử mô hình thật dùng ảnh tham chiếu lặp lại và một ảnh khác người để kiểm tra đường xử lý; không suy ra FAR/FRR từ đó.

Linux tương đương: `python3.12 -m venv .venv`, `.venv/bin/python -m pip install -r requirements.txt`, `.venv/bin/python scripts/run.py`.

Để đánh giá event trên dữ liệu có nhãn, dùng `scripts/evaluate_events.py --predictions predicted.json --labels labels.json --output evaluation.json`. Mỗi bản ghi gồm `clip_id`, `event_type`, `start_ms`, `end_ms`. Ghép một-một theo temporal IoU ≥ 0,3, ưu tiên IoU cao trước; dự đoán trùng tính FP. Dữ liệu giả không thay thế bộ dữ liệu phát triển/điều chỉnh/kiểm thử chia theo người của tài liệu 03.

Benchmark máy mục tiêu cần video đủ dài, nguồn điện và điều kiện thiết bị được ghi lại:

```powershell
.\.venv\Scripts\python.exe scripts\analyze_source.py --video "C:\DuLieuThu\phien_60_phut.mp4" --seconds 3600 --realtime --power-note "AC; Windows; webcam 640x480" --output "C:\DuLieuThu\benchmark_01.jsonl"
```

Đây là phép đo pipeline nguồn độc lập; benchmark độ trễ thông báo API và UI cần đo thêm trên luồng phiên thực. Script ghi CPU/RAM và thời gian xử lý, không tự điền nhiệt độ/VRAM. Không ngoại suy kết quả container sang i7-10510U/MX130.

## 7. Phần còn thiếu / thử nghiệm

- Chưa có tập dữ liệu thi được đồng ý và gán nhãn: chưa nghiệm thu chất lượng người/điện thoại/hướng đầu, FAR/FRR hay cảnh báo nhầm mỗi giờ. Chưa chạy điện thoại thật trước webcam trong môi trường này.
- Hướng nhìn chỉ có heuristic iris ngang tương đối sau hiệu chỉnh, **mặc định tắt**; bật thì luôn mang nhãn thử nghiệm. Không có tọa độ nhìn chính xác hay hiệu chỉnh 9 điểm.
- Chống giả mạo chưa triển khai/kiểm chứng. Endpoint capability và challenge trả lý do `UNAVAILABLE`, không cấp kết quả giả.
- Chưa xác minh UI trong trình duyệt thật của môi trường này, webcam vật lý, bộ cài PowerShell hoặc benchmark 60 phút trên Windows. Có test script và quy trình để chạy tại máy mục tiêu.
- Adapter standalone chỉ dành cho demo, tối đa 2 phiên cấu hình bảo vệ tài nguyên; **không phải tuyên bố hỗ trợ 2 phiên đạt SLA**. Chưa có hệ thống thi thật, hệ thống phân tán hoặc object store từ xa.

Xem `docs/DOI_CHIEU_YEU_CAU.md` và `reports/BAO_CAO_KIEM_THU.md` để phân biệt phần đã chạy, phần kiểm bằng dữ liệu giả và ca BLOCKED.
