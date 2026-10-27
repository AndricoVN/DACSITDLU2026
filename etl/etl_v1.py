import sys
import datetime
import pymysql
import pyodbc
import time
import threading

def fmt_duration(sec):
    """Đổi số giây sang dạng dễ đọc: 940s -> '15p 40s'."""
    sec = float(sec)
    if sec < 60:
        return f"{sec:.1f}s"
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}p {s:02d}s" if h else f"{m}p {s:02d}s"

class LiveTimer:
    """Luồng nền: cứ mỗi `interval` giây in ra thời gian đã chạy."""
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

STEP_TIMES = []   # danh sách (tên bước, số giây)

def run_step(name, fn, *args):
    """Chạy một bước ETL và đo thời gian."""
    t0 = time.perf_counter()
    try:
        fn(*args)
    finally:
        dt = time.perf_counter() - t0
        STEP_TIMES.append((name, dt))
        print(f"    ⏱ {name}: {fmt_duration(dt)}", flush=True)
        
MARIADB_CONFIG = {
    "host": "mariadb",
    "port": 3306,
    "user": "moodleuser",
    "password": "moodlepassword",
    "database": "moodledb",
    "charset": "utf8mb4"
}

MSSQL_CONN_STR = (
    "DRIVER={ODBC Driver 18 for SQL Server};"
    "SERVER=sqlserver,1433;"
    "DATABASE=lms_datawarehouse;"
    "UID=sa;"
    "PWD=SuperStrongPass123!;"
    "TrustServerCertificate=yes;"
    "Connection Timeout=30;"
)

def get_mariadb_conn():
    return pymysql.connect(**MARIADB_CONFIG, cursorclass=pymysql.cursors.DictCursor)

def get_mssql_conn():
    return pyodbc.connect(MSSQL_CONN_STR)

def ts_to_dt(ts):
    if ts and ts > 0:
        try:
            return datetime.datetime.fromtimestamp(ts)
        except Exception:
            return None
    return None

def ts_to_datekey(ts):
    if ts and ts > 0:
        try:
            dt = datetime.datetime.fromtimestamp(ts)
            return int(dt.strftime("%Y%m%d"))
        except Exception:
            return None
    return None

def calculate_letter_grade(score_10):
    if score_10 >= 8.5:
        return 'A', 'Xuất sắc'
    elif score_10 >= 7.0:
        return 'B', 'Khá'
    elif score_10 >= 5.5:
        return 'C', 'Trung bình'
    elif score_10 >= 4.0:
        return 'D', 'Trung bình yếu'
    else:
        return 'F', 'Kém (Rớt môn)'

def sync_dim_users(m_conn, s_conn):
    print("[*] Đồng bộ Dim_User (Tài khoản & Lớp sinh hoạt DLU)...")
    sql_extract = """
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
                WHEN EXISTS (
                    SELECT 1 FROM role_assignments ra1 JOIN role r1 ON ra1.roleid = r1.id
                    WHERE ra1.userid = u.id AND r1.shortname = 'student'
                )
                AND NOT EXISTS (
                    SELECT 1 FROM role_assignments ra2 JOIN role r2 ON ra2.roleid = r2.id
                    WHERE ra2.userid = u.id AND r2.shortname IN ('manager','coursecreator','editingteacher','teacher')
                )
                THEN 1 ELSE 0
            END AS IsStudent
        FROM user u
        LEFT JOIN cohort_members cm ON u.id = cm.userid
        LEFT JOIN cohort c ON cm.cohortid = c.id
        WHERE u.deleted = 0 AND u.id > 1;
    """
    with m_conn.cursor() as cursor:
        cursor.execute(sql_extract)
        rows = cursor.fetchall()

    s_cursor = s_conn.cursor()
    for r in rows:
        created_at = ts_to_dt(r['TimeCreated'])
        s_cursor.execute("""
            MERGE Dim_User AS target
            USING (SELECT ? AS SourceUserID) AS source
            ON (target.SourceUserID = source.SourceUserID)
            WHEN MATCHED THEN
                UPDATE SET 
                    Username = ?, PasswordHash = ?, IdNumber = ?, FullName = ?, 
                    Email = ?, Department = ?, Institution = ?, CohortName = ?, IsStudent = ?
            WHEN NOT MATCHED THEN
                INSERT (SourceUserID, Username, PasswordHash, IdNumber, FullName, Email, Department, Institution, CohortName, CreatedAt, IsStudent)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            r['SourceUserID'],
            r['Username'], r['PasswordHash'], r['IdNumber'], r['FullName'], r['Email'], r['Department'], r['Institution'], r['CohortName'], int(r['IsStudent']),
            r['SourceUserID'], r['Username'], r['PasswordHash'], r['IdNumber'], r['FullName'], r['Email'], r['Department'], r['Institution'], r['CohortName'], created_at, int(r['IsStudent'])
        ))
    s_conn.commit()
    print(f"[+] Dim_User hoàn tất: {len(rows)} tài khoản.")

def sync_dim_courses(m_conn, s_conn):
    print("[*] Đồng bộ Dim_Course...")
    sql_extract = """
        SELECT id AS SourceCourseID, fullname AS CourseName, shortname AS CourseShortName, idnumber AS CourseCode, startdate AS StartDate, enddate AS EndDate
        FROM course WHERE id > 1;
    """
    with m_conn.cursor() as cursor:
        cursor.execute(sql_extract)
        rows = cursor.fetchall()

    s_cursor = s_conn.cursor()
    for r in rows:
        s_cursor.execute("""
            MERGE Dim_Course AS target
            USING (SELECT ? AS SourceCourseID) AS source
            ON (target.SourceCourseID = source.SourceCourseID)
            WHEN MATCHED THEN
                UPDATE SET CourseName = ?, CourseShortName = ?, CourseCode = ?, StartDate = ?, EndDate = ?
            WHEN NOT MATCHED THEN
                INSERT (SourceCourseID, CourseName, CourseShortName, CourseCode, StartDate, EndDate)
                VALUES (?, ?, ?, ?, ?, ?);
        """, (
            r['SourceCourseID'],
            r['CourseName'], r['CourseShortName'], r['CourseCode'], ts_to_dt(r['StartDate']), ts_to_dt(r['EndDate']),
            r['SourceCourseID'], r['CourseName'], r['CourseShortName'], r['CourseCode'], ts_to_dt(r['StartDate']), ts_to_dt(r['EndDate'])
        ))
    s_conn.commit()
    print(f"[+] Dim_Course hoàn tất: {len(rows)} môn học.")

def sync_dim_quizzes(m_conn, s_conn):
    print("[*] Đồng bộ Dim_Quiz...")
    s_cursor = s_conn.cursor()
    s_cursor.execute("SELECT SourceCourseID, CourseKey FROM Dim_Course")
    course_map = dict(s_cursor.fetchall())

    sql_extract = """
        SELECT id AS SourceQuizID, course AS SourceCourseID, name AS QuizName, timeopen AS TimeOpen, timeclose AS TimeClose, timelimit AS TimeLimitSeconds, grade AS MaxGrade
        FROM quiz;
    """
    with m_conn.cursor() as cursor:
        cursor.execute(sql_extract)
        rows = cursor.fetchall()

    for r in rows:
        course_key = course_map.get(r['SourceCourseID'])
        if not course_key:
            continue
        time_limit_min = (r['TimeLimitSeconds'] or 0) // 60
        max_grade = float(r['MaxGrade'] or 10.0)

        s_cursor.execute("""
            MERGE Dim_Quiz AS target
            USING (SELECT ? AS SourceQuizID) AS source
            ON (target.SourceQuizID = source.SourceQuizID)
            WHEN MATCHED THEN
                UPDATE SET CourseKey = ?, QuizName = ?, TimeOpen = ?, TimeClose = ?, TimeLimitMinutes = ?, MaxGrade = ?
            WHEN NOT MATCHED THEN
                INSERT (SourceQuizID, CourseKey, QuizName, TimeOpen, TimeClose, TimeLimitMinutes, MaxGrade)
                VALUES (?, ?, ?, ?, ?, ?, ?);
        """, (
            r['SourceQuizID'],
            course_key, r['QuizName'], ts_to_dt(r['TimeOpen']), ts_to_dt(r['TimeClose']), time_limit_min, max_grade,
            r['SourceQuizID'], course_key, r['QuizName'], ts_to_dt(r['TimeOpen']), ts_to_dt(r['TimeClose']), time_limit_min, max_grade
        ))
    s_conn.commit()
    print(f"[+] Dim_Quiz hoàn tất: {len(rows)} bài trắc nghiệm.")

def sync_dim_assigns(m_conn, s_conn):
    print("[*] Đồng bộ Dim_Assign...")
    s_cursor = s_conn.cursor()
    s_cursor.execute("SELECT SourceCourseID, CourseKey FROM Dim_Course")
    course_map = dict(s_cursor.fetchall())

    sql_extract = """
        SELECT id AS SourceAssignID, course AS SourceCourseID, name AS AssignName, allowsubmissionsfromdate AS AllowFromDate, duedate AS DueDate, grade AS MaxGrade
        FROM assign;
    """
    with m_conn.cursor() as cursor:
        cursor.execute(sql_extract)
        rows = cursor.fetchall()

    for r in rows:
        course_key = course_map.get(r['SourceCourseID'])
        if not course_key:
            continue
        max_grade = float(r['MaxGrade'] or 10.0)

        s_cursor.execute("""
            MERGE Dim_Assign AS target
            USING (SELECT ? AS SourceAssignID) AS source
            ON (target.SourceAssignID = source.SourceAssignID)
            WHEN MATCHED THEN
                UPDATE SET CourseKey = ?, AssignName = ?, AllowFromDate = ?, DueDate = ?, MaxGrade = ?
            WHEN NOT MATCHED THEN
                INSERT (SourceAssignID, CourseKey, AssignName, AllowFromDate, DueDate, MaxGrade)
                VALUES (?, ?, ?, ?, ?, ?);
        """, (
            r['SourceAssignID'],
            course_key, r['AssignName'], ts_to_dt(r['AllowFromDate']), ts_to_dt(r['DueDate']), max_grade,
            r['SourceAssignID'], course_key, r['AssignName'], ts_to_dt(r['AllowFromDate']), ts_to_dt(r['DueDate']), max_grade
        ))
    s_conn.commit()
    print(f"[+] Dim_Assign hoàn tất: {len(rows)} bài tập nộp.")

def sync_dim_questions(m_conn, s_conn):
    print("[*] Đồng bộ Dim_Question...")
    sql_extract = "SELECT id AS SourceQuestionID, name AS QuestionName, questiontext AS QuestionText, qtype AS QuestionType, defaultmark AS DefaultMark FROM question;"
    with m_conn.cursor() as cursor:
        cursor.execute(sql_extract)
        rows = cursor.fetchall()

    s_cursor = s_conn.cursor()
    for r in rows:
        s_cursor.execute("""
            MERGE Dim_Question AS target
            USING (SELECT ? AS SourceQuestionID) AS source
            ON (target.SourceQuestionID = source.SourceQuestionID)
            WHEN MATCHED THEN
                UPDATE SET QuestionName = ?, QuestionText = ?, QuestionType = ?, DefaultMark = ?
            WHEN NOT MATCHED THEN
                INSERT (SourceQuestionID, QuestionName, QuestionText, QuestionType, DefaultMark)
                VALUES (?, ?, ?, ?, ?);
        """, (
            r['SourceQuestionID'],
            r['QuestionName'], r['QuestionText'] or '', r['QuestionType'], float(r['DefaultMark'] or 1.0),
            r['SourceQuestionID'], r['QuestionName'], r['QuestionText'] or '', r['QuestionType'], float(r['DefaultMark'] or 1.0)
        ))
    s_conn.commit()
    print(f"[+] Dim_Question hoàn tất: {len(rows)} câu hỏi.")

def load_lookups(s_conn):
    s_cursor = s_conn.cursor()
    s_cursor.execute("SELECT SourceUserID, UserKey FROM Dim_User")
    user_map = dict(s_cursor.fetchall())
    s_cursor.execute("SELECT SourceCourseID, CourseKey FROM Dim_Course")
    course_map = dict(s_cursor.fetchall())
    s_cursor.execute("SELECT SourceQuizID, QuizKey FROM Dim_Quiz")
    quiz_map = dict(s_cursor.fetchall())
    s_cursor.execute("SELECT SourceAssignID, AssignKey FROM Dim_Assign")
    assign_map = dict(s_cursor.fetchall())
    s_cursor.execute("SELECT SourceQuestionID, QuestionKey FROM Dim_Question")
    question_map = dict(s_cursor.fetchall())
    return user_map, course_map, quiz_map, assign_map, question_map

def sync_fact_course_grades(m_conn, s_conn, user_map, course_map):
    print("[*] Đồng bộ Fact_Course_Grades...")
    sql_extract = """
        SELECT gg.userid AS SourceUserID, gi.courseid AS SourceCourseID, gg.finalgrade AS RawGrade, gi.grademax AS MaxGrade, gg.timemodified AS TimeModified
        FROM grade_grades gg
        JOIN grade_items gi ON gg.itemid = gi.id
        WHERE gi.itemtype = 'course' AND gg.finalgrade IS NOT NULL;
    """
    with m_conn.cursor() as cursor:
        cursor.execute(sql_extract)
        rows = cursor.fetchall()

    s_cursor = s_conn.cursor()
    s_cursor.fast_executemany = True
    s_cursor.execute("DELETE FROM Fact_Course_Grades;")

    insert_sql = """
        INSERT INTO Fact_Course_Grades (
            UserKey, CourseKey, DateKey, RawGrade, MaxGrade,
            GradeScaled10, LetterGrade, GradeClassification, IsPassed, IsAtRisk
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """

    insert_batch = []
    total = 0
    for r in rows:
        user_key = user_map.get(r['SourceUserID'])
        course_key = course_map.get(r['SourceCourseID'])
        if not user_key or not course_key:
            continue

        date_key = ts_to_datekey(r['TimeModified'])
        raw_grade = float(r['RawGrade'] or 0.0)
        max_grade = float(r['MaxGrade'] or 100.0)

        grade_scaled_10 = round((raw_grade / max_grade) * 10.0, 2) if max_grade > 0 else 0.0
        grade_scaled_10 = min(10.0, max(0.0, grade_scaled_10))
        letter_grade, classification = calculate_letter_grade(grade_scaled_10)

        is_passed = 1 if grade_scaled_10 >= 4.0 else 0
        is_at_risk = 1 if grade_scaled_10 < 5.0 else 0

        insert_batch.append((
            user_key, course_key, date_key, raw_grade, max_grade,
            grade_scaled_10, letter_grade, classification, is_passed, is_at_risk
        ))

        if len(insert_batch) >= 10000:
            s_cursor.executemany(insert_sql, insert_batch)
            s_conn.commit()
            total += len(insert_batch)
            insert_batch.clear()

    if insert_batch:
        s_cursor.executemany(insert_sql, insert_batch)
        s_conn.commit()
        total += len(insert_batch)
    s_conn.commit()
    print(f"[+] Fact_Course_Grades hoàn tất: {total} bảng điểm môn.")

def sync_fact_quiz_attempts(m_conn, s_conn, user_map, quiz_map, course_map):
    print("[*] Đồng bộ Fact_Quiz_Attempts...")
    sql_extract = """
        SELECT qa.id AS SourceAttemptID, qa.userid AS SourceUserID, qa.quiz AS SourceQuizID, q.course AS SourceCourseID,
               qa.attempt AS AttemptNumber, qa.state AS State, qa.timestart AS TimeStart, qa.timefinish AS TimeFinish,
               qa.sumgrades AS RawScore, q.sumgrades AS QuizMaxScore
        FROM quiz_attempts qa
        JOIN quiz q ON qa.quiz = q.id;
    """
    with m_conn.cursor() as cursor:
        cursor.execute(sql_extract)
        rows = cursor.fetchall()

    s_cursor = s_conn.cursor()
    s_cursor.fast_executemany = True
    s_cursor.execute("DELETE FROM Fact_Quiz_Attempts;")

    insert_sql = """
        INSERT INTO Fact_Quiz_Attempts (
            SourceAttemptID, UserKey, QuizKey, CourseKey, DateKey,
            AttemptNumber, State, DurationSeconds, RawScore, ScoreScaled10,
            IsPassed, TimeStart, TimeFinish
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """

    insert_batch = []
    total = 0
    for r in rows:
        user_key = user_map.get(r['SourceUserID'])
        quiz_key = quiz_map.get(r['SourceQuizID'])
        course_key = course_map.get(r['SourceCourseID'])
        if not user_key or not quiz_key or not course_key:
            continue

        date_key = ts_to_datekey(r['TimeFinish'] or r['TimeStart'])
        t_start = ts_to_dt(r['TimeStart'])
        t_finish = ts_to_dt(r['TimeFinish'])
        duration_sec = (r['TimeFinish'] - r['TimeStart']) if (r['TimeFinish'] and r['TimeStart'] and r['TimeFinish'] >= r['TimeStart']) else 0
        raw_score = float(r['RawScore'] or 0.0)
        max_score = float(r['QuizMaxScore'] or 10.0)

        scaled_10 = round((raw_score / max_score) * 10.0, 2) if max_score > 0 else 0.0
        scaled_10 = min(10.0, max(0.0, scaled_10))
        is_passed = 1 if scaled_10 >= 5.0 else 0

        insert_batch.append((
            r['SourceAttemptID'], user_key, quiz_key, course_key, date_key,
            r['AttemptNumber'], r['State'], duration_sec, raw_score, scaled_10,
            is_passed, t_start, t_finish
        ))

        if len(insert_batch) >= 10000:
            s_cursor.executemany(insert_sql, insert_batch)
            s_conn.commit()
            total += len(insert_batch)
            insert_batch.clear()

    if insert_batch:
        s_cursor.executemany(insert_sql, insert_batch)
        s_conn.commit()
        total += len(insert_batch)
    s_conn.commit()
    print(f"[+] Fact_Quiz_Attempts hoàn tất: {total} lượt làm quiz.")

def sync_fact_assign_submissions(m_conn, s_conn, user_map, assign_map, course_map):
    print("[*] Đồng bộ Fact_Assign_Submissions...")
    sql_extract = """
        SELECT s.id AS SourceSubmissionID, s.userid AS SourceUserID, s.assignment AS SourceAssignID, a.course AS SourceCourseID,
               s.status AS Status, s.timemodified AS SubmissionTime, a.duedate AS DueDate, a.grade AS MaxGrade, ag.grade AS Grade
        FROM assign_submission s
        JOIN assign a ON s.assignment = a.id
        LEFT JOIN assign_grades ag ON (s.assignment = ag.assignment AND s.userid = ag.userid)
        WHERE s.latest = 1;
    """
    with m_conn.cursor() as cursor:
        cursor.execute(sql_extract)
        rows = cursor.fetchall()

    s_cursor = s_conn.cursor()
    s_cursor.fast_executemany = True
    s_cursor.execute("DELETE FROM Fact_Assign_Submissions;")

    insert_sql = """
        INSERT INTO Fact_Assign_Submissions (
            SourceSubmissionID, UserKey, AssignKey, CourseKey, DateKey,
            SubmissionStatus, IsLate, DaysLate, Grade, GradeScaled10,
            IsPassed, SubmissionTime, DueDate
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """

    insert_batch = []
    total = 0
    for r in rows:
        user_key = user_map.get(r['SourceUserID'])
        assign_key = assign_map.get(r['SourceAssignID'])
        course_key = course_map.get(r['SourceCourseID'])
        if not user_key or not assign_key or not course_key:
            continue

        date_key = ts_to_datekey(r['SubmissionTime'])
        sub_time = ts_to_dt(r['SubmissionTime'])
        due_time = ts_to_dt(r['DueDate'])

        is_late = 1 if (r['DueDate'] and r['DueDate'] > 0 and r['SubmissionTime'] and r['SubmissionTime'] > r['DueDate']) else 0
        days_late = (r['SubmissionTime'] - r['DueDate']) // 86400 + 1 if is_late else 0
        status = r['Status'] if r['Status'] else 'missing'
        raw_grade = float(r['Grade']) if r['Grade'] is not None else None
        max_grade = float(r['MaxGrade'] or 10.0)

        scaled_10 = round((raw_grade / max_grade) * 10.0, 2) if raw_grade is not None and max_grade > 0 else 0.0
        is_passed = 1 if raw_grade is not None and scaled_10 >= 5.0 else 0

        insert_batch.append((
            r['SourceSubmissionID'], user_key, assign_key, course_key, date_key,
            status, is_late, days_late, raw_grade, scaled_10,
            is_passed, sub_time, due_time
        ))

        if len(insert_batch) >= 10000:
            s_cursor.executemany(insert_sql, insert_batch)
            s_conn.commit()
            total += len(insert_batch)
            insert_batch.clear()

    if insert_batch:
        s_cursor.executemany(insert_sql, insert_batch)
        s_conn.commit()
        total += len(insert_batch)
    s_conn.commit()
    print(f"[+] Fact_Assign_Submissions hoàn tất: {total} bài tập nộp.")

def sync_fact_question_attempts(m_conn, s_conn, user_map, quiz_map, question_map, course_map):
    print("[*] Đồng bộ Fact_Question_Attempts...")
    sql_extract = """
        SELECT qa.id AS SourceQAID, qat.userid AS SourceUserID, qat.quiz AS SourceQuizID, q.course AS SourceCourseID,
               qa.questionid AS SourceQuestionID, qa.slot AS Slot, qa.maxmark AS MaxMark, qas.fraction AS MaxFraction, qa.timemodified AS TimeModified,
               qa.responsesummary AS StudentResponse, qa.rightanswer AS RightAnswer
        FROM question_attempts qa
        JOIN quiz_attempts qat ON qa.questionusageid = qat.uniqueid
        JOIN quiz q ON qat.quiz = q.id
        JOIN (
            SELECT s1.questionattemptid, s1.fraction
            FROM question_attempt_steps s1
            WHERE s1.fraction IS NOT NULL
              AND s1.sequencenumber = (
                  SELECT MAX(s2.sequencenumber)
                  FROM question_attempt_steps s2
                  WHERE s2.questionattemptid = s1.questionattemptid AND s2.fraction IS NOT NULL
              )
        ) qas ON qas.questionattemptid = qa.id
        WHERE qat.state = 'finished';
    """
    with m_conn.cursor() as cursor:
        cursor.execute(sql_extract)
        rows = cursor.fetchall()

    s_cursor = s_conn.cursor()
    s_cursor.fast_executemany = True
    s_cursor.execute("DELETE FROM Fact_Question_Attempts;")
    
    insert_batch = []
    for r in rows:
        user_key = user_map.get(r['SourceUserID'])
        quiz_key = quiz_map.get(r['SourceQuizID'])
        question_key = question_map.get(r['SourceQuestionID'])
        course_key = course_map.get(r['SourceCourseID'])
        if not user_key or not quiz_key or not question_key or not course_key:
            continue
            
        date_key = ts_to_datekey(r['TimeModified'])
        max_mark = float(r['MaxMark'] or 1.0)
        fraction = float(r['MaxFraction'] or 0.0)
        earned_mark = max_mark * fraction
        is_correct = 1 if fraction >= 1.0 else 0
        is_wrong = 1 if fraction < 0.5 else 0
        
        student_response = r['StudentResponse'] if r['StudentResponse'] else None
        right_answer = r['RightAnswer'] if r['RightAnswer'] else None

        insert_batch.append((
            r['SourceQAID'], user_key, quiz_key, question_key, course_key, date_key,
            r['Slot'], max_mark, earned_mark, fraction, is_correct, is_wrong,
            student_response, right_answer
        ))
        
        if len(insert_batch) >= 10000:
            s_cursor.executemany("""
                INSERT INTO Fact_Question_Attempts (
                    SourceQAID, UserKey, QuizKey, QuestionKey, CourseKey, DateKey,
                    Slot, MaxMark, EarnedMark, Fraction, IsCorrect, IsWrong,
                    StudentResponse, RightAnswer
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, insert_batch)
            s_conn.commit()
            insert_batch.clear()

    if insert_batch:
        s_cursor.executemany("""
            INSERT INTO Fact_Question_Attempts (
                SourceQAID, UserKey, QuizKey, QuestionKey, CourseKey, DateKey,
                Slot, MaxMark, EarnedMark, Fraction, IsCorrect, IsWrong,
                StudentResponse, RightAnswer
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, insert_batch)
        s_conn.commit()
    print(f"[+] Fact_Question_Attempts hoàn tất: {len(rows)} câu hỏi tương tác.")

def run_etl():
    print("=" * 60)
    print("BẮT ĐẦU CHẠY PIPELINE ETL CHO CỔNG SINH VIÊN DLU")
    print(f"Thời gian bắt đầu: {datetime.datetime.now()}")
    print("=" * 60)

    timer = LiveTimer(interval=30)
    timer.start()
    status = "THÀNH CÔNG"

    m_conn = get_mariadb_conn()
    s_conn = get_mssql_conn()

    try:
        run_step("Dim_User",     sync_dim_users,     m_conn, s_conn)
        run_step("Dim_Course",   sync_dim_courses,   m_conn, s_conn)
        run_step("Dim_Quiz",     sync_dim_quizzes,   m_conn, s_conn)
        run_step("Dim_Assign",   sync_dim_assigns,   m_conn, s_conn)
        run_step("Dim_Question", sync_dim_questions, m_conn, s_conn)

        user_map, course_map, quiz_map, assign_map, question_map = load_lookups(s_conn)

        run_step("Fact_Course_Grades",      sync_fact_course_grades,      m_conn, s_conn, user_map, course_map)
        run_step("Fact_Quiz_Attempts",      sync_fact_quiz_attempts,      m_conn, s_conn, user_map, quiz_map, course_map)
        run_step("Fact_Assign_Submissions", sync_fact_assign_submissions, m_conn, s_conn, user_map, assign_map, course_map)
        run_step("Fact_Question_Attempts",  sync_fact_question_attempts,  m_conn, s_conn, user_map, quiz_map, question_map, course_map)

    except Exception as e:
        status = "THẤT BẠI"
        print(f"[LỖI] Pipeline thất bại: {str(e)}")
        raise SystemExit(1)
    finally:
        total = timer.stop()
        m_conn.close()
        s_conn.close()

        print("=" * 60)
        print(f"[{status}] TỔNG KẾT THỜI GIAN")
        for name, dt in STEP_TIMES:
            pct = dt / total * 100 if total else 0
            print(f"  {name:<26} {fmt_duration(dt):>10}  ({pct:4.1f}%)")
        print("-" * 60)
        print(f"  {'TỔNG':<26} {fmt_duration(total):>10}")
        print(f"Kết thúc lúc: {datetime.datetime.now()}")
        print("=" * 60)

if __name__ == "__main__":
    run_etl()
