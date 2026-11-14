# Vấn đề và cách giải quyết

`☐` Chưa hoàn tất · `☑` Đã triển khai và kiểm tra. Mốc 1–2 đã xong trong phạm vi nguồn/Moodle v2 và DWH rỗng; backend/dashboard cũ chưa chuyển nguồn. Mục chờ ETL/giao diện không tick dù đã làm một phần.

Kết quả kiểm kê và kế hoạch các mốc: [KeHoachV2.md](KeHoachV2.md). Không tick chức năng chỉ vì đã kiểm kê dữ liệu.

| # | Vấn đề | Cách giải quyết đã chốt | Triển khai |
|---|---|---|:---:|
| 1 | Dòng đầu `new/dlu_user.csv` lỗi CSV | Đã bỏ đúng bản ghi đầu khi nạp; giữ nguyên file gốc. Cột chưa rõ nghĩa chỉ giữ raw, không tự gán. | ☑ |
| 2 | `idnumber` gần như trống | Lấy MSSV là chuỗi 7 số trong username; không có thì tìm trong phần email trước `@`; ưu tiên username khi khác nhau. | ☐ |
| 3 | Nhận diện sinh viên | Có mã 7 số theo quy tắc trên thì cho xét đăng nhập; không có thì loại. Quan hệ dữ liệu vẫn nối bằng `user.id`. | ☐ |
| 4 | Tài khoản bị xóa/đình chỉ | Không cho đăng nhập khi `deleted = 1` hoặc `suspended = 1`; có thể giữ lịch sử học tập. | ☐ |
| 5 | Thiếu mật khẩu thật | Mật khẩu demo bằng username, lưu bcrypt trong Moodle DB; ETL chuyển hash sang DWH, giữ luồng xác thực. Chỉ dùng môi trường demo được kiểm soát. | ☐ |
| 6 | Cohort không phản ánh lớp | Bỏ phụ thuộc cohort trong ETL và bỏ lớp trên dashboard; không suy đoán lớp. | ☐ |
| 7 | Quiz/câu sai thiếu dữ liệu phù hợp | Bỏ toàn bộ quiz, lịch/điểm/lượt làm quiz và ôn tập câu sai, kể cả đầu điểm quiz trong chức năng thay thế. | ☐ |
| 8 | Chức năng thay quiz | Để “Điểm thành phần” sau cùng; kiểm tra dữ liệu rồi mới quyết định triển khai. | ☐ |
| 9 | Môn chưa có điểm bị mất | Thêm bảng ghi danh nhỏ trong DWH và bước ETL; backend lấy môn từ ghi danh, ghép điểm nếu có; giữ bảng điểm và endpoint hiện tại. | ☐ |
| 10 | Giới hạn 173 môn không đủ toàn trường | Dùng toàn bộ danh sách 9.058 khóa học; mỗi sinh viên chỉ thấy môn đã ghi danh. | ☐ |
| 11 | Thiếu đầu điểm ngoài phạm vi CNTT | Không xin thêm; phân biệt “Thiếu dữ liệu điểm” với “Chưa có điểm”; không suy ra điểm 0. | ☐ |
| 12 | Ghi danh trùng | Mỗi sinh viên–môn một dòng; còn quyền khi ít nhất một ghi danh hợp lệ và đáp ứng điều kiện truy cập khác. | ☐ |
| 13 | Ghi danh hết hạn/đình chỉ | Giữ lịch sử môn/điểm còn lưu; chỉ nhắc bài và deadline ở môn hiện còn quyền truy cập. | ☐ |
| 14 | Xác định năm học | Lấy từ danh mục môn, không từ ngày chấm; không xác định được chỉ hiện trong “Tất cả”. | ☐ |
| 15 | Bộ lọc năm học | Mặc định tất cả; danh sách môn và điểm trung bình cùng cập nhật theo năm chọn. | ☐ |
| 16 | Bài tập nhóm thiếu thành viên | Vẫn hiện bài/hạn; có điểm cá nhân hoặc trạng thái xác định được thì hiện, không có thì “Không có kết quả”; không suy ra kết quả cho người khác. | ☐ |
| 17 | Thống kê sai do bài nhóm | Không tính trường hợp chưa xác định vào chưa nộp, quá hạn hay tỷ lệ đúng hạn; thành viên tự trao đổi kết quả. | ☐ |
| 18 | Điểm NULL | Hiện “Chưa có điểm”; không tính trung bình/xếp loại/cảnh báo; điểm 0 thật vẫn tính. | ☐ |
| 19 | Điểm ẩn | Dùng để hiển thị và thống kê trong demo nội bộ; công bố cho sinh viên thật cần được cho phép. | ☐ |
| 20 | Điểm âm/thang điểm không hợp lệ | Không ép về 0 hoặc 10, không tự mặc định thang thiếu thành 100; hiện “Chưa có điểm”, loại khỏi tính toán và ghi log. | ☐ |
| 21 | Chuẩn hóa điểm | Giữ công thức ETL: điểm / điểm tối đa × 10; kiểm tra thang điểm, không tự chuyển thang phi số. | ☐ |
| 22 | Nhãn GPA không đúng bản chất | Đổi thành “Điểm trung bình”; trung bình cộng điểm tổng kết hợp lệ đã quy về 10, không tính GPA tín chỉ chính thức. | ☐ |
| 23 | Xếp điểm/đạt không thống nhất | A ≥ 8,5; B ≥ 7; C ≥ 5,5; D ≥ 4; F < 4. Quy đổi 4/3/2/1/0; bỏ cảnh báo < 5 và kết luận học lực không có căn cứ. | ☐ |
| 24 | Làm tròn nâng hạng | 8,45 giữ 8,45, xếp B; xếp theo điểm chưa làm tròn hiển thị; không làm tròn lên để đổi hạng. | ☐ |
| 25 | Dashboard nhiều chữ | Giữ khung hiện có; bỏ lớp/quiz, ưu tiên điểm số + A/B/C/D/F; không thêm chú thích dài hay nhãn “Dưới ngưỡng đạt”. | ☐ |
| 26 | Bài tập ẩn | Loại bài hoặc phần chứa bài đang ẩn khỏi danh sách và mọi thống kê. | ☐ |
| 27 | Điều kiện truy cập | Chỉ hiện/tính bài xác định được quyền truy cập, xét cả bài và phần chứa; không đạt hoặc không xác định được thì bỏ qua. | ☐ |
| 28 | Thiếu dữ liệu hoạt động ngoài CNTT | Không tự coi là có quyền; hiện “Thiếu dữ liệu bài tập” khi phù hợp, không kết luận “Không có bài tập”. | ☐ |
| 29 | Ngày tính deadline | Dùng ngày giờ thực tế, múi giờ Việt Nam; không dịch deadline lịch sử sang ngày mới. | ☐ |
| 30 | Gia hạn riêng | Dùng hạn chung; chấp nhận chưa xét gia hạn cá nhân/nhóm. | ☐ |
| 31 | Bài chưa mở nộp | Nếu được phép xem thì hiện “Chưa mở nộp”; chưa tính vào bài cần nộp/deadline khẩn. | ☐ |
| 32 | Bản nháp | Chỉ `submitted` là đã nộp; `draft` hiện “Bản nháp”, vẫn nhắc hạn khi đủ điều kiện. | ☐ |
| 33 | Nhiều lần nộp/chấm | Mỗi sinh viên–bài một dòng; dùng lần nộp mới nhất và điểm đúng lần đó, không gắn điểm cũ sang lần mới. | ☐ |
| 34 | Không có hạn nộp | Hiện “Không có hạn nộp”; không tính deadline khẩn, quá hạn hoặc tỷ lệ đúng hạn. | ☐ |
| 35 | KPI cốt lõi | Điểm trung bình; deadline trong 48 giờ; tỷ lệ đúng hạn = bài cá nhân đã nộp đúng hạn / bài cá nhân đã nộp có hạn hợp lệ. Không coi là tỷ lệ hoàn thành. | ☐ |
| 36 | KPI thiếu dữ liệu | Có phần tính được thì hiện kết quả phần đó, không gắn nhãn “Dữ liệu một phần”; không đủ để tính thì `--`; chỉ hiện 0 khi kết quả tính thật là 0. | ☐ |
| 37 | HTML/nội dung nguồn | Hiển thị tên môn/bài/user bằng văn bản an toàn, giữ tiếng Việt; chỉ xử lý HTML có kiểm soát khi cần, không sửa nguồn. | ☐ |
| 38 | Bảo vệ hệ thống cũ | Đã restore thử backup, tạo hai DB v2; không đổi cấu hình/dữ liệu nghiệp vụ cũ. Cron cũ vẫn tự ghi log. | ☑ |
| 39 | DWH v2 ban đầu | Đã tạo 8 bảng, kiểm tra 0 bản ghi; ETL chờ mốc 3. | ☑ |
| 40 | Dữ liệu thật và giả xung đột | Nguồn thật được giữ ở 17 bảng raw có hash/count; không copy nghiệp vụ giả, không ghép theo ID trùng/tên. | ☑ |
| 41 | Tận dụng Moodle gốc | Dùng cùng image/version để dựng nền sạch tương thích; không kéo dữ liệu nghiệp vụ hoặc script tùy chỉnh cũ sang v2. | ☑ |
| 42 | Khóa nối/bản ghi mâu thuẫn | Kiểm tra quan hệ; cách ly/loại có kiểm soát, không tự gán sang người/môn khác; ghi log mọi điều chỉnh, giữ nguyên CSV gốc. | ☐ |
| 43 | Moodle v2 cần cách ly | Đã có container/volume, cookie và DB user riêng; chỉ localhost:8081; native/HTTP login và quyền cách ly đã kiểm tra. | ☑ |
| 44 | CSV không phải backup đầy đủ | Đã kiểm tra và ghi giới hạn trong kế hoạch; rebuild context, không tuyên bố khôi phục activity/plugin/file/gradebook còn thiếu. | ☑ |
| 45 | Môn thiếu cấu trúc vận hành | Đã giữ 9.058 course nguồn; 9.057 shell học tập bị ẩn an toàn, không dựng activity giả; metadata gốc vẫn ở raw cho ETL. | ☑ |
| 46 | Thiếu tài liệu/file bài nộp | Điểm/trạng thái giữ ở raw; không copy file giả hoặc tạo liên kết tải không có nguồn thật. | ☑ |
| 47 | Tài khoản quản trị | Admin riêng đã đăng nhập được, mật khẩu mạnh, ID không trùng; đánh dấu tài khoản kỹ thuật ngoài raw. Có thêm guest kỹ thuật bắt buộc của Moodle. | ☑ |
| 48 | Email thật bị gửi ngoài ý muốn | Đã chặn email (`noemailever=true`) và tắt cron v2, không copy SMTP/token cũ; email thật giữ nguyên trong raw. | ☑ |
| 49 | Nghiệm thu dữ liệu và ứng dụng | Kiểm tra schema, độ bao phủ, khóa nối, quyền, phép tính và Moodle/dashboard v2; chỉ tick sau khi triển khai và xác minh. | ☐ |

## Thứ tự

Nguồn v2 an toàn → kiểm tra Moodle v2 → DWH/ETL → backend/dashboard cốt lõi → nghiệm thu → xét “Điểm thành phần” sau cùng.
