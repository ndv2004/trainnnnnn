# Quyết định triển khai 0.1.0

Nguồn: đọc đủ `01_Yeu_cau_AI.md`, `02_Thiet_ke_du_lieu_API.md`, `03_Du_lieu_kiem_thu_nghiem_thu.md` trong ZIP. Workspace ban đầu không có repository, AGENTS.md trên đĩa, mã nguồn hay prototype. Hướng dẫn AGENTS do người dùng cung cấp về dự án Train AI được áp dụng. Không có phần mã người dùng bị ghi đè.

Ba tài liệu cùng phiên bản 1.0, không thấy mâu thuẫn trực tiếp. Những chỗ còn mở được chốt như sau:

| Điểm còn mở | Quyết định và hệ quả |
|---|---|
| DB/kho ảnh chưa khóa công nghệ | Adapter SQLite WAL và kho file Fernet ngoài webroot, một worker. Có khóa chặn worker thứ hai. Không có object store từ xa. |
| Mô hình vật thể “loại nhỏ”, tài liệu dẫn Ultralytics | Chọn NanoDet-Plus-m-1.5x 416 bản OpenCV Zoo: ~3,8 MB, ONNX CPU, giấy phép thư mục bao gồm mô hình là Apache-2.0. Không bắt buộc framework của ví dụ tham chiếu. |
| Mô hình xác thực chưa chọn | YuNet + SFace OpenCV Zoo. SFace có giấy phép thư mục Apache-2.0. Cosine 0,363 là ngưỡng tham khảo LFW từ tài liệu OpenCV, đặt trong cấu hình thử nghiệm; chưa hiệu chỉnh cho kỳ thi hoặc chứng minh FAR/FRR. |
| API 02 không liệt kê quản lý hồ sơ/manual override/consent | Bổ sung endpoint quản trị hồ sơ, consent, manual-decision; quyền và lý do bắt buộc. Không cho thí sinh đăng ký ảnh chuẩn qua verify. |
| API start cần backend nhưng chưa có hệ thống thi | Endpoint start chính chỉ backend/admin. Route `/demo/.../start` gọi adapter cục bộ sau khi kiểm đủ điều kiện; UI ghi rõ demo. Tắt demo thì route này mất hiệu lực. |
| Vai trò giám thị và tài khoản | Bản localhost giả danh có A/B/ngoài phân công. Phiên lấy candidate/exam/phân công từ adapter phía máy chủ. Không giả định đã kết nối HAU hay hệ thống thi trước đó. |
| Đồng hồ client có thể bị khai giả | Thời lượng dựa trên monotonic phía máy chủ. seq và offset kiểm tra tăng dần, lệch tối đa cấu hình. Không dùng offset để tăng thời gian hành vi; không tuyên bố webcam trực tiếp hay chống phát lại. |
| Kết quả theo lịch thưa | `NOT_SCHEDULED` không cấp thêm mẫu/thời gian. Tick vẫn làm hết hạn chuỗi khi không có quan sát thực mới. UNKNOWN thật ngắt ứng viên; ảnh/kết quả hết hạn không duy trì hành vi. |
| Hysteresis hướng đầu | Ngoài ngưỡng mở là POSITIVE; trong vùng trung tính là NEGATIVE; dải giữa là UNKNOWN. Dải giữa không thể giả thành trở về trung tính để tăng lượt quay. |
| Sự kiện tần suất/danh tính | Tạo sự kiện điểm khi điều kiện đủ, đóng `RULE_CONFIRMED`, có latch chống lặp. Sáu quy tắc thời lượng dùng NORMAL/CANDIDATE/ACTIVE/COOLDOWN đầy đủ. |
| Giới hạn ba lần xác thực | Mỗi lần gọi verify hợp lệ về trạng thái/ảnh giải mã được dùng một lượt, kể cả INCONCLUSIVE. Hết ba lượt cần giám thị. Lỗi transport/ảnh hỏng không dùng lượt. |
| Hiệu chỉnh mắt chưa có mô hình gaze được nghiệm thu | Hiệu chỉnh lấy median yaw/pitch/iris từ ≥10 mẫu trong ≥2 giây, đầu ổn định, mắt thấy được. Gaze mặc định tắt; bật chỉ heuristic ngang thử nghiệm, không dùng để chặn kỳ thi. |
| Chống giả mạo chưa kiểm chứng | Không triển khai challenge thành công. Capability và challenge trả `UNAVAILABLE / LIVENESS_UNAVAILABLE`. TC06 vẫn BLOCKED. |
| Nhận ra camera đổi | So thiết bị client khai báo và kích thước server giải mã. Thay đổi bắt buộc hiệu chỉnh; có `camera_changed` khi resume. Dịch chuyển vật lý không đổi thiết bị chưa được phát hiện đầy đủ. |
| Mất camera/mạng và chất lượng thấp | Ghi TECHNICAL riêng: CAMERA_INTERRUPTED, WORKER_INTERRUPTED, PROCESSING_GAP, MODEL_UNAVAILABLE, QUALITY_INSUFFICIENT. Không suy ra FACE_NOT_VISIBLE từ thiếu ảnh. |
| Restart | Khôi phục DB, đóng event mở tại `last_observed_at`, bỏ ứng viên, ghi gián đoạn và yêu cầu resume với UUID luồng chưa dùng. Token generation cũ không thu ảnh sau resume. |
| URL minh chứng | Hạn 60 giây, còn phải có Bearer token đúng người đã xin URL và đúng phân công. URL đơn lẻ không đủ đọc ảnh. Không có đường dẫn ảnh tĩnh. |
| Lưu ảnh thất bại | Event/outbox còn được ghi, Evidence MISSING và reason, không bịa object_key. Lưu tối đa 3 ảnh/event. Không triển khai clip hay thumbnail. |
| Cấu hình đang hoạt động | API lưu phiên bản bất biến và con trỏ active bền vững. Phiên cũ giữ snapshot; restart giữ active đã chọn. File JSON là cấu hình bootstrap khi chưa có con trỏ. |
| API lọc/log | Lỗi trả code/message/fields, không echo dữ liệu nhập. Không access log URL có signature. Audit không chứa token, ảnh hay vector. |

Các thay đổi ngưỡng phải tạo mã cấu hình mới và đánh giá trên tập điều chỉnh riêng. Không xem số test PASS là chỉ số chất lượng mô hình. Chưa có bộ dữ liệu, nên mục tiêu nghiệm thu trong tài liệu 03 được giữ nguyên, không hạ để vừa kết quả smoke.
