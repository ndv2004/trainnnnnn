# Camera AI — prototype webcam chạy độc lập

Mở cửa sổ camera trực tiếp trên Windows, chạy CPU; không cần server, trình duyệt hoặc tài khoản. Camera tự bật, hiển thị khuôn mặt/tên đã đăng ký, vật thể được hỗ trợ, hướng đầu, hướng mắt **ước lượng**, bộ đếm và cảnh báo theo thời gian. Luồng thi/API cũ được giữ riêng, không tham gia bản camera; hướng dẫn cũ ở [docs/README_LEGACY.md](docs/README_LEGACY.md).

## Cài và mở

Trong thư mục `ai_proctor`, chạy lần đầu:

```bat
setup_camera.bat
```

Sau khi cài thành công, mỗi lần dùng chỉ mở:

```bat
start_camera.bat
```

Setup tự tìm **Python 3.12 x64** qua Python Launcher, PATH, registry hoặc Python gốc của `.venv` hiện có, rồi tạo `.venv-camera` riêng. Không sửa `.venv` cũ hoặc gỡ Python khác. Nếu thiếu Python, cài bản 3.12 64-bit từ [python.org](https://www.python.org/downloads/windows/), bật **pip, tcl/tk and IDLE, Python launcher**, rồi chạy lại setup. Khi lỗi, script dừng ngay và nêu bước thiếu; không gọi Python chưa tồn tại. Có thể chỉ định Python:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_camera.ps1 -Python "C:\DuongDan\Python312\python.exe"
```

Chạy trực tiếp hoặc chọn camera khác:

```powershell
.\.venv-camera\Scripts\python.exe run_camera.py
.\.venv-camera\Scripts\python.exe run_camera.py --camera 1
```

## Dùng menu ☰

- **Đăng ký khuôn mặt:** nhập tên tiếng Việt → Bắt đầu thu mẫu → chỉ một mặt, đủ sáng/rõ, đổi góc nhẹ → thu 10–15 mẫu hợp lệ → **Lưu**. Có thể chọn đặt làm người chính. **Hủy** bỏ toàn bộ mẫu chưa lưu. Không tự tạo hồ sơ từ người đầu tiên và không mở camera thứ hai.
- **Danh sách khuôn mặt:** xem và xóa từng hồ sơ; xóa có xác nhận.
- **Chọn người giám sát chính:** chọn hồ sơ hoặc theo dõi tạm “Người”. Người khác không thay thế người chính khi người chính vắng mặt. Theo dõi tạm không đăng ký danh tính; muốn chọn lại đối tượng tạm, dùng mục này hoặc căn chỉnh lại.
- **Căn chỉnh lại đầu/mắt:** nhìn gần thẳng, đầu ổn định, mở mắt khoảng vài giây. Tiến độ/hướng dẫn nằm trên camera. Căn chỉnh không tạo danh tính. Cảnh báo hành vi tạm dừng trong khi đăng ký/căn chỉnh.
- **Thoát:** đóng nguồn camera và worker.

Chưa có hồ sơ hoặc không khớp: **Người**. Ảnh không đủ dữ liệu: **Chưa đủ dữ liệu/Đang xác định**. Tên cần vượt ngưỡng cosine và chênh lệch với hồ sơ đứng thứ hai, cùng xác nhận qua nhiều mẫu; không chọn tên chỉ vì điểm cao nhất.

Vật thể có hộp nét đứt `VT`; khuôn mặt có hộp nét liền, người chính màu xanh lá. Sáu lớp đang hiển thị: **Người, Điện thoại, Chai, Cốc, Sách, Laptop** theo đầu ra thật NanoDet. Số người của detector và số khuôn mặt là hai quan sát riêng. Không gán điện thoại cho người cụ thể. Chưa có chống ảnh giả: khuôn mặt trong ảnh/điện thoại có thể được nhận diện; kết quả không xác minh “người thật”.

Preview webcam mặc định lật gương; **trái/phải mô tả phía của người trước camera**, độc lập phép lật hình. Hộp được lật và resize cùng hình, giữ tỷ lệ. Pose và iris luôn xử lý ảnh gốc. Mắt thiếu mốc căn chỉnh, nhắm, nhỏ, mờ, tối hoặc không đủ điều kiện trả **Chưa xác định**. Kính che mắt vẫn có thể đánh lừa landmark; hướng mắt là thử nghiệm.

## Dữ liệu, cấu hình, mô hình

Hồ sơ gồm ID, tên, embedding SFace 128 chiều, phiên bản/checksum mô hình và thời điểm UTC; lưu mã hóa **Windows DPAPI gắn với tài khoản Windows hiện tại** trong `.camera-data/profiles.enc`. Không lưu ảnh, video, vector trong log; mẫu đăng ký chưa lưu chỉ nằm trong RAM. Đóng/mở ứng dụng vẫn giữ hồ sơ. Không đưa `.camera-data/`, `runtime/` hoặc bản sao lưu hồ sơ vào Git/gói chia sẻ. Sao lưu file mã hóa dưới cùng tài khoản Windows; ứng dụng không tự tạo lại kho khi dữ liệu hỏng hoặc không giải mã được. Trên hệ khác có Fernet với file khóa quyền riêng, chưa phải đích triển khai đã thử.

[config/camera-v1.json](config/camera-v1.json) chứa ngưỡng phiên bản: nhận diện 1:N thử nghiệm `0.48`, margin `0.08`, vắng mặt 3 giây, nhiều người/mặt 2 giây, điện thoại 1 giây và ít nhất 3 mẫu, quay đầu dài 4 giây, 4 lượt độc lập/60 giây, thiếu dữ liệu tối đa 1,5 giây. Một lượt quay dài không thành nhiều lượt. Ảnh cũ, mất camera, tối/mờ hoặc lỗi mô hình trả UNKNOWN và không cộng thời gian hành vi. Không có điểm/phần trăm hoặc kết luận gian lận.

Trọng số được đóng kèm và xác minh SHA256. Kiểm tra/tải phần thiếu:

```powershell
.\.venv-camera\Scripts\python.exe scripts\check_camera_models.py --check --smoke
.\.venv-camera\Scripts\python.exe scripts\check_camera_models.py --download --smoke
```

File sai checksum được giữ nguyên. Chỉ dùng `--download --repair` khi chủ động muốn giữ bản cũ thành backup rồi thay bằng trọng số đã xác minh. Thiếu/lỗi mô hình được báo theo mô-đun trên cửa sổ; không tạo kết quả giả. API, phiên bản và giấy phép MIT/Apache-2.0: [docs/CAMERA_MODELS.md](docs/CAMERA_MODELS.md), bản sao giấy phép trong `third_party/`.

## Video và kiểm thử

Video dùng cùng pipeline và hiển thị rõ là video phát lại:

```powershell
.\.venv-camera\Scripts\python.exe run_camera.py --video "C:\DuLieuThu\camera_test.mp4"
.\.venv-camera\Scripts\python.exe run_camera.py --headless --video "C:\DuLieuThu\camera_test.mp4" --seconds 20 --report reports\local_video_check.json
.\.venv-camera\Scripts\python.exe run_camera.py --headless --seconds 10 --report reports\local_camera_check.json
```

Báo cáo không chứa ảnh, embedding hoặc tên; file đã có không bị ghi đè. Không dùng chế độ headless để đăng ký hồ sơ tự động.

Kiểm thử phát triển (không cần cài các gói backend):

```powershell
.\.venv-camera\Scripts\python.exe -m pip install -r requirements-camera-dev.txt
.\.venv-camera\Scripts\python.exe scripts\prepare_smoke_assets.py
.\.venv-camera\Scripts\python.exe -m pytest -c pytest-camera.ini -q
.\.venv-camera\Scripts\python.exe -m ruff check camera_app run_camera.py scripts\check_camera_models.py tests_camera
```

Hai ảnh công khai tải riêng vào `test_data/` để gọi mô hình thật; không đóng kèm hồ sơ/ảnh cá nhân vào mã nguồn. Mẫu biến đổi từ ảnh công khai và video tổng hợp chỉ kiểm luồng chức năng; không thay thế thử trực tiếp nhiều người, điện thoại hoặc đo accuracy. Tkinter cần phiên Windows desktop hoạt động; không chạy trên server không có màn hình. Kết quả thực tế và phần chưa xác minh: [reports/CAMERA_TEST_REPORT.md](reports/CAMERA_TEST_REPORT.md).

Nếu thư mục tạm Windows bị lỗi quyền truy cập, chạy test với thư mục mới trong workspace, ví dụ `--basetemp runtime/camera-tests-20261113-01 -p no:cacheprovider`; thay hậu tố cho mỗi lượt thử. Các test DPAPI/Tk cần chạy bằng tài khoản Windows có desktop và quyền truy cập hồ sơ mã hóa.

Mã chính: `camera_app/camera.py` (một nguồn, ảnh mới nhất), `vision.py` (CPU thật), `identity.py` (lưu/thu mẫu), `tracking.py` (từng khuôn mặt), `pose.py`, `events.py` (quan sát độc lập camera), `runtime.py` (worker), `ui.py` (Tkinter chỉ cập nhật từ luồng UI). Không lưu dòng webcam liên tục.
#   t r a i n n n n n n 
 
 