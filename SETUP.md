# SETUP.md — Hướng dẫn cài đặt & chạy hệ thống từ đầu

Hướng dẫn này dành cho trường hợp **mới clone code về máy** và cần dựng lại toàn
bộ hệ thống (MariaDB nguồn → Data Warehouse SQL Server → Backend → Frontend).
Tất cả lệnh chạy trong **Terminal của VSCode** (menu `Terminal > New Terminal`,
hoặc phím tắt `` Ctrl + ` ``).

> Máy Windows: nên dùng **WSL2** hoặc **Git Bash** vì các script (`.sh`) viết cho bash.

## 0. Yêu cầu trước khi bắt đầu

- Đã cài **Docker Desktop** (bật sẵn Docker Engine + Docker Compose v2)
- Đã cài **Git**
- Dung lượng ổ đĩa trống tối thiểu ~2GB (do có sẵn dữ liệu MariaDB + dump ~470MB)

Kiểm tra nhanh trong terminal:
```bash
docker --version
docker compose version
```

## 1. Clone project và mở bằng VSCode

```bash
git clone <đường-dẫn-repo-của-bạn> DACSITDLU2026
cd DACSITDLU2026
code .
```

Mở Terminal ngay trong VSCode để chạy các bước tiếp theo (không cần thoát ra terminal ngoài).

## 2. Cấp quyền thực thi cho các script `.sh` (chỉ cần làm 1 lần)

```bash
chmod +x clone_db.sh run_all.sh
```

## 3. Khởi động container MariaDB (database nguồn của Moodle)

```bash
docker compose up -d mariadb
```

Đợi vài giây rồi kiểm tra container đã "healthy" chưa:
```bash
docker ps
```
Cột `STATUS` của container `moodle_db` phải hiện `(healthy)`.

## 4. Import dữ liệu Moodle vào MariaDB

Có 2 cách — **chọn 1 trong 2**:

### Cách A (khuyến nghị — dùng bản dump có sẵn trong repo)
Repo đã kèm sẵn bản dump mới nhất `moodledb_clone_20260914_205709.sql`. Import trực tiếp:
```bash
docker exec -i moodle_db mysql -u root -pmoodlerootpassword moodledb < moodledb_clone_20260914_205709.sql
```
Lệnh này có thể mất vài phút vì file dump khá lớn (~99MB).

### Cách B (lấy dữ liệu mới trực tiếp từ server Moodle thật)
Nếu muốn đồng bộ dữ liệu **mới nhất** từ server thay vì dùng bản dump cũ trong repo:
```bash
./clone_db.sh
```
Script này tự động: dump dữ liệu từ server remote → tự khởi động MariaDB local
(nếu chưa chạy) → import thẳng vào container. Muốn đổi host/user/pass remote,
sửa các biến `REMOTE_HOST`, `REMOTE_USER`, `REMOTE_PASS` ở đầu file `clone_db.sh`.

> Lưu ý: thư mục `data/mariadb/` trong repo hiện đã có sẵn file dữ liệu MariaDB
> (do được commit vào Git). Nếu container `moodle_db` khởi động và đã thấy có
> dữ liệu, bước import ở trên vẫn an toàn để chạy đè cập nhật lại nhưng nếu muốn
> chắc chắn sạch từ đầu, có thể xoá nội dung `data/mariadb/` trước khi
> `docker compose up -d mariadb`.

## 5. Khởi động toàn bộ phần còn lại (SQL Server → Schema → ETL → Backend)

Cách nhanh nhất — dùng script tự động đã viết sẵn:
```bash
./run_all.sh
```
Script này sẽ tự động:
1. Khởi động `sqlserver` (song song với `mariadb`)
2. Tạo schema Data Warehouse (`dwh/schema.sql`) vào SQL Server
3. Chạy ETL (`etl/etl_pipeline.py`) để đồng bộ Moodle → Data Warehouse
4. Khởi động `backend`

### Nếu muốn chạy thủ công từng bước (để dễ debug)
```bash
# 1. Khởi động SQL Server
docker compose up -d sqlserver
sleep 20   # đợi SQL Server khởi động xong

# 2. Tạo schema cho Data Warehouse
docker compose run --rm etl python /dwh/schema.sql   # xem chú thích bên dưới
# HOẶC dùng script Python thay thế sqlcmd (khuyến nghị, ổn định hơn):
docker compose run --rm etl python run_schema.py

# 3. Seed dữ liệu bảng ngày tháng (Dim_Date, 2024-2030) — bước này KHÔNG được
#    run_all.sh gọi tự động, cần chạy tay 1 lần:
docker compose run --rm etl python /dwh/seed_dim_date.py

# 4. Chạy ETL đồng bộ dữ liệu Moodle -> Data Warehouse
docker compose run --rm etl

# 5. Khởi động Backend + Frontend
docker compose up -d backend
```

## 6. Truy cập hệ thống

| Dịch vụ | URL | Ghi chú |
|---|---|---|
| Cổng thông tin Sinh viên (Frontend + API) | http://localhost:5050 | Giao diện chính, đăng nhập bằng tài khoản Moodle sinh viên |
| Moodle gốc (LMS quản trị) | http://localhost:8080 | Giao diện Moodle đầy đủ |
| SQL Server (Data Warehouse) | `localhost:1433` | Kết nối bằng công cụ như Azure Data Studio / DBeaver |
| MariaDB (Moodle DB) | `localhost:3306` | Kết nối bằng DBeaver / MySQL Workbench |
| code-server (VSCode qua trình duyệt) | http://localhost:8443 | Môi trường dev từ xa, tuỳ chọn |

## 7. Bảng tài khoản đăng nhập theo từng vai trò/hệ thống

| Hệ thống | Tài khoản | Mật khẩu | Vai trò / Mục đích |
|---|---|---|---|
| MariaDB — quản trị | `root` | `moodlerootpassword` | Toàn quyền MariaDB, dùng để import/export dump |
| MariaDB — user ứng dụng | `moodleuser` | `moodlepassword` | User giới hạn, ETL dùng để đọc dữ liệu `moodledb` |
| SQL Server (Data Warehouse) | `sa` | `SuperStrongPass123!` | Toàn quyền SQL Server / DWH |
| Moodle Web (http://localhost:8080) | Tài khoản admin gốc nằm sẵn trong dữ liệu đã clone | Mật khẩu gốc từ server thật — **không có trong code**, do đến từ dữ liệu Moodle thật đã clone về | Quản trị toàn bộ hệ thống LMS Moodle |
| Cổng Sinh viên (http://localhost:5050) | Bất kỳ `username` nào có `IsStudent = 1` trong bảng `Dim_User` (đồng bộ từ Moodle) | Mật khẩu Moodle gốc (đã mã hoá bcrypt) **hoặc** mật khẩu test cố định: `123456` hoặc `dlu@2026` | Chỉ tài khoản vai trò **sinh viên** được vào; giảng viên/quản trị viên bị chặn ở API đăng nhập |
| code-server (http://localhost:8443) | không cần username | `abc123456` | Mật khẩu truy cập VSCode Web (sudo cũng dùng chung mật khẩu này) |
| JWT Secret (backend) | — | `DLU_SuperSecretKey_2026` | Không phải tài khoản đăng nhập, dùng để ký/giải mã token phiên đăng nhập |

### ⚠️ Lưu ý bảo mật cần biết
- `backend/server.js` có cơ chế **mật khẩu dự phòng (fallback)**: nếu so khớp bcrypt
  thất bại, hệ thống vẫn cho đăng nhập nếu người dùng nhập đúng `123456` hoặc
  `dlu@2026` — đây là cơ chế phục vụ **test/demo**, cần gỡ bỏ trước khi đưa vào
  môi trường thật.
- Toàn bộ mật khẩu ở trên (MariaDB, SQL Server, JWT secret, code-server) đang bị
  **hard-code** thẳng trong `docker-compose.yml` / `server.js` / `etl_pipeline.py`
  — chỉ phù hợp cho môi trường học tập/đồ án, không nên dùng nguyên trạng cho
  môi trường production.

## 8. Dừng hệ thống

```bash
docker compose down
```
(Dữ liệu trong `data/mariadb/` và volume `mssql_data` vẫn được giữ lại, lần sau
`docker compose up -d` không cần import lại từ đầu.)
