"""
benchmark_read.py
Nghiên cứu thực nghiệm tối ưu hóa khâu ĐỌC dữ liệu (Extract Optimization):
So sánh 4 phương pháp đọc trường từ MariaDB (Moodle):
  1. SELECT * (Đọc toàn bộ ~100% cột, bao gồm LONGTEXT off-page) + DictCursor + fetchall()
  2. SELECT 20% cột cần thiết (Column Pruning) + DictCursor + fetchall()
  3. SELECT 20% cột cần thiết + Cursor (Tuple) + fetchall()
  4. SELECT 20% cột cần thiết + Lọc sớm (Predicate Pushdown) + SSCursor (Streaming Tuple) + fetchmany(10000) [etl_v4]
Kết quả được xuất ra file CSV: etl/read_optimization_benchmark.csv
"""
import os
import csv
import time
import tracemalloc
import pymysql
import pymysql.cursors

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BENCHMARK_CSV_PATH = os.path.join(BASE_DIR, "read_optimization_benchmark.csv")

MARIADB_CONFIG = {
    "host": os.environ.get("MARIADB_HOST", "mariadb"),
    "port": int(os.environ.get("MARIADB_PORT", 3306)),
    "user": os.environ.get("MARIADB_USER", "moodleuser"),
    "password": os.environ.get("MARIADB_PASSWORD", "moodlepassword"),
    "database": os.environ.get("MARIADB_DB", "moodledb"),
    "charset": "utf8mb4",
}

SQL_ALL_COLS = """
    SELECT qa.*, qat.*, q.*
    FROM question_attempts qa
    JOIN quiz_attempts qat ON qa.questionusageid = qat.uniqueid
    JOIN quiz q ON qat.quiz = q.id
    WHERE qat.state = 'finished';
"""

SQL_PRUNED_COLS = """
    SELECT qa.id, qat.userid, qat.quiz, q.course,
           qa.questionid, qa.slot, qa.maxmark, qa.timemodified,
           qa.responsesummary, qa.rightanswer
    FROM question_attempts qa
    JOIN quiz_attempts qat ON qa.questionusageid = qat.uniqueid
    JOIN quiz q ON qat.quiz = q.id
    WHERE qat.state = 'finished';
"""

SQL_V4_OPTIMIZED = """
    SELECT qa.id, qat.userid, qat.quiz, q.course,
           qa.questionid, qa.slot, qa.maxmark, qas.fraction, qa.timemodified,
           qa.responsesummary, qa.rightanswer
    FROM question_attempts qa
    JOIN quiz_attempts qat ON qa.questionusageid = qat.uniqueid
    JOIN quiz q ON qat.quiz = q.id
    JOIN (
        SELECT questionattemptid, fraction
        FROM (
            SELECT
                questionattemptid,
                fraction,
                ROW_NUMBER() OVER (PARTITION BY questionattemptid ORDER BY sequencenumber DESC) AS rn
            FROM question_attempt_steps
            WHERE fraction IS NOT NULL
        ) ranked
        WHERE rn = 1 AND fraction < 0.5
    ) qas ON qas.questionattemptid = qa.id
    WHERE qat.state = 'finished';
"""


def measure_strategy(name, cols_desc, cursor_cls, sql, use_chunks=False, chunk_size=10000):
    conn = pymysql.connect(**MARIADB_CONFIG, cursorclass=cursor_cls)
    tracemalloc.start()
    t0 = time.perf_counter()
    row_count = 0
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            if use_chunks:
                while True:
                    rows = cur.fetchmany(chunk_size)
                    if not rows:
                        break
                    row_count += len(rows)
            else:
                rows = cur.fetchall()
                row_count = len(rows)
        dt = time.perf_counter() - t0
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        conn.close()

    return {
        "Method": name,
        "ColumnsRead": cols_desc,
        "CursorType": cursor_cls.__name__,
        "BatchStrategy": f"fetchmany({chunk_size})" if use_chunks else "fetchall()",
        "RowsRead": row_count,
        "ReadTimeSec": round(dt, 3),
        "PeakRamMB": round(peak_bytes / (1024 * 1024), 2),
        "RowsPerSec": round(row_count / dt, 0) if dt > 0 else 0,
    }


def run_benchmark():
    print("=" * 75)
    print("THỰC NGHIỆM TỐI ƯU ĐỌC TRƯỜNG DỮ LIỆU (EXTRACT BENCHMARK)")
    print("=" * 75)
    results = [
        measure_strategy(
            "1. Đọc toàn bộ cột (SELECT *)",
            "75/75 cột (100%)",
            pymysql.cursors.DictCursor,
            SQL_ALL_COLS,
            use_chunks=False,
        ),
        measure_strategy(
            "2. Cắt giảm trường (Column Pruning)",
            "11/75 cột (14.7%)",
            pymysql.cursors.DictCursor,
            SQL_PRUNED_COLS,
            use_chunks=False,
        ),
        measure_strategy(
            "3. Cắt giảm trường + Tuple Cursor",
            "11/75 cột (14.7%)",
            pymysql.cursors.Cursor,
            SQL_PRUNED_COLS,
            use_chunks=False,
        ),
        measure_strategy(
            "4. Cắt trường + Lọc sớm + SSCursor Lô 10.000 (v4)",
            "11/75 cột (14.7%)",
            pymysql.cursors.SSCursor,
            SQL_V4_OPTIMIZED,
            use_chunks=True,
            chunk_size=10000,
        ),
    ]

    with open(BENCHMARK_CSV_PATH, mode="w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    for r in results:
        print(
            f"  {r['Method']:<48} | {r['ReadTimeSec']:>6.2f}s | "
            f"RAM: {r['PeakRamMB']:>6.2f} MB | {r['RowsRead']:>8} dòng"
        )
    print(f"[+] Đã xuất kết quả thực nghiệm ra: {BENCHMARK_CSV_PATH}")


if __name__ == "__main__":
    run_benchmark()
