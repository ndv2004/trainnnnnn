# Đặc tả yêu cầu AI hỗ trợ giám sát thi trực tuyến

Phiên bản 1.0 | Ngày 13 tháng 11 năm 2026 | Chủ dự án Nguyễn Duy Việt

Tài liệu này quy định hành vi cần có của dịch vụ AI giám sát một thí sinh qua một webcam. Người phát triển dùng các mã yêu cầu để triển khai; người kiểm thử dùng chúng để đối chiếu kết quả. Đây là đặc tả thiết kế cho sản phẩm dự kiến, không phải báo cáo về chức năng đã triển khai hoặc chất lượng đã đạt được.

## 1 Phạm vi và nguyên tắc

Hệ thống hỗ trợ xác thực khuôn mặt, phát hiện người và điện thoại, theo dõi khuôn mặt, hướng đầu và hướng nhìn tương đối. Mỗi dấu hiệu được tổng hợp theo thời gian thành sự kiện có minh chứng. Giám thị xem xét sự kiện và quyết định xử lý theo quy trình kỳ thi. AI không tự trừ điểm, đình chỉ hoặc kết luận gian lận.

Phạm vi đầu tiên là một webcam trên máy thử nghiệm. Khi tích hợp, dịch vụ nhận ảnh từ hệ thống thi và trả về sự kiện. Quản lý ngân hàng câu hỏi, sinh đề, chấm bài và phân tích học tập thuộc hệ thống thi, không nằm trong bộ đặc tả này. Âm thanh, giám sát màn hình và xác định chính xác điểm mắt nhìn trên màn hình chưa thuộc phạm vi.

Ảnh điện thoại đang phát khuôn mặt, người đi ngang và hình in khuôn mặt đều có thể gây nhầm lẫn. Phát hiện khuôn mặt không đồng nghĩa phát hiện người thật; xác thực danh tính không đồng nghĩa đã chống được mọi hình thức giả mạo.

## 2 Thuật ngữ và vai trò

| Thuật ngữ | Ý nghĩa |
|---|---|
| Phiên giám sát | Một lượt theo dõi gắn với một lượt làm bài và một thí sinh |
| Quan sát | Kết quả xử lý một ảnh hợp lệ, có thời điểm và chất lượng |
| Sự kiện | Một dấu hiệu đã thỏa quy tắc tồn tại hoặc lặp lại theo thời gian |
| Minh chứng | Ảnh và dữ liệu liên quan tới sự kiện, có kiểm soát truy cập |
| Độ tin cậy mô hình | Điểm mô hình trả về; không phải xác suất gian lận |
| Không đủ dữ liệu | Không thể đánh giá tin cậy vì ảnh, kết nối hoặc mô hình không đáp ứng |

Thí sinh kiểm tra camera, xác thực và nhận hướng dẫn. Giám thị xem các phiên được phân công, đọc minh chứng và ghi nhận xử lý. Quản trị viên quản lý cấu hình và phiên bản mô hình. Hệ thống thi cấp quyền, liên kết lượt làm bài và tiếp nhận kết quả. Dịch vụ AI không tin các mã thí sinh hoặc quyền hạn chỉ do trình duyệt tự khai báo.

## 3 Điều kiện đầu vào

REQ-01. Mỗi phiên phải liên kết duy nhất với một lượt làm bài hợp lệ. Tài khoản và phạm vi truy cập do hệ thống thi xác nhận. Tạo lại cùng yêu cầu không được sinh phiên trùng.

REQ-02. Ảnh đăng ký xác thực phải có nguồn gốc đã được quản lý kỳ thi xác minh. Không cho phép thí sinh tự thay ảnh chuẩn trong lúc xác thực. Thiếu ảnh chuẩn thì trả trạng thái cần xử lý thủ công, không tự lấy ảnh webcam hiện tại làm danh tính gốc.

REQ-03. Trước khi chạy mô hình, kiểm tra quyền camera, độ sáng, độ rõ, kích thước khuôn mặt và số khuôn mặt. Trả riêng từng nguyên nhân không đạt. Cấu hình kiểm tra chất lượng được lưu phiên bản; số đo và ngưỡng cụ thể phải được hiệu chỉnh qua dữ liệu thử nghiệm.

REQ-04. Ảnh nhận vào phải giải mã được, nằm trong giới hạn kích thước, có số thứ tự và thời gian chụp. Ảnh lặp, cũ, sai phiên hoặc đến sau khi phiên kết thúc không được làm tăng thời gian hành vi.

## 4 Yêu cầu chức năng

### 4 1 Xác thực và hiệu chỉnh

FR-01. Khi chỉ có một khuôn mặt đủ chất lượng, hệ thống tạo vector đặc trưng và so sánh với hồ sơ chuẩn. Kết quả gồm MATCH, NO_MATCH hoặc INCONCLUSIVE; kèm điểm tương đồng, ngưỡng áp dụng và phiên bản mô hình. Không dùng một ngưỡng cố định cho mọi mô hình.

FR-02. Cho phép tối đa ba lần xác thực tự động mỗi đợt thử nghiệm. Sau đó chuyển sang chờ giám thị. Giám thị có thể cho phép thử tiếp hoặc chấp nhận thủ công với lý do; mọi quyết định phải được ghi audit. Giới hạn ba lần là cấu hình đề xuất.

FR-03. Kiểm tra tính sống là mô-đun riêng. Nếu bật thử thách quay đầu, thử thách phải có thứ tự ngẫu nhiên, thời hạn và mã dùng một lần. Chưa kiểm chứng chống phát lại thì giao diện ghi đúng mức bảo đảm, không hiển thị xác nhận chống giả mạo tuyệt đối.

FR-04. Hiệu chỉnh hướng đầu và hướng nhìn sử dụng chuỗi ảnh đủ rõ khi thí sinh nhìn bình thường vào màn hình. Hệ thống lưu mốc, chất lượng và phiên bản hiệu chỉnh. Camera đổi vị trí đáng kể thì yêu cầu hiệu chỉnh lại; thời gian hiệu chỉnh không tính là hành vi đáng ngờ.

### 4 2 Giám sát liên tục

FR-05. Dịch vụ phát hiện khuôn mặt và người theo hai loại kết quả riêng. Sự kiện MULTIPLE_PEOPLE dựa trên phát hiện người; nhiều khuôn mặt đơn thuần tạo MULTIPLE_FACES và yêu cầu xem xét. Không cộng số mặt với số người để suy ra số người thật.

FR-06. Khi camera vẫn có ảnh đủ chất lượng nhưng không phát hiện khuôn mặt liên tục, tạo FACE_NOT_VISIBLE. Tên hiển thị là không phát hiện khuôn mặt; không hiển thị khẳng định rời chỗ. Khi chỉ thiếu sáng hoặc mô hình lỗi, chuyển sang trạng thái không đủ dữ liệu.

FR-07. Phát hiện điện thoại phải trả hộp giới hạn và độ tin cậy. Chỉ tạo PHONE_VISIBLE sau khi vượt ngưỡng trong nhiều quan sát. Không suy ra điện thoại ngoài khung hình và không gán quyền sở hữu thiết bị từ ảnh.

FR-08. Hướng đầu được tính tương đối so với mốc cá nhân. Theo dõi riêng quay ngang và cúi ngẩng. Khi góc quá lớn làm mất điểm mốc, kết quả là UNKNOWN; không giữ góc cũ như kết quả hiện tại.

FR-09. HEAD_TURN_FREQUENT đếm các lượt quay độc lập có quay về vùng trung tính giữa hai lượt. Một lần quay kéo dài không được tính thành nhiều lần quay liên tục. Đếm trong cửa sổ trượt theo thời gian.

FR-10. GAZE_AWAY chỉ hoạt động sau khi hiệu chỉnh đạt và vùng mắt đủ chất lượng. Kết quả là vùng nhìn tương đối, không phải tọa độ chính xác trên màn hình. Không đủ điều kiện thì hiển thị không đánh giá được và không tạo cảnh báo hướng nhìn.

FR-11. Kiểm tra lại danh tính định kỳ chỉ chạy trên ảnh đủ chuẩn. Lịch thử nghiệm là mỗi 60 giây, có thể cấu hình. Hai kết quả không khớp đủ chất lượng liên tiếp mới tạo IDENTITY_REVIEW_REQUIRED; kết quả không xác định không tính như không khớp.

FR-12. Thiếu ảnh mới, lỗi mô hình, ảnh không đạt và mất quyền camera phải được ghi thành sự cố kỹ thuật riêng. Những khoảng này không làm tăng bộ đếm vắng mặt hoặc nhìn lệch. Khi trở lại, hệ thống khởi tạo lại các ứng viên sự kiện chưa xác nhận.

### 4 3 Sự kiện và xem xét

FR-13. Mỗi loại dấu hiệu được xử lý bởi trạng thái NORMAL, CANDIDATE, ACTIVE và COOLDOWN. Một sự kiện ACTIVE được cập nhật thời gian kết thúc và minh chứng, không tạo sự kiện mới cho mỗi ảnh.

FR-14. Sự kiện phải có loại, phiên, khoảng thời gian, trạng thái kết thúc, phiên bản cấu hình và mô hình. Có thể có nhiều loại đồng thời nhưng giao diện nhóm các sự kiện giao nhau để tránh hiểu là nhiều lần vi phạm độc lập.

FR-15. Minh chứng tối thiểu là một ảnh đại diện nếu chính sách cho phép. Nếu không lưu được ảnh, vẫn giữ sự kiện với evidence_status=MISSING và lý do. Không tạo đường dẫn minh chứng giả hoặc coi ghi ảnh thất bại là giám sát thành công đầy đủ.

FR-16. Giám thị đánh dấu UNREVIEWED, DISMISSED, NEEDS_FOLLOWUP hoặc CONFIRMED_OBSERVATION. Xác nhận quan sát chỉ xác nhận dấu hiệu đã thấy, không tự kết luận gian lận. Thay đổi trạng thái cần phiên bản bản ghi, người xử lý, thời điểm và lý do.

FR-17. Khi kết thúc lượt thi, ngừng nhận ảnh và đóng các sự kiện đang mở với lý do SESSION_ENDED. Xuất dòng thời gian gồm cả khoảng gián đoạn kỹ thuật. Sự kiện đã đóng vẫn có thể được giám thị xem xét trong thời hạn lưu.

## 5 Đặc tả ca sử dụng

### UC01 Kiểm tra và xác thực trước thi

Tác nhân chính là thí sinh. Điều kiện trước: đã đăng nhập, có lượt làm bài và hồ sơ chuẩn. Thí sinh cho phép camera; hệ thống kiểm tra chất lượng; thu ảnh xác thực; trả kết quả; khi khớp thì hiệu chỉnh; khi hiệu chỉnh đạt thì sẵn sàng giám sát. Việc bắt đầu làm bài vẫn theo quyền điều khiển của hệ thống thi.

Nhánh thay thế: từ chối camera thì hướng dẫn cấp quyền; ảnh nhiều mặt thì yêu cầu chỉ một người trước camera; ảnh chuẩn thiếu thì chuyển giám thị; hết lượt thử thì chờ xử lý thủ công. Không được bỏ qua bước xác thực bằng cách gọi trực tiếp API kích hoạt phiên.

### UC02 Theo dõi một phiên

Điều kiện trước: phiên ở MONITORING. Trình duyệt gửi ảnh theo lịch; dịch vụ loại ảnh không hợp lệ; chạy mô hình theo lịch từng mô-đun; cập nhật quan sát; tổng hợp sự kiện; ghi dữ liệu; phát thông báo. Kết quả cuối là chuỗi quan sát và sự kiện có thể truy vết.

Nhánh thay thế: mất mạng làm phiên DEGRADED; quá tải thì bỏ ảnh cũ theo chính sách và báo khoảng trống; mô hình điện thoại lỗi thì chỉ mô-đun đó UNAVAILABLE, không tuyên bố mọi phát hiện bình thường; kết thúc phiên thì từ chối ảnh mới.

### UC03 Xem xét cảnh báo

Giám thị mở danh sách phiên được phân công, lọc sự kiện chưa xem, đọc ảnh và thời gian, chọn kết quả rồi ghi lý do. Máy chủ kiểm tra quyền, lưu quyết định và audit trong cùng giao dịch. Hai người sửa đồng thời thì người sau nhận xung đột và phải tải bản mới.

### UC04 Khôi phục giám sát

Khi ảnh hợp lệ trở lại, hệ thống ghi thời điểm phục hồi, bỏ dữ liệu chờ đã cũ và kiểm tra chất lượng. Nếu camera thay đổi thì hiệu chỉnh lại. Hành vi trong thời gian mất dữ liệu được giữ là không quan sát được, không được suy diễn từ ảnh trước hoặc sau.

## 6 Yêu cầu phi chức năng

| Mã | Yêu cầu | Cách xác minh |
|---|---|---|
| NFR-01 | Không rò ảnh hoặc vector qua log, URL công khai hoặc API ngoài quyền | Kiểm tra log và kiểm thử truy cập chéo phiên |
| NFR-02 | Dùng HTTPS khi truyền qua mạng; mã hóa dữ liệu nhạy cảm khi lưu | Kiểm tra cấu hình triển khai và khóa |
| NFR-03 | Lặp yêu cầu không tạo bản ghi trùng | Gửi lại cùng khóa idempotency |
| NFR-04 | Mọi cảnh báo truy được mô hình và cấu hình | Kiểm tra sự kiện và nhật ký phiên bản |
| NFR-05 | Chạy thử một phiên trên máy i7 10510U RAM 16 GB MX130 2 GB | Benchmark có cấu hình và nhiệt độ tải dài |
| NFR-06 | API báo rõ UNAVAILABLE hoặc DEGRADED khi thiếu dữ liệu | Giả lập lỗi từng thành phần |
| NFR-07 | Cảnh báo p95 không quá 2 giây sau khi đủ điều kiện thời gian | Mục tiêu thử nghiệm một phiên, chưa phải kết quả đạt |
| NFR-08 | Chịu mất kết nối và khởi động lại không mất sự kiện đã xác nhận | Kiểm thử dừng tiến trình và phục hồi |

Không ấn định số phiên đồng thời trước khi đo. Độ trễ nhận diện, thời gian chờ đủ điều kiện hành vi và độ trễ phát cảnh báo phải được báo cáo riêng.

## 7 Chính sách dữ liệu và giới hạn

Trước khi thu thập, giao diện thông báo loại dữ liệu, mục đích và thời hạn lưu. Với thử nghiệm có người tham gia, ghi nhận sự đồng ý và khả năng dừng tham gia. Chính sách vận hành chính thức do đơn vị tổ chức phê duyệt; tài liệu này không thay thế chính sách đó.

Đề xuất thử nghiệm: không lưu toàn bộ video; lưu ảnh minh chứng tối thiểu, xóa sau 30 ngày nếu không có yêu cầu giữ để xử lý. Hồ sơ khuôn mặt có thời hạn riêng và chức năng xóa. Mốc 30 ngày là tham số thiết kế cần chốt, không phải yêu cầu pháp luật được viện dẫn.

Giới hạn còn cần kiểm chứng gồm điện thoại nhỏ hoặc bị che, khuôn mặt trên màn hình, kính phản sáng, thiếu sáng, khác biệt webcam và hướng nhìn. Mô-đun không đạt tiêu chí phải được vô hiệu hóa hoặc giữ nhãn thử nghiệm, không âm thầm bỏ chức năng rồi báo hoàn thành.

## 8 Các quyết định cần chốt trước vận hành

| Quyết định | Phương án khởi đầu | Điều kiện chốt |
|---|---|---|
| Mô hình xác thực | Chưa khóa tên và trọng số | Có giấy phép phù hợp và đánh giá FAR FRR |
| Ngưỡng cảnh báo | Dùng cấu hình thử nghiệm của tài liệu 02 | Đánh giá trên tập điều chỉnh riêng |
| Hướng nhìn | Chức năng thử nghiệm có thể tắt | Có kiểm chứng sai số và cảnh báo nhầm |
| Lưu trữ | Ảnh sự kiện tối thiểu | Đơn vị tổ chức phê duyệt thời hạn và quyền |
| Năng lực phục vụ | Một phiên thử nghiệm | Benchmark trước khi tăng tải |

Tài liệu 02 mô tả cách triển khai các yêu cầu. Tài liệu 03 quy định dữ liệu thử, chỉ số và ca nghiệm thu tương ứng.
