# Mô hình, runtime và nguồn

Các file trọng số được khóa bằng SHA256 trong `models/manifest.json`, kiểm khi tải và khi khởi động. OpenCV Zoo commit: `48b10fa505aeb51fbd127fc60a5c660040e823b7`. Không tải nhánh `main` động khi chạy chương trình.

| Mô-đun | Trọng số / runtime | Giấy phép / nguồn chính thức |
|---|---|---|
| Mặt | `face_detection_yunet_2023mar.onnx`, OpenCV FaceDetectorYN CPU | MIT; [OpenCV Zoo YuNet](https://github.com/opencv/opencv_zoo/tree/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/face_detection_yunet) |
| Danh tính | `face_recognition_sface_2021dec.onnx`, FaceRecognizerSF CPU | Apache-2.0, thư mục ghi áp dụng mọi file; [SFace](https://github.com/opencv/opencv_zoo/tree/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/face_recognition_sface) |
| Người/điện thoại | `object_detection_nanodet_2022nov.onnx`, NanoDet-Plus-m-1.5x 416, OpenCV DNN CPU, COCO class 0/67 | Apache-2.0, thư mục ghi áp dụng mọi file; [NanoDet Zoo](https://github.com/opencv/opencv_zoo/tree/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/object_detection_nanodet) |
| Landmark/hướng đầu | `face_landmarker.task`, Google float16/version 1, MediaPipe Tasks 0.10.32 CPU | [Face Landmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker); [model card Face Mesh V2](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20MediaPipe%20Face%20Mesh%20V2.pdf) ghi Apache-2.0 |
| Hướng nhìn | Không có trọng số gaze riêng; heuristic iris tương đối | Mã dự án, mặc định tắt, chưa có đánh giá vùng nhìn |
| Chống giả mạo | Không có trọng số | UNAVAILABLE, chưa triển khai/chứng minh |

Bản sao giấy phép và README nguồn nằm trong `third_party/`. Tài liệu kỹ thuật đã tham khảo: [OpenCV FaceDetectorYN/SFace](https://docs.opencv.org/4.x/d0/dd4/tutorial_dnn_face.html), [MediaPipe Python options](https://developers.google.com/edge/api/mediapipe/python/mp/tasks/vision/FaceLandmarkerOptions). Đường tải chính xác nằm trong manifest.

Ba mô hình ONNX dùng SHA256 do Git-LFS nguồn công bố. Google task có upstream CRC32C `2FSEdQ==`, đã kiểm trên byte tải; SHA256 được tính và khóa trong manifest. Không ghi nhầm SHA256 khóa cục bộ thành SHA256 được Google công bố. Downloader không chấp nhận nội dung khác checksum.

SFace alignCrop sử dụng năm điểm mốc YuNet rồi trích vector 128 chiều. MediaPipe không tham gia xác thực danh tính. Vector chuẩn được mã hóa Fernet; API không xuất vector. Hồ sơ phải cùng hash mô hình trước khi so sánh. Ngưỡng 0,363 của OpenCV là tham khảo trên LFW, không phải kết quả đo của dự án.

Không dùng số liệu benchmark do nhà phát hành công bố làm kết quả của máy i7-10510U hoặc dữ liệu thi. Chưa kiểm điện thoại nhỏ/bị che, kính phản sáng, mặt trên màn hình và nhiều webcam. Thu thập/giấy phép tập huấn luyện gốc không được dự án này đánh giá lại; trước vận hành cần đánh giá bộ dữ liệu theo tài liệu 03 và chính sách đơn vị.
