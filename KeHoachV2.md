# Kế hoạch v2 — kiểm kê và tiến độ

## Trạng thái

- Mốc 1: **đã kiểm kê và lập kế hoạch**.
- Mốc 2: **đã đạt kiểm tra nguồn raw, Moodle v2 cách ly và DWH v2 rỗng**; chi tiết/giới hạn ở mục 8.
- Đã khôi phục thử cả hai backup, nạp dữ liệu thật vào v2 và kiểm tra đăng nhập HTTP. Chưa chạy ETL hoặc chuyển backend/dashboard sang v2.
- `VanDe.md` chỉ tick phần đã triển khai và kiểm tra; chức năng dashboard vẫn chờ mốc 3–5.
- Không cần xin thêm dữ liệu. Chỗ không chứng minh được phải cách ly hoặc vô hiệu hóa, không lấy dữ liệu giả lấp vào.

## 1. Môi trường đã xác minh tại mốc 1

| Thành phần | Thực tế tại thời điểm kiểm tra |
|---|---|
| Moodle | `moodle_app`, cổng 8080; code và DB cùng version `2026042001.08`, release `5.2.1+ (Build: 20260728)` |
| Image Moodle | Pin image đang chạy, không pull lại tag `latest`: `elestio/moodle@sha256:898a2d2d08136a8dc65cb0b646fb514fd9c759742d84a2da1b773a7596ed8d2f` |
| MariaDB | `moodle_db`, MariaDB 11.4, database `moodledb`, prefix rỗng; 490 bảng |
| SQL Server | `datawarehouse_sqlserver`, version `16.0.4265.3`, database `lms_datawarehouse` ONLINE |
| V2 | Chưa thấy `moodledb_v2` trong schema khả dụng; chưa thấy `lms_datawarehouse_v2` trên SQL Server |
| Giờ | Moodle cấu hình `Asia/Ho_Chi_Minh`; SQL Server trả offset `+00:00`; phải chuẩn hóa hợp đồng thời gian trước khi tính deadline |
| Lưu trữ Moodle cũ | Volume `dacs_moodle_data` chứa cả cây ứng dụng tại `/var/www/html`; code hiện ở `public`; không gắn lại volume này cho v2 |
| ETL đang chạy | Chưa xác minh lịch/process thực thi. Có `etl_pipeline.py` và `etl_v3.py`; không tự chọn chỉ vì tên v3 mới hơn |

Số lượng nền để đối chiếu (không phải dữ liệu sẽ giữ lại):

| Bảng | Moodle cũ | DWH cũ |
|---|---:|---:|
| User / Dim_User | 2.172 | 2.171 |
| Course / Dim_Course | 871 | 870 |
| Assign / Dim_Assign | 3.039 | 3.039 |
| Assign submission / fact | 381.332 | 381.332 |

Không đụng các container khác, đặc biệt `standalone_sqlserver` không phải SQL Server của dự án này.

## 2. Dữ liệu thật đã kiểm đếm

25 file CSV, không thấy ID cột đầu bị trùng. Sau khi bỏ đúng bản ghi user đầu đã chốt, toàn bộ file đọc được bằng CSV strict. Công cụ chỉ mô phỏng bỏ qua khi đọc, **không sửa file gốc**.

| Nguồn chính | Số dòng | Số cột |
|---|---:|---:|
| `new/dlu_user.csv` sau bỏ dòng lỗi | 44.563 | 15 |
| `dlu_course.csv` | 9.058 | 36 |
| `dlu_course_categories_cntt.csv` | 41 | 14 |
| `dlu_enrol.csv` | 18.546 | 35 |
| `dlu_user_enrolments.csv` | 521.715 | 9 |
| `dlu_assign.csv` | 25.683 | 30 |
| `dlu_assign_submission.csv` | 893.033 | 9 |
| `dlu_assign_grades.csv` | 297.981 | 8 |
| `dlu_grade_items.csv` | 2.725 | 31 |
| `dlu_grade_grades.csv` | 3.227.303 | 23 |
| `dlu_course_modules_cntt.csv` | 6.442 | 21 |
| `dlu_course_sections.csv` | 1.287 | 10 |
| `dlu_context.csv` | 413.010 | 6 |
| `dlu_role_assignments.csv` | 521.015 | 9 |

Cohort và quiz/question vẫn được kiểm kê/hash để bảo toàn nguồn, nhưng không thuộc pipeline/dashboard v2.

### Độ bao phủ

- 166 môn có đầu điểm, cũng là 166 môn có đầu điểm tổng kết.
- Metadata hoạt động và section bao phủ **172 môn**, không phải đủ cả 173 môn trong danh sách CNTT.
- Có bài tập thuộc 5.062 môn, nhưng không đồng nghĩa đủ metadata để xét quyền/ẩn cho tất cả các bài.
- 8.847/9.058 môn có mã danh mục không tìm thấy trong file danh mục được xuất. Không thể suy ra tên khoa/năm học cho các môn này.
- 3.063.390 dòng điểm không tìm được `grade_items` trong bộ xuất; 163.913 dòng nối được. Đây chủ yếu là chênh lệch phạm vi, không tự kết luận điểm nguồn sai.
- Sau khi chỉ xét các ghi danh có `enrol` tương ứng: 516.514 cặp user–course, dư 1.728 dòng do trùng cặp; 505.677 cặp không có đầu điểm tổng kết trong bộ xuất.

### Quan hệ cần cách ly/kiểm tra

Các số dưới đây dựa trên ánh xạ vị trí được ghi rõ trong công cụ, chưa thay cho DDL nguồn.

| Kiểm tra | Số dòng không tìm thấy cha | Xử lý dự kiến |
|---|---:|---|
| Ghi danh → enrol | 3.473 | Không tạo cặp môn khi chưa xác định được course |
| Ghi danh → user mới | 122 | Cách ly; không phục hồi người dùng bằng dữ liệu giả |
| Bài nộp → bài tập | 3 | Cách ly |
| Bài nộp → user mới, gồm userid 0 | 20.686 | Tách 19.997 bản ghi userid 0 của nhóm/placeholder; không coi tất cả là orphan cá nhân |
| Điểm bài tập → user mới | 2 | Cách ly |
| Grade_grades → user mới | 636 | Cách ly khỏi tập nạp có người dùng hợp lệ |
| Module hoạt động → section | 2 | Không tự gán section giả rồi hiện bài |
| Role assignment → context | 2.492 | Không gán quyền bằng ID trùng ở môi trường cũ |
| Role assignment → user mới | 128 | Cách ly |

Không phát hiện trùng cặp user–assignment có `latest=1` trong bản nộp cá nhân; không trùng khóa user–assignment–attempt trong điểm bài tập. Có **88.231 điểm bài tập âm trên toàn bộ nguồn**: giữ raw, không coi là điểm thật. Con số này khác số lượng trong riêng phạm vi CNTT.

## 3. Khác schema — không import theo thứ tự cột đích

| Bảng | Cột CSV | Cột Moodle đang chạy | Điểm cần lưu ý |
|---|---:|---:|---|
| assign | 30 | 39 | Nhiều trường đích mới; không điền dữ liệu nghiệp vụ giả |
| assign_submission | 9 | 10 | Đích thêm `timestarted` trước status: import vị trí sẽ lệch status/group/attempt/latest |
| assign_grades | 8 | 9 | Đích có `penalty` trước attemptnumber |
| course | 36 | 38 | Cần mapping tên cột, không copy nguyên dòng |
| course_modules | 21 | 26 | `section` nguồn ở vị trí 4; availability nguồn ở 19, đích ở 20 |
| course_sections | 10 | 12 | Cần giữ visibility/availability đúng nghĩa |
| grade_grades | 23 | 24 | Đích có trường mới; không dịch vị trí cột |

- File không có header; schema Moodle cũ của giáo viên **chưa được xác nhận phiên bản chính xác**.
- User mới là phép SELECT riêng, không phải toàn bộ bảng user. Mapping cột 13 chưa xác định chắc là institution hay department; không tự sử dụng. Các cờ deleted/suspended cũng cần đối chiếu trước import vận hành.
- Kiểm kê MSSV tạm dùng mã độc lập 7 chữ số, username trước email-local-part: 42.030 ứng viên, 41.794 còn hoạt động theo cờ giả định. Có 734 username chứa dãy dài hơn 7 số. Đây là **số liệu kiểm tra bảo thủ**, chưa triển khai quy tắc đăng nhập; chuỗi dài/nhiều mã cần ghi nhận mơ hồ thay vì tự cắt gán tùy tiện.
- Có 12 role nguồn, chỉ 8 role trên Moodle cũ: không copy role assignment theo ID và giữ capabilities cũ. Phải map quyền được kiểm chứng, theo nguyên tắc tối thiểu.
- Bảng grade_categories, cấu hình/plugin bài tập, nhóm và file nguồn chưa được cung cấp. Không thể nạp gradebook/plugin hoàn chỉnh bằng cách nối ID giả.
- Các loại module nguồn chưa thấy version cài đặt tương ứng: `assignment`, `attendance`, `chat`, `game`, `googlemeet`, `hvp`, `jitsi`, `journal`, `questionnaire`, `survey`, `videotime`. Không tạo đăng ký module giả; activity không hỗ trợ bị vô hiệu hóa/cách ly. Điểm attendance có thể giữ ở raw mà không cài plugin.
- Availability thực tế có `date`, `group`, `grouping`, `profile`. Điều kiện nhóm chưa có dữ liệu thành viên; chỉ chấp nhận quyền khi có thể chứng minh, không mặc định cho phép.

**Cổng an toàn:** chưa được import CSV vào bảng Moodle hoạt động nếu mapping trường bắt buộc chưa được xác minh bằng cấu trúc, kiểu, quan hệ và thử nghiệm. Thiếu schema nguồn không được giải quyết bằng đoán âm thầm.

## 4. Kiến trúc nạp dự kiến cho mốc 2

1. Raw landing riêng trong `moodledb_v2` cho dữ liệu CSV đã parse, có manifest hash và provenance; không ghi đè bảng Moodle ngay. Không tự đặt tên cột chưa biết thành cột nghiệp vụ đã xác nhận.
2. Tách bảng Moodle hoạt động khỏi landing. Chỉ chuyển phần xác minh được sang bảng chuẩn Moodle bằng mapping **tên cột rõ ràng**, giao dịch và báo cáo loại trừ.
3. Giữ ID thực thể thật khi khả thi; context/module/role kỹ thuật cần map ID minh bạch. Không copy path/context/capability hoặc cache của dữ liệu giả sang người thật.
4. Thiếu danh mục/section/plugin: dùng cấu trúc kỹ thuật trung tính nếu cần để Moodle vận hành, ghi provenance; không bịa khoa, lớp, năm học hay hoạt động học tập. Course shell chưa đủ điều kiện phải ẩn.
5. Metadata raw gốc và cờ ẩn vận hành v2 là hai khái niệm khác nhau: ẩn course để an toàn không được xóa lịch sử ghi danh/điểm của dashboard. ETL cần tập dữ liệu curated và coverage riêng, không suy luận độ đủ từ shell Moodle.
6. Không cho Moodle/cron tự tính lại và ghi đè điểm snapshot của giáo viên khi gradebook chưa đầy đủ. Điểm nguồn phải được bảo toàn trong landing; chưa đủ quan hệ thì không ép nạp gradebook core.
7. Tạo mật khẩu demo bcrypt theo username cho tài khoản được phép; admin kỹ thuật riêng. Không sao chép sessions, tokens, OAuth secrets, scheduled tasks hoặc thông tin xác thực cũ sang danh tính thật.
8. V2 dùng URL/cổng riêng (dự kiến localhost:8081, kiểm tra trống trước khi dùng), code/moodledata/config riêng, cùng image digest. Không mount volume code/data đang chạy. Tắt email và cron ban đầu; không công khai cổng chứa dữ liệu thật/mật khẩu dễ đoán.
9. Không chạy các script seed/fix/custom có sẵn trong cây Moodle cũ: nhiều script quản trị không thuộc core phải được loại khỏi bản phục vụ v2 hoặc chặn truy cập trước khi khởi động.

Chưa có cam kết khôi phục mọi activity: CSV không chứa backup khóa học, nội dung file, grade categories hoặc toàn bộ cấu hình plugin. Mục tiêu đầu tiên là Moodle v2 mở/quản trị được, danh tính và dữ liệu thật không bị trộn, các tính năng không đủ dữ liệu bị vô hiệu hóa an toàn.

## 5. Sao lưu và rollback (nguyên tắc; tiến độ thực tế ở mục 8)

- Lưu ngoài Git và hạn chế quyền: SQL dump nhất quán của MariaDB, native backup SQL Server; lưu checksum, phiên bản/image và cấu hình bí mật riêng.
- Sao lưu cả mã/plugin/config và moodledata thực tế, không chỉ database. Không copy thư mục dữ liệu MariaDB/SQL Server đang chạy như một bản backup hợp lệ.
- Chọn cửa sổ nhất quán; nếu phải maintenance/quiesce dịch vụ cũ thì thực hiện có kiểm soát ở mốc 2, không làm trong kiểm kê.
- Chỉ đánh dấu backup đạt sau khi kiểm tra khả năng đọc/restore vào môi trường thử, không chỉ vì có file dump.
- Mọi script v2 phải chặn nhầm DB cũ bằng allowlist tên DB và preflight; không drop/recreate đích đã tồn tại nếu chưa có xác nhận.
- Không xóa volume/container/database cũ. Trước chuyển ứng dụng, rollback chỉ là không dùng v2; sau chuyển phải phục hồi cấu hình kết nối cũ theo bản lưu.

## 6. Các mốc và cổng nghiệm thu

| Mốc | Nội dung | Cổng đạt | GitHub |
|---|---|---|---|
| 1 | Inventory/schema/runtime/kế hoạch | Báo cáo tổng hợp, công cụ chỉ đọc có test, rõ giới hạn và điểm cách ly | Không push; gộp vào mốc 2 |
| 2 | Backup được kiểm tra, nguồn + Moodle v2, DWH v2 chỉ cấu trúc/rỗng | Không đụng dữ liệu cũ; đối chiếu raw, mapping, orphan; admin/login/ẩn môn/email hoạt động đúng | Push lần 1 |
| 3 | ETL/DWH v2 | Ghi danh, điểm, bài đúng grain; chạy lại không trùng; có log; nạp atomic hoặc publish sau validation, không DELETE fact đích trước khi kiểm tra nguồn | Push lần 2 |
| 4 | Backend | Danh sách môn từ ghi danh; điểm/quyền/KPI/auth đúng; bỏ phụ thuộc cohort/quiz | Push lần 3 |
| 5 | Dashboard hiện có | Ít chữ, bỏ lớp/quiz; sáng/tối tại 1366/768/375 px, không lỗi console/tràn ngang; API khớp hiển thị | Push lần 4 |
| 6 | Nghiệm thu toàn luồng | Dựng/chạy lại theo hướng dẫn, kiểm tra rollback/coverage/tính toán; tick đúng mục trong VanDe | Push lần 5 |
| 7 | Xét Điểm thành phần | Đánh giá dữ liệu trước, không bắt buộc triển khai | Chỉ push nếu có thay đổi hữu ích |

Hiện chưa xác minh deployment gọi ETL nào. Mốc 3 chọn một entrypoint v2 rõ ràng, không sửa/rerun cả bốn bản ETL cũ. Backend/frontend không đổi trước mốc tương ứng.

## 7. Công cụ và kiểm thử mốc 1

`etl/audit_source_v2.py` chỉ dùng thư viện chuẩn Python, không import pipeline. Chế độ mặc định đọc CSV, xuất `local-v2/audit.json` chứa hash, số lượng, độ bao phủ, quan hệ; không có bản ghi cá nhân. Chế độ probe không bootstrap Moodle: PHP đọc config trong bộ nhớ, PDO mở transaction READ ONLY; SQL Server chỉ SELECT metadata/count. Không in/lưu secret.

Từ thư mục gốc (thay `python` bằng executable Python 3 khả dụng trên máy):

```sh
python -m unittest discover -s etl -p test_audit_source_v2.py -v
python etl/audit_source_v2.py
python etl/audit_source_v2.py --probe-only --moodle-container moodle_app --mssql-container datawarehouse_sqlserver --output local-v2/runtime-schema.json
```

Máy này đã chạy bằng Python 3.12.14 tại `C:/Users/giahu/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe`; lệnh `python` mặc định chưa dùng được.

- Đã đọc đủ 25 CSV; dòng user lỗi chỉ được bỏ qua trong bộ đọc, nguồn không thay đổi.
- Đã chạy probe đọc schema/count trên đúng MariaDB và SQL Server của dự án.
- Đã chạy 6 unit test dữ liệu tổng hợp: bỏ một record lỗi, strict CSV, nhiều dòng/tiếng Việt, mã sinh viên, trùng ID, quan hệ thiếu, output không chứa mẫu PII.
- Mốc 2 đã kiểm thử restore/import và login native/HTTP; chưa kiểm thử ETL v2 hoặc giao diện bằng trình duyệt thật.

`.gitignore` đã loại `/Data_11-2026/`, `/local-v2/`, `/backups/` và các dump thông dụng. Kiểm tra Git hiện không có CSV nguồn được track; ignore không tự xóa dữ liệu từng bị commit trong lịch sử. Không dùng `git add .` nếu chưa xem diff/staged; không đưa `.env`, CSV thật, file Moodle hoặc backup lên GitHub.

## 8. Mốc 2 — kết quả và vận hành

### Kết quả

- Backup riêng `backups/20261114_052425/`: MariaDB khoảng 105 MiB, code/dataroot khoảng 213 MiB, SQL Server khoảng 177 MiB. Đã kiểm hash, đọc archive, **restore thật cả hai database** và đối chiếu bảng nghiệp vụ; SQL Server có CHECKSUM và DBCC CHECKDB PHYSICAL_ONLY. Manifest `complete=true`.
- Lần đầu bị timeout 300 giây đã bỏ đích restore dở; lần tiếp tục với giới hạn 900 giây thành công. Mọi database/container/volume restore tạm đã được dọn. Chỉ giữ một bộ backup đã xác minh.
- `moodledb_v2` có **17 bảng `v2_raw_*` dữ liệu nguồn** (không tính manifest). JSON array giữ nguyên chuỗi, NULL dạng CSV, ID và hash; bỏ đúng bản ghi user đầu, không nhập cohort/quiz/question. Loader có transaction, resume theo hash/count, từ chối bảng partial; batch 8 MiB hỗ trợ nội dung bài tập lớn hơn 1 MiB mà không đổi global max_allowed_packet.
- Moodle cùng image/version nhưng **cài nền sạch**, không copy dữ liệu nghiệp vụ hay script tùy chỉnh từ volume cũ. Đây là cách giữ tương thích mà tránh kéo dữ liệu giả theo.
- Native Moodle: **44.563 user thật + 2 tài khoản kỹ thuật** (admin riêng, guest bắt buộc của Moodle vì nguồn không có guest); 9.058 course gồm front page kỹ thuật và 9.057 course shell thật đang ẩn; 1.287 section metadata thật và section 0 kỹ thuật khi thiếu.
- Có 41 danh mục thật và 402 placeholder trung tính, có provenance; không coi tên placeholder là khoa/năm học thật.
- Nạp **518.120 lượt ghi danh hợp lệ**; cách ly 3.595 dòng thiếu enrol/user, giữ nguyên raw để kiểm toán. Context được dựng bằng Moodle API, không dùng ID/path cũ. Quyền vận hành dùng vai trò student tối thiểu và enrol manual; phương thức/vai trò nguồn vẫn giữ trong raw, không phục hồi capabilities giáo viên/quản trị từ ID cũ.
- `lms_datawarehouse_v2`: **8 bảng, 0 bản ghi**, kể cả Dim_Date. Đã execute schema trên SQL Server và kiểm chứng chạy lại bị guard từ chối.
- Web v2 **http://localhost:8081**, chỉ bind `127.0.0.1`; container `moodle_app_v2`, volume `dacs_v2_moodle_code` và `dacs_v2_moodle_data`, cookie `MoodleSessionV2`. Moodle cũ vẫn cổng 8080, backend/dashboard chưa chuyển nguồn.
- Email ra ngoài và cron v2 tắt; không dùng SMTP/OAuth/token/phiên đăng nhập của hệ cũ. Builder/app DB user chỉ có quyền trên v2, đã thử đọc DB cũ và bị từ chối.
- Tài khoản demo dùng bcrypt mật khẩu bằng username; cost 4 để dựng bộ dữ liệu demo lớn, **không dùng cấu hình này cho production/public**. Moodle nâng cấp hash khi xác thực nếu cần. Admin có mật khẩu mạnh riêng.

### Giới hạn phải giữ khi làm mốc sau

- **0 hoạt động core được khôi phục**, không có file bài nộp/tài liệu hoặc gradebook hoàn chỉnh trong giao diện Moodle. Các bảng assign/điểm/bài nộp thật nằm đầy đủ ở raw; không được diễn giải bảng core trống là sinh viên không có dữ liệu.
- Toàn bộ course shell bị ẩn có chủ ý vì chưa đủ cấu hình/plugin/file để chứng minh hoạt động an toàn. Dashboard/ETL phải lấy metadata gốc và coverage từ raw, không lấy cờ ẩn kỹ thuật này làm visibility nguồn.
- Không phục hồi toàn bộ chức năng Moodle của trường. Native account auth được đổi sang manual cho demo; không tuyên bố đây là SSO hoặc quyền giảng viên thật.
- Cột user chưa xác định nghĩa không được gán; snapshot raw và provenance là nguồn để mốc 3 tạo tập curated có kiểm tra quan hệ.
- Backup online không phải snapshot đồng bộ tuyệt đối giữa filesystem và DB. Hệ cũ vẫn chạy cron nên task_log tăng; các bảng khác và DWH giữ nguyên số lượng qua kiểm tra. Không thay đổi cấu hình/hay dữ liệu nghiệp vụ hệ cũ.

### Kiểm tra đã chạy

- 23 unit test ETL/audit/backup/preparation/raw-loader + 7 test schema tĩnh đạt.
- PHP syntax và kiểm tra native: admin/sinh viên đăng nhập được, mật khẩu sai bị từ chối, sinh viên không có quyền hệ thống/admin, guest kỹ thuật không trùng ID thật.
- HTTP thật: login có CSRF token/cookie riêng; admin mở quản lý khóa học, sinh viên mở trang cá nhân; không thấy trang báo lỗi ứng dụng.
- Đối chiếu hash/count 17 raw tables, ghi danh + quarantine, quan hệ core user/enrol/context/role và DWH rỗng.
- **Chưa kiểm tra bằng trình duyệt thật hoặc responsive dashboard**; thuộc mốc 5. Chưa chạy ETL hoặc API v2; thuộc mốc 3–4.

### File cần giữ và cách dùng

- `local-v2/runtime-secrets.json`: tài khoản quản trị và credential v2, private/ignored; không chép lên chat/GitHub. Không đổi trường `phase` thủ công để vượt preflight.
- `local-v2/milestone2-report.json`, `activation-report.json`, `audit-final.json`, `runtime-schema.json`: báo cáo/schema cần cho mốc 3. Không cần giữ các bản report trùng hay log debug.
- `backups/20261114_052425/`: giữ bộ backup đã kiểm chứng; không xóa khi dọn tạm. Bản `.bak` trùng trong container SQL Server đã xóa sau restore-check; bản local vẫn nguyên checksum.
- Script trong `etl/` và `dwh/schema_v2.sql`: source triển khai/kiểm thử, không phải file tạm.
- Dừng/mở lại v2 khi cần: `docker stop moodle_app_v2` / `docker start moodle_app_v2`. Không stop hoặc prune container/volume cũ.
- Kiểm tra lại mốc 2 (chỉ khi DWH còn rỗng): `python -B etl/validate_v2.py --execute`.

### Dọn dẹp sau nghiệm thu

Đã xóa 8 đường dẫn tạm/trùng (report cũ, log lỗi, cache Python, manifest lần backup chưa tạo dữ liệu), bản backup SQL trùng phía server và mọi đích restore tạm. Riêng file/cache/bản sao trùng thu hồi khoảng **177,41 MiB**, chưa tính dung lượng volume restore đã xóa. Một `.pyc` cũ từng bị track cũng được loại khỏi Git; source `.py` giữ nguyên. Không chạy Docker prune, không xóa CSV, backup đã kiểm chứng, raw tables hoặc volume đang dùng. Restart riêng Moodle v2 và kiểm tra native/HTTP lại đạt. Giữ service cần cho mốc sau; không flush cache/bộ nhớ chung của DB cũ.

Đã đối chiếu lại **25/25 hash CSV không đổi**. Báo cáo dọn dẹp tại `local-v2/cleanup-report.json`. Các kiểm tra cuối đạt 30/30 unit/static tests; DWH vẫn 0 bản ghi, hệ cũ không chuyển nguồn.

Trình tự dựng trên máy mới: backup + restore-check bằng `backup_v2.py --execute`; `prepare_v2.py --execute --backup backups/<timestamp>`; `source_v2.py --execute`; tạo `/opt/dacs-v2` trong container và copy `activate_moodle_v2.php` vào đó rồi chạy CLI dưới user www-data; execute `dwh/schema_v2.sql` bằng sqlcmd -b với credential an toàn; cuối cùng `validate_v2.py --execute`. Mọi script từ chối ghi vào database cũ. Không rerun SQL schema lên target đã tồn tại và không tự xóa target để vượt guard.
