# DACSITDLU2026 — Cổng Thông Tin Học Tập Sinh Viên (DLU)

Đồ án xây dựng **Data Warehouse (DWH)** từ dữ liệu Moodle LMS và ứng dụng web
tra cứu điểm/deadline/quiz cho sinh viên Trường Đại học Đà Lạt.

## 1. Kiến trúc hệ thống

```
Moodle (moodleweb) ──► MariaDB (moodledb)  ──ETL──►  SQL Server (lms_datawarehouse) ◄── Backend (Express) ◄── Frontend (HTML/JS)
                         [dữ liệu nguồn]              [dữ liệu đích - star schema]         [REST API]           [Dashboard SV]
```

| Thành phần | Công nghệ | Vai trò |
|---|---|---|
| `mariadb` | MariaDB 11.4 | Lưu dữ liệu gốc của Moodle (bảng `mdl_*`) |
| `moodleweb` | Moodle (elestio image) | Giao diện quản trị LMS gốc |
| `sqlserver` | Azure SQL Edge | Kho dữ liệu (Data Warehouse) dạng star schema |
| `etl/` | Python (pymysql, pyodbc) | Đồng bộ dữ liệu Moodle → DWH |
| `dwh/` | SQL Server script | Định nghĩa schema (`Dim_*`, `Fact_*`) của DWH |
| `backend/` | Node.js + Express | REST API phục vụ frontend, xác thực bằng JWT |
| `frontend/` | HTML/CSS/JS thuần | Giao diện Cổng sinh viên (đăng nhập, xem điểm, deadline, quiz) |
| `code-server` | VSCode chạy trên trình duyệt | Môi trường dev từ xa (tuỳ chọn) |

## 2. Danh sách file/thư mục đã được dọn dẹp (đã xoá khỏi bản này)

Các mục sau **không phải source code**, chỉ là rác hệ thống / cache / dữ liệu tạm
của các lần chạy thử trước đó — đã xoá vì không ảnh hưởng đến khả năng chạy lại
dự án từ đầu:

| Đã xoá | Lý do |
|---|---|
| `.DS_Store` | Rác hệ thống macOS |
| `__MACOSX/` | Rác sinh ra khi nén file zip trên macOS |
| `etl/__pycache__/` | Cache bytecode Python, tự sinh lại khi chạy |
| `clone_debug.log` | Log của lần chạy `clone_db.sh` trước, không được script nào đọc lại |
| `moodle_backup.sql` | File rỗng (0 byte) |
| `moodledb_clone_20260914_205036.sql` | File rỗng (0 byte) |
| `moodledb_clone_20260914_205109.sql` | Bản dump trung gian cũ, không script nào tham chiếu tên cố định |
| `moodledb_clone_20260914_205411.sql` | Bản dump trung gian cũ, tương tự |
| `test.sql` | Bản dump thử nghiệm rời rạc (36KB), không được `docker-compose.yml`/`run_all.sh`/`clone_db.sh` gọi tới |

## 3. Các mục được GIỮ LẠI dù trông giống thừa

| Giữ lại | Vì sao không xoá |
|---|---|
| `moodledb_clone_20260914_205709.sql` (bản dump mới nhất, ~99MB) | Đây là bản sao dữ liệu Moodle **đầy đủ nhất** hiện có — cần để import lại vào MariaDB khi thiết lập môi trường mới (xem `SETUP.md`) |
| `data/mariadb/` (~372MB) | Là **volume dữ liệu thật** của container MariaDB đang chạy (khai báo trong `docker-compose.yml`). Xoá sẽ làm mất toàn bộ dữ liệu Moodle hiện có trong container |
| `.git/` | Lịch sử version control của dự án |
| `backend/package-lock.json` | Khoá phiên bản chính xác của các dependency Node.js, cần thiết để `npm install` cho ra kết quả nhất quán giữa các máy |

## 4. Cấu trúc thư mục sau khi dọn dẹp

```
DACSITDLU2026/
├── backend/                  # REST API (Node.js/Express)
├── dwh/                      # Schema + seed script cho Data Warehouse
├── etl/                      # Pipeline đồng bộ Moodle → DWH
├── frontend/                 # Giao diện web sinh viên
├── data/mariadb/             # Volume dữ liệu MariaDB (KHÔNG xoá)
├── docker-compose.yml        # Định nghĩa toàn bộ service
├── clone_db.sh                # Script clone DB Moodle từ server remote
├── run_all.sh                 # Script khởi động toàn bộ hệ thống từ đầu
├── moodledb_clone_20260914_205709.sql   # Bản dump Moodle mới nhất (để import)
├── README.md                  # File này
└── SETUP.md                   # Hướng dẫn cài đặt & vận hành từ đầu
```

Xem hướng dẫn cài đặt/chạy từ đầu (bao gồm cách import dữ liệu vào MariaDB,
chạy ETL, và bảng tài khoản đăng nhập) tại **[`SETUP.md`](./SETUP.md)**.
