const fs = require('fs');
const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '.env') });
const express = require('express');
const cors = require('cors');
const sql = require('mssql');
const bcrypt = require('bcryptjs');
const jwt = require('jsonwebtoken');

const app = express();
const PORT = process.env.PORT || 5000;
const JWT_SECRET = process.env.JWT_SECRET || 'DLU_SuperSecretKey_2026';

app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname, '../frontend')));

const dbConfig = {
    user: process.env.DB_USER || 'sa',
    password: process.env.DB_PASSWORD || 'SuperStrongPass123!',
    server: process.env.DB_SERVER || 'sqlserver',
    port: parseInt(process.env.DB_PORT, 10) || 1433,
    database: process.env.DB_NAME || 'lms_datawarehouse',
    options: {
        encrypt: false,
        trustServerCertificate: true,
        enableArithAbort: true
    },
    pool: { max: 15, min: 0, idleTimeoutMillis: 30000 }
};

const poolPromise = new sql.ConnectionPool(dbConfig)
    .connect()
    .then(pool => {
        console.log('[+] Kết nối thành công SQL Server Data Warehouse!');
        return pool;
    })
    .catch(err => {
        console.error('[!] Lỗi kết nối SQL Server:', err.message);
    });

function authenticateToken(req, res, next) {
    const authHeader = req.headers['authorization'];
    const token = authHeader && authHeader.split(' ')[1];
    
    if (!token) {
        return res.status(401).json({ success: false, message: 'Vui lòng đăng nhập để tiếp tục.' });
    }

    jwt.verify(token, JWT_SECRET, (err, user) => {
        if (err) {
            return res.status(403).json({ success: false, message: 'Phiên làm việc đã hết hạn.' });
        }
        req.user = user;
        next();
    });
}

app.post('/api/auth/login', async (req, res) => {
    const { username, password } = req.body;
    
    if (!username || !password) {
        return res.status(400).json({ success: false, message: 'Vui lòng nhập tên đăng nhập và mật khẩu.' });
    }

    try {
        const pool = await poolPromise;
        const result = await pool.request()
            .input('username', sql.NVarChar, username.trim())
            .query(`
                SELECT UserKey, SourceUserID, Username, PasswordHash, IdNumber, FullName, Email, Department, Institution, CohortName, IsStudent
                FROM Dim_User 
                WHERE Username = @username;
            `);

        if (result.recordset.length === 0) {
            return res.status(401).json({ success: false, message: 'Tài khoản không tồn tại trên hệ thống DLU.' });
        }

        const user = result.recordset[0];

        if (!user.IsStudent) {
            return res.status(403).json({ 
                success: false, 
                message: 'Cổng này chỉ dành riêng cho sinh viên. Giảng viên/Quản trị viên vui lòng sử dụng hệ thống quản trị.' 
            });
        }

        let isMatch = false;
        try {
            if (user.PasswordHash.startsWith('$2y$') || user.PasswordHash.startsWith('$2a$') || user.PasswordHash.startsWith('$2b$')) {
                const normalizedHash = user.PasswordHash.replace(/^\$2y\$/, '$2a$');
                isMatch = await bcrypt.compare(password, normalizedHash);
            }
        } catch (e) {
            console.error('Bcrypt error:', e.message);
        }

        if (!isMatch) {
            return res.status(401).json({ success: false, message: 'Mật khẩu không chính xác.' });
        }

        const token = jwt.sign(
            { 
                userKey: user.UserKey, 
                sourceUserId: user.SourceUserID,
                username: user.Username, 
                fullName: user.FullName 
            }, 
            JWT_SECRET, 
            { expiresIn: '7d' }
        );

        res.json({
            success: true,
            message: 'Đăng nhập thành công!',
            token,
            user: {
                userKey: user.UserKey,
                username: user.Username,
                idNumber: user.IdNumber || user.Username,
                fullName: user.FullName,
                email: user.Email,
                department: user.Department || 'Khoa Công nghệ Thông tin',
                cohortName: user.CohortName || 'CTK44-PM',
                institution: user.Institution || 'Trường Đại học Đà Lạt'
            }
        });

    } catch (err) {
        res.status(500).json({ success: false, message: 'Lỗi server: ' + err.message });
    }
});

app.get('/api/student/summary', authenticateToken, async (req, res) => {
    try {
        const { userKey } = req.user;
        const pool = await poolPromise;
        const request = pool.request().input('userKey', sql.Int, userKey);

        const gpaRes = await request.query(`
            SELECT 
                COUNT(*) AS EnrolledCourses,
                AVG(GradeScaled10) AS GPA10,
                SUM(CASE WHEN GradeScaled10 < 5.0 THEN 1 ELSE 0 END) AS AtRiskCourses
            FROM Fact_Course_Grades
            WHERE UserKey = @userKey;
        `);
        const gpaData = gpaRes.recordset[0] || {};
        const gpaValue = gpaData.GPA10 === null || gpaData.GPA10 === undefined
            ? null
            : parseFloat(gpaData.GPA10);
        const gpa10 = gpaValue === null ? null : gpaValue.toFixed(2);
        
        let gpaClassification = 'Chưa có xếp loại';
        if (gpaValue !== null) {
            if (gpaValue >= 8.5) gpaClassification = 'Xuất sắc';
            else if (gpaValue >= 7.0) gpaClassification = 'Khá';
            else if (gpaValue >= 5.5) gpaClassification = 'Trung bình';
            else if (gpaValue >= 4.0) gpaClassification = 'Trung bình yếu';
            else gpaClassification = 'Kém (rớt môn)';
        }

        const deadlineRes = await request.query(`
            SELECT 
                COUNT(*) AS TotalPending,
                SUM(CASE WHEN da.DueDate >= GETDATE() AND da.DueDate <= DATEADD(hour, 48, GETDATE()) THEN 1 ELSE 0 END) AS UrgentDeadlines,
                SUM(CASE WHEN da.DueDate < GETDATE() THEN 1 ELSE 0 END) AS OverdueCount
            FROM Dim_Assign da
            JOIN Dim_Course dc ON da.CourseKey = dc.CourseKey
            INNER JOIN Fact_Course_Grades fcg ON (fcg.CourseKey = dc.CourseKey AND fcg.UserKey = @userKey)
            LEFT JOIN Fact_Assign_Submissions fas ON (da.AssignKey = fas.AssignKey AND fas.UserKey = @userKey)
            WHERE fas.SubmissionStatus IS NULL OR fas.SubmissionStatus <> 'submitted';
        `);
        const dlData = deadlineRes.recordset[0] || {};

        const disciplineRes = await request.query(`
            SELECT 
                SUM(CASE WHEN DueDate IS NOT NULL THEN 1 ELSE 0 END) AS TotalSubmitted,
                SUM(CASE WHEN DueDate IS NOT NULL AND IsLate = 0 THEN 1 ELSE 0 END) AS OnTimeCount,
                SUM(CASE WHEN DueDate IS NOT NULL AND IsLate = 1 THEN 1 ELSE 0 END) AS LateCount
            FROM Fact_Assign_Submissions
            WHERE UserKey = @userKey AND SubmissionStatus = 'submitted';
        `);
        const discData = disciplineRes.recordset[0] || {};
        const totalSub = discData.TotalSubmitted || 0;
        const onTimeRate = totalSub > 0 ? (((discData.OnTimeCount || 0) / totalSub) * 100).toFixed(1) : null;

        const quizRes = await request.query(`
            WITH RankedFinished AS (
                SELECT
                    ScoreScaled10,
                    ROW_NUMBER() OVER (
                        PARTITION BY QuizKey
                        ORDER BY AttemptNumber DESC, TimeFinish DESC, SourceAttemptID DESC
                    ) AS AttemptRank
                FROM Fact_Quiz_Attempts
                WHERE UserKey = @userKey AND State = 'finished'
            )
            SELECT 
                COUNT(*) AS CompletedQuizzes,
                AVG(ScoreScaled10) AS AvgQuizScore
            FROM RankedFinished
            WHERE AttemptRank = 1;
        `);
        const qData = quizRes.recordset[0] || {};

        res.json({
            success: true,
            data: {
                gpa10: gpa10 === null ? null : parseFloat(gpa10),
                gpa4: null,
                gpaClassification,
                enrolledCourses: gpaData.EnrolledCourses || 0,
                atRiskCourses: gpaData.AtRiskCourses || 0,
                urgentDeadlines: dlData.UrgentDeadlines || 0,
                overdueCount: dlData.OverdueCount || 0,
                onTimeRate: onTimeRate === null ? null : parseFloat(onTimeRate),
                completedQuizzes: qData.CompletedQuizzes || 0,
                avgQuizScore: qData.AvgQuizScore === null || qData.AvgQuizScore === undefined
                    ? null
                    : parseFloat(qData.AvgQuizScore).toFixed(2)
            }
        });
    } catch (err) {
        res.status(500).json({ success: false, message: err.message });
    }
});

app.get('/api/student/deadlines', authenticateToken, async (req, res) => {
    try {
        const { userKey } = req.user;
        const pool = await poolPromise;
        const request = pool.request().input('userKey', sql.Int, userKey);

        const query = `
            SELECT 
                da.AssignKey,
                da.AssignName,
                dc.CourseName,
                dc.CourseShortName,
                da.DueDate,
                fas.SubmissionStatus,
                fas.SubmissionTime,
                fas.Grade,
                fas.GradeScaled10,
                fas.IsLate,
                CASE
                    WHEN da.DueDate < GETDATE() THEN -1.0
                    ELSE DATEDIFF(second, GETDATE(), da.DueDate) / 60.0
                END AS MinutesRemaining
            FROM Dim_Assign da
            JOIN Dim_Course dc ON da.CourseKey = dc.CourseKey
            INNER JOIN Fact_Course_Grades fcg ON (fcg.CourseKey = dc.CourseKey AND fcg.UserKey = @userKey)
            LEFT JOIN Fact_Assign_Submissions fas ON (da.AssignKey = fas.AssignKey AND fas.UserKey = @userKey)
            ORDER BY 
                CASE 
                    WHEN fas.SubmissionStatus = 'submitted' THEN 3
                    WHEN fas.SubmissionStatus = 'draft' THEN 2
                    ELSE 1 
                END,
                da.DueDate ASC;
        `;
        const result = await request.query(query);

        const formatted = result.recordset.map(item => {
            let statusTag = item.SubmissionStatus === 'draft' ? 'Bản nháp (Chưa gửi)' : 'Chưa nộp';
            let statusColor = item.SubmissionStatus === 'draft' ? 'warning' : 'info';
            let timeRemainingText = '';
            const mins = item.MinutesRemaining;
            if (item.SubmissionStatus === 'submitted') {
                statusTag = item.IsLate ? 'Đã nộp (Trễ)' : 'Đã nộp (Đúng hạn)';
                statusColor = item.IsLate ? 'warning' : 'success';
                timeRemainingText = 'Hoàn thành';
            } else if (mins === null) {
                timeRemainingText = 'Không giới hạn';
            } else if (mins < 0) {
                statusTag = 'Quá hạn';
                statusColor = 'danger';
                timeRemainingText = 'Quá hạn';
            } else if (mins <= 24 * 60) {
                statusTag = 'Hết hạn hôm nay';
                statusColor = 'danger';
                const hours = Math.floor(mins / 60);
                timeRemainingText = `Còn ${hours}h ${mins % 60}p`;
            } else {
                const days = Math.floor(mins / (60 * 24));
                timeRemainingText = `Còn ${days} ngày nữa`;
            }

            return {
                ...item,
                statusTag,
                statusColor,
                timeRemainingText
            };
        });

        res.json({ success: true, data: formatted });
    } catch (err) {
        res.status(500).json({ success: false, message: err.message });
    }
});

app.get('/api/student/courses', authenticateToken, async (req, res) => {
    try {
        const { userKey } = req.user;
        const pool = await poolPromise;
        const request = pool.request().input('userKey', sql.Int, userKey);

        const query = `
            WITH LatestFinishedQuiz AS (
                SELECT
                    UserKey,
                    CourseKey,
                    QuizKey,
                    ScoreScaled10,
                    ROW_NUMBER() OVER (
                        PARTITION BY UserKey, QuizKey
                        ORDER BY AttemptNumber DESC, TimeFinish DESC, SourceAttemptID DESC
                    ) AS AttemptRank
                FROM Fact_Quiz_Attempts
                WHERE UserKey = @userKey AND State = 'finished'
            )
            SELECT 
                dc.CourseKey,
                dc.CourseName,
                dc.CourseShortName,
                dc.CourseCode,
                fcg.GradeScaled10 AS FinalGrade10,
                fcg.LetterGrade,
                fcg.GradeClassification,
                fcg.IsPassed,
                COALESCE(dd.Year, YEAR(GETDATE())) AS Year,
                quiz_stat.AvgQuiz AS QuizAverage,
                sub_stat.AssignAverage AS AssignAverage,
                COALESCE(sub_stat.PendingAssigns, 0) AS PendingAssigns,
                COALESCE(assign_cnt.TotalAssigns, 0) AS TotalAssigns,
                COALESCE(assign_cnt.SubmittedAssigns, 0) AS SubmittedAssigns,
                COALESCE(quiz_cnt.TotalQuizzes, 0) AS TotalQuizzes,
                COALESCE(quiz_cnt.DoneQuizzes, 0) AS DoneQuizzes
            FROM Fact_Course_Grades fcg
            JOIN Dim_Course dc ON fcg.CourseKey = dc.CourseKey
            LEFT JOIN Dim_Date dd ON fcg.DateKey = dd.DateKey
            OUTER APPLY (
                SELECT AVG(q.ScoreScaled10) AS AvgQuiz
                FROM LatestFinishedQuiz q
                WHERE q.UserKey = @userKey AND q.CourseKey = dc.CourseKey AND q.AttemptRank = 1
            ) quiz_stat
            OUTER APPLY (
                SELECT 
                    AVG(CASE WHEN fas.SubmissionStatus = 'submitted' AND fas.Grade IS NOT NULL THEN fas.GradeScaled10 END) AS AssignAverage,
                    SUM(CASE WHEN fas.SubmissionStatus <> 'submitted' OR fas.SubmissionStatus IS NULL THEN 1 ELSE 0 END) AS PendingAssigns
                FROM Fact_Assign_Submissions fas
                WHERE fas.UserKey = @userKey AND fas.CourseKey = dc.CourseKey
            ) sub_stat
            OUTER APPLY (
                -- Tiến độ bài tập của môn: tổng số bài + số bài đã nộp
                SELECT
                    COUNT(DISTINCT da2.AssignKey) AS TotalAssigns,
                    COUNT(DISTINCT CASE WHEN fas.SubmissionStatus = 'submitted' THEN da2.AssignKey END) AS SubmittedAssigns
                FROM Dim_Assign da2
                LEFT JOIN Fact_Assign_Submissions fas
                    ON fas.AssignKey = da2.AssignKey AND fas.UserKey = @userKey
                WHERE da2.CourseKey = dc.CourseKey
            ) assign_cnt
            OUTER APPLY (
                -- Tiến độ quiz của môn: tổng số quiz + số quiz đã hoàn thành
                SELECT
                    COUNT(DISTINCT dq2.QuizKey) AS TotalQuizzes,
                    COUNT(DISTINCT CASE WHEN fqa.State = 'finished' THEN dq2.QuizKey END) AS DoneQuizzes
                FROM Dim_Quiz dq2
                LEFT JOIN Fact_Quiz_Attempts fqa
                    ON fqa.QuizKey = dq2.QuizKey AND fqa.UserKey = @userKey
                WHERE dq2.CourseKey = dc.CourseKey
            ) quiz_cnt
            WHERE fcg.UserKey = @userKey
            ORDER BY fcg.GradeScaled10 ASC;
        `;
        const result = await request.query(query);
        res.json({ success: true, data: result.recordset });
    } catch (err) {
        res.status(500).json({ success: false, message: err.message });
    }
});

app.get('/api/student/quizzes', authenticateToken, async (req, res) => {
    try {
        const { userKey } = req.user;
        const pool = await poolPromise;
        const request = pool.request().input('userKey', sql.Int, userKey);

        const query = `
            SELECT 
                dq.QuizKey,
                dq.QuizName,
                dc.CourseName,
                dc.CourseShortName,
                dq.TimeOpen,
                dq.TimeClose,
                dq.TimeLimitMinutes,
                CASE WHEN dq.TimeClose IS NOT NULL AND dq.TimeClose < GETDATE() THEN 1 ELSE 0 END AS IsClosed,
                latest.State,
                latest.AttemptNumber,
                latest.ScoreScaled10,
                latest.DurationSeconds,
                latest.TimeFinish
            FROM Dim_Quiz dq
            JOIN Dim_Course dc ON dq.CourseKey = dc.CourseKey
            INNER JOIN Fact_Course_Grades fcg ON (fcg.CourseKey = dc.CourseKey AND fcg.UserKey = @userKey)
            OUTER APPLY (
                SELECT TOP 1
                    fqa.State,
                    fqa.AttemptNumber,
                    fqa.ScoreScaled10,
                    fqa.DurationSeconds,
                    fqa.TimeFinish
                FROM Fact_Quiz_Attempts fqa
                WHERE dq.QuizKey = fqa.QuizKey AND fqa.UserKey = @userKey
                ORDER BY fqa.AttemptNumber DESC, fqa.SourceAttemptID DESC
            ) latest
            ORDER BY dq.TimeClose ASC;
        `;
        const result = await request.query(query);
        res.json({ success: true, data: result.recordset });
    } catch (err) {
        res.status(500).json({ success: false, message: err.message });
    }
});

app.get('/api/student/wrong-questions', authenticateToken, async (req, res) => {
    try {
        const { userKey } = req.user;
        const { courseKey } = req.query;
        const pool = await poolPromise;
        const request = pool.request().input('userKey', sql.Int, userKey);

        let query = `
            SELECT 
                fqa.QuestionAttemptKey,
                fqa.SourceQAID,
                dc.CourseKey,
                dc.CourseName,
                dc.CourseShortName,
                dqz.QuizKey,
                dqz.QuizName,
                dq.QuestionKey,
                dq.QuestionName,
                COALESCE(dq.QuestionText, dq.QuestionName) AS QuestionText,
                dq.QuestionType,
                fqa.Slot,
                fqa.MaxMark,
                fqa.EarnedMark,
                fqa.Fraction,
                COALESCE(fqa.StudentResponse, N'(Chưa trả lời hoặc hết giờ)') AS StudentResponse,
                COALESCE(fqa.RightAnswer, N'(Chưa có đáp án lưu)') AS RightAnswer,
                fqa.IsWrong,
                fqa.DateKey,
                dd.FullDate
            FROM Fact_Question_Attempts fqa
            JOIN Dim_Course dc ON fqa.CourseKey = dc.CourseKey
            JOIN Dim_Quiz dqz ON fqa.QuizKey = dqz.QuizKey
            JOIN Dim_Question dq ON fqa.QuestionKey = dq.QuestionKey
            LEFT JOIN Dim_Date dd ON fqa.DateKey = dd.DateKey
            WHERE fqa.UserKey = @userKey AND fqa.IsWrong = 1
        `;

        if (courseKey && courseKey !== 'all') {
            request.input('courseKey', sql.Int, parseInt(courseKey, 10));
            query += ` AND fqa.CourseKey = @courseKey `;
        }

        query += ` ORDER BY fqa.DateKey DESC, dc.CourseName ASC;`;

        const result = await request.query(query);
        res.json({ success: true, data: result.recordset });
    } catch (err) {
        res.status(500).json({ success: false, message: err.message });
    }
});

// ============================================================================
// ETL STUDIO, BẢNG COST (CSV) & DEMO MINH HỌA GỘP ĐA BẢNG (BẢNG A + BẢNG B -> DWH)
// ============================================================================

function parseCsvFile(filePath) {
    if (!fs.existsSync(filePath)) return [];
    const content = fs.readFileSync(filePath, 'utf8').replace(/^\uFEFF/, '').trim();
    if (!content) return [];
    const lines = content.split(/\r?\n/).filter(l => l.trim().length > 0);
    if (lines.length < 2) return [];

    const parseLine = (line) => {
        const res = [];
        let cur = '';
        let inQuotes = false;
        for (let i = 0; i < line.length; i++) {
            const ch = line[i];
            if (ch === '"') {
                if (inQuotes && line[i + 1] === '"') {
                    cur += '"';
                    i++;
                } else {
                    inQuotes = !inQuotes;
                }
            } else if (ch === ',' && !inQuotes) {
                res.push(cur);
                cur = '';
            } else {
                cur += ch;
            }
        }
        res.push(cur);
        return res;
    };

    const headers = parseLine(lines[0]);
    return lines.slice(1).map(line => {
        const vals = parseLine(line);
        const obj = {};
        headers.forEach((h, idx) => {
            const raw = vals[idx] !== undefined ? vals[idx] : '';
            const num = Number(raw);
            obj[h.trim()] = (raw !== '' && !isNaN(num) && !/^\s*$/.test(raw)) ? num : raw;
        });
        return obj;
    });
}

app.get('/api/etl/csv/:type', (req, res) => {
    const map = {
        cost: path.join(__dirname, '../etl/etl_cost_report.csv'),
        mapping: path.join(__dirname, '../etl/etl_column_mapping.csv'),
        benchmark: path.join(__dirname, '../etl/read_optimization_benchmark.csv')
    };
    const filePath = map[req.params.type];
    if (!filePath || !fs.existsSync(filePath)) {
        return res.status(404).json({ success: false, message: 'Không tìm thấy file CSV.' });
    }
    res.download(filePath);
});

app.get('/api/etl/cost-report', async (req, res) => {
    try {
        const costRows = parseCsvFile(path.join(__dirname, '../etl/etl_cost_report.csv'));
        const mappingRows = parseCsvFile(path.join(__dirname, '../etl/etl_column_mapping.csv'));
        const benchmarkRows = parseCsvFile(path.join(__dirname, '../etl/read_optimization_benchmark.csv'));

        // Nếu SQL Server đang kết nối, lấy thêm số dòng thực tế hiện tại trong DWH để đối chiếu trực tiếp
        let liveCounts = {};
        try {
            const pool = await poolPromise;
            if (pool) {
                const cntRes = await pool.request().query(`
                    SELECT 'Dim_User' AS T, COUNT(*) AS C FROM Dim_User UNION ALL
                    SELECT 'Dim_Course', COUNT(*) FROM Dim_Course UNION ALL
                    SELECT 'Dim_Quiz', COUNT(*) FROM Dim_Quiz UNION ALL
                    SELECT 'Dim_Assign', COUNT(*) FROM Dim_Assign UNION ALL
                    SELECT 'Dim_Question', COUNT(*) FROM Dim_Question UNION ALL
                    SELECT 'Fact_Course_Grades', COUNT(*) FROM Fact_Course_Grades UNION ALL
                    SELECT 'Fact_Quiz_Attempts', COUNT(*) FROM Fact_Quiz_Attempts UNION ALL
                    SELECT 'Fact_Assign_Submissions', COUNT(*) FROM Fact_Assign_Submissions UNION ALL
                    SELECT 'Fact_Question_Attempts', COUNT(*) FROM Fact_Question_Attempts;
                `);
                cntRes.recordset.forEach(r => { liveCounts[r.T] = r.C; });
            }
        } catch (_) {
            // Bỏ qua nếu DB đang khởi động
        }

        const enrichedCost = costRows.map(r => ({
            ...r,
            LiveDwhRows: liveCounts[r.TableName] !== undefined ? liveCounts[r.TableName] : r.LoadedRowCount
        }));

        const totalSourceCols = enrichedCost.reduce((s, r) => s + (Number(r.TotalSourceCols) || 0), 0);
        const totalSelectedCols = enrichedCost.reduce((s, r) => s + (Number(r.SelectedCols) || 0), 0);
        const totalDirectCols = enrichedCost.reduce((s, r) => s + (Number(r.DirectMappedCols) || 0), 0);
        const totalTransformedCols = enrichedCost.reduce((s, r) => s + (Number(r.TransformedCols) || 0), 0);
        const totalSourceRows = enrichedCost.reduce((s, r) => s + (Number(r.SourceRowCount) || 0), 0);
        const totalExtractedRows = enrichedCost.reduce((s, r) => s + (Number(r.ExtractedRowCount) || 0), 0);
        const totalTimeV1 = enrichedCost.reduce((s, r) => s + (Number(r.TimeV1Sec) || 0), 0);
        const totalTimeV2 = enrichedCost.reduce((s, r) => s + (Number(r.TimeV2Sec) || 0), 0);
        const totalTimeV3 = enrichedCost.reduce((s, r) => s + (Number(r.TimeV3Sec) || 0), 0);
        const totalTimeV4 = enrichedCost.reduce((s, r) => s + (Number(r.TotalTimeSec) || 0), 0);

        res.json({
            success: true,
            data: {
                summary: {
                    totalSourceCols,
                    totalSelectedCols,
                    totalPrunedCols: totalSourceCols - totalSelectedCols,
                    overallColReductionPct: totalSourceCols > 0 ? Number(((1 - totalSelectedCols / totalSourceCols) * 100).toFixed(1)) : 0,
                    totalDirectCols,
                    totalTransformedCols,
                    totalSourceRows,
                    totalExtractedRows,
                    rowReductionPct: totalSourceRows > 0 ? Number(((1 - totalExtractedRows / totalSourceRows) * 100).toFixed(1)) : 0,
                    totalTimeV1: Number(totalTimeV1.toFixed(2)),
                    totalTimeV2: Number(totalTimeV2.toFixed(2)),
                    totalTimeV3: Number(totalTimeV3.toFixed(2)),
                    totalTimeV4: Number(totalTimeV4.toFixed(2)),
                    speedupV4VsV1: totalTimeV4 > 0 ? Number((totalTimeV1 / totalTimeV4).toFixed(1)) : 1
                },
                costTable: enrichedCost,
                columnMappings: mappingRows,
                readBenchmarks: benchmarkRows
            }
        });
    } catch (err) {
        res.status(500).json({ success: false, message: err.message });
    }
});

// API Minh họa: Gộp 5 bảng Dim (Bảng A, B, C, D, E) + Fact vào 1 bảng tổng hợp của mình
app.get('/api/etl/multi-table-demo', async (req, res) => {
    try {
        let userKey = req.query.userKey ? parseInt(req.query.userKey, 10) : null;
        const authHeader = req.headers['authorization'];
        const token = authHeader && authHeader.split(' ')[1];
        if (!userKey && token) {
            try {
                const decoded = jwt.verify(token, JWT_SECRET);
                userKey = decoded.userKey;
            } catch (_) {}
        }

        const pool = await poolPromise;
        if (!pool) {
            return res.status(503).json({ success: false, message: 'Chưa kết nối được SQL Server DWH.' });
        }

        if (!userKey) {
            const topStudent = await pool.request().query(`
                SELECT TOP 1 u.UserKey
                FROM Dim_User u
                JOIN Fact_Course_Grades fcg ON u.UserKey = fcg.UserKey
                WHERE u.IsStudent = 1
                ORDER BY u.UserKey ASC;
            `);
            userKey = topStudent.recordset[0]?.UserKey || 1;
        }

        const reqDb = pool.request().input('userKey', sql.Int, userKey);

        // 1. Bảng A: Dim_User (Chỉ đọc các trường cốt lõi đã tinh gọn)
        const tableA = await reqDb.query(`
            SELECT UserKey, SourceUserID, Username, IdNumber, FullName, CohortName, Department
            FROM Dim_User WHERE UserKey = @userKey;
        `);

        // 2. Bảng B: Dim_Course (Các môn sinh viên này đang học)
        const tableB = await reqDb.query(`
            SELECT TOP 5 dc.CourseKey, dc.SourceCourseID, dc.CourseCode, dc.CourseName, dc.CourseShortName
            FROM Dim_Course dc
            JOIN Fact_Course_Grades fcg ON dc.CourseKey = fcg.CourseKey
            WHERE fcg.UserKey = @userKey
            ORDER BY dc.CourseKey ASC;
        `);

        // 3. Bảng C: Dim_Assign (Bài tập thuộc các môn trên)
        const tableC = await reqDb.query(`
            SELECT TOP 5 da.AssignKey, da.SourceAssignID, da.CourseKey, da.AssignName, da.DueDate, da.MaxGrade
            FROM Dim_Assign da
            JOIN Fact_Course_Grades fcg ON da.CourseKey = fcg.CourseKey
            WHERE fcg.UserKey = @userKey
            ORDER BY da.DueDate DESC;
        `);

        // 4. Bảng D: Dim_Quiz (Bài kiểm tra thuộc các môn trên)
        const tableD = await reqDb.query(`
            SELECT TOP 5 dq.QuizKey, dq.SourceQuizID, dq.CourseKey, dq.QuizName, dq.TimeLimitMinutes, dq.MaxGrade
            FROM Dim_Quiz dq
            JOIN Fact_Course_Grades fcg ON dq.CourseKey = fcg.CourseKey
            WHERE fcg.UserKey = @userKey
            ORDER BY dq.QuizKey DESC;
        `);

        // 5. Bảng E: Dim_Date (Chiều thời gian gắn với điểm môn học)
        const tableE = await reqDb.query(`
            SELECT DISTINCT TOP 5 dd.DateKey, dd.FullDate, dd.MonthName, dd.Quarter, dd.Year
            FROM Dim_Date dd
            JOIN Fact_Course_Grades fcg ON dd.DateKey = fcg.DateKey
            WHERE fcg.UserKey = @userKey
            ORDER BY dd.DateKey DESC;
        `);

        // 6. BẢNG TỔNG HỢP CỦA MÌNH: Hợp nhất 5 bảng Dim (A + B + C + D + E) + Fact
        const unifiedTable = await reqDb.query(`
            SELECT TOP 8
                u.UserKey,
                u.IdNumber AS [A_IdNumber],
                u.FullName AS [A_FullName],
                u.CohortName AS [A_CohortName],
                dc.CourseKey,
                dc.CourseCode AS [B_CourseCode],
                dc.CourseName AS [B_CourseName],
                sample_assign.AssignName AS [C_SampleAssign],
                sample_quiz.QuizName AS [D_SampleQuiz],
                COALESCE(CONVERT(VARCHAR(10), dd.FullDate, 103), N'Học kỳ hiện tại') AS [E_SyncDate],
                COALESCE(dd.Quarter, DATEPART(QUARTER, GETDATE())) AS [E_Quarter],
                COALESCE(dd.Year, YEAR(GETDATE())) AS [E_Year],
                fcg.RawGrade AS [Fact_RawGrade],
                fcg.GradeScaled10 AS [Fact_Grade10],
                fcg.LetterGrade AS [Fact_Letter],
                fcg.GradeClassification AS [Fact_Classification]
            FROM Fact_Course_Grades fcg
            JOIN Dim_User u ON fcg.UserKey = u.UserKey
            JOIN Dim_Course dc ON fcg.CourseKey = dc.CourseKey
            LEFT JOIN Dim_Date dd ON fcg.DateKey = dd.DateKey
            OUTER APPLY (
                SELECT TOP 1 da.AssignName
                FROM Dim_Assign da
                WHERE da.CourseKey = dc.CourseKey
                ORDER BY da.AssignKey DESC
            ) sample_assign
            OUTER APPLY (
                SELECT TOP 1 dq.QuizName
                FROM Dim_Quiz dq
                WHERE dq.CourseKey = dc.CourseKey
                ORDER BY dq.QuizKey DESC
            ) sample_quiz
            WHERE fcg.UserKey = @userKey
            ORDER BY fcg.CourseKey ASC;
        `);

        res.json({
            success: true,
            data: {
                userKey,
                sourceDims: {
                    tableA_DimUser: tableA.recordset,
                    tableB_DimCourse: tableB.recordset,
                    tableC_DimAssign: tableC.recordset,
                    tableD_DimQuiz: tableD.recordset,
                    tableE_DimDate: tableE.recordset
                },
                unifiedTable: unifiedTable.recordset
            }
        });
    } catch (err) {
        res.status(500).json({ success: false, message: err.message });
    }
});

// API Demo Sống: Thay đổi dữ liệu -> Đổ vào Staging -> MERGE vào Dim (Bảo toàn Surrogate Key) & Cập nhật ngay
app.post('/api/etl/live-sync-demo', async (req, res) => {
    const t0 = Date.now();
    try {
        const { targetType, userKey, courseKey, newCohortName, newDepartment, newCourseName, newRawGrade } = req.body;
        const pool = await poolPromise;
        if (!pool) {
            return res.status(503).json({ success: false, message: 'Chưa kết nối được SQL Server DWH.' });
        }

        const targetUserKey = parseInt(userKey, 10) || 1;

        if (targetType === 'Dim_User') {
            // 1. Lấy trạng thái TRƯỚC khi MERGE
            const beforeRes = await pool.request()
                .input('uk', sql.Int, targetUserKey)
                .query(`
                    SELECT u.UserKey, u.SourceUserID, u.Username, u.PasswordHash, u.IdNumber, u.FullName,
                           u.Email, u.Department, u.Institution, u.CohortName, u.IsStudent, u.CreatedAt,
                           (SELECT COUNT(*) FROM Fact_Course_Grades WHERE UserKey = u.UserKey) AS FactGradesRefCount,
                           (SELECT COUNT(*) FROM Fact_Quiz_Attempts WHERE UserKey = u.UserKey) AS FactQuizRefCount
                    FROM Dim_User u WHERE u.UserKey = @uk;
                `);
            if (!beforeRes.recordset.length) {
                return res.status(404).json({ success: false, message: 'Không tìm thấy sinh viên để demo.' });
            }
            const before = beforeRes.recordset[0];
            const updatedCohort = (newCohortName && newCohortName.trim()) ? newCohortName.trim() : before.CohortName;
            const updatedDept = (newDepartment && newDepartment.trim()) ? newDepartment.trim() : before.Department;

            // 2. Đổ dữ liệu mới qua bảng Staging (stag_Dim_User) và thực thi Set-based MERGE
            await pool.request()
                .input('sourceUserId', sql.BigInt, before.SourceUserID)
                .input('username', sql.NVarChar, before.Username)
                .input('passwordHash', sql.NVarChar, before.PasswordHash)
                .input('idNumber', sql.NVarChar, before.IdNumber)
                .input('fullName', sql.NVarChar, before.FullName)
                .input('email', sql.NVarChar, before.Email)
                .input('department', sql.NVarChar, updatedDept)
                .input('institution', sql.NVarChar, before.Institution)
                .input('cohortName', sql.NVarChar, updatedCohort)
                .input('isStudent', sql.Bit, before.IsStudent)
                .input('createdAt', sql.DateTime, before.CreatedAt)
                .query(`
                    IF OBJECT_ID('stag_Dim_User', 'U') IS NULL
                        CREATE TABLE stag_Dim_User (
                            SourceUserID BIGINT NOT NULL, Username NVARCHAR(100), PasswordHash NVARCHAR(255),
                            IdNumber NVARCHAR(100), FullName NVARCHAR(200), Email NVARCHAR(100),
                            Department NVARCHAR(255), Institution NVARCHAR(255), CohortName NVARCHAR(254),
                            IsStudent BIT, CreatedAt DATETIME
                        );
                    TRUNCATE TABLE stag_Dim_User;
                    INSERT INTO stag_Dim_User (
                        SourceUserID, Username, PasswordHash, IdNumber, FullName,
                        Email, Department, Institution, CohortName, IsStudent, CreatedAt
                    ) VALUES (
                        @sourceUserId, @username, @passwordHash, @idNumber, @fullName,
                        @email, @department, @institution, @cohortName, @isStudent, @createdAt
                    );
                    MERGE Dim_User AS target
                    USING stag_Dim_User AS source
                    ON target.SourceUserID = source.SourceUserID
                    WHEN MATCHED THEN
                        UPDATE SET
                            CohortName = source.CohortName,
                            Department = source.Department,
                            FullName = source.FullName;
                `);

            // 3. Lấy trạng thái SAU khi MERGE
            const afterRes = await pool.request()
                .input('uk', sql.Int, targetUserKey)
                .query(`
                    SELECT u.UserKey, u.SourceUserID, u.IdNumber, u.FullName, u.CohortName, u.Department,
                           (SELECT COUNT(*) FROM Fact_Course_Grades WHERE UserKey = u.UserKey) AS FactGradesRefCount,
                           (SELECT COUNT(*) FROM Fact_Quiz_Attempts WHERE UserKey = u.UserKey) AS FactQuizRefCount
                    FROM Dim_User u WHERE u.UserKey = @uk;
                `);
            const after = afterRes.recordset[0];

            return res.json({
                success: true,
                message: `Đã đổ dữ liệu qua stag_Dim_User và MERGE vào Dim_User thành công (${Date.now() - t0}ms). Khóa UserKey=${after.UserKey} được bảo toàn 100%!`,
                data: {
                    targetTable: 'Dim_User (Bảng A)',
                    stagingTable: 'stag_Dim_User',
                    durationMs: Date.now() - t0,
                    keyPreserved: before.UserKey === after.UserKey,
                    surrogateKeyName: 'UserKey',
                    surrogateKeyValue: after.UserKey,
                    before: {
                        UserKey: before.UserKey,
                        SourceUserID: before.SourceUserID,
                        FullName: before.FullName,
                        CohortName: before.CohortName,
                        Department: before.Department,
                        FactRefs: `${before.FactGradesRefCount} bảng điểm, ${before.FactQuizRefCount} lượt quiz`
                    },
                    after: {
                        UserKey: after.UserKey,
                        SourceUserID: after.SourceUserID,
                        FullName: after.FullName,
                        CohortName: after.CohortName,
                        Department: after.Department,
                        FactRefs: `${after.FactGradesRefCount} bảng điểm, ${after.FactQuizRefCount} lượt quiz (Không bị đứt khóa ngoại)`
                    }
                }
            });
        } else if (targetType === 'Dim_Course') {
            const targetCourseKey = parseInt(courseKey, 10);
            const beforeRes = await pool.request()
                .input('ck', sql.Int, targetCourseKey)
                .query(`
                    SELECT c.CourseKey, c.SourceCourseID, c.CourseName, c.CourseShortName, c.CourseCode, c.StartDate, c.EndDate,
                           (SELECT COUNT(*) FROM Fact_Course_Grades WHERE CourseKey = c.CourseKey) AS FactRefCount
                    FROM Dim_Course c WHERE c.CourseKey = @ck;
                `);
            if (!beforeRes.recordset.length) {
                return res.status(404).json({ success: false, message: 'Không tìm thấy môn học để demo.' });
            }
            const before = beforeRes.recordset[0];
            const updatedName = (newCourseName && newCourseName.trim()) ? newCourseName.trim() : before.CourseName;

            await pool.request()
                .input('sourceCourseId', sql.BigInt, before.SourceCourseID)
                .input('courseName', sql.NVarChar, updatedName)
                .input('courseShortName', sql.NVarChar, before.CourseShortName)
                .input('courseCode', sql.NVarChar, before.CourseCode)
                .input('startDate', sql.DateTime, before.StartDate)
                .input('endDate', sql.DateTime, before.EndDate)
                .query(`
                    IF OBJECT_ID('stag_Dim_Course', 'U') IS NULL
                        CREATE TABLE stag_Dim_Course (
                            SourceCourseID BIGINT NOT NULL, CourseName NVARCHAR(1000),
                            CourseShortName NVARCHAR(255), CourseCode NVARCHAR(100),
                            StartDate DATETIME, EndDate DATETIME
                        );
                    TRUNCATE TABLE stag_Dim_Course;
                    INSERT INTO stag_Dim_Course (
                        SourceCourseID, CourseName, CourseShortName, CourseCode, StartDate, EndDate
                    ) VALUES (
                        @sourceCourseId, @courseName, @courseShortName, @courseCode, @startDate, @endDate
                    );
                    MERGE Dim_Course AS target
                    USING stag_Dim_Course AS source
                    ON target.SourceCourseID = source.SourceCourseID
                    WHEN MATCHED THEN
                        UPDATE SET CourseName = source.CourseName;
                `);

            const afterRes = await pool.request()
                .input('ck', sql.Int, targetCourseKey)
                .query(`
                    SELECT c.CourseKey, c.SourceCourseID, c.CourseName, c.CourseCode,
                           (SELECT COUNT(*) FROM Fact_Course_Grades WHERE CourseKey = c.CourseKey) AS FactRefCount
                    FROM Dim_Course c WHERE c.CourseKey = @ck;
                `);
            const after = afterRes.recordset[0];

            return res.json({
                success: true,
                message: `Đã đổ dữ liệu qua stag_Dim_Course và MERGE vào Dim_Course thành công (${Date.now() - t0}ms). Khóa CourseKey=${after.CourseKey} được giữ nguyên!`,
                data: {
                    targetTable: 'Dim_Course (Bảng B)',
                    stagingTable: 'stag_Dim_Course',
                    durationMs: Date.now() - t0,
                    keyPreserved: before.CourseKey === after.CourseKey,
                    surrogateKeyName: 'CourseKey',
                    surrogateKeyValue: after.CourseKey,
                    before: {
                        CourseKey: before.CourseKey,
                        SourceCourseID: before.SourceCourseID,
                        CourseCode: before.CourseCode,
                        CourseName: before.CourseName,
                        FactRefs: `${before.FactRefCount} dòng Fact_Course_Grades tham chiếu`
                    },
                    after: {
                        CourseKey: after.CourseKey,
                        SourceCourseID: after.SourceCourseID,
                        CourseCode: after.CourseCode,
                        CourseName: after.CourseName,
                        FactRefs: `${after.FactRefCount} dòng Fact_Course_Grades tham chiếu (An toàn 100%)`
                    }
                }
            });
        } else if (targetType === 'Fact_Course_Grades') {
            const targetCourseKey = parseInt(courseKey, 10);
            const rawVal = Math.min(100, Math.max(0, parseFloat(newRawGrade) || 8.5));

            const beforeRes = await pool.request()
                .input('uk', sql.Int, targetUserKey)
                .input('ck', sql.Int, targetCourseKey)
                .query(`
                    SELECT fcg.CourseGradeKey, fcg.UserKey, fcg.CourseKey, fcg.RawGrade, fcg.MaxGrade,
                           fcg.GradeScaled10, fcg.LetterGrade, fcg.GradeClassification, dc.CourseName
                    FROM Fact_Course_Grades fcg
                    JOIN Dim_Course dc ON fcg.CourseKey = dc.CourseKey
                    WHERE fcg.UserKey = @uk AND fcg.CourseKey = @ck;
                `);
            if (!beforeRes.recordset.length) {
                return res.status(404).json({ success: false, message: 'Không tìm thấy bản ghi điểm môn học.' });
            }
            const before = beforeRes.recordset[0];
            const maxG = parseFloat(before.MaxGrade) || 10.0;
            const scaled10 = maxG <= 10.0
                ? Math.min(10.0, Math.max(0.0, Number(rawVal.toFixed(2))))
                : Math.min(10.0, Math.max(0.0, Number(((rawVal / maxG) * 10.0).toFixed(2))));

            let letter = 'F', classification = 'Kém (Rớt môn)';
            if (scaled10 >= 8.5) { letter = 'A'; classification = 'Xuất sắc'; }
            else if (scaled10 >= 7.0) { letter = 'B'; classification = 'Khá'; }
            else if (scaled10 >= 5.5) { letter = 'C'; classification = 'Trung bình'; }
            else if (scaled10 >= 4.0) { letter = 'D'; classification = 'Trung bình yếu'; }

            const isPassed = scaled10 >= 4.0 ? 1 : 0;
            const isAtRisk = scaled10 < 5.0 ? 1 : 0;

            await pool.request()
                .input('uk', sql.Int, targetUserKey)
                .input('ck', sql.Int, targetCourseKey)
                .input('rawGrade', sql.Decimal(10, 2), rawVal)
                .input('scaled10', sql.Decimal(10, 2), scaled10)
                .input('letter', sql.VarChar(5), letter)
                .input('classify', sql.NVarChar(50), classification)
                .input('passed', sql.Bit, isPassed)
                .input('atRisk', sql.Bit, isAtRisk)
                .query(`
                    UPDATE Fact_Course_Grades
                    SET RawGrade = @rawGrade,
                        GradeScaled10 = @scaled10,
                        LetterGrade = @letter,
                        GradeClassification = @classify,
                        IsPassed = @passed,
                        IsAtRisk = @atRisk,
                        LastUpdated = GETDATE()
                    WHERE UserKey = @uk AND CourseKey = @ck;
                `);

            return res.json({
                success: true,
                message: `Đã biến đổi (Transform) và nạp điểm mới cho môn "${before.CourseName}" thành công (${Date.now() - t0}ms)!`,
                data: {
                    targetTable: 'Fact_Course_Grades (Bảng Fact)',
                    stagingTable: 'Transform trên RAM -> Update Fact',
                    durationMs: Date.now() - t0,
                    keyPreserved: true,
                    surrogateKeyName: 'UserKey + CourseKey',
                    surrogateKeyValue: `${before.UserKey} + ${before.CourseKey}`,
                    before: {
                        CourseName: before.CourseName,
                        RawGrade: before.RawGrade,
                        GradeScaled10: before.GradeScaled10,
                        LetterGrade: before.LetterGrade,
                        GradeClassification: before.GradeClassification
                    },
                    after: {
                        CourseName: before.CourseName,
                        RawGrade: rawVal,
                        GradeScaled10: scaled10,
                        LetterGrade: letter,
                        GradeClassification: classification
                    }
                }
            });
        }

        res.status(400).json({ success: false, message: 'Loại bảng demo không hợp lệ.' });
    } catch (err) {
        res.status(500).json({ success: false, message: err.message });
    }
});

app.get('*', (req, res) => {
    res.sendFile(path.join(__dirname, '../frontend/index.html'));
});

app.listen(PORT, () => {
    console.log(`[🚀] Cổng Sinh viên DLU đang chạy tại: http://localhost:${PORT}`);
});
