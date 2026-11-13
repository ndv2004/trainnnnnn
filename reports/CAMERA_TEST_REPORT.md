# Kiểm thử bản camera độc lập — 13/11/2026

## Phạm vi và môi trường

Đã triển khai trực tiếp module `camera_app/`, entrypoint `run_camera.py`, menu Tkinter, nhận diện 1:N bằng SFace thật, đăng ký chủ động và lưu hồ sơ DPAPI. Không dùng API/server/browser trong luồng mới. Mã backend, SQLite và dữ liệu cũ được giữ nguyên; README cũ được giữ ở `docs/README_LEGACY.md`. Workspace không có Git hoặc AGENTS.md trong thư mục/chuỗi thư mục cha được kiểm tra.

Môi trường chạy thử: Windows, Python 3.12.10 x64. `py -0p` không thấy Python; setup mới tìm được Python gốc từ `.venv` cũ và tạo thành công `.venv-camera` riêng. `pip check` đạt. Chạy CPU với OpenCV 4.11.0.86, MediaPipe 0.10.32, NumPy 2.3.5, Pillow 13.0.0, cryptography 50.0.0. Không sử dụng CUDA. Các phiên bản khác nằm trong requirements khóa riêng.

Tcl/Tk và DPAPI bị giới hạn khi chạy trong sandbox của công cụ; chạy bằng tài khoản Windows thực ngoài sandbox thì hoạt động. Không thay đổi Windows privacy/security hoặc cài lại Python để xử lý giới hạn này.

## Kết quả đã chạy thật

Lượt cuối trong **`.venv-camera`: 44/44 kiểm thử đạt, không skip, 29,44 giây**, chạy với `pytest-camera.ini`. Kết quả máy: `reports/camera-tests.xml`. Ruff và compileall đạt; `pip check` không có dependency lỗi. Môi trường camera không cài FastAPI hoặc Uvicorn.

- Bộ cài `scripts/setup_camera.ps1`: tạo môi trường mới, cài dependency camera, kiểm tra Tk và thực hiện suy luận cả bốn mô hình; mã thoát 0. Đường Python sai dừng trước khi tạo/cài môi trường. Trọng số thử nghiệm bị hỏng/thiếu được liệt kê theo mô-đun và giữ nguyên file đã có.
- `scripts/check_camera_models.py --check --smoke`: SHA256 đúng cả bốn trọng số; YuNet, SFace, NanoDet và MediaPipe chạy CPU thành công.
- `run_camera.py --headless --seconds 8`: camera 0 mở bằng DirectShow, đọc khung 640×480, thu 24 kết quả xử lý mới, tối đa một khuôn mặt và phát hiện lớp Người; không có lỗi bốn mô-đun. Kết quả máy ở `reports/local_camera_runtime_01.json`. Số kết quả này không phải phép đo FPS, accuracy hoặc chứng minh webcam không phải nguồn ảo.
- `start_camera.bat --smoke-ui-seconds 4`: mở cửa sổ camera thật và tự đóng, mã thoát 0. Chạy lại bằng entrypoint trực tiếp cũng đạt.
- Test mô hình trên hai ảnh công khai đã xác minh checksum: phát hiện mặt, SFace 128 chiều, 478 landmark, pose, iris và detector vật thể. 15 mẫu SFace từ các biến đổi ảnh chân dung được nhận vào bộ thu mẫu; mẫu ảnh khác người giữa chừng bị từ chối. Lưu tên tiếng Việt bằng DPAPI, đóng/mở kho, nhận lại hồ sơ và giữ lựa chọn người chính đều đạt. Đây là fixture chức năng, không phải đăng ký sinh trắc người dùng thật hay bộ đo FAR/FRR.
- Test Tk native: preview qua PhotoImage, nội dung tiếng Việt, tọa độ hộp/lật gương, hộp đăng ký và Lưu/Hủy, chọn/xóa hồ sơ, căn chỉnh, xóa hình cũ khi mất nguồn. Test Tk + pipeline thật dùng video mẫu: nhận hình/khuôn mặt, bốn mô hình không lỗi, đóng cửa sổ dừng cả reader/worker và giải phóng VideoCapture.
- Test tích hợp đăng ký xuyên suốt: video MJPG biến đổi từ ảnh công khai → CameraSource duy nhất → worker với mô hình thật → lệnh Bắt đầu → ít nhất 10 mẫu hợp lệ → kiểm tra chưa có hồ sơ trước Lưu → Lưu tên tiếng Việt/đặt người chính → nhận lại đúng tên trong cùng pipeline → đóng worker/camera → mở lại kho DPAPI vẫn đúng tên và người chính. Cảnh báo bị tạm dừng trong lúc thu mẫu. Không cần API hoặc trang web.

Đã sửa lỗi thật: NanoDet kèm dự án xuất 6 tensor (ba stride), adapter mới ban đầu đòi 8; nay hỗ trợ đúng 6 hoặc 8. Cũng đã bổ sung chặn kết quả quá hạn trước khi thu mẫu/căn chỉnh, tách lỗi kỹ thuật khỏi vắng mặt, bỏ hộp vật thể đã hết hạn và tạm dừng trong căn chỉnh chủ động khi chưa thấy người chính.

Kiểm thử Tk ban đầu bị native abort khi bộ thu gom Python dọn các root Tk đã hủy trên thread AI trong một suite có nhiều cửa sổ nối tiếp. Đã hủy ảnh trên luồng Tk, thu gom root test trên luồng chính và cô lập test Tk + AI trong subprocess để kiểm tra cả mã thoát/native crash. Suite sau chỉnh sửa đã chạy thành công; không che lỗi bằng skip.

## Đối chiếu ca bắt buộc

| Ca | Bằng chứng và giới hạn |
|---|---|
| Mở không cần server | Camera thật + launcher + Tk pipeline đạt |
| Chưa hồ sơ không gán tên ngẫu nhiên | Test gallery trống/từ chối + camera chạy với kho riêng trống |
| Đăng ký tiếng Việt và nhận lại | Luồng runtime thật từ video qua Bắt đầu/thu mẫu/Lưu/nhận lại + DPAPI; thao tác widget cũng được kiểm thử |
| Giữ hồ sơ sau đóng/mở | Kho DPAPI mở lại thật, giữ tên/ID/main/model |
| Nhiều người đã đăng ký | Hai embedding thật và tracker; chưa thử hai người sống đứng cạnh trước webcam |
| Người lạ hiện Người | Gallery từ chối embedding ảnh khác người; chưa đo FAR/FRR |
| Đổi vị trí không chuyển nhãn/main | Tracker với hai embedding thật và tọa độ đổi chỗ; chưa thử đi lại/che nhau trực tiếp |
| Vật thể + phone ổn định | Người được detector thật phát hiện; phone/các ngưỡng thời gian kiểm bằng quan sát giả; chưa thử đủ sáu vật thật |
| Ngoảnh mặt/neutral/một lượt dài | Đồng hồ giả, pose phép chiếu tổng hợp và MediaPipe thật; chưa đo người quay đầu thực tế |
| Camera lỗi/ảnh cũ không cộng thời gian | Test capture/video EOF, freshness, UNKNOWN/gap với đồng hồ giả |
| Đăng ký/căn chỉnh dừng cảnh báo | Test engine, runtime và lệnh widget; không có hồ sơ tự tạo trước Lưu |
| Thiếu/lỗi mô hình | Thư mục trọng số trống/corrupt và adapter thật trả UNAVAILABLE/None |

## Phần còn thử nghiệm/chưa xác minh

Hướng mắt là heuristic iris riêng, không dùng yaw/pitch giả làm gaze. Khi không đủ điều kiện trả Chưa xác định; kính che mắt vẫn có thể đánh lừa landmark. Ngưỡng 1:N 0,48 và margin 0,08 là cấu hình thử nghiệm; chưa được nghiệm thu theo quy mô gallery. Chưa có chống ảnh in/video/điện thoại hay xác minh người thật. Chưa thử trực tiếp điện thoại, đủ sáu lớp vật thể, hai người đổi chỗ, hướng trái/phải của người đang quay đầu, và đăng ký qua thao tác tay trước camera. Không công bố accuracy/FPS hoặc ngoại suy hiệu năng sang i7-10510U/MX130.

Công cụ desktop xác nhận cửa sổ tồn tại nhưng hai lần chụp cửa sổ lỗi `FrameArrived timed out` / `window capture timed out`. Vì vậy bố cục chưa được nghiệm thu qua ảnh chụp; nội dung Unicode/widget/resize đã được kiểm bằng Tk native, không chỉ kiểm tra mã.

## Lệnh tái lập

```powershell
.\.venv-camera\Scripts\python.exe -m pip install -r requirements-camera-dev.txt
.\.venv-camera\Scripts\python.exe scripts\prepare_smoke_assets.py
.\.venv-camera\Scripts\python.exe -m pytest -c pytest-camera.ini -q --basetemp runtime/camera-tests-new-run -p no:cacheprovider
.\.venv-camera\Scripts\python.exe -m ruff check camera_app run_camera.py scripts\check_camera_models.py tests_camera
```

Dùng thư mục `--basetemp` mới mỗi lượt; pytest dọn thư mục test được chỉ định. Dữ liệu thử nằm trong `runtime/` hoặc `test_data/` đã bị loại khỏi Git; không dùng kho `.camera-data` của người vận hành làm fixture. API và giấy phép: `docs/CAMERA_MODELS.md`.
