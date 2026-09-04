/**
 * dashboard.js — v2 (UI Enhanced)
 * Cổng Học tập Sinh viên DLU — Dashboard cá nhân
 * Tính năng v2:
 *  - Action Center dạng tab (Bài tập / Quiz) + tìm kiếm, lọc trạng thái, lọc môn, sắp xếp
 *  - Mặc định chỉ hiện "việc cần làm"; đếm ngược deadline tự cập nhật (live) mỗi 30s
 *  - KPI có progress ring, bấm card để nhảy tới section tương ứng
 *  - Timeline 7 ngày, skeleton loading, toast, dark/light theme, auto refresh 5 phút
 */

const RING_CIRCUMFERENCE = 119.4; // 2 * PI * r(19)

const state = {
    deadlines: [],
    quizzes: [],
    courses: [],
    deadlinesFetchedAt: null,
    activeTab: 'assignments',
    assign: { status: 'pending', search: '', course: 'all', sort: 'due_asc' },
    quiz: { status: 'pending', search: '', course: 'all', sort: 'close_asc' },
    transcriptSort: { key: null, dir: 'asc' },
    scoreYearFilter: 'all',
    lastUpdated: null
};

let scoreDistChartInstance = null;

// ============================ HELPERS ============================
const $ = (id) => document.getElementById(id);

function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// Format ngày giờ theo mốc UTC (giữ convention cũ: DB lưu giờ GMT+7, hiển thị qua getter UTC)
function fmtDate(dateStr) {
    if (!dateStr) return 'Không giới hạn';
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return 'Không giới hạn';
    const pad = (n) => String(n).padStart(2, '0');
    return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} ${pad(d.getUTCDate())}/${pad(d.getUTCMonth() + 1)}/${d.getUTCFullYear()}`;
}

// Countdown "live": MinutesRemaining do backend tính bằng đồng hồ DB (chính xác).
// Chỉ cần trừ số phút trôi qua kể từ lúc fetch -> không lo lệch múi giờ client/server.
function elapsedMin() {
    return state.deadlinesFetchedAt ? (Date.now() - state.deadlinesFetchedAt) / 60000 : 0;
}
function liveMins(item) {
    if (item.MinutesRemaining === null || item.MinutesRemaining === undefined) return null;
    return item.MinutesRemaining - elapsedMin();
}

function formatRemaining(mins) {
    if (mins === null) return { text: 'Không giới hạn', tone: 'success' };
    if (mins < 0) {
        const over = Math.abs(mins);
        const d = Math.floor(over / 1440);
        const h = Math.floor((over % 1440) / 60);
        const m = Math.floor(over % 60);
        const txt = d > 0 ? `${d} ngày` : h > 0 ? `${h} giờ` : `${m} phút`;
        return { text: `Quá hạn ${txt}`, tone: 'danger' };
    }
    const d = Math.floor(mins / 1440);
    const h = Math.floor((mins % 1440) / 60);
    const m = Math.floor(mins % 60);
    let text;
    if (d > 0) text = `Còn ${d} ngày ${h} giờ`;
    else if (h > 0) text = `Còn ${h} giờ ${m} phút`;
    else text = `Còn ${m} phút`;
    const tone = mins <= 1440 ? 'danger' : mins <= 2880 ? 'warning' : 'success';
    return { text, tone };
}

// % thanh progress (cửa sổ 7 ngày): càng gần deadline thanh càng đầy
function deadlinePct(mins) {
    if (mins === null) return 0;
    if (mins < 0) return 100;
    return Math.min(100, Math.max(3, 100 - (mins / (7 * 1440)) * 100));
}

function setRing(svgId, pct) {
    const svg = $(svgId);
    if (!svg) return;
    const fg = svg.querySelector('.ring-fg');
    if (!fg) return;
    const clamped = Math.max(0, Math.min(100, pct || 0));
    fg.setAttribute('stroke-dashoffset', (RING_CIRCUMFERENCE * (1 - clamped / 100)).toFixed(1));
}

// ============================ TOAST ============================
function showToast(message, type = 'info') {
    const wrap = $('toastContainer');
    if (!wrap) return;
    const icons = {
        success: 'fa-circle-check',
        error: 'fa-circle-xmark',
        warning: 'fa-triangle-exclamation',
        info: 'fa-circle-info'
    };
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.innerHTML = `<i class="fa-solid ${icons[type] || icons.info}"></i><div>${message}</div>`;
    wrap.appendChild(el);
    setTimeout(() => {
        el.classList.add('out');
        setTimeout(() => el.remove(), 380);
    }, 3500);
}

// ============================ THEME ============================
function applyTheme(theme, rerenderChart = true) {
    document.body.classList.toggle('light', theme === 'light');
    localStorage.setItem('dlu_theme', theme);
    const icon = $('themeIcon');
    if (icon) icon.className = theme === 'light' ? 'fa-solid fa-sun' : 'fa-solid fa-moon';
    if (rerenderChart && state.courses.length) renderChart();
}
function toggleTheme() {
    applyTheme(document.body.classList.contains('light') ? 'dark' : 'light');
}

// ============================ AUTH ============================
function checkAuthState() {
    const token = ApiService.getToken();
    const loginView = $('loginView');
    const dashboardView = $('dashboardView');

    if (!token) {
        loginView.classList.remove('hidden');
        dashboardView.classList.add('hidden');
    } else {
        loginView.classList.add('hidden');
        dashboardView.classList.remove('hidden');
        initStudentProfile();
        renderSkeletonAll();
        loadStudentData(true);
    }
}

async function handleLogin() {
    const username = $('txtUsername').value.trim();
    const password = $('txtPassword').value.trim();
    const errorBox = $('loginError');
    const btnLogin = $('btnLogin');

    errorBox.classList.add('hidden');
    btnLogin.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Đang xác thực...`;
    btnLogin.disabled = true;

    try {
        const res = await ApiService.login(username, password);
        if (res.success) {
            ApiService.setToken(res.token);
            localStorage.setItem('dlu_student_user', JSON.stringify(res.user));
            checkAuthState();
        } else {
            errorBox.textContent = res.message || 'Đăng nhập thất bại. Vui lòng kiểm tra lại.';
            errorBox.classList.remove('hidden');
            const card = document.querySelector('.login-card');
            card.classList.remove('shake');
            void card.offsetWidth; // reset animation
            card.classList.add('shake');
        }
    } catch (err) {
        errorBox.textContent = 'Không thể kết nối tới server. Vui lòng thử lại sau.';
        errorBox.classList.remove('hidden');
    } finally {
        btnLogin.innerHTML = `<span>Đăng nhập</span> <i class="fa-solid fa-arrow-right-to-bracket"></i>`;
        btnLogin.disabled = false;
    }
}

function initStudentProfile() {
    const userJson = localStorage.getItem('dlu_student_user');
    if (userJson) {
        const u = JSON.parse(userJson);
        $('txtFullName').textContent = u.fullName;
        $('txtIdNumber').textContent = `MSSV: ${u.idNumber}`;
        $('txtCohort').textContent = `Lớp: ${u.cohortName}`;
        $('txtInstitution').textContent = u.institution || 'Trường Đại học Đà Lạt';

        const initials = u.fullName.split(' ').map(n => n[0]).slice(-2).join('').toUpperCase();
        $('userAvatar').textContent = initials || 'SV';
    }
}

// ============================ SKELETON ============================
function renderSkeletonRow(tbodyId, cols, rows) {
    const tbody = $(tbodyId);
    if (!tbody) return;
    let html = '';
    for (let r = 0; r < rows; r++) {
        html += '<tr>';
        for (let c = 0; c < cols; c++) {
            html += `<td><div class="skeleton ${c % 2 === 0 ? 'w-80' : 'w-60'}"></div></td>`;
        }
        html += '</tr>';
    }
    tbody.innerHTML = html;
}
function renderSkeletonAll() {
    renderSkeletonRow('deadlinesTableBody', 6, 6);
    renderSkeletonRow('quizzesTableBody', 6, 5);
    renderSkeletonRow('coursesTableBody', 6, 4);
    const tl = $('timelineStrip');
    if (tl) tl.innerHTML = '';
}

// ============================ EMPTY STATE ============================
function emptyStateHTML(icon, title, desc) {
    return `<tr><td colspan="99"><div class="empty-state"><i class="fa-solid ${icon}"></i><h4>${title}</h4><p>${desc}</p></div></td></tr>`;
}

// ============================ DATA LOADING ============================
function updateLastUpdated() {
    const el = $('lastUpdated');
    if (!el || !state.lastUpdated) return;
    const t = state.lastUpdated.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });
    el.innerHTML = `<i class="fa-regular fa-clock"></i> Cập nhật ${t}`;
}

async function loadStudentData(silent = false) {
    const btn = $('btnRefresh');
    if (btn) btn.classList.add('loading');
    try {
        const results = await Promise.all([
            fetchSummary(),
            fetchDeadlines(),
            fetchCourses(),
            fetchQuizzes()
        ]);
        const fails = results.filter(r => !r).length;
        state.lastUpdated = new Date();
        updateLastUpdated();
        if (fails === 4) showToast('Không thể kết nối máy chủ. Kiểm tra backend & SQL Server.', 'error');
        else if (fails > 0) showToast('Một số dữ liệu không tải được (xem Console log).', 'warning');
        else if (!silent) showToast('Đã làm mới dữ liệu từ Data Warehouse.', 'success');
    } catch (e) {
        console.error('Lỗi loadStudentData:', e);
        showToast('Có lỗi khi tải dữ liệu.', 'error');
    } finally {
        if (btn) btn.classList.remove('loading');
    }
}

// 1. SUMMARY — 4 thẻ KPI
async function fetchSummary() {
    try {
        const res = await ApiService.getSummary();
        if (res.success) { renderSummary(res.data); return true; }
        return false;
    } catch (e) { console.error('Lỗi summary:', e); return false; }
}

function renderSummary(d) {
    // GPA (backend trả string hoặc number — chuẩn hóa về number)
    const g10 = parseFloat(d.gpa10) || 0;
    $('valGpa10').textContent = g10.toFixed(2);
    $('valGpa4').textContent = (parseFloat(d.gpa4) || 0).toFixed(2);

    const badgeGpa = $('badgeGpaClass');
    badgeGpa.textContent = d.gpaClassification;
    if (g10 >= 8.5) badgeGpa.className = 'badge badge-success';
    else if (g10 >= 7.0) badgeGpa.className = 'badge badge-info';
    else if (g10 >= 5.0) badgeGpa.className = 'badge badge-warning';
    else badgeGpa.className = 'badge badge-danger';

    // Hạn khẩn cấp
    $('valUrgentDeadlines').textContent = d.urgentDeadlines;
    const subUrgent = $('subUrgentText');
    const alertBanner = $('urgentAlertBox');
    if (d.urgentDeadlines > 0 || d.overdueCount > 0) {
        subUrgent.innerHTML = `<span class="tone-danger"><i class="fa-solid fa-triangle-exclamation"></i> Có ${d.urgentDeadlines} bài gấp & ${d.overdueCount} bài trễ!</span>`;
        alertBanner.classList.remove('hidden');
        $('urgentAlertDesc').textContent = `Bạn đang có ${d.urgentDeadlines} bài tập sắp hết hạn (< 48h) và ${d.overdueCount} bài chưa nộp đã quá hạn.`;
    } else {
        subUrgent.innerHTML = `<i class="fa-solid fa-circle-check tone-success"></i> Không có bài tập gấp trong 48h tới.`;
        alertBanner.classList.add('hidden');
    }

    // On-time rate + progress ring
    $('valOnTimeRate').textContent = `${d.onTimeRate}%`;
    setRing('ringOnTime', parseFloat(d.onTimeRate) || 0);

    // Quiz + progress ring (điểm /10 -> %)
    $('valAvgQuizScore').textContent = `${d.avgQuizScore} / 10`;
    $('valQuizCount').textContent = `${d.completedQuizzes} bài hoàn thành`;
    setRing('ringQuiz', (parseFloat(d.avgQuizScore) || 0) * 10);
}

// 2. DEADLINES — bài tập & hạn nộp
async function fetchDeadlines() {
    try {
        const res = await ApiService.getDeadlines();
        if (!res.success) return false;
        state.deadlines = res.data || [];
        state.deadlinesFetchedAt = Date.now();
        populateCourseSelect($('assignCourse'), state.deadlines.map(i => i.CourseName), state.assign.course);
        renderTimeline();
        renderAssignments();
        return true;
    } catch (e) { console.error('Lỗi deadlines:', e); return false; }
}

function populateCourseSelect(select, names, current) {
    const unique = [...new Set(names)].sort((a, b) => a.localeCompare(b, 'vi'));
    select.innerHTML = '<option value="all">Tất cả môn học</option>' +
        unique.map(n => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join('');
    select.value = unique.includes(current) ? current : 'all';
}

// Timeline: đếm khối lượng việc theo cửa sổ thời gian (dựa MinutesRemaining — cùng đồng hồ DB)
function renderTimeline() {
    const pending = state.deadlines.filter(i => i.SubmissionStatus !== 'submitted');
    const m = (i) => liveMins(i);
    const cnt = (fn) => pending.filter(i => m(i) !== null && fn(m(i))).length;
    const noLimit = pending.filter(i => m(i) === null).length;
    const chips = [
        { icon: 'fa-triangle-exclamation', label: 'Quá hạn', n: cnt(v => v < 0), cls: 'tl-danger' },
        { icon: 'fa-stopwatch', label: 'Trong 24h', n: cnt(v => v >= 0 && v <= 1440), cls: 'tl-warning' },
        { icon: 'fa-hourglass-half', label: '24–48h', n: cnt(v => v > 1440 && v <= 2880), cls: 'tl-warning' },
        { icon: 'fa-calendar-week', label: 'Tuần này', n: cnt(v => v > 2880 && v <= 10080), cls: 'tl-info' },
        { icon: 'fa-calendar-days', label: 'Sau 7 ngày', n: cnt(v => v > 10080) + noLimit, cls: 'tl-muted' },
        { icon: 'fa-circle-check', label: 'Đã nộp', n: state.deadlines.length - pending.length, cls: 'tl-success' }
    ];
    $('timelineStrip').innerHTML = chips.map(c =>
        `<span class="tl-chip ${c.cls} ${c.n === 0 ? 'is-zero' : ''}"><i class="fa-solid ${c.icon}"></i> ${c.label}: <strong>${c.n}</strong></span>`
    ).join('');
}

// 3. COURSES — bảng điểm
async function fetchCourses() {
    try {
        const res = await ApiService.getCourses();
        if (!res.success) return false;
        renderCourses(res.data || []);
        return true;
    } catch (e) { console.error('Lỗi courses:', e); return false; }
}

// 4. QUIZZES — bài kiểm tra trắc nghiệm
async function fetchQuizzes() {
    try {
        const res = await ApiService.getQuizzes();
        if (!res.success) return false;
        state.quizzes = res.data || [];
        populateCourseSelect($('quizCourse'), state.quizzes.map(q => q.CourseName), state.quiz.course);
        renderQuizzes();
        return true;
    } catch (e) { console.error('Lỗi quizzes:', e); return false; }
}

// ============================ ACTION CENTER: TABS & PILLS ============================
function switchTab(tab) {
    state.activeTab = tab;
    document.querySelectorAll('#actionCenter .tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
    $('panel-assignments').classList.toggle('active', tab === 'assignments');
    $('panel-quizzes').classList.toggle('active', tab === 'quizzes');
}

function bindPills(rowId, filterKey) {
    const row = $(rowId);
    if (!row) return;
    row.querySelectorAll('.pill').forEach(p => {
        p.addEventListener('click', () => {
            row.querySelectorAll('.pill').forEach(x => x.classList.remove('active'));
            p.classList.add('active');
            state[filterKey].status = p.dataset.status;
            if (filterKey === 'assign') renderAssignments();
            else renderQuizzes();
        });
    });
}

// Kích hoạt pill theo trạng thái (dùng khi điều hướng từ KPI/banner)
function setPill(rowId, status) {
    const row = $(rowId);
    if (!row) return;
    const target = row.querySelector(`.pill[data-status="${status}"]`);
    if (target) target.click();
}

// ============================ ASSIGNMENTS ============================
// Phân loại: submitted / overdue / urgent / pending (draft chưa gửi vẫn tính là việc cần làm)
function assignCategory(item) {
    if (item.SubmissionStatus === 'submitted') return 'submitted';
    const mins = liveMins(item);
    if (mins !== null && mins < 0) return 'overdue';
    if (mins !== null && mins <= 48 * 60) return 'urgent';
    return 'pending';
}

function filterAssignments() {
    const f = state.assign;
    return state.deadlines.filter(item => {
        const cat = assignCategory(item);
        if (f.status === 'pending' && cat === 'submitted') return false;
        if (f.status === 'urgent' && cat !== 'urgent') return false;
        if (f.status === 'overdue' && cat !== 'overdue') return false;
        if (f.status === 'submitted' && cat !== 'submitted') return false;
        if (f.course !== 'all' && item.CourseName !== f.course) return false;
        const q = f.search.trim().toLowerCase();
        if (q && !(item.AssignName.toLowerCase().includes(q) || item.CourseName.toLowerCase().includes(q))) return false;
        return true;
    });
}

function sortAssignments(list) {
    const arr = [...list];
    const grp = (i) => assignCategory(i) === 'submitted' ? 1 : 0;
    const mins = (i) => { const v = liveMins(i); return v === null ? Number.MAX_SAFE_INTEGER : v; };
    if (state.assign.sort === 'due_desc') arr.sort((a, b) => grp(a) - grp(b) || mins(b) - mins(a));
    else if (state.assign.sort === 'course') arr.sort((a, b) => a.CourseName.localeCompare(b.CourseName, 'vi') || mins(a) - mins(b));
    else arr.sort((a, b) => grp(a) - grp(b) || mins(a) - mins(b));
    return arr;
}

function renderAssignments() {
    const tbody = $('deadlinesTableBody');
    if (!tbody) return;
    const list = sortAssignments(filterAssignments());
    const total = state.deadlines.length;

    // Badge đếm trên tab = số việc cần làm (chưa nộp)
    const need = state.deadlines.filter(i => assignCategory(i) !== 'submitted').length;
    const tabCount = $('tabAssignCount');
    tabCount.textContent = need;
    tabCount.classList.toggle('alert', need > 0);

    if (total === 0) {
        tbody.innerHTML = emptyStateHTML('fa-mug-hot', 'Không có bài tập nào', 'Tuyệt vời! Bạn không còn bài tập nào trong các môn đang học.');
        $('assignResultCount').textContent = '';
        return;
    }
    if (list.length === 0) {
        const f = state.assign;
        tbody.innerHTML = emptyStateHTML(
            f.status === 'submitted' ? 'fa-box-archive' : 'fa-filter',
            'Không có kết quả phù hợp',
            'Thử đổi bộ lọc, chọn "Tất cả" hoặc xóa từ khóa tìm kiếm.'
        );
        $('assignResultCount').textContent = `0 / ${total}`;
        return;
    }

    $('assignResultCount').textContent = `Hiển thị ${list.length} / ${total}`;
    tbody.innerHTML = list.map(item => {
        const cat = assignCategory(item);
        const mins = liveMins(item);

        // Cột "Còn lại": text + thanh progress thời gian
        let remainCell;
        if (item.DueDate == null) {
            remainCell = '<span class="text-muted-sm">Không giới hạn</span>';
        } else {
            const r = formatRemaining(mins);
            remainCell = `<div class="countdown-wrap"><span class="countdown-text tone-${r.tone}">${r.text}</span><div class="mini-progress"><span class="tone-${r.tone}" style="width:${deadlinePct(mins)}%"></span></div></div>`;
        }

        // Cột trạng thái
        let badge;
        if (cat === 'submitted') badge = item.IsLate ? '<span class="badge badge-warning">Đã nộp (Trễ)</span>' : '<span class="badge badge-success">Đã nộp đúng hạn</span>';
        else if (cat === 'overdue') badge = '<span class="badge badge-danger">Quá hạn nộp</span>';
        else if (cat === 'urgent') badge = '<span class="badge badge-danger">Sắp hết hạn</span>';
        else badge = item.SubmissionStatus === 'draft' ? '<span class="badge badge-warning">Nháp — chưa gửi</span>' : '<span class="badge badge-info">Chưa nộp</span>';

        // Cột điểm
        let grade = '<span class="text-muted-sm">--</span>';
        if (item.GradeScaled10 !== null && item.GradeScaled10 !== undefined && item.GradeScaled10 > 0) {
            grade = `<span class="score-strong ${item.GradeScaled10 >= 5 ? 'score-pass' : 'score-fail'}">${item.GradeScaled10.toFixed(2)}đ</span>`;
        } else if (item.SubmissionStatus === 'submitted' || item.SubmissionStatus === 'draft') {
            grade = '<span class="text-muted-sm">Chờ chấm</span>';
        }

        return `<tr>
            <td><div class="assign-name">${escapeHtml(item.AssignName)}</div>${item.SubmissionStatus === 'draft' ? '<span class="draft-hint"><i class="fa-solid fa-pen"></i> Bản nháp chưa gửi</span>' : ''}</td>
            <td><span class="course-name">${escapeHtml(item.CourseName)}</span></td>
            <td><span class="text-muted-sm">${fmtDate(item.DueDate)}</span></td>
            <td>${remainCell}</td>
            <td>${badge}</td>
            <td>${grade}</td>
        </tr>`;
    }).join('');
}

// ============================ QUIZZES ============================
// Phân loại: done / doing / closed / pending (IsClosed do backend tính bằng đồng hồ DB)
function quizCategory(q) {
    if (q.State === 'finished') return 'done';
    if (q.State === 'inprogress') return 'doing';
    if (q.IsClosed === 1 || q.IsClosed === true) return 'closed';
    return 'pending';
}

function filterQuizzes() {
    const f = state.quiz;
    return state.quizzes.filter(q => {
        const cat = quizCategory(q);
        if (f.status !== 'all' && cat !== f.status) return false;
        if (f.course !== 'all' && q.CourseName !== f.course) return false;
        const s = f.search.trim().toLowerCase();
        if (s && !(q.QuizName.toLowerCase().includes(s) || q.CourseName.toLowerCase().includes(s))) return false;
        return true;
    });
}

function sortQuizzes(list) {
    const arr = [...list];
    const closeMs = (q) => q.TimeClose ? new Date(q.TimeClose).getTime() : Number.MAX_SAFE_INTEGER;
    if (state.quiz.sort === 'score_desc') arr.sort((a, b) => (b.ScoreScaled10 ?? -1) - (a.ScoreScaled10 ?? -1));
    else if (state.quiz.sort === 'course') arr.sort((a, b) => a.CourseName.localeCompare(b.CourseName, 'vi') || closeMs(a) - closeMs(b));
    else arr.sort((a, b) => closeMs(a) - closeMs(b));
    return arr;
}

function renderQuizzes() {
    const tbody = $('quizzesTableBody');
    if (!tbody) return;
    const list = sortQuizzes(filterQuizzes());
    const total = state.quizzes.length;

    const need = state.quizzes.filter(q => ['pending', 'doing'].includes(quizCategory(q))).length;
    const tabCount = $('tabQuizCount');
    tabCount.textContent = need;
    tabCount.classList.toggle('alert', need > 0);

    if (total === 0) {
        tbody.innerHTML = emptyStateHTML('fa-stopwatch-20', 'Không có bài kiểm tra', 'Chưa có quiz nào trong các môn bạn đang học.');
        $('quizResultCount').textContent = '';
        return;
    }
    if (list.length === 0) {
        tbody.innerHTML = emptyStateHTML('fa-filter', 'Không có kết quả phù hợp', 'Thử chọn "Tất cả" hoặc xóa từ khóa tìm kiếm.');
        $('quizResultCount').textContent = `0 / ${total}`;
        return;
    }

    $('quizResultCount').textContent = `Hiển thị ${list.length} / ${total}`;
    tbody.innerHTML = list.map(q => {
        const cat = quizCategory(q);
        const durationText = q.TimeLimitMinutes > 0 ? `${q.TimeLimitMinutes} phút` : 'Tự do';
        const closeFormatted = q.TimeClose ? fmtDate(q.TimeClose) : 'Không đóng';

        // Cột điểm: kèm số lần làm + thời gian làm thực tế
        let scoreCell = '<span class="text-muted-sm">--</span>';
        if (q.State === 'finished' && q.ScoreScaled10 !== null && q.ScoreScaled10 !== undefined) {
            scoreCell = `<span class="score-strong ${q.ScoreScaled10 >= 5 ? 'score-pass' : 'score-fail'}">${q.ScoreScaled10.toFixed(2)}đ</span>` +
                `<span class="attempt-note">Lần ${q.AttemptNumber || 1}${q.DurationSeconds ? ' · ' + Math.round(q.DurationSeconds / 60) + ' phút' : ''}</span>`;
        } else if (q.State === 'inprogress') {
            scoreCell = `<span class="attempt-note">Lần ${q.AttemptNumber || 1} đang thực hiện...</span>`;
        }

        let badge;
        if (cat === 'done') badge = '<span class="badge badge-success">Đã hoàn thành</span>';
        else if (cat === 'doing') badge = '<span class="badge badge-purple">Đang làm</span>';
        else if (cat === 'closed') badge = '<span class="badge badge-danger">Đã đóng</span>';
        else badge = '<span class="badge badge-warning">Chưa làm</span>';

        return `<tr>
            <td><div class="assign-name">${escapeHtml(q.QuizName)}</div></td>
            <td><span class="course-name">${escapeHtml(q.CourseName)}</span></td>
            <td><span class="text-muted-sm">${durationText}</span></td>
            <td><span class="text-muted-sm">${closeFormatted}</span></td>
            <td>${scoreCell}</td>
            <td>${badge}</td>
        </tr>`;
    }).join('');
}

// ============================ TRANSCRIPT (Bảng điểm) ============================
function sortVal(c, key) {
    if (key === 'name') return c.CourseName || '';
    if (key === 'quiz') return c.QuizAverage ?? -1;
    if (key === 'assign') return c.AssignAverage ?? -1;
    return c.FinalGrade10 ?? -1;
}

function renderCourses(data) {
    state.courses = data;
    const tbody = $('coursesTableBody');
    if (!tbody) return;

    if (!data.length) {
        tbody.innerHTML = emptyStateHTML('fa-graduation-cap', 'Chưa có dữ liệu bảng điểm', 'Dữ liệu sẽ xuất hiện sau khi ETL đồng bộ từ Moodle.');
        renderChart();
        return;
    }

    let list = [...data];
    const { key, dir } = state.transcriptSort;
    if (key) {
        const mul = dir === 'asc' ? 1 : -1;
        list.sort((a, b) => {
            const va = sortVal(a, key), vb = sortVal(b, key);
            if (typeof va === 'string') return va.localeCompare(vb, 'vi') * mul;
            return (va - vb) * mul;
        });
    }

    tbody.innerHTML = list.map(c => {
        const grade10 = (c.FinalGrade10 !== null && c.FinalGrade10 !== undefined) ? c.FinalGrade10.toFixed(2) : '--';
        const isDanger = c.FinalGrade10 !== null && c.FinalGrade10 < 5.0;

        let letterBadge = 'badge-success';
        if (c.LetterGrade === 'F') letterBadge = 'badge-danger';
        else if (c.LetterGrade === 'D') letterBadge = 'badge-warning';

        return `<tr>
            <td><div class="assign-name">${escapeHtml(c.CourseName)}</div></td>
            <td>${progressCell(c)}</td>
            <td>${(c.QuizAverage ?? 0).toFixed(2)}</td>
            <td>${(c.AssignAverage ?? 0).toFixed(2)}</td>
            <td><strong class="${isDanger ? 'grade-fail' : 'grade-pass'}">${grade10}</strong></td>
            <td><span class="badge ${letterBadge}">${escapeHtml(c.LetterGrade)}</span><div class="classify-small">${escapeHtml(c.GradeClassification)}</div></td>
        </tr>`;
    }).join('');

    renderChart();
}

// Ô tiến độ: BT 5/8 · Quiz 3/4 (cần backend trả thêm TotalAssigns/SubmittedAssigns/TotalQuizzes/DoneQuizzes —
// nếu chưa có thì hiển thị '--' để vẫn tương thích ngược)
function progressCell(c) {
    const hasA = c.TotalAssigns !== undefined && c.TotalAssigns !== null && c.TotalAssigns > 0;
    const hasQ = c.TotalQuizzes !== undefined && c.TotalQuizzes !== null && c.TotalQuizzes > 0;
    if (!hasA && !hasQ) return '<span class="text-muted-sm">--</span>';

    let bars = '';
    const labels = [];
    if (hasA) {
        const pct = Math.round(100 * (c.SubmittedAssigns || 0) / c.TotalAssigns);
        bars += `<div class="mini-progress"><span class="p-cyan" style="width:${pct}%"></span></div>`;
        labels.push(`BT ${c.SubmittedAssigns || 0}/${c.TotalAssigns}`);
    }
    if (hasQ) {
        const pct = Math.round(100 * (c.DoneQuizzes || 0) / c.TotalQuizzes);
        bars += `<div class="mini-progress"><span class="p-purple" style="width:${pct}%"></span></div>`;
        labels.push(`Quiz ${c.DoneQuizzes || 0}/${c.TotalQuizzes}`);
    }
    return `<div class="progress-cell">${bars}<span class="progress-label">${labels.join(' · ')}</span></div>`;
}

// ============================ PHỔ ĐIỂM (1 - 10) & BỘ LỌC NĂM ============================
function chartThemeColors() {
    const cs = getComputedStyle(document.body);
    const light = document.body.classList.contains('light');
    return {
        text: (cs.getPropertyValue('--text-secondary') || '#94a3b8').trim(),
        grid: light ? 'rgba(15, 23, 42, 0.08)' : 'rgba(255, 255, 255, 0.05)',
        tooltipBg: light ? '#ffffff' : '#1e293b',
        tooltipText: light ? '#0f172a' : '#f8fafc'
    };
}

function updateScoreYearOptions() {
    const yearSelect = $('scoreYearSelect');
    if (!yearSelect || !state.courses.length) return;

    const yearsInData = state.courses.map(c => Number(c.Year)).filter(n => !isNaN(n) && n > 0);
    const maxYear = yearsInData.length ? Math.max(...yearsInData) : new Date().getFullYear();
    const currentYear = maxYear || 2026;
    const prevYear = currentYear - 1;

    // Cập nhật nhãn trên các nút bấm
    const btnCurrent = document.querySelector('#scoreYearToggle button[data-year="current"]');
    if (btnCurrent) btnCurrent.innerHTML = `<i class="fa-solid fa-calendar-check"></i> Năm nay (${currentYear})`;

    const btnPrevious = document.querySelector('#scoreYearToggle button[data-year="previous"]');
    if (btnPrevious) btnPrevious.innerHTML = `<i class="fa-solid fa-calendar-minus"></i> Năm trước (${prevYear})`;

    const availableYears = [...new Set(yearsInData)].sort((a, b) => b - a);
    const existingValues = Array.from(yearSelect.options).map(o => o.value);

    availableYears.forEach(yr => {
        const val = String(yr);
        if (!existingValues.includes(val)) {
            const opt = document.createElement('option');
            opt.value = val;
            opt.textContent = `Năm ${yr}`;
            yearSelect.appendChild(opt);
        }
    });
}

function renderScoreDistributionChart() {
    const canvas = $('scoreDistributionChart');
    if (!canvas) return;

    updateScoreYearOptions();

    if (!state.courses.length) {
        if (scoreDistChartInstance) {
            scoreDistChartInstance.destroy();
            scoreDistChartInstance = null;
        }
        const summaryEl = $('scoreDistSummary');
        if (summaryEl) summaryEl.innerHTML = '<span class="text-muted-sm">Chưa có dữ liệu học phần</span>';
        return;
    }

    // 1. Xác định năm hiện tại & các năm có trong CSDL
    const yearsInData = state.courses.map(c => Number(c.Year)).filter(n => !isNaN(n) && n > 0);
    const maxYear = yearsInData.length ? Math.max(...yearsInData) : new Date().getFullYear();
    const currentYear = maxYear || 2026;
    const prevYear = currentYear - 1;
    const filter = state.scoreYearFilter;

    // 2. Lọc học phần theo bộ lọc năm
    let filteredCourses = state.courses;
    let currentFilterLabel = 'Tất cả các năm';

    if (filter === 'current') {
        filteredCourses = state.courses.filter(c => {
            const y = c.Year !== undefined && c.Year !== null ? Number(c.Year) : currentYear;
            return y === currentYear;
        });
        currentFilterLabel = `Năm nay (${currentYear})`;
    } else if (filter === 'previous') {
        filteredCourses = state.courses.filter(c => {
            const y = c.Year !== undefined && c.Year !== null ? Number(c.Year) : null;
            return y === prevYear;
        });
        currentFilterLabel = `Năm trước (${prevYear})`;
    } else if (filter !== 'all') {
        const targetYr = parseInt(filter, 10);
        if (!isNaN(targetYr)) {
            filteredCourses = state.courses.filter(c => {
                const y = c.Year !== undefined && c.Year !== null ? Number(c.Year) : currentYear;
                return y === targetYr;
            });
            currentFilterLabel = `Năm ${targetYr}`;
        }
    }

    // 3. Phân bổ điểm 1-10 (Thang 1 đến 10)
    const scoreLabels = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10'];
    const counts = new Array(10).fill(0);
    const coursesInScore = Array.from({ length: 10 }, () => []);

    filteredCourses.forEach(c => {
        if (c.FinalGrade10 !== null && c.FinalGrade10 !== undefined) {
            const val = parseFloat(c.FinalGrade10);
            const bucket = Math.min(10, Math.max(1, Math.round(val)));
            counts[bucket - 1]++;
            coursesInScore[bucket - 1].push({
                name: c.CourseName,
                score: val.toFixed(2),
                letter: c.LetterGrade || '--',
                classification: c.GradeClassification || ''
            });
        }
    });

    // Bảng màu phân hóa học lực cho từng mức điểm 1-10
    const barColors = [
        'rgba(239, 68, 68, 0.85)',   // 1 điểm: Đỏ rực (Rớt môn)
        'rgba(239, 68, 68, 0.85)',   // 2 điểm: Đỏ rực
        'rgba(239, 68, 68, 0.85)',   // 3 điểm: Đỏ rực
        'rgba(249, 115, 22, 0.85)',  // 4 điểm: Cam (Trung bình yếu / D)
        'rgba(234, 179, 8, 0.85)',   // 5 điểm: Vàng hổ phách (Trung bình / C)
        'rgba(234, 179, 8, 0.85)',   // 6 điểm: Vàng hổ phách (Trung bình / C)
        'rgba(16, 185, 129, 0.85)',  // 7 điểm: Xanh lá (Khá / B)
        'rgba(16, 185, 129, 0.85)',  // 8 điểm: Xanh lá (Khá / B)
        'rgba(6, 182, 212, 0.85)',   // 9 điểm: Xanh Cyan (Giỏi / A)
        'rgba(168, 85, 247, 0.85)'   // 10 điểm: Tím sang trọng (Xuất sắc / A+)
    ];
    const borderColors = [
        '#ef4444', '#ef4444', '#ef4444',
        '#f97316',
        '#eab308', '#eab308',
        '#10b981', '#10b981',
        '#06b6d4',
        '#a855f7'
    ];

    const th = chartThemeColors();
    if (scoreDistChartInstance) {
        scoreDistChartInstance.destroy();
        scoreDistChartInstance = null;
    }

    const maxCount = Math.max(...counts, 0);

    scoreDistChartInstance = new Chart(canvas.getContext('2d'), {
        type: 'bar',
        data: {
            labels: scoreLabels.map(s => `${s} điểm`),
            datasets: [{
                label: 'Số lượng môn học',
                data: counts,
                backgroundColor: barColors,
                borderColor: borderColors,
                borderWidth: 1.5,
                borderRadius: 8,
                maxBarThickness: 45
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 450 },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: th.tooltipBg,
                    titleColor: th.tooltipText,
                    bodyColor: th.text,
                    padding: 12,
                    boxPadding: 6,
                    cornerRadius: 8,
                    callbacks: {
                        title: (items) => {
                            const idx = items[0].dataIndex;
                            const count = counts[idx];
                            return `Thang điểm ${scoreLabels[idx]} (${count} môn học)`;
                        },
                        label: (item) => {
                            const idx = item.dataIndex;
                            const list = coursesInScore[idx];
                            if (!list.length) return ' Không có môn học nào ở mức này.';
                            return list.map(c => `• ${c.name}: ${c.score} (${c.letter} - ${c.classification})`);
                        }
                    }
                }
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { color: th.text, font: { size: 11, family: 'Inter', weight: 600 } }
                },
                y: {
                    min: 0,
                    max: Math.max(maxCount + 1, 4),
                    ticks: {
                        stepSize: 1,
                        precision: 0,
                        color: th.text,
                        font: { size: 11, family: 'Inter' }
                    },
                    grid: { color: th.grid },
                    title: {
                        display: true,
                        text: 'Số môn đạt điểm',
                        color: th.text,
                        font: { size: 10, family: 'Inter' }
                    }
                }
            }
        }
    });

    // 4. Render thống kê tóm tắt nhanh
    const summaryEl = $('scoreDistSummary');
    if (summaryEl) {
        const total = filteredCourses.length;
        const validGrades = filteredCourses.filter(c => c.FinalGrade10 !== null && c.FinalGrade10 !== undefined);
        const avg = validGrades.length ? (validGrades.reduce((acc, c) => acc + c.FinalGrade10, 0) / validGrades.length).toFixed(2) : '--';
        const passed = validGrades.filter(c => c.FinalGrade10 >= 4.0).length;
        const failed = validGrades.filter(c => c.FinalGrade10 < 4.0).length;

        if (total === 0) {
            summaryEl.innerHTML = `
                <div style="width: 100%; text-align: center; color: var(--text-secondary); padding: 8px 0;">
                    <i class="fa-solid fa-circle-info"></i> Không tìm thấy môn học nào thuộc <strong>${escapeHtml(currentFilterLabel)}</strong>.
                </div>
            `;
        } else {
            summaryEl.innerHTML = `
                <div style="display:flex; align-items:center; gap:6px;">
                    <i class="fa-solid fa-book-bookmark text-cyan"></i>
                    <span>Tổng môn: <strong style="color:var(--text-primary); font-weight:700;">${total}</strong></span>
                </div>
                <div style="display:flex; align-items:center; gap:6px;">
                    <i class="fa-solid fa-calculator text-purple"></i>
                    <span>Điểm TB (${escapeHtml(currentFilterLabel)}): <strong style="color:var(--text-primary); font-weight:700;">${avg}</strong></span>
                </div>
                <div style="display:flex; align-items:center; gap:6px;">
                    <i class="fa-solid fa-circle-check tone-success"></i>
                    <span>Đạt (≥ 4.0): <strong class="tone-success" style="font-weight:700;">${passed}</strong></span>
                </div>
                <div style="display:flex; align-items:center; gap:6px;">
                    <i class="fa-solid fa-triangle-exclamation tone-danger"></i>
                    <span>Cần cải thiện (< 4.0): <strong class="tone-danger" style="font-weight:700;">${failed}</strong></span>
                </div>
            `;
        }
    }
}

// Giữ alias tương thích
const renderChart = renderScoreDistributionChart;

// ============================ ĐIỀU HƯỚNG TỪ KPI/BANNER ============================
function goToFilteredTab(tab, pillStatus) {
    switchTab(tab);
    setPill(tab === 'assignments' ? 'assignPills' : 'quizPills', pillStatus);
    $('actionCenter').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ============================ EVENT BINDINGS ============================
function debounce(fn, ms) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function bindEvents() {
    // Login / Logout
    $('loginForm').addEventListener('submit', async (e) => { e.preventDefault(); await handleLogin(); });
    $('btnLogout').addEventListener('click', () => {
        if (confirm('Bạn có chắc chắn muốn đăng xuất?')) {
            ApiService.clearToken();
            checkAuthState();
        }
    });

    // Theme sáng/tối
    $('btnTheme').addEventListener('click', toggleTheme);

    // Refresh thủ công + tự động refresh mỗi 5 phút
    $('btnRefresh').addEventListener('click', () => loadStudentData(false));
    setInterval(() => { if (ApiService.getToken()) loadStudentData(true); }, 5 * 60 * 1000);

    // Countdown tick: render lại danh sách bài tập mỗi 30s để "Còn lại" luôn chính xác
    setInterval(() => { if (state.deadlines.length) renderAssignments(); }, 30 * 1000);

    // Tabs
    document.querySelectorAll('#actionCenter .tab').forEach(t => t.addEventListener('click', () => switchTab(t.dataset.tab)));

    // Filter pills
    bindPills('assignPills', 'assign');
    bindPills('quizPills', 'quiz');

    // Tìm kiếm (debounce 200ms)
    const onAssignSearch = debounce(() => { state.assign.search = $('assignSearch').value; renderAssignments(); }, 200);
    const onQuizSearch = debounce(() => { state.quiz.search = $('quizSearch').value; renderQuizzes(); }, 200);
    $('assignSearch').addEventListener('input', onAssignSearch);
    $('quizSearch').addEventListener('input', onQuizSearch);

    // Lọc môn + sắp xếp
    $('assignCourse').addEventListener('change', (e) => { state.assign.course = e.target.value; renderAssignments(); });
    $('assignSort').addEventListener('change', (e) => { state.assign.sort = e.target.value; renderAssignments(); });
    $('quizCourse').addEventListener('change', (e) => { state.quiz.course = e.target.value; renderQuizzes(); });
    $('quizSort').addEventListener('change', (e) => { state.quiz.sort = e.target.value; renderQuizzes(); });

    // Filter phổ điểm theo năm (Nút bấm: Tất cả / Năm nay / Năm trước)
    document.querySelectorAll('#scoreYearToggle button').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#scoreYearToggle button').forEach(x => x.classList.remove('active'));
            btn.classList.add('active');
            const val = btn.dataset.year;
            state.scoreYearFilter = val;
            const selectEl = $('scoreYearSelect');
            if (selectEl) selectEl.value = val;
            renderScoreDistributionChart();
        });
    });

    // Filter phổ điểm theo năm (Dropdown chọn từng năm cụ thể)
    const scoreSelect = $('scoreYearSelect');
    if (scoreSelect) {
        scoreSelect.addEventListener('change', (e) => {
            const val = e.target.value;
            state.scoreYearFilter = val;
            document.querySelectorAll('#scoreYearToggle button').forEach(b => {
                b.classList.toggle('active', b.dataset.year === val);
            });
            renderScoreDistributionChart();
        });
    }

    // KPI click → nhảy tới section tương ứng
    $('kpiGpa').addEventListener('click', () => $('transcriptSection').scrollIntoView({ behavior: 'smooth', block: 'start' }));
    $('kpiUrgent').addEventListener('click', () => goToFilteredTab('assignments', 'pending'));
    $('kpiOnTime').addEventListener('click', () => goToFilteredTab('assignments', 'all'));
    $('kpiQuiz').addEventListener('click', () => goToFilteredTab('quizzes', 'all'));

    // Banner cảnh báo: "Xem việc cần làm"
    $('btnViewUrgent').addEventListener('click', () => goToFilteredTab('assignments', 'pending'));

    // Sắp xếp bảng điểm (click tiêu đề cột)
    document.querySelectorAll('#transcriptSection .th-sortable').forEach(th => {
        th.addEventListener('click', () => {
            const key = th.dataset.sort;
            if (state.transcriptSort.key === key) state.transcriptSort.dir = state.transcriptSort.dir === 'asc' ? 'desc' : 'asc';
            else state.transcriptSort = { key, dir: 'asc' };
            document.querySelectorAll('#transcriptSection .th-sortable').forEach(t => t.classList.remove('asc', 'desc'));
            th.classList.add(state.transcriptSort.dir);
            renderCourses(state.courses);
        });
    });

    // Phím tắt "/" focus ô tìm kiếm của tab đang mở
    document.addEventListener('keydown', (e) => {
        if (e.key !== '/') return;
        const tag = (document.activeElement && document.activeElement.tagName) || '';
        if (['INPUT', 'SELECT', 'TEXTAREA'].includes(tag)) return;
        e.preventDefault();
        const input = state.activeTab === 'assignments' ? $('assignSearch') : $('quizSearch');
        input.focus();
        input.select();
    });
}

// ============================ INIT ============================
document.addEventListener('DOMContentLoaded', () => {
    applyTheme(localStorage.getItem('dlu_theme') || 'dark', false);
    bindEvents();
    checkAuthState();
});
