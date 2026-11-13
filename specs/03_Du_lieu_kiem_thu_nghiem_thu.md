# Kế hoạch dữ liệu kiểm thử và nghiệm thu AI giám sát thi

Phiên bản 1.0 | Ngày 13 tháng 11 năm 2026 | Liên quan tài liệu 01 và 02

Tài liệu xác định dữ liệu cần thu, cách đánh giá mô hình và ca kiểm thử hệ thống. Các giá trị nghiệm thu là mục tiêu đề xuất, chưa phải số liệu thực nghiệm. Bộ kiểm thử phải chứng minh cả khả năng phát hiện dấu hiệu và khả năng tránh báo nhầm khi thí sinh sinh hoạt bình thường.

## 1 Phân cấp kiểm thử

Kiểm thử mô hình đo chất lượng nhận diện trên ảnh hoặc video đã gán nhãn. Kiểm thử bộ tổng hợp dùng chuỗi quan sát xác định để đo thời gian và loại trùng. Kiểm thử API kiểm tra trạng thái, quyền, dữ liệu và khôi phục. Kiểm thử toàn luồng chạy webcam hoặc video phát lại qua đường xử lý thật, có ghi phiên bản và cấu hình.

Phải báo cáo bốn lớp này riêng. Bộ quy tắc vượt kiểm thử không có nghĩa mô hình nhận diện đã đạt; mô hình tốt trên ảnh tĩnh không có nghĩa cảnh báo toàn phiên ít báo nhầm.

## 2 Kế hoạch thu dữ liệu

Đợt khảo sát đầu đề xuất 12 người tình nguyện, mỗi người ba phiên ở điều kiện khác nhau. Đây là quy mô cho prototype, không đủ để khẳng định khả năng đại diện cho toàn bộ sinh viên. Người tham gia được biết mục đích, thời hạn lưu, người truy cập và cách yêu cầu dừng hoặc xóa dữ liệu.

| Nhóm tình huống | Nội dung cần quay | Nhãn kỳ vọng |
|---|---|---|
| Bình thường | Làm bài, chớp mắt, chỉnh tóc, đeo kính, suy nghĩ, uống nước | Không có điện thoại hoặc người thứ hai |
| Không thấy mặt | Cúi, quay khỏi camera, ra ngoài khung | Ghi đúng điều quan sát được |
| Nhiều người | Người vào đứng cạnh, đi ngang, xuất hiện một phần | Hộp người và khoảng xuất hiện |
| Điện thoại | Cầm dọc ngang, sát xa, bị tay che một phần | Hộp điện thoại và mức nhìn thấy |
| Hướng đầu | Quay riêng từng lần, quay liên tục, cúi lâu | Góc hoặc vùng tương đối và khoảng thời gian |
| Hướng nhìn | Nhìn các điểm đã chỉ định sau hiệu chỉnh | Nhãn đích nhìn dự kiến và độ tin cậy |
| Giả mạo | Ảnh in, ảnh mặt trên điện thoại, video phát lại | Loại tấn công mô phỏng, không trộn với người thật |
| Kỹ thuật | Mất mạng, tắt camera, ảnh tối, đổi camera | Khoảng không quan sát được |

Đợt đầu mỗi người cần ít nhất 20 phút hành vi bình thường và các đoạn mô phỏng riêng. Mục tiêu 30 lần xuất hiện cho từng loại sự kiện trên tập kiểm thử; nếu không đủ thì ghi rõ số mẫu và không kết luận đạt chỉ bằng tỷ lệ cao từ vài ví dụ.

Ghi thiết bị, độ phân giải, điều kiện ánh sáng, khoảng cách camera, kính, góc đặt và thời lượng. Không thu thuộc tính cá nhân không phục vụ đánh giá. Không dùng ảnh giấy tờ có thông tin thừa trong video mẫu.

## 3 Chia tập và gán nhãn

Chia theo người thành 6 người phát triển, 3 người điều chỉnh và 3 người kiểm thử cố định. Một người không xuất hiện trong nhiều tập, kể cả ở video hoặc ảnh cắt khác nhau. Dữ liệu tăng cường chỉ được tạo từ tập phát triển. Ngưỡng được điều chỉnh trên tập điều chỉnh; mở tập kiểm thử sau khi đã khóa cấu hình.

Với xác thực, mỗi người trong tập kiểm thử có ảnh chuẩn được thu riêng trước phiên kiểm thử. Đó là hồ sơ đăng ký cho phép, không phải dữ liệu dùng để huấn luyện mô hình. Tạo cặp chính chủ và khác người; các cặp dùng chung người không độc lập thống kê, phải ghi rõ giới hạn.

Mỗi nhãn video gồm clip_id, participant_id giả danh, start_ms, end_ms, event_type, visibility, quality và annotator_id. Hộp vật thể lưu frame_id, class, x_min, y_min, x_max, y_max theo tọa độ ảnh gốc. Không đủ khả năng xác định thì dùng UNKNOWN hoặc OCCLUDED; không ép nhãn âm.

Hai người gán nhãn độc lập trên tối thiểu 20 phần trăm dữ liệu. So sánh sai khác về loại và thời điểm; thảo luận bất đồng rồi lưu nhãn cuối cùng cùng lịch sử. Việc giám thị nhận xét một hành vi đáng ngờ không được dùng thay cho nhãn quan sát khách quan.

Hướng nhìn có nhãn điểm được yêu cầu nhìn nhưng không có thiết bị đo mắt chuẩn, do đó chỉ dùng làm chỉ báo đánh giá vùng nhìn tương đối. Báo cáo phải nêu hạn chế này; không công bố độ chính xác vị trí mắt nhìn theo pixel.

## 4 Chỉ số đánh giá

Precision sự kiện = TP chia cho TP cộng FP. Recall sự kiện = TP chia cho TP cộng FN. F1 là trung bình điều hòa của Precision và Recall. Một dự đoán được ghép một đối một với nhãn cùng loại khi temporal IoU đạt ít nhất 0,3. IoU là thời lượng giao chia thời lượng hợp; dự đoán thừa cho cùng một nhãn là FP. Công bố cả quy tắc ghép để người khác tái lập.

Số cảnh báo nhầm mỗi giờ = số sự kiện FP chia tổng số giờ bình thường được quan sát hợp lệ. Báo cáo thêm số giờ không quan sát được và tỷ lệ UNKNOWN để tránh mô hình giảm báo nhầm bằng cách từ chối hầu hết dữ liệu.

Độ trễ phát hiện = confirmed_at trừ thời điểm bắt đầu nhãn. Độ trễ hệ thống = thời điểm cảnh báo tới client trừ thời điểm đã đủ điều kiện quy tắc. Tính p50 và p95; không gộp thời gian cố ý chờ 3 hoặc 5 giây vào độ trễ xử lý thuần.

Xác thực dùng FAR, FRR và tỷ lệ INCONCLUSIVE. FAR tính trên các lần thử khác người đủ điều kiện; FRR tính trên các lần thử chính chủ đủ điều kiện. Đồng thời báo số lần bị loại vì chất lượng trên toàn bộ lần thử. Không loại âm thầm những tình huống khó khỏi mẫu số tổng.

Phát hiện vật thể có thể báo Precision Recall và mAP theo lớp người, điện thoại. Chỉ số cấp ảnh phục vụ chẩn đoán; quyết định nghiệm thu luồng cảnh báo dựa cả trên video và sự kiện.

## 5 Tiêu chí đề xuất cho prototype

| Hạng mục | Mục tiêu thử nghiệm | Ghi chú |
|---|---|---|
| Nhiều người và không thấy mặt | Precision và Recall sự kiện ít nhất 0,90 | Chỉ trên tập kiểm thử đã mô tả |
| Điện thoại | Precision ít nhất 0,90 Recall ít nhất 0,80 | Tách nhóm nhỏ xa và bị che |
| Hướng đầu | Precision ít nhất 0,85 Recall ít nhất 0,80 | Dùng mốc cá nhân và nhãn thời gian |
| Hướng nhìn | Báo riêng, chưa làm điều kiện chặn kỳ thi | Chưa đủ dữ liệu thì giữ thử nghiệm |
| Báo nhầm | Không quá 2 sự kiện mỗi giờ bình thường | Báo cả theo loại và tổng hệ thống |
| Xác thực | FAR không quá 1 phần trăm FRR không quá 5 phần trăm | Cần đủ cặp thử; không tuyên bố bảo đảm an ninh |
| Dữ liệu không đánh giá được | Không quá 10 phần trăm trong điều kiện chuẩn | Báo riêng điều kiện ánh sáng xấu |
| Độ trễ hệ thống | p95 không quá 2 giây sau đủ điều kiện | Một phiên trên cấu hình máy thử |
| Ổn định | Chạy 60 phút không crash không tồn hàng đợi tăng dần | Ghi RAM và CPU theo phút |

Đối với FAR thấp, vài trăm cặp có tương quan không đủ chứng minh khả năng tổng quát. Báo số thử, số lỗi và khoảng tin cậy phù hợp; nếu bằng chứng chưa đủ thì ghi chưa đủ cơ sở nghiệm thu. Không giảm mục tiêu sau khi nhìn kết quả kiểm thử mà không tạo phiên bản tiêu chí mới và kiểm thử lại.

## 6 Ca kiểm thử chức năng

Mọi ca dưới đây cần lưu đầu vào, cấu hình, đầu ra thực tế và kết luận. Thời gian trong các ca dùng cấu hình của tài liệu 02. Chuỗi tổng hợp dùng đồng hồ giả lập để kiểm tra đúng biên, không dùng chờ thực tế trong kiểm thử đơn vị.

| Mã | Yêu cầu liên quan | Đầu vào và thao tác | Kết quả mong đợi |
|---|---|---|---|
| TC01 | REQ-01 FR-17 | Tạo lại cùng attempt_id và khóa | Chỉ một phiên; phản hồi cùng id |
| TC02 | REQ-02 FR-01 | Không có hồ sơ mặt chuẩn | Chờ xử lý; không tự đăng ký mặt hiện tại |
| TC03 | REQ-03 FR-02 | Ảnh tối hoặc nhiều mặt trước thi | Hướng dẫn đúng nguyên nhân; không MATCH |
| TC04 | FR-01 | Cặp chính chủ và khác người rõ | Trả điểm và phiên bản; kết quả theo ngưỡng |
| TC05 | FR-02 | Ba lần không khớp | Chờ giám thị; không tự start |
| TC06 | FR-03 | Phát lại mã thử thách đã dùng | Bị từ chối; không ghi sống đạt |
| TC07 | FR-04 | Hiệu chỉnh thiếu vùng mắt hoặc đổi camera | Thất bại hoặc yêu cầu làm lại |
| TC08 | FR-06 FR-13 | Không mặt trong 2,9 giây | Chưa có FACE_NOT_VISIBLE |
| TC09 | FR-06 FR-13 | Không mặt ít nhất 3 giây và đủ mẫu | Một sự kiện với start_at đúng ảnh đầu |
| TC10 | FR-12 | Mất ảnh trong 5 giây | Sự cố kỹ thuật; không sự kiện không mặt |
| TC11 | FR-05 | Hai người ổn định ít nhất 2 giây | Một MULTIPLE_PEOPLE có hộp và ảnh |
| TC12 | FR-05 | Điện thoại có ảnh khuôn mặt | Ghi kết quả mặt và người riêng; kiểm tra báo nhầm |
| TC13 | FR-07 | Điện thoại một ảnh rồi biến mất | Chưa tạo PHONE_VISIBLE |
| TC14 | FR-07 | Điện thoại ít nhất 1 giây đủ ba mẫu | Một PHONE_VISIBLE và minh chứng |
| TC15 | FR-08 | Quay đầu dưới 4 giây rồi về giữa | Không HEAD_AWAY |
| TC16 | FR-08 | Lệch mốc đủ 4 giây rồi mất landmark | Mở đúng thời điểm; UNKNOWN không kéo dài góc cũ |
| TC17 | FR-09 | Một lượt quay dài 20 giây | Một lượt; không đếm thành bốn lượt |
| TC18 | FR-09 | Bốn lượt độc lập trong 60 giây | Một HEAD_TURN_FREQUENT |
| TC19 | FR-10 | Kính lóa làm mất điểm mắt | Gaze UNKNOWN; không cảnh báo nhìn lệch |
| TC20 | FR-11 | NO_MATCH INCONCLUSIVE NO_MATCH trong 180 giây | Chỉ hai lần đủ chuẩn tăng đếm; tạo cần xem danh tính |
| TC21 | FR-11 | NO_MATCH rồi MATCH rồi NO_MATCH | Chưa mở sự kiện danh tính |
| TC22 | FR-13 | Dấu hiệu kéo dài 30 giây | Một event_id được cập nhật |
| TC23 | FR-13 | Khoảng thiếu dữ liệu vượt 1,5 giây | Ngắt chuỗi ứng viên; không cộng dồn hai phía |
| TC24 | FR-15 | Kho minh chứng không ghi được | Giữ sự kiện; MISSING và lý do |
| TC25 | FR-16 | Hai giám thị cập nhật cùng version | Một thành công một 409; audit không trùng |
| TC26 | FR-17 | End lặp lại rồi gửi ảnh mới | End idempotent; từ chối ảnh; đóng event mở |

## 7 Kiểm thử API an toàn và khả năng phục hồi

| Mã | Phạm vi | Thao tác | Kết quả mong đợi |
|---|---|---|---|
| IT01 | REQ-04 NFR-03 | Gửi seq lặp cùng hash rồi khác hash | Lần đầu không xử lý trùng; lần sau 409 |
| IT02 | REQ-04 | Ảnh đến sai thứ tự hoặc phiên đã kết thúc | Không tác động bộ đếm; lỗi có mã |
| IT03 | NFR-01 | Thí sinh A đọc phiên hoặc minh chứng B | Không truy cập được và không lộ dữ liệu |
| IT04 | NFR-01 | Giám thị ngoài phân công đăng ký WebSocket | Bị từ chối cả kênh và API |
| IT05 | NFR-02 | Token hết hạn hoặc đã thu hồi | Không nhận ảnh; hướng dẫn xin token mới |
| IT06 | API giới hạn | Ảnh quá 1 MiB hoặc vượt số pixel | 413 trước suy luận |
| IT07 | API giải mã | File giả JPEG hoặc ảnh hỏng | 415 hoặc 422 đúng nguyên nhân |
| IT08 | NFR-06 | Dừng mô hình điện thoại | Module UNAVAILABLE; không tạo âm giả |
| IT09 | NFR-08 | Dừng tiến trình khi event ACTIVE | Khôi phục dữ liệu đã ghi; ghi khoảng gián đoạn |
| IT10 | Outbox | Ngắt WebSocket sau khi ghi event | Event còn trong DB; phát lại hoặc đọc API |
| IT11 | Queue | Gửi nhanh hơn suy luận trong 10 phút | Chỉ ảnh chờ mới nhất; có dropped_frames |
| IT12 | Cấu hình | Đổi cấu hình toàn hệ thống giữa phiên | Phiên cũ giữ bản đã chụp; phiên mới dùng bản mới |
| IT13 | Minh chứng | URL ký hết 60 giây | Không đọc tiếp bằng URL cũ |
| IT14 | Xóa dữ liệu | Chạy tác vụ quá hạn | Byte và thumbnail bị xóa; metadata đánh dấu DELETED |
| IT15 | Audit | Tìm ảnh vector token trong log thử nghiệm | Không có dữ liệu nhạy cảm trong log |
| IT16 | Retry | Lặp thao tác review cùng khóa | Không tạo nhiều ReviewAction |
| IT17 | Readiness | Thiếu trọng số bắt buộc hoặc sai checksum | Không sẵn sàng; mã lỗi rõ |
| IT18 | Thời gian | Client gửi offset nhảy tương lai | Loại ảnh; không làm hành vi đủ thời lượng |

## 8 Kịch bản toàn luồng

E2E01. Thí sinh đăng nhập, kiểm tra camera, xác thực, hiệu chỉnh và bắt đầu thi. Thực hiện lần lượt một đoạn bình thường, giơ điện thoại, có người thứ hai và quay đầu. Giám thị nhận các sự kiện tương ứng, xem ảnh, ghi kết quả. Kết thúc bài đóng phiên và không nhận ảnh nữa.

E2E02. Mất mạng giữa lúc có ứng viên không thấy mặt chưa đủ thời gian. Sau khi kết nối lại, ứng viên cũ được bỏ; gián đoạn hiện trên dòng thời gian. Không có cảnh báo vắng mặt bao trùm khoảng mất mạng. Nếu camera đổi vị trí thì hiệu chỉnh lại.

E2E03. Worker dừng sau khi sự kiện đã được ghi nhưng trước khi gửi WebSocket. Sau phục hồi, giám thị vẫn đọc được sự kiện qua API. Nếu thông báo phát hai lần thì giao diện chỉ hiển thị một sự kiện.

E2E04. Thí sinh chính chủ bị từ chối vì ảnh mờ rồi thử ảnh rõ. Hệ thống phân biệt không đủ chất lượng với không khớp. Giám thị thấy lịch sử và lý do, không thấy điểm giả do mô hình chưa chạy.

## 9 Quy trình benchmark trên máy thử

Máy mục tiêu ban đầu là HP 15S DU1040TX với i7 10510U RAM 16 GB và MX130 2 GB theo cấu hình đã trao đổi. Ghi phiên bản hệ điều hành, Python, thư viện, model hash, chế độ CPU hoặc GPU, nguồn điện và độ phân giải webcam. Không mặc định GPU tăng tốc được mọi mô-đun.

Khởi động nóng mô hình rồi chạy một phiên ít nhất 60 phút với cả đoạn bình thường và dấu hiệu mô phỏng. Ghi p50 p95 suy luận từng mô-đun, p95 cảnh báo, CPU RAM VRAM, nhiệt và số ảnh bỏ. Chạy lại với cùng cấu hình để kiểm tra ổn định. Chỉ tăng tải khi đã xác định nút thắt; báo số phiên đạt cùng ngân sách độ trễ, không ngoại suy tuyến tính từ một phiên.

## 10 Truy vết yêu cầu và bàn giao

| Nhóm yêu cầu | Bằng chứng bắt buộc |
|---|---|
| REQ-01 đến REQ-04 | TC01 TC02 TC03 IT01 IT02 IT18 |
| FR-01 đến FR-04 | TC02 đến TC07 và báo cáo xác thực |
| FR-05 đến FR-10 | TC08 đến TC19 và chỉ số sự kiện từng loại |
| FR-11 đến FR-12 | TC10 TC20 TC21 IT08 và E2E02 |
| FR-13 đến FR-17 | TC22 đến TC26 IT09 IT10 IT16 |
| NFR-01 đến NFR-04 | IT01 IT03 IT04 IT05 IT12 IT13 IT15 |
| NFR-05 đến NFR-08 | Benchmark IT08 IT09 IT11 IT17 E2E03 |

Hồ sơ bàn giao gồm mã nguồn và lệnh chạy, danh mục trọng số và giấy phép, file cấu hình phiên bản, hợp đồng API được triển khai, dữ liệu kiểm thử được phép chia sẻ, kết quả từng ca, báo cáo chất lượng mô hình, benchmark và danh sách lỗi còn lại. Không đóng gói dữ liệu khuôn mặt thật vào mã nguồn hoặc bộ cài công khai.

Mẫu biên bản mỗi ca: test_id, requirement_ids, build_id, model_versions, config_hash, input_ref, expected, actual, status, evidence_ref, tester và executed_at. Trạng thái chỉ dùng PASS, FAIL hoặc BLOCKED; ca chưa chạy không được ghi PASS.

Hoàn thành prototype khi các ca bắt buộc đạt, các chỉ số đạt trong phạm vi dữ liệu công bố và giới hạn còn lại được ghi rõ. Chưa đủ dữ liệu đánh giá hướng nhìn hoặc chống giả mạo thì hai phần đó tiếp tục mang trạng thái thử nghiệm, dù các chức năng khác đã hoạt động.
