#!/bin/bash
# ============================================================
# Clone du lieu MariaDB (moodledb) tu server remote ve local
# Chay tren MAY CUA BAN (noi da co docker-compose.yml + docker)
# ============================================================
set -e

# ---- CAU HINH: sua neu can ----
REMOTE_HOST="42.116.56.24"
REMOTE_PORT="3306"
REMOTE_USER="root"                 # doi thanh moodleuser neu khong co quyen root
REMOTE_PASS="moodlerootpassword"   # doi thanh moodlepassword neu dung moodleuser
REMOTE_DB="moodledb"

DUMP_FILE="moodledb_clone_$(date +%Y%m%d_%H%M%S).sql"

echo "[1/3] Dang dump du lieu tu $REMOTE_HOST:$REMOTE_PORT ..."
# Dung docker de muon mysqldump, khong can cai MariaDB client tren may ban
docker run --rm mariadb:11.4 \
  mysqldump -h "$REMOTE_HOST" -P "$REMOTE_PORT" -u "$REMOTE_USER" -p"$REMOTE_PASS" \
  --single-transaction --quick --routines --triggers --events \
  "$REMOTE_DB" > "$DUMP_FILE"

echo "[2/3] Dump xong: $DUMP_FILE ($(du -h "$DUMP_FILE" | cut -f1))"

echo "[3/3] Khoi dong container mariadb local (docker-compose) neu chua chay..."
docker compose up -d mariadb

echo "Doi mariadb local san sang..."
until docker exec moodle_db mysqladmin ping -h 127.0.0.1 -u root -pmoodlerootpassword --silent; do
  sleep 2
done

echo "Dang import vao container local (moodle_db) ..."
docker exec -i moodle_db mysql -u root -pmoodlerootpassword moodledb < "$DUMP_FILE"

echo "=========================================="
echo "HOAN TAT! Du lieu da duoc clone ve container 'moodle_db' (database moodledb)."
echo "File dump duoc luu tai: $DUMP_FILE"
echo "=========================================="`