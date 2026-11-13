# Mô hình của bản camera độc lập

Kiểm tra nguồn chính thức ngày 13/11/2026. Bản camera dùng lại bốn trọng số hiện có, chạy CPU, không cần CUDA, PyTorch hay server. URL bất biến và SHA256 nằm trong `models/manifest.json`; ONNX khóa theo OpenCV Zoo commit `48b10fa505aeb51fbd127fc60a5c660040e823b7`.

| Chức năng | Trọng số và API | Giấy phép / nguồn chính thức |
|---|---|---|
| Phát hiện mặt | YuNet `face_detection_yunet_2023mar.onnx`; OpenCV `FaceDetectorYN.create`, DNN CPU | [MIT](https://github.com/opencv/opencv_zoo/blob/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/face_detection_yunet/LICENSE) |
| Embedding danh tính | SFace `face_recognition_sface_2021dec.onnx`; `FaceRecognizerSF.alignCrop` và `feature`, vector 128 chiều | [Apache-2.0](https://github.com/opencv/opencv_zoo/blob/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/face_recognition_sface/LICENSE) |
| Vật thể | NanoDet Plus m 1.5x 416 `object_detection_nanodet_2022nov.onnx`; OpenCV DNN CPU | [Apache-2.0](https://github.com/opencv/opencv_zoo/blob/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/object_detection_nanodet/LICENSE); [README mô hình](https://github.com/opencv/opencv_zoo/tree/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/object_detection_nanodet) |
| Mốc mặt / iris | Google `face_landmarker.task` float16 version 1; MediaPipe Tasks FaceLandmarker, 478 mốc | Apache-2.0 theo [model card Face Mesh V2](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20MediaPipe%20Face%20Mesh%20V2.pdf) |

Các bản sao giấy phép được giữ trong `third_party/`. Runtime trực tiếp khóa ở `requirements-camera.txt`: OpenCV contrib 4.11.0.86, MediaPipe 0.10.32, NumPy 2.3.5, Pillow 13.0.0 và cryptography 50.0.0; dependencies gián tiếp cần thiết cũng khóa phiên bản. Tkinter có sẵn với bộ cài Python chuẩn Windows khi bật tcl/tk. Không cài FastAPI hoặc các dependencies backend cũ vào môi trường camera.

Setup kiểm tra Python 3.12 x64 và thực sự tạo/đóng một cửa sổ Tk ẩn trước khi tạo môi trường. Nếu Tcl/Tk không dùng được, script dừng và in chẩn đoán để sửa thành phần tcl/tk bằng bộ cài Python; không gỡ Python hoặc sửa môi trường khác. Có thể chạy riêng bước này: `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_camera.ps1 -CheckOnly`.

OpenCV 4.11 mô tả chính thức cách dùng [YuNet và SFace](https://docs.opencv.org/4.11.0/d0/dd4/tutorial_dnn_face.html). Ngưỡng cosine 0,363 trong bảng LFW là tham khảo so cặp 1:1, **không phải ngưỡng 1:N đã nghiệm thu cho ứng dụng này**. Bản camera có ngưỡng từ chối và khoảng cách điểm hai hồ sơ trong cấu hình; các giá trị hiện tại là ngưỡng thử nghiệm, cần đánh giá lại theo quy mô danh sách, người và điều kiện camera thực tế. Landmark không được dùng thay embedding để xác định danh tính.

NanoDet được huấn luyện trên COCO 80 lớp. Bản camera ưu tiên các ID COCO: người 0, chai 39, cốc 41, laptop 63, điện thoại 67, sách 73. Chỉ hiển thị lớp do detector trả về và được cấu hình hỗ trợ. Không xác minh người thật từ khuôn mặt trong ảnh/màn hình và không tự gán vật thể cho người.

[MediaPipe Face Landmarker Python](https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker/python) cung cấp mốc mặt trong chế độ ảnh/video. Pose dùng các mốc và phép chiếu; hướng mắt là heuristic iris riêng sau căn chỉnh, mang nhãn ước lượng. Đây không phải mô hình gaze đã được đo độ chính xác; model card ghi mốc mặt không cung cấp nhận diện danh tính. Không có mô hình chống giả mạo/liveness và không khẳng định đã chống ảnh in, video hay điện thoại.

Kiểm tra / tải / gọi mô hình thật:

```powershell
.\.venv-camera\Scripts\python.exe scripts\check_camera_models.py --check
.\.venv-camera\Scripts\python.exe scripts\check_camera_models.py --download --smoke
```

`--check` liệt kê đầy đủ mô-đun thiếu hoặc sai checksum, trả mã lỗi. `--download` chỉ dùng downloader đã khóa; file sai không bị ghi đè. Nếu chủ động sửa trọng số sai, `--download --repair` lưu bản cũ thành file backup trước khi thay bằng byte đã xác minh. `--smoke` thực sự khởi tạo và gọi cả bốn mô hình CPU với ảnh trống, kiểm tra lỗi runtime; không phải kiểm thử độ chính xác, FPS, webcam hoặc nhận diện danh tính. Kiểm thử ảnh có mặt và luồng ứng dụng được ghi riêng trong báo cáo bàn giao.
