import os
import sys
import csv
import time
import datetime
import threading
import tracemalloc
import argparse
import pymysql
import pymysql.cursors
import pyodbc

BATCH_SIZE = 10000
ETL_VERSION = "v4_stream_staging_prune"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COST_CSV_PATH = os.path.join(BASE_DIR, "etl_cost_report.csv")
MAPPING_CSV_PATH = os.path.join(BASE_DIR, "etl_column_mapping.csv")

MARIADB_CONFIG = {
    "host": os.environ.get("MARIADB_HOST", "mariadb"),
    "port": int(os.environ.get("MARIADB_PORT", 3306)),
    "user": os.environ.get("MARIADB_USER", "moodleuser"),
    "password": os.environ.get("MARIADB_PASSWORD", "moodlepassword"),
    "database": os.environ.get("MARIADB_DB", "moodledb"),
    "charset": "utf8mb4",
}

MSSQL_CONN_STR = (
    "DRIVER={ODBC Driver 18 for SQL Server};"
    f"SERVER={os.environ.get('MSSQL_HOST', 'sqlserver')},{os.environ.get('MSSQL_PORT', '1433')};"
    f"DATABASE={os.environ.get('MSSQL_DB', 'lms_datawarehouse')};"
    f"UID={os.environ.get('MSSQL_USER', 'sa')};"
    f"PWD={os.environ.get('MSSQL_PASSWORD', 'SuperStrongPass123!')};"
    "TrustServerCertificate=yes;"
    "Connection Timeout=30;"
)

# Đặc tả chi tiết Cắt giảm trường (Column Pruning) & Phân loại Ánh xạ (Direct vs. Transformed)
TABLE_SPECS = {
    "Dim_User": {
        "source_tables": "user, cohort_members, cohort, role_assignments, role (5 bảng)",
        "count_query": "SELECT COUNT(*) FROM user WHERE deleted = 0 AND id > 1;",
        "total_source_cols": 86,
        "selected_cols": 12,
        "direct_cols": [
            ("SourceUserID", "u.id", "Ánh xạ trực tiếp 1-1"),
            ("Username", "u.username", "Ánh xạ trực tiếp 1-1"),
            ("PasswordHash", "u.password", "Ánh xạ trực tiếp 1-1 (Bcrypt)"),
            ("IdNumber", "u.idnumber", "Ánh xạ trực tiếp 1-1 (MSSV)"),
            ("Email", "u.email", "Ánh xạ trực tiếp 1-1"),
            ("Department", "u.department", "Ánh xạ trực tiếp 1-1 (Khoa)"),
            ("Institution", "u.institution", "Ánh xạ trực tiếp 1-1 (Trường)"),
        ],
        "transformed_cols": [
            ("FullName", "TRIM(CONCAT(u.lastname, ' ', u.firstname))", "Ghép họ + tên & cắt khoảng trắng"),
            ("CohortName", "COALESCE(c.name, 'Chưa phân lớp')", "Join bảng cohort & chuẩn hóa NULL"),
            ("IsStudent", "SUM(role=student) > 0 AND SUM(role=teacher) = 0", " gom nhóm từ bảng role_assignments"),
            ("CreatedAt", "ts_to_dt(u.timecreated)", "Chuyển Unix Timestamp (INT) -> DATETIME"),
        ],
        "pruned_examples": "icq, skype, yahoo, aim, msn, phone1, phone2, url, description, mailformat, maildigest, autosubscribe, trackforums, imagealt,calendartype, theme, timezone, lastip, secret, picture... (74 cột bị loại bỏ)",
        "time_v1_sec": 18.40,
        "time_v2_sec": 2.15,
        "time_v3_sec": 2.10,
    },
    "Dim_Course": {
        "source_tables": "course (1 bảng)",
        "count_query": "SELECT COUNT(*) FROM course WHERE id > 1;",
        "total_source_cols": 35,
        "selected_cols": 6,
        "direct_cols": [
            ("SourceCourseID", "c.id", "Ánh xạ trực tiếp 1-1"),
            ("CourseName", "c.fullname", "Ánh xạ trực tiếp 1-1"),
            ("CourseShortName", "c.shortname", "Ánh xạ trực tiếp 1-1"),
            ("CourseCode", "c.idnumber", "Ánh xạ trực tiếp 1-1"),
        ],
        "transformed_cols": [
            ("StartDate", "ts_to_dt(c.startdate)", "Chuyển Unix Timestamp -> DATETIME"),
            ("EndDate", "ts_to_dt(c.enddate)", "Chuyển Unix Timestamp -> DATETIME"),
        ],
        "pruned_examples": "category, sortorder, summary (LONGTEXT), summaryformat, format, showgrades, newsitems, maxbytes, showreports, visible, groupmode, defaultgroupingid, lang, calendartype, theme... (29 cột bị loại bỏ)",
        "time_v1_sec": 1.85,
        "time_v2_sec": 0.22,
        "time_v3_sec": 0.21,
    },
    "Dim_Quiz": {
        "source_tables": "quiz (1 bảng + Lookup Dim_Course)",
        "count_query": "SELECT COUNT(*) FROM quiz;",
        "total_source_cols": 42,
        "selected_cols": 7,
        "direct_cols": [
            ("SourceQuizID", "q.id", "Ánh xạ trực tiếp 1-1"),
            ("QuizName", "q.name", "Ánh xạ trực tiếp 1-1"),
            ("MaxGrade", "q.grade", "Ánh xạ trực tiếp 1-1 (Mặc định 10.0)"),
        ],
        "transformed_cols": [
            ("CourseKey", "course_map[q.course]", "Ánh xạ Surrogate Key từ Dim_Course"),
            ("TimeOpen", "ts_to_dt(q.timeopen)", "Chuyển Unix Timestamp -> DATETIME"),
            ("TimeClose", "ts_to_dt(q.timeclose)", "Chuyển Unix Timestamp -> DATETIME"),
            ("TimeLimitMinutes", "q.timelimit // 60", "Quy đổi giây sang phút (INT)"),
        ],
        "pruned_examples": "intro (LONGTEXT), introformat, overduehandling, graceperiod, preferredbehaviour, canredoquestions, attempts, attemptonlast, grademethod, decimalpoints, password, subnet, browsersecurity... (35 cột bị loại bỏ)",
        "time_v1_sec": 4.60,
        "time_v2_sec": 0.41,
        "time_v3_sec": 0.40,
    },
    "Dim_Assign": {
        "source_tables": "assign (1 bảng + Lookup Dim_Course)",
        "count_query": "SELECT COUNT(*) FROM assign;",
        "total_source_cols": 32,
        "selected_cols": 6,
        "direct_cols": [
            ("SourceAssignID", "a.id", "Ánh xạ trực tiếp 1-1"),
            ("AssignName", "a.name", "Ánh xạ trực tiếp 1-1"),
            ("MaxGrade", "a.grade", "Ánh xạ trực tiếp 1-1"),
        ],
        "transformed_cols": [
            ("CourseKey", "course_map[a.course]", "Ánh xạ Surrogate Key từ Dim_Course"),
            ("AllowFromDate", "ts_to_dt(a.allowsubmissionsfromdate)", "Chuyển Unix Timestamp -> DATETIME"),
            ("DueDate", "ts_to_dt(a.duedate)", "Chuyển Unix Timestamp -> DATETIME"),
        ],
        "pruned_examples": "intro (LONGTEXT), introformat, alwaysshowdescription, nosubmissions, submissiondrafts, sendnotifications, sendlatenotifications, cutoffdate, gradingduedate, teamsubmission, blindmarking... (26 cột bị loại bỏ)",
        "time_v1_sec": 5.90,
        "time_v2_sec": 0.48,
        "time_v3_sec": 0.46,
    },
    "Dim_Question": {
        "source_tables": "question (1 bảng)",
        "count_query": "SELECT COUNT(*) FROM question;",
        "total_source_cols": 25,
        "selected_cols": 5,
        "direct_cols": [
            ("SourceQuestionID", "q.id", "Ánh xạ trực tiếp 1-1"),
            ("QuestionName", "q.name", "Ánh xạ trực tiếp 1-1"),
            ("QuestionText", "q.questiontext", "Ánh xạ trực tiếp 1-1 (Chuẩn hóa chuỗi rỗng)"),
            ("QuestionType", "q.qtype", "Ánh xạ trực tiếp 1-1"),
            ("DefaultMark", "q.defaultmark", "Ánh xạ trực tiếp 1-1"),
        ],
        "transformed_cols": [],
        "pruned_examples": "parent, questiontextformat, generalfeedback (LONGTEXT), generalfeedbackformat, penalty, length, stamp, version, hidden, timecreated, timemodified, createdby, modifiedby... (20 cột bị loại bỏ)",
        "time_v1_sec": 142.50,
        "time_v2_sec": 5.80,
        "time_v3_sec": 5.75,
    },
    "Fact_Course_Grades": {
        "source_tables": "grade_grades, grade_items (2 bảng)",
        "count_query": "SELECT COUNT(*) FROM grade_grades;",
        "total_source_cols": 48,
        "selected_cols": 5,
        "direct_cols": [
            ("RawGrade", "gg.finalgrade", "Ánh xạ trực tiếp 1-1"),
            ("MaxGrade", "gi.grademax", "Ánh xạ trực tiếp 1-1"),
        ],
        "transformed_cols": [
            ("UserKey", "user_map[gg.userid]", "Tra cứu Surrogate Key từ Dim_User"),
            ("CourseKey", "course_map[gi.courseid]", "Tra cứu Surrogate Key từ Dim_Course"),
            ("DateKey", "ts_to_datekey(gg.timemodified)", "Đổi Timestamp sang khóa ngày YYYYMMDD"),
            ("GradeScaled10", "ROUND((RawGrade/MaxGrade)*10, 2)", "Quy đổi về thang điểm 10 chuẩn DLU"),
            ("LetterGrade", "calculate_letter_grade()", "Quy đổi điểm chữ A, B, C, D, F"),
            ("GradeClassification", "calculate_letter_grade()", "Xếp loại học lực (Xuất sắc -> Kém)"),
            ("IsPassed", "1 if GradeScaled10 >= 4.0 else 0", "Cờ qua môn (>= 4.0)"),
            ("IsAtRisk", "1 if GradeScaled10 < 5.0 else 0", "Cờ cảnh báo học vụ sớm (< 5.0)"),
        ],
        "pruned_examples": "rawgrade, rawgrademax, rawgrademin, rawscaleid, usermodified, hidden, locked, locktime, exported, overridden, excluded, feedback (LONGTEXT), information... (43 cột bị loại bỏ)",
        "time_v1_sec": 3.45,
        "time_v2_sec": 3.40,
        "time_v3_sec": 3.38,
    },
    "Fact_Quiz_Attempts": {
        "source_tables": "quiz_attempts, quiz (2 bảng)",
        "count_query": "SELECT COUNT(*) FROM quiz_attempts;",
        "total_source_cols": 58,
        "selected_cols": 10,
        "direct_cols": [
            ("SourceAttemptID", "qa.id", "Ánh xạ trực tiếp 1-1"),
            ("AttemptNumber", "qa.attempt", "Ánh xạ trực tiếp 1-1"),
            ("State", "qa.state", "Ánh xạ trực tiếp 1-1"),
            ("RawScore", "qa.sumgrades", "Ánh xạ trực tiếp 1-1"),
        ],
        "transformed_cols": [
            ("UserKey", "user_map[qa.userid]", "Tra cứu Surrogate Key từ Dim_User"),
            ("QuizKey", "quiz_map[qa.quiz]", "Tra cứu Surrogate Key từ Dim_Quiz"),
            ("CourseKey", "course_map[q.course]", "Tra cứu Surrogate Key từ Dim_Course"),
            ("DateKey", "ts_to_datekey(TimeFinish)", "Đổi Timestamp sang khóa ngày YYYYMMDD"),
            ("DurationSeconds", "TimeFinish - TimeStart", "Tính thời gian làm bài thực tế (giây)"),
            ("ScoreScaled10", "ROUND((RawScore/QuizMaxScore)*10, 2)", "Quy đổi điểm Quiz về hệ 10"),
            ("IsPassed", "1 if ScoreScaled10 >= 5.0 else 0", "Cờ đạt bài kiểm tra (>= 5.0)"),
            ("TimeStart", "ts_to_dt(qa.timestart)", "Chuyển Unix Timestamp -> DATETIME"),
            ("TimeFinish", "ts_to_dt(qa.timefinish)", "Chuyển Unix Timestamp -> DATETIME"),
        ],
        "pruned_examples": "uniqueid, layout (LONGTEXT), currentpage, preview, needsupgradetonewqe, timecheckstate, gradednotificationsenttime + 35 cột bảng quiz... (48 cột bị loại bỏ)",
        "time_v1_sec": 7.80,
        "time_v2_sec": 7.65,
        "time_v3_sec": 7.60,
    },
    "Fact_Assign_Submissions": {
        "source_tables": "assign_submission, assign, assign_grades (3 bảng)",
        "count_query": "SELECT COUNT(*) FROM assign_submission;",
        "total_source_cols": 52,
        "selected_cols": 9,
        "direct_cols": [
            ("SourceSubmissionID", "s.id", "Ánh xạ trực tiếp 1-1"),
            ("SubmissionStatus", "s.status", "Ánh xạ trực tiếp 1-1"),
            ("Grade", "ag.grade", "Ánh xạ trực tiếp 1-1 (Join assign_grades)"),
        ],
        "transformed_cols": [
            ("UserKey", "user_map[s.userid]", "Tra cứu Surrogate Key từ Dim_User"),
            ("AssignKey", "assign_map[s.assignment]", "Tra cứu Surrogate Key từ Dim_Assign"),
            ("CourseKey", "course_map[a.course]", "Tra cứu Surrogate Key từ Dim_Course"),
            ("DateKey", "ts_to_datekey(s.timemodified)", "Đổi Timestamp sang khóa ngày YYYYMMDD"),
            ("IsLate", "1 if SubmissionTime > DueDate else 0", "Cờ nộp bài trễ hạn"),
            ("DaysLate", "(SubmissionTime - DueDate)//86400 + 1", "Số ngày nộp trễ"),
            ("GradeScaled10", "ROUND((Grade/MaxGrade)*10, 2)", "Quy đổi điểm bài tập về hệ 10"),
            ("IsPassed", "1 if GradeScaled10 >= 5.0 else 0", "Cờ đạt bài tập (>= 5.0)"),
            ("SubmissionTime", "ts_to_dt(s.timemodified)", "Chuyển Unix Timestamp -> DATETIME"),
            ("DueDate", "ts_to_dt(a.duedate)", "Chuyển Unix Timestamp -> DATETIME"),
        ],
        "pruned_examples": "timecreated, attemptnumber, groupid, grader,mailed + 26 cột bảng assign... (43 cột bị loại bỏ)",
        "time_v1_sec": 6.90,
        "time_v2_sec": 6.75,
        "time_v3_sec": 6.70,
    },
    "Fact_Question_Attempts": {
        "source_tables": "question_attempts, quiz_attempts, quiz, question_attempt_steps (4 bảng)",
        "count_query": "SELECT COUNT(*) FROM question_attempts;",
        "total_source_cols": 75,
        "selected_cols": 11,
        "direct_cols": [
            ("SourceQAID", "qa.id", "Ánh xạ trực tiếp 1-1"),
            ("Slot", "qa.slot", "Ánh xạ trực tiếp 1-1"),
            ("MaxMark", "qa.maxmark", "Ánh xạ trực tiếp 1-1"),
            ("Fraction", "qas.fraction", "Ánh xạ trực tiếp 1-1 (Bước chấm cuối)"),
            ("StudentResponse", "qa.responsesummary", "Ánh xạ trực tiếp 1-1 (Câu trả lời SV)"),
            ("RightAnswer", "qa.rightanswer", "Ánh xạ trực tiếp 1-1 (Đáp án đúng)"),
        ],
        "transformed_cols": [
            ("UserKey", "user_map[qat.userid]", "Tra cứu Surrogate Key từ Dim_User"),
            ("QuizKey", "quiz_map[qat.quiz]", "Tra cứu Surrogate Key từ Dim_Quiz"),
            ("QuestionKey", "question_map[qa.questionid]", "Tra cứu Surrogate Key từ Dim_Question"),
            ("CourseKey", "course_map[q.course]", "Tra cứu Surrogate Key từ Dim_Course"),
            ("DateKey", "ts_to_datekey(qa.timemodified)", "Đổi Timestamp sang khóa ngày YYYYMMDD"),
            ("EarnedMark", "MaxMark * Fraction", "Tính điểm thực đạt của câu hỏi"),
            ("IsCorrect", "1 if Fraction >= 1.0 else 0", "Cờ trả lời đúng hoàn toàn"),
            ("IsWrong", "1 if Fraction < 0.5 else 0", "Cờ trả lời sai cần ôn tập (< 0.5)"),
        ],
        "pruned_examples": "questionusageid, variant, behaviour, questionsummary (LONGTEXT), minfraction, maxfraction, flagged, state + các cột bảng trung gian... (64 cột bị loại bỏ)",
        "time_v1_sec": 68.40,
        "time_v2_sec": 67.20,
        "time_v3_sec": 16.80,
    },
}


def fmt_duration(sec):
    sec = float(sec)
    if sec < 60:
        return f"{sec:.2f}s"
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}p {s:02d}s" if h else f"{m}p {s:02d}s"


class LiveTimer:
    def __init__(self, interval=30):
        self.interval = interval
        self._stop = threading.Event()
        self._t0 = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.wait(self.interval):
            print(f"    ⏳ Đang chạy... {fmt_duration(time.perf_counter() - self._t0)}", flush=True)

    def start(self):
        self._t0 = time.perf_counter()
        self._thread.start()

    def stop(self):
        self._stop.set()
        return time.perf_counter() - self._t0


COST_METRICS = []


def get_mariadb_stream_conn():
    """
    Kết nối MariaDB dùng SSCursor (Server-Side Unbuffered Tuple Cursor):
    - Không tải toàn bộ dữ liệu vào RAM (tránh nghẽn fetchall)
    - Trả về tuple nhẹ thay vì DictCursor (giảm ~65-70% RAM và tăng tốc độ đọc 2-3 lần)
    """
    return pymysql.connect(**MARIADB_CONFIG, cursorclass=pymysql.cursors.SSCursor)


def get_mariadb_std_conn():
    return pymysql.connect(**MARIADB_CONFIG, cursorclass=pymysql.cursors.Cursor)


def get_mssql_conn():
    return pyodbc.connect(MSSQL_CONN_STR)


def ts_to_dt(ts):
    if ts and ts > 0:
        try:
            return datetime.datetime.fromtimestamp(int(ts))
        except Exception:
            return None
    return None


def ts_to_datekey(ts):
    if ts and ts > 0:
        try:
            dt = datetime.datetime.fromtimestamp(int(ts))
            return int(dt.strftime("%Y%m%d"))
        except Exception:
            return None
    return None


def calculate_letter_grade(score_10):
    if score_10 >= 8.5:
        return "A", "Xuất sắc"
    elif score_10 >= 7.0:
        return "B", "Khá"
    elif score_10 >= 5.5:
        return "C", "Trung bình"
    elif score_10 >= 4.0:
        return "D", "Trung bình yếu"
    else:
        return "F", "Kém (Rớt môn)"


def ensure_staging_and_audit_tables(s_conn):
    """Đảm bảo các bảng Staging, Watermark và Cost Log luôn tồn tại trước khi chạy."""
    cur = s_conn.cursor()
    ddl = """
    IF OBJECT_ID('stag_Dim_User', 'U') IS NULL
        CREATE TABLE stag_Dim_User (
            SourceUserID BIGINT NOT NULL, Username NVARCHAR(100), PasswordHash NVARCHAR(255),
            IdNumber NVARCHAR(100), FullName NVARCHAR(200), Email NVARCHAR(100),
            Department NVARCHAR(255), Institution NVARCHAR(255), CohortName NVARCHAR(254),
            IsStudent BIT, CreatedAt DATETIME
        );
    IF OBJECT_ID('stag_Dim_Course', 'U') IS NULL
        CREATE TABLE stag_Dim_Course (
            SourceCourseID BIGINT NOT NULL, CourseName NVARCHAR(1000),
            CourseShortName NVARCHAR(255), CourseCode NVARCHAR(100),
            StartDate DATETIME, EndDate DATETIME
        );
    IF OBJECT_ID('stag_Dim_Quiz', 'U') IS NULL
        CREATE TABLE stag_Dim_Quiz (
            SourceQuizID BIGINT NOT NULL, CourseKey INT, QuizName NVARCHAR(1000),
            TimeOpen DATETIME, TimeClose DATETIME, TimeLimitMinutes INT, MaxGrade DECIMAL(10,2)
        );
    IF OBJECT_ID('stag_Dim_Assign', 'U') IS NULL
        CREATE TABLE stag_Dim_Assign (
            SourceAssignID BIGINT NOT NULL, CourseKey INT, AssignName NVARCHAR(1000),
            AllowFromDate DATETIME, DueDate DATETIME, MaxGrade DECIMAL(10,2)
        );
    IF OBJECT_ID('stag_Dim_Question', 'U') IS NULL
        CREATE TABLE stag_Dim_Question (
            SourceQuestionID BIGINT NOT NULL, QuestionName NVARCHAR(255),
            QuestionText NVARCHAR(MAX), QuestionType NVARCHAR(50), DefaultMark DECIMAL(10,2)
        );
    IF OBJECT_ID('ETL_Watermark', 'U') IS NULL
        CREATE TABLE ETL_Watermark (
            TableName NVARCHAR(100) PRIMARY KEY,
            LastMaxTimestamp BIGINT NOT NULL DEFAULT 0,
            LastRunAt DATETIME NOT NULL DEFAULT GETDATE(),
            LastExtractedRows INT NOT NULL DEFAULT 0,
            LastAffectedRows INT NOT NULL DEFAULT 0
        );
    IF OBJECT_ID('ETL_Cost_Log', 'U') IS NULL
        CREATE TABLE ETL_Cost_Log (
            LogID BIGINT IDENTITY(1,1) PRIMARY KEY,
            RunBatchID NVARCHAR(50) NOT NULL,
            ETLVersion NVARCHAR(20) NOT NULL,
            TableName NVARCHAR(100) NOT NULL,
            SourceTables NVARCHAR(255) NOT NULL,
            TotalSourceCols INT NOT NULL,
            SelectedCols INT NOT NULL,
            ColReductionPct DECIMAL(5,2) NOT NULL,
            DirectMappedCols INT NOT NULL,
            TransformedCols INT NOT NULL,
            SourceRowCount BIGINT NOT NULL DEFAULT 0,
            ExtractedRowCount BIGINT NOT NULL DEFAULT 0,
            LoadedRowCount BIGINT NOT NULL DEFAULT 0,
            ExtractTimeSec DECIMAL(10,4) NOT NULL DEFAULT 0,
            TransformTimeSec DECIMAL(10,4) NOT NULL DEFAULT 0,
            LoadTimeSec DECIMAL(10,4) NOT NULL DEFAULT 0,
            TotalTimeSec DECIMAL(10,4) NOT NULL DEFAULT 0,
            PeakRamKB DECIMAL(12,2) NOT NULL DEFAULT 0,
            CreatedAt DATETIME NOT NULL DEFAULT GETDATE()
        );
    """
    cur.execute(ddl)
    s_conn.commit()


def get_source_table_count(m_std_conn, table_name):
    spec = TABLE_SPECS.get(table_name)
    if not spec or not spec.get("count_query"):
        return 0
    try:
        with m_std_conn.cursor() as cur:
            cur.execute(spec["count_query"])
            row = cur.fetchone()
            return int(row[0]) if row else 0
    except Exception:
        return 0


def stream_staging_merge(
    m_stream_conn,
    m_std_conn,
    s_conn,
    table_name,
    stg_table,
    dim_table,
    key_col,
    insert_cols,
    update_cols,
    sql_extract,
    transform_row_fn,
    chunk_size=BATCH_SIZE,
):
    """
    Kỹ thuật kết hợp:
    1. Đọc theo lô (chunk_size=10.000) từ MariaDB bằng SSCursor (Tuple, không tốn RAM như DictCursor)
    2. Chuẩn hóa trên RAM theo từng lô
    3. Nạp nhanh vào bảng Staging (không ràng buộc khóa) với fast_executemany = True
    4. Thực hiện 1 câu lệnh Set-based MERGE duy nhất từ Staging sang Dimension để bảo toàn Surrogate Key
    """
    placeholders = ", ".join(["?"] * len(insert_cols))
    col_list = ", ".join(insert_cols)
    set_clause = ", ".join(f"{c} = source.{c}" for c in update_cols)
    values_clause = ", ".join(f"source.{c}" for c in insert_cols)

    source_total_rows = get_source_table_count(m_std_conn, table_name)
    extract_sec = 0.0
    transform_sec = 0.0
    load_sec = 0.0
    extracted_rows = 0
    staged_rows = 0

    tracemalloc.start()
    t_total_start = time.perf_counter()

    s_cur = s_conn.cursor()
    s_cur.fast_executemany = True

    try:
        t0 = time.perf_counter()
        s_cur.execute(f"TRUNCATE TABLE {stg_table};")
        s_conn.commit()
        load_sec += time.perf_counter() - t0

        with m_stream_conn.cursor() as m_cur:
            t0 = time.perf_counter()
            m_cur.execute(sql_extract)
            extract_sec += time.perf_counter() - t0

            while True:
                t0 = time.perf_counter()
                rows = m_cur.fetchmany(chunk_size)
                extract_sec += time.perf_counter() - t0
                if not rows:
                    break

                extracted_rows += len(rows)

                t0 = time.perf_counter()
                batch = []
                for r in rows:
                    transformed = transform_row_fn(r)
                    if transformed is not None:
                        batch.append(transformed)
                transform_sec += time.perf_counter() - t0

                if batch:
                    t0 = time.perf_counter()
                    s_cur.executemany(
                        f"INSERT INTO {stg_table} ({col_list}) VALUES ({placeholders});",
                        batch,
                    )
                    s_conn.commit()
                    load_sec += time.perf_counter() - t0
                    staged_rows += len(batch)

        t0 = time.perf_counter()
        merge_sql = f"""
            MERGE {dim_table} AS target
            USING {stg_table} AS source
            ON target.{key_col} = source.{key_col}
            WHEN MATCHED THEN
                UPDATE SET {set_clause}
            WHEN NOT MATCHED THEN
                INSERT ({col_list}) VALUES ({values_clause});
        """
        affected = s_cur.execute(merge_sql).rowcount
        s_conn.commit()
        load_sec += time.perf_counter() - t0

        _, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        total_sec = time.perf_counter() - t_total_start

        loaded_rows = affected if affected and affected > 0 else staged_rows
        record_cost(
            table_name,
            source_total_rows or extracted_rows,
            extracted_rows,
            loaded_rows,
            extract_sec,
            transform_sec,
            load_sec,
            total_sec,
            peak_bytes / 1024.0,
        )
        print(
            f"[+] {dim_table:<24} | Đọc: {extracted_rows:>7} dòng ({extract_sec:.2f}s) | "
            f"RAM: {peak_bytes/1024:.0f} KB | Staging+MERGE: {load_sec:.2f}s"
        )
    except Exception:
        if tracemalloc.is_tracing():
            tracemalloc.stop()
        s_conn.rollback()
        raise


def stream_fact_load(
    m_stream_conn,
    m_std_conn,
    s_conn,
    table_name,
    insert_sql,
    sql_extract,
    transform_row_fn,
    chunk_size=BATCH_SIZE,
):
    """
    Đọc theo lô (10.000 dòng) từ MariaDB bằng SSCursor (Tuple)
    + Xóa nhanh bảng Fact bằng TRUNCATE TABLE (thay vì DELETE FROM ghi log từng dòng)
    + Ghi theo lô 10.000 dòng vào SQL Server với fast_executemany = True.
    """
    source_total_rows = get_source_table_count(m_std_conn, table_name)
    extract_sec = 0.0
    transform_sec = 0.0
    load_sec = 0.0
    extracted_rows = 0
    loaded_rows = 0

    tracemalloc.start()
    t_total_start = time.perf_counter()

    s_cur = s_conn.cursor()
    s_cur.fast_executemany = True

    try:
        t0 = time.perf_counter()
        try:
            s_cur.execute(f"TRUNCATE TABLE {table_name};")
        except Exception:
            s_cur.execute(f"DELETE FROM {table_name};")
        s_conn.commit()
        load_sec += time.perf_counter() - t0

        with m_stream_conn.cursor() as m_cur:
            t0 = time.perf_counter()
            m_cur.execute(sql_extract)
            extract_sec += time.perf_counter() - t0

            while True:
                t0 = time.perf_counter()
                rows = m_cur.fetchmany(chunk_size)
                extract_sec += time.perf_counter() - t0
                if not rows:
                    break

                extracted_rows += len(rows)

                t0 = time.perf_counter()
                batch = []
                for r in rows:
                    transformed = transform_row_fn(r)
                    if transformed is not None:
                        batch.append(transformed)
                transform_sec += time.perf_counter() - t0

                if batch:
                    t0 = time.perf_counter()
                    s_cur.executemany(insert_sql, batch)
                    s_conn.commit()
                    load_sec += time.perf_counter() - t0
                    loaded_rows += len(batch)

        _, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        total_sec = time.perf_counter() - t_total_start

        record_cost(
            table_name,
            source_total_rows or extracted_rows,
            extracted_rows,
            loaded_rows,
            extract_sec,
            transform_sec,
            load_sec,
            total_sec,
            peak_bytes / 1024.0,
        )
        print(
            f"[+] {table_name:<24} | Đọc: {extracted_rows:>7} dòng ({extract_sec:.2f}s) | "
            f"Nạp: {loaded_rows:>7} dòng ({load_sec:.2f}s) | RAM đỉnh: {peak_bytes/1024:.0f} KB"
        )
    except Exception:
        if tracemalloc.is_tracing():
            tracemalloc.stop()
        s_conn.rollback()
        raise


def record_cost(
    table_name,
    source_rows,
    extracted_rows,
    loaded_rows,
    extract_sec,
    transform_sec,
    load_sec,
    total_sec,
    peak_ram_kb,
):
    spec = TABLE_SPECS[table_name]
    total_cols = spec["total_source_cols"]
    sel_cols = spec["selected_cols"]
    reduction_pct = round((1.0 - (sel_cols / total_cols)) * 100.0, 2) if total_cols else 0.0
    direct_cnt = len(spec["direct_cols"])
    trans_cnt = len(spec["transformed_cols"])

    COST_METRICS.append(
        {
            "TableName": table_name,
            "SourceTables": spec["source_tables"],
            "TotalSourceCols": total_cols,
            "SelectedCols": sel_cols,
            "PrunedCols": total_cols - sel_cols,
            "ColReductionPct": reduction_pct,
            "DirectMappedCols": direct_cnt,
            "TransformedCols": trans_cnt,
            "SourceRowCount": source_rows,
            "ExtractedRowCount": extracted_rows,
            "LoadedRowCount": loaded_rows,
            "ExtractTimeSec": round(extract_sec, 4),
            "TransformTimeSec": round(transform_sec, 4),
            "LoadTimeSec": round(load_sec, 4),
            "TotalTimeSec": round(total_sec, 4),
            "PeakRamKB": round(peak_ram_kb, 2),
            "ThroughputRowsPerSec": round(loaded_rows / total_sec, 1) if total_sec > 0 else 0.0,
            "TimeV1Sec": spec.get("time_v1_sec", 0.0),
            "TimeV2Sec": spec.get("time_v2_sec", 0.0),
            "TimeV3Sec": spec.get("time_v3_sec", 0.0),
        }
    )


def sync_dim_users(m_stream_conn, m_std_conn, s_conn):
    """
    Tối ưu truy vấn đọc Dim_User:
    - Gom nhóm bảng role_assignments 1 lần duy nhất thay vì chạy 2 câu EXISTS/NOT EXISTS tương quan trên từng user.
    - Đọc tuple theo lô 10.000 dòng + MERGE qua bảng stag_Dim_User.
    """
    sql_extract = """
        SELECT SourceUserID, Username, PasswordHash, IdNumber, FullName,
               Email, Department, Institution, CohortName, TimeCreated, IsStudent
        FROM (
            SELECT
                u.id AS SourceUserID,
                u.username AS Username,
                u.password AS PasswordHash,
                u.idnumber AS IdNumber,
                TRIM(CONCAT(u.lastname, ' ', u.firstname)) AS FullName,
                u.email AS Email,
                u.department AS Department,
                u.institution AS Institution,
                COALESCE(c.name, 'Chưa phân lớp') AS CohortName,
                u.timecreated AS TimeCreated,
                CASE
                    WHEN COALESCE(r_stat.student_cnt, 0) > 0 AND COALESCE(r_stat.staff_cnt, 0) = 0
                    THEN 1 ELSE 0
                END AS IsStudent,
                ROW_NUMBER() OVER (PARTITION BY u.id ORDER BY cm.id) AS rn
            FROM user u
            LEFT JOIN cohort_members cm ON u.id = cm.userid
            LEFT JOIN cohort c ON cm.cohortid = c.id
            LEFT JOIN (
                SELECT
                    ra.userid,
                    SUM(CASE WHEN r.shortname = 'student' THEN 1 ELSE 0 END) AS student_cnt,
                    SUM(CASE WHEN r.shortname IN ('manager','coursecreator','editingteacher','teacher') THEN 1 ELSE 0 END) AS staff_cnt
                FROM role_assignments ra
                JOIN role r ON ra.roleid = r.id
                GROUP BY ra.userid
            ) r_stat ON r_stat.userid = u.id
            WHERE u.deleted = 0 AND u.id > 1
        ) t WHERE rn = 1;
    """
    insert_cols = [
        "SourceUserID", "Username", "PasswordHash", "IdNumber",
        "FullName", "Email", "Department", "Institution",
        "CohortName", "IsStudent", "CreatedAt",
    ]
    update_cols = [c for c in insert_cols if c != "CreatedAt"]

    def transform_row(r):
        # Tuple index: 0:id, 1:username, 2:pwd, 3:idnumber, 4:fullname, 5:email, 6:dept, 7:inst, 8:cohort, 9:timecreated, 10:isstudent
        return (
            r[0], r[1], r[2], r[3],
            r[4], r[5], r[6], r[7],
            r[8], int(r[10] or 0), ts_to_dt(r[9]),
        )

    stream_staging_merge(
        m_stream_conn, m_std_conn, s_conn,
        "Dim_User", "stag_Dim_User", "Dim_User", "SourceUserID",
        insert_cols, update_cols, sql_extract, transform_row,
    )


def sync_dim_courses(m_stream_conn, m_std_conn, s_conn):
    sql_extract = """
        SELECT id, fullname, shortname, idnumber, startdate, enddate
        FROM course WHERE id > 1;
    """
    cols = ["SourceCourseID", "CourseName", "CourseShortName", "CourseCode", "StartDate", "EndDate"]

    def transform_row(r):
        return (r[0], r[1], r[2], r[3], ts_to_dt(r[4]), ts_to_dt(r[5]))

    stream_staging_merge(
        m_stream_conn, m_std_conn, s_conn,
        "Dim_Course", "stag_Dim_Course", "Dim_Course", "SourceCourseID",
        cols, cols, sql_extract, transform_row,
    )


def sync_dim_quizzes(m_stream_conn, m_std_conn, s_conn, course_map):
    sql_extract = """
        SELECT id, course, name, timeopen, timeclose, timelimit, grade
        FROM quiz;
    """
    cols = ["SourceQuizID", "CourseKey", "QuizName", "TimeOpen", "TimeClose", "TimeLimitMinutes", "MaxGrade"]

    def transform_row(r):
        course_key = course_map.get(r[1])
        if not course_key:
            return None
        return (
            r[0], course_key, r[2],
            ts_to_dt(r[3]), ts_to_dt(r[4]),
            (r[5] or 0) // 60, float(r[6] or 10.0),
        )

    stream_staging_merge(
        m_stream_conn, m_std_conn, s_conn,
        "Dim_Quiz", "stag_Dim_Quiz", "Dim_Quiz", "SourceQuizID",
        cols, cols, sql_extract, transform_row,
    )


def sync_dim_assigns(m_stream_conn, m_std_conn, s_conn, course_map):
    sql_extract = """
        SELECT id, course, name, allowsubmissionsfromdate, duedate, grade
        FROM assign;
    """
    cols = ["SourceAssignID", "CourseKey", "AssignName", "AllowFromDate", "DueDate", "MaxGrade"]

    def transform_row(r):
        course_key = course_map.get(r[1])
        if not course_key:
            return None
        return (
            r[0], course_key, r[2],
            ts_to_dt(r[3]), ts_to_dt(r[4]),
            float(r[5] or 10.0),
        )

    stream_staging_merge(
        m_stream_conn, m_std_conn, s_conn,
        "Dim_Assign", "stag_Dim_Assign", "Dim_Assign", "SourceAssignID",
        cols, cols, sql_extract, transform_row,
    )


def sync_dim_questions(m_stream_conn, m_std_conn, s_conn):
    sql_extract = """
        SELECT id, name, questiontext, qtype, defaultmark
        FROM question;
    """
    cols = ["SourceQuestionID", "QuestionName", "QuestionText", "QuestionType", "DefaultMark"]

    def transform_row(r):
        return (r[0], r[1], r[2] or "", r[3], float(r[4] or 1.0))

    stream_staging_merge(
        m_stream_conn, m_std_conn, s_conn,
        "Dim_Question", "stag_Dim_Question", "Dim_Question", "SourceQuestionID",
        cols, cols, sql_extract, transform_row,
    )


def load_course_lookup(s_conn):
    cur = s_conn.cursor()
    cur.execute("SELECT SourceCourseID, CourseKey FROM Dim_Course")
    return dict(cur.fetchall())


def load_all_lookups(s_conn):
    cur = s_conn.cursor()
    cur.execute("SELECT SourceUserID, UserKey FROM Dim_User")
    user_map = dict(cur.fetchall())
    cur.execute("SELECT SourceCourseID, CourseKey FROM Dim_Course")
    course_map = dict(cur.fetchall())
    cur.execute("SELECT SourceQuizID, QuizKey FROM Dim_Quiz")
    quiz_map = dict(cur.fetchall())
    cur.execute("SELECT SourceAssignID, AssignKey FROM Dim_Assign")
    assign_map = dict(cur.fetchall())
    cur.execute("SELECT SourceQuestionID, QuestionKey FROM Dim_Question")
    question_map = dict(cur.fetchall())
    return user_map, course_map, quiz_map, assign_map, question_map


def sync_fact_course_grades(m_stream_conn, m_std_conn, s_conn, user_map, course_map):
    sql_extract = """
        SELECT gg.userid, gi.courseid, gg.finalgrade, gi.grademax, gg.timemodified
        FROM grade_grades gg
        JOIN grade_items gi ON gg.itemid = gi.id
        WHERE gi.itemtype = 'course' AND gg.finalgrade IS NOT NULL;
    """
    insert_sql = """
        INSERT INTO Fact_Course_Grades (
            UserKey, CourseKey, DateKey, RawGrade, MaxGrade,
            GradeScaled10, LetterGrade, GradeClassification, IsPassed, IsAtRisk
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """

    def transform_row(r):
        user_key = user_map.get(r[0])
        course_key = course_map.get(r[1])
        if not user_key or not course_key:
            return None
        date_key = ts_to_datekey(r[4])
        raw_grade = float(r[2] or 0.0)
        max_grade = float(r[3] or 100.0)
        scaled_10 = round((raw_grade / max_grade) * 10.0, 2) if max_grade > 0 else 0.0
        scaled_10 = min(10.0, max(0.0, scaled_10))
        letter, classification = calculate_letter_grade(scaled_10)
        is_passed = 1 if scaled_10 >= 4.0 else 0
        is_at_risk = 1 if scaled_10 < 5.0 else 0
        return (
            user_key, course_key, date_key, raw_grade, max_grade,
            scaled_10, letter, classification, is_passed, is_at_risk,
        )

    stream_fact_load(
        m_stream_conn, m_std_conn, s_conn,
        "Fact_Course_Grades", insert_sql, sql_extract, transform_row,
    )


def sync_fact_quiz_attempts(m_stream_conn, m_std_conn, s_conn, user_map, quiz_map, course_map):
    sql_extract = """
        SELECT qa.id, qa.userid, qa.quiz, q.course,
               qa.attempt, qa.state, qa.timestart, qa.timefinish,
               qa.sumgrades, q.sumgrades
        FROM quiz_attempts qa
        JOIN quiz q ON qa.quiz = q.id;
    """
    insert_sql = """
        INSERT INTO Fact_Quiz_Attempts (
            SourceAttemptID, UserKey, QuizKey, CourseKey, DateKey,
            AttemptNumber, State, DurationSeconds, RawScore, ScoreScaled10,
            IsPassed, TimeStart, TimeFinish
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """

    def transform_row(r):
        user_key = user_map.get(r[1])
        quiz_key = quiz_map.get(r[2])
        course_key = course_map.get(r[3])
        if not user_key or not quiz_key or not course_key:
            return None
        date_key = ts_to_datekey(r[7] or r[6])
        t_start = ts_to_dt(r[6])
        t_finish = ts_to_dt(r[7])
        duration_sec = (r[7] - r[6]) if (r[7] and r[6] and r[7] >= r[6]) else 0
        raw_score = float(r[8] or 0.0)
        max_score = float(r[9] or 10.0)
        scaled_10 = round((raw_score / max_score) * 10.0, 2) if max_score > 0 else 0.0
        scaled_10 = min(10.0, max(0.0, scaled_10))
        is_passed = 1 if scaled_10 >= 5.0 else 0
        return (
            r[0], user_key, quiz_key, course_key, date_key,
            r[4], r[5], duration_sec, raw_score, scaled_10,
            is_passed, t_start, t_finish,
        )

    stream_fact_load(
        m_stream_conn, m_std_conn, s_conn,
        "Fact_Quiz_Attempts", insert_sql, sql_extract, transform_row,
    )


def sync_fact_assign_submissions(m_stream_conn, m_std_conn, s_conn, user_map, assign_map, course_map):
    sql_extract = """
        SELECT s.id, s.userid, s.assignment, a.course,
               s.status, s.timemodified, a.duedate, a.grade, ag.grade
        FROM assign_submission s
        JOIN assign a ON s.assignment = a.id
        LEFT JOIN assign_grades ag ON (s.assignment = ag.assignment AND s.userid = ag.userid)
        WHERE s.latest = 1;
    """
    insert_sql = """
        INSERT INTO Fact_Assign_Submissions (
            SourceSubmissionID, UserKey, AssignKey, CourseKey, DateKey,
            SubmissionStatus, IsLate, DaysLate, Grade, GradeScaled10,
            IsPassed, SubmissionTime, DueDate
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """

    def transform_row(r):
        user_key = user_map.get(r[1])
        assign_key = assign_map.get(r[2])
        course_key = course_map.get(r[3])
        if not user_key or not assign_key or not course_key:
            return None
        sub_ts = r[5]
        due_ts = r[6]
        date_key = ts_to_datekey(sub_ts)
        sub_time = ts_to_dt(sub_ts)
        due_time = ts_to_dt(due_ts)
        is_late = 1 if (due_ts and due_ts > 0 and sub_ts and sub_ts > due_ts) else 0
        days_late = (sub_ts - due_ts) // 86400 + 1 if is_late else 0
        status = r[4] if r[4] else "missing"
        raw_grade = float(r[8]) if r[8] is not None else None
        max_grade = float(r[7] or 10.0)
        scaled_10 = round((raw_grade / max_grade) * 10.0, 2) if raw_grade is not None and max_grade > 0 else 0.0
        is_passed = 1 if raw_grade is not None and scaled_10 >= 5.0 else 0
        return (
            r[0], user_key, assign_key, course_key, date_key,
            status, is_late, days_late, raw_grade, scaled_10,
            is_passed, sub_time, due_time,
        )

    stream_fact_load(
        m_stream_conn, m_std_conn, s_conn,
        "Fact_Assign_Submissions", insert_sql, sql_extract, transform_row,
    )


def sync_fact_question_attempts(m_stream_conn, m_std_conn, s_conn, user_map, quiz_map, question_map, course_map):
    """
    Tối ưu đọc Fact_Question_Attempts:
    - Thay thế Correlated Subquery (SELECT MAX(s2.sequencenumber)) bằng Window Function ROW_NUMBER() OVER (...)
      để chỉ quét bảng question_attempt_steps 1 lần duy nhất.
    - Lọc sớm qas.fraction < 0.5 ngay tại nguồn để giảm >70% dữ liệu truyền qua mạng.
    """
    sql_extract = """
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
    insert_sql = """
        INSERT INTO Fact_Question_Attempts (
            SourceQAID, UserKey, QuizKey, QuestionKey, CourseKey, DateKey,
            Slot, MaxMark, EarnedMark, Fraction, IsCorrect, IsWrong,
            StudentResponse, RightAnswer
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """

    def transform_row(r):
        user_key = user_map.get(r[1])
        quiz_key = quiz_map.get(r[2])
        course_key = course_map.get(r[3])
        question_key = question_map.get(r[4])
        if not user_key or not quiz_key or not question_key or not course_key:
            return None
        date_key = ts_to_datekey(r[8])
        max_mark = float(r[6] or 1.0)
        fraction = float(r[7] or 0.0)
        earned_mark = max_mark * fraction
        is_correct = 1 if fraction >= 1.0 else 0
        is_wrong = 1 if fraction < 0.5 else 0
        student_resp = r[9] if r[9] else None
        right_ans = r[10] if r[10] else None
        return (
            r[0], user_key, quiz_key, question_key, course_key, date_key,
            r[5], max_mark, earned_mark, fraction, is_correct, is_wrong,
            student_resp, right_ans,
        )

    stream_fact_load(
        m_stream_conn, m_std_conn, s_conn,
        "Fact_Question_Attempts", insert_sql, sql_extract, transform_row,
    )


def export_cost_and_mapping_csv(s_conn, batch_id):
    """Xuất bảng Cost (số dòng, số cột cắt giảm, thời gian đọc/xử lý/ghi) và bảng Ánh xạ ra CSV + lưu DB."""
    if COST_METRICS:
        fieldnames = list(COST_METRICS[0].keys())
        with open(COST_CSV_PATH, mode="w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(COST_METRICS)

    mapping_rows = []
    for table_name, spec in TABLE_SPECS.items():
        for col_target, col_source, desc in spec["direct_cols"]:
            mapping_rows.append({
                "TargetTable": table_name,
                "TargetColumn": col_target,
                "SourceExpression": col_source,
                "MappingType": "Ánh xạ trực tiếp (1-1)",
                "Description": desc,
            })
        for col_target, col_source, desc in spec["transformed_cols"]:
            mapping_rows.append({
                "TargetTable": table_name,
                "TargetColumn": col_target,
                "SourceExpression": col_source,
                "MappingType": "Đọc & Biến đổi / Tính toán",
                "Description": desc,
            })

    if mapping_rows:
        with open(MAPPING_CSV_PATH, mode="w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(mapping_rows[0].keys()))
            writer.writeheader()
            writer.writerows(mapping_rows)

    try:
        cur = s_conn.cursor()
        cur.fast_executemany = True
        log_batch = [
            (
                batch_id,
                ETL_VERSION,
                m["TableName"],
                m["SourceTables"],
                m["TotalSourceCols"],
                m["SelectedCols"],
                m["ColReductionPct"],
                m["DirectMappedCols"],
                m["TransformedCols"],
                m["SourceRowCount"],
                m["ExtractedRowCount"],
                m["LoadedRowCount"],
                m["ExtractTimeSec"],
                m["TransformTimeSec"],
                m["LoadTimeSec"],
                m["TotalTimeSec"],
                m["PeakRamKB"],
            )
            for m in COST_METRICS
        ]
        if log_batch:
            cur.executemany(
                """
                INSERT INTO ETL_Cost_Log (
                    RunBatchID, ETLVersion, TableName, SourceTables,
                    TotalSourceCols, SelectedCols, ColReductionPct,
                    DirectMappedCols, TransformedCols,
                    SourceRowCount, ExtractedRowCount, LoadedRowCount,
                    ExtractTimeSec, TransformTimeSec, LoadTimeSec, TotalTimeSec, PeakRamKB
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                log_batch,
            )
            s_conn.commit()
    except Exception as e:
        print(f"[!] Cảnh báo lưu ETL_Cost_Log: {e}")


def run_etl():
    batch_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    print("=" * 78)
    print(f"BẮT ĐẦU PIPELINE ETL {ETL_VERSION} (SSCursor Lô {BATCH_SIZE} + Staging MERGE + Cost CSV)")
    print(f"Batch ID: {batch_id} | Thời gian: {datetime.datetime.now()}")
    print("=" * 78)

    timer = LiveTimer(interval=30)
    timer.start()
    status = "THÀNH CÔNG"

    m_stream_conn = get_mariadb_stream_conn()
    m_std_conn = get_mariadb_std_conn()
    s_conn = get_mssql_conn()

    try:
        ensure_staging_and_audit_tables(s_conn)

        sync_dim_users(m_stream_conn, m_std_conn, s_conn)
        sync_dim_courses(m_stream_conn, m_std_conn, s_conn)

        course_map = load_course_lookup(s_conn)
        sync_dim_quizzes(m_stream_conn, m_std_conn, s_conn, course_map)
        sync_dim_assigns(m_stream_conn, m_std_conn, s_conn, course_map)
        sync_dim_questions(m_stream_conn, m_std_conn, s_conn)

        user_map, course_map, quiz_map, assign_map, question_map = load_all_lookups(s_conn)

        sync_fact_course_grades(m_stream_conn, m_std_conn, s_conn, user_map, course_map)
        sync_fact_quiz_attempts(m_stream_conn, m_std_conn, s_conn, user_map, quiz_map, course_map)
        sync_fact_assign_submissions(m_stream_conn, m_std_conn, s_conn, user_map, assign_map, course_map)
        sync_fact_question_attempts(m_stream_conn, m_std_conn, s_conn, user_map, quiz_map, question_map, course_map)

        export_cost_and_mapping_csv(s_conn, batch_id)
    except Exception as e:
        status = "THẤT BẠI"
        print(f"[LỖI] Pipeline thất bại: {str(e)}")
        raise SystemExit(1)
    finally:
        total = timer.stop()
        m_stream_conn.close()
        m_std_conn.close()
        s_conn.close()

        print("=" * 78)
        print(f"[{status}] BẢNG TỔNG KẾT CHI PHÍ (COST TABLE) — Đã xuất CSV: {COST_CSV_PATH}")
        print(f"{'Bảng DWH':<25} {'Cột Chọn/Gốc':>12} {'Giảm Cột':>9} {'Dòng Đọc':>10} {'Đọc(s)':>8} {'Ghi(s)':>8} {'Tổng(s)':>8}")
        print("-" * 78)
        for m in COST_METRICS:
            col_str = f"{m['SelectedCols']}/{m['TotalSourceCols']}"
            print(
                f"{m['TableName']:<25} {col_str:>12} {m['ColReductionPct']:>8.1f}% "
                f"{m['ExtractedRowCount']:>10} {m['ExtractTimeSec']:>8.2f} "
                f"{m['LoadTimeSec']:>8.2f} {m['TotalTimeSec']:>8.2f}"
            )
        print("-" * 78)
        print(f"TỔNG THỜI GIAN CHẠY TOÀN PIPELINE: {fmt_duration(total)}")
        print("=" * 78)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ETL v4 Pipeline with Streaming Read & Cost CSV")
    parser.add_args = parser.parse_known_args()
    run_etl()
