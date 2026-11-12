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
    wrongQuestions: [],
    deadlinesFetchedAt: null,
    activeTab: 'assignments',
    assign: { status: 'pending', search: '', course: 'all', sort: 'due_asc' },
    quiz: { status: 'pending', search: '', course: 'all', sort: 'close_asc' },
    review: { search: '', course: 'all' },
    transcriptSort: { key: null, dir: 'asc' },
    scoreYearFilter: 'all',
    lastUpdated: null
};

let scoreDistChartInstance = null;
let etlColPruningChartInstance = null;
let etlVersionTimeChartInstance = null;

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
    if (mins < 0) return { text: 'Quá hạn', tone: 'danger' };
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

function applyTheme(theme, rerenderChart = true) {
    document.body.classList.toggle('light', theme === 'light');
    localStorage.setItem('dlu_theme', theme);
    const icon = $('themeIcon');
    if (icon) icon.className = theme === 'light' ? 'fa-solid fa-sun' : 'fa-solid fa-moon';
    if (rerenderChart && state.courses.length) renderChart();
    if (rerenderChart && state.etlCostData) renderEtlCharts(state.etlCostData.costTable);
}
function toggleTheme() {
    applyTheme(document.body.classList.contains('light') ? 'dark' : 'light');
}

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
        btnLogin.innerHTML = '<span>Đăng nhập</span>';
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
    const rl = $('reviewList');
    if (rl) {
        rl.innerHTML = Array.from({ length: 3 }).map(() => `
            <div class="review-item">
                <div class="review-item-head">
                    <div class="skeleton w-80" style="height:14px;"></div>
                </div>
            </div>`).join('');
    }
    const tl = $('timelineStrip');
    if (tl) tl.innerHTML = '';
}

function emptyStateHTML(title, desc) {
    return `<tr><td colspan="99"><div class="empty-state"><h4>${title}</h4><p>${desc}</p></div></td></tr>`;
}

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
            fetchQuizzes(),
            fetchWrongQuestions()
        ]);
        loadEtlStudioData();
        const fails = results.filter(r => !r).length;
        state.lastUpdated = new Date();
        updateLastUpdated();
        if (fails === results.length) showToast('Không thể kết nối máy chủ. Kiểm tra backend & SQL Server.', 'error');
        else if (fails > 0) showToast('Một số dữ liệu không tải được (xem Console log).', 'warning');
        else if (!silent) showToast('Đã làm mới dữ liệu từ Data Warehouse.', 'success');
    } catch (e) {
        console.error('Lỗi loadStudentData:', e);
        showToast('Có lỗi khi tải dữ liệu.', 'error');
    } finally {
        if (btn) btn.classList.remove('loading');
    }
}

async function fetchSummary() {
    try {
        const res = await ApiService.getSummary();
        if (res.success) { renderSummary(res.data); return true; }
        return false;
    } catch (e) { console.error('Lỗi summary:', e); return false; }
}

function renderSummary(d) {
    const g10 = d.gpa10 === null || d.gpa10 === undefined ? null : Number(d.gpa10);
    $('valGpa10').textContent = g10 === null ? '--' : g10.toFixed(2);

    const badgeGpa = $('badgeGpaClass');
    badgeGpa.textContent = d.gpaClassification;
    if (d.gpaClassification === 'Xuất sắc') badgeGpa.className = 'badge badge-success';
    else if (d.gpaClassification === 'Khá') badgeGpa.className = 'badge badge-info';
    else if (d.gpaClassification === 'Trung bình' || d.gpaClassification === 'Trung bình yếu') badgeGpa.className = 'badge badge-warning';
    else if (d.gpaClassification === 'Kém (rớt môn)') badgeGpa.className = 'badge badge-danger';
    else badgeGpa.className = 'badge badge-info';

    $('valUrgentDeadlines').textContent = d.urgentDeadlines;
    $('badgeUrgent').classList.toggle('hidden', !(d.urgentDeadlines > 0));
    const subUrgent = $('subUrgentText');
    if (d.urgentDeadlines > 0 || d.overdueCount > 0) {
        subUrgent.innerHTML = `<span class="tone-danger"><i class="fa-solid fa-triangle-exclamation"></i> ${d.urgentDeadlines} bài trong 48h · ${d.overdueCount} bài quá hạn.</span>`;
    } else {
        subUrgent.innerHTML = `<i class="fa-solid fa-circle-check tone-success"></i> Không có bài tập đến hạn trong 48h tới.`;
    }

    const onTimeRate = d.onTimeRate === null || d.onTimeRate === undefined ? null : Number(d.onTimeRate);
    $('valOnTimeRate').textContent = onTimeRate === null ? '--' : `${onTimeRate}%`;
    setRing('ringOnTime', onTimeRate ?? 0);

    const avgQuizScore = d.avgQuizScore === null || d.avgQuizScore === undefined ? null : Number(d.avgQuizScore);
    $('valAvgQuizScore').textContent = avgQuizScore === null ? '-- / 10' : `${avgQuizScore.toFixed(2)} / 10`;
    $('valQuizCount').textContent = `${d.completedQuizzes} bài hoàn thành`;
    setRing('ringQuiz', (avgQuizScore ?? 0) * 10);
}

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
    const chips = [
        { label: 'Quá hạn', n: cnt(v => v < 0), cls: 'tl-danger' },
        { label: 'Trong 24h', n: cnt(v => v >= 0 && v <= 1440), cls: 'tl-warning' },
        { label: '24–48h', n: cnt(v => v > 1440 && v <= 2880), cls: 'tl-warning' },
        { label: 'Tuần này', n: cnt(v => v > 2880 && v <= 10080), cls: 'tl-info' },
        { label: 'Sau 7 ngày', n: cnt(v => v > 10080), cls: 'tl-muted' },
        { label: 'Không hạn', n: pending.filter(i => m(i) === null).length, cls: 'tl-muted' },
        { label: 'Đã nộp', n: state.deadlines.length - pending.length, cls: 'tl-success' }
    ];
    $('timelineStrip').innerHTML = chips.map(c =>
        `<span class="tl-chip ${c.cls} ${c.n === 0 ? 'is-zero' : ''}">${c.label}: <strong>${c.n}</strong></span>`
    ).join('');
}

async function fetchCourses() {
    try {
        const res = await ApiService.getCourses();
        if (!res.success) return false;
        renderCourses(res.data || []);
        return true;
    } catch (e) { console.error('Lỗi courses:', e); return false; }
}

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

async function fetchWrongQuestions() {
    try {
        const res = await ApiService.getWrongQuestions('all');
        if (!res.success) return false;
        state.wrongQuestions = res.data || [];
        renderReviewCourseFilters();
        renderWrongQuestions();
        return true;
    } catch (e) { console.error('Lỗi wrong-questions:', e); return false; }
}

function switchTab(tab) {
    state.activeTab = tab;
    document.querySelectorAll('#actionCenter .tab').forEach(t => {
        t.classList.toggle('active', t.dataset.tab === tab);
        t.setAttribute('aria-selected', String(t.dataset.tab === tab));
    });
    $('panel-assignments').classList.toggle('active', tab === 'assignments');
    $('panel-quizzes').classList.toggle('active', tab === 'quizzes');
    $('panel-review').classList.toggle('active', tab === 'review');
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
        if (f.status === 'pending' && (cat === 'submitted' || cat === 'overdue')) return false;
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
        tbody.innerHTML = emptyStateHTML('Không có bài tập nào', 'Tuyệt vời! Bạn không còn bài tập nào trong các môn đang học.');
        $('assignResultCount').textContent = '';
        return;
    }
    if (list.length === 0) {
        tbody.innerHTML = emptyStateHTML(
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

        let remainCell;
        if (item.DueDate == null) {
            remainCell = '<span class="text-muted-sm">Không giới hạn</span>';
        } else if (cat === 'overdue') {
            remainCell = '<span class="countdown-text tone-danger">Quá hạn</span>';
        } else {
            const r = formatRemaining(mins);
            remainCell = `<div class="countdown-wrap"><span class="countdown-text tone-${r.tone}">${r.text}</span><div class="mini-progress"><span class="tone-${r.tone}" style="width:${deadlinePct(mins)}%"></span></div></div>`;
        }

        let badge;
        if (cat === 'submitted') badge = item.IsLate ? '<span class="badge badge-warning">Đã nộp (Trễ)</span>' : '<span class="badge badge-success">Đã nộp đúng hạn</span>';
        else if (cat === 'overdue') badge = item.SubmissionStatus === 'draft' ? '<span class="badge badge-warning">Nháp — chưa gửi</span>' : '<span class="badge badge-info">Chưa nộp</span>';
        else if (cat === 'urgent') badge = '<span class="badge badge-danger">Sắp hết hạn</span>';
        else badge = item.SubmissionStatus === 'draft' ? '<span class="badge badge-warning">Nháp — chưa gửi</span>' : '<span class="badge badge-info">Chưa nộp</span>';

        let grade = '<span class="text-muted-sm">--</span>';
        if (cat === 'overdue') {
            grade = '<span class="text-muted-sm">Không có điểm</span>';
        } else if (item.Grade !== null && item.Grade !== undefined) {
            grade = `<span class="score-strong ${item.GradeScaled10 >= 5 ? 'score-pass' : 'score-fail'}">${item.GradeScaled10.toFixed(2)}đ</span>`;
        } else if (item.SubmissionStatus === 'submitted') {
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
        tbody.innerHTML = emptyStateHTML('Không có bài kiểm tra', 'Chưa có quiz nào trong các môn bạn đang học.');
        $('quizResultCount').textContent = '';
        return;
    }
    if (list.length === 0) {
        tbody.innerHTML = emptyStateHTML('Không có kết quả phù hợp', 'Thử chọn "Tất cả" hoặc xóa từ khóa tìm kiếm.');
        $('quizResultCount').textContent = `0 / ${total}`;
        return;
    }

    $('quizResultCount').textContent = `Hiển thị ${list.length} / ${total}`;
    tbody.innerHTML = list.map(q => {
        const cat = quizCategory(q);
        const durationText = q.TimeLimitMinutes > 0 ? `${q.TimeLimitMinutes} phút` : 'Tự do';
        const closeFormatted = q.TimeClose ? fmtDate(q.TimeClose) : 'Không đóng';

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

function filterWrongQuestions() {
    const f = state.review;
    return state.wrongQuestions.filter(q => {
        if (f.course !== 'all' && q.CourseName !== f.course) return false;
        const s = f.search.trim().toLowerCase();
        if (s) {
            const hay = `${q.QuestionText || q.QuestionName || ''} ${q.QuizName || ''} ${q.CourseName || ''}`.toLowerCase();
            if (!hay.includes(s)) return false;
        }
        return true;
    });
}

function renderReviewCourseFilters() {
    const wrap = $('reviewCourseFilters');
    if (!wrap) return;
    const filterPanel = $('reviewCourseFilter');

    const counts = new Map();
    state.wrongQuestions.forEach(q => counts.set(q.CourseName, (counts.get(q.CourseName) || 0) + 1));
    const courses = [...counts.keys()].sort((a, b) => a.localeCompare(b, 'vi'));
    if (state.review.course !== 'all' && !counts.has(state.review.course)) state.review.course = 'all';
    if (filterPanel) filterPanel.classList.toggle('hidden', courses.length === 0);

    const options = [
        ['all', 'Tất cả môn', state.wrongQuestions.length],
        ...courses.map(course => [course, course, counts.get(course)])
    ];
    wrap.innerHTML = options.map(([value, label, count]) => `
        <button type="button" class="pill ${state.review.course === value ? 'active' : ''}"
            data-review-course="${escapeHtml(value)}" aria-pressed="${state.review.course === value}">
            ${escapeHtml(label)} <span class="review-course-count">${count}</span>
        </button>
    `).join('');
}

function wrongQuestionCardHTML(q, idx) {
    const scorePct = (q.MaxMark && q.MaxMark > 0)
        ? Math.round(((q.EarnedMark || 0) / q.MaxMark) * 100)
        : (q.Fraction != null ? Math.round(q.Fraction * 100) : null);
    return `
        <div class="review-item" data-idx="${idx}">
            <button type="button" class="review-item-head">
                <div class="review-item-main">
                    <span class="review-quiz-name">${escapeHtml(q.QuizName)}</span>
                </div>
                <div class="review-item-meta">
                    ${scorePct !== null ? `<span class="badge badge-danger">${scorePct}%</span>` : '<span class="badge badge-danger">Sai</span>'}
                    <i class="fa-solid fa-chevron-down review-chevron"></i>
                </div>
            </button>
            <p class="review-question-text">${escapeHtml(q.QuestionText || q.QuestionName)}</p>
            <div class="review-answer-body">
                <div class="review-answer-row review-answer-wrong">
                    <i class="fa-solid fa-xmark"></i>
                    <div><span class="review-answer-label">Bạn đã chọn (sai)</span><p>${escapeHtml(q.StudentResponse)}</p></div>
                </div>
                <div class="review-answer-row review-answer-correct">
                    <i class="fa-solid fa-check"></i>
                    <div><span class="review-answer-label">Đáp án đúng</span><p>${escapeHtml(q.RightAnswer)}</p></div>
                </div>
            </div>
        </div>`;
}

function renderWrongQuestions() {
    const wrap = $('reviewList');
    if (!wrap) return;
    const list = filterWrongQuestions();
    const total = state.wrongQuestions.length;

    const tabCount = $('tabReviewCount');
    if (tabCount) tabCount.textContent = total;

    if (total === 0) {
        wrap.innerHTML = `<div class="empty-state"><h4>Chưa có câu nào sai</h4><p>Bạn chưa trả lời sai câu trắc nghiệm nào — tiếp tục phát huy nhé!</p></div>`;
        return;
    }
    if (list.length === 0) {
        wrap.innerHTML = `<div class="empty-state"><h4>Không có kết quả phù hợp</h4><p>Thử đổi môn học hoặc xóa từ khóa tìm kiếm.</p></div>`;
        return;
    }

    const groups = new Map();
    list.forEach(q => {
        if (!groups.has(q.CourseName)) groups.set(q.CourseName, []);
        groups.get(q.CourseName).push(q);
    });
    const sortedGroups = [...groups.entries()].sort(([a], [b]) => a.localeCompare(b, 'vi'));
    wrap.innerHTML = sortedGroups.map(([course, questions]) => `
        <section class="review-course-group">
            <header class="review-course-heading">
                <h4>${escapeHtml(course)}</h4>
                <span>${questions.length} câu sai</span>
            </header>
            <div class="review-course-items">
                ${questions.map((q, idx) => wrongQuestionCardHTML(q, idx)).join('')}
            </div>
        </section>
    `).join('');

    wrap.querySelectorAll('.review-item-head').forEach(btn => {
        btn.addEventListener('click', () => btn.closest('.review-item').classList.toggle('open'));
    });
}



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
        tbody.innerHTML = emptyStateHTML('Chưa có dữ liệu bảng điểm', 'Dữ liệu sẽ xuất hiện sau khi ETL đồng bộ từ Moodle.');
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
        const gradeClass = c.FinalGrade10 === null || c.FinalGrade10 === undefined
            ? 'text-muted-sm'
            : c.FinalGrade10 < 4.0 ? 'grade-fail' : c.FinalGrade10 < 5.0 ? 'grade-warning' : 'grade-pass';
        const quizAverage = c.QuizAverage === null || c.QuizAverage === undefined ? '--' : Number(c.QuizAverage).toFixed(2);
        const assignAverage = c.AssignAverage === null || c.AssignAverage === undefined ? '--' : Number(c.AssignAverage).toFixed(2);

        let letterBadge = 'badge-success';
        if (c.LetterGrade === 'F') letterBadge = 'badge-danger';
        else if (c.LetterGrade === 'D') letterBadge = 'badge-warning';

        return `<tr>
            <td><div class="assign-name">${escapeHtml(c.CourseName)}</div></td>
            <td>${progressCell(c)}</td>
            <td>${quizAverage}</td>
            <td>${assignAverage}</td>
            <td><strong class="${gradeClass}">${grade10}</strong></td>
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

function chartThemeColors() {
    const cs = getComputedStyle(document.body);
    const token = (name) => cs.getPropertyValue(name).trim();
    return {
        text: token('--text-secondary'),
        grid: token('--chart-grid'),
        tooltipBg: token('--toast-bg'),
        tooltipText: token('--text-primary'),
        danger: token('--color-danger'),
        orange: token('--color-orange'),
        amber: token('--color-amber'),
        success: token('--color-success'),
        cyan: token('--color-cyan'),
        purple: token('--color-purple')
    };
}

function updateScoreYearOptions() {
    const yearSelect = $('scoreYearSelect');
    if (!yearSelect || !state.courses.length) return;

    const yearsInData = state.courses.map(c => Number(c.Year)).filter(n => !isNaN(n) && n > 0);
    const currentYear = new Date().getFullYear();
    const prevYear = currentYear - 1;

    const btnCurrent = document.querySelector('#scoreYearToggle button[data-year="current"]');
    if (btnCurrent) btnCurrent.textContent = `Năm nay (${currentYear})`;

    const btnPrevious = document.querySelector('#scoreYearToggle button[data-year="previous"]');
    if (btnPrevious) btnPrevious.textContent = `Năm trước (${prevYear})`;

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

    const currentYear = new Date().getFullYear();
    const prevYear = currentYear - 1;
    const filter = state.scoreYearFilter;

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

    const scoreLabels = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '10'];
    const counts = new Array(11).fill(0);
    const coursesInScore = Array.from({ length: 11 }, () => []);

    filteredCourses.forEach(c => {
        if (c.FinalGrade10 !== null && c.FinalGrade10 !== undefined) {
            const val = parseFloat(c.FinalGrade10);
            const bucket = Math.min(10, Math.max(0, Math.round(val)));
            counts[bucket]++;
            coursesInScore[bucket].push({
                name: c.CourseName,
                score: val.toFixed(2),
                letter: c.LetterGrade || '--',
                classification: c.GradeClassification || ''
            });
        }
    });

    const th = chartThemeColors();
    const barColors = [
        th.danger, th.danger, th.danger, th.danger,
        th.orange, th.amber, th.amber,
        th.success, th.success, th.cyan, th.purple
    ];
    const borderColors = barColors;
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
                        text: 'Số môn học',
                        color: th.text,
                        font: { size: 10, family: 'Inter' }
                    }
                }
            }
        }
    });

    const summaryEl = $('scoreDistSummary');
    if (summaryEl) {
        const validGrades = filteredCourses.filter(c => c.FinalGrade10 !== null && c.FinalGrade10 !== undefined);
        const avg = validGrades.length ? (validGrades.reduce((acc, c) => acc + c.FinalGrade10, 0) / validGrades.length).toFixed(2) : '--';
        const passed = validGrades.filter(c => c.FinalGrade10 >= 4.0).length;
        const failed = validGrades.filter(c => c.FinalGrade10 < 4.0).length;

        if (filteredCourses.length === 0) {
            summaryEl.innerHTML = `
                <div style="width: 100%; text-align: center; color: var(--text-secondary); padding: 8px 0;">
                    Không tìm thấy môn học nào thuộc <strong>${escapeHtml(currentFilterLabel)}</strong>.
                </div>
            `;
        } else {
            summaryEl.innerHTML = `
                <div style="display:flex; align-items:center; gap:6px;">
                    <span>Môn có điểm: <strong style="color:var(--text-primary); font-weight:700;">${validGrades.length}</strong></span>
                </div>
                <div style="display:flex; align-items:center; gap:6px;">
                    <span>Điểm TB (${escapeHtml(currentFilterLabel)}): <strong style="color:var(--text-primary); font-weight:700;">${avg}</strong></span>
                </div>
                <div style="display:flex; align-items:center; gap:6px;">
                    <span>Đạt (≥ 4.0): <strong class="tone-success" style="font-weight:700;">${passed}</strong></span>
                </div>
                <div style="display:flex; align-items:center; gap:6px;">
                    <span>Cần cải thiện (< 4.0): <strong class="tone-danger" style="font-weight:700;">${failed}</strong></span>
                </div>
            `;
        }
    }
}

// Giữ alias tương thích
const renderChart = renderScoreDistributionChart;

// ============================================================================
// ETL STUDIO: BẢNG COST (CSV), NGHIÊN CỨU ĐỌC TRƯỜNG & DEMO ĐA BẢNG (A+B -> DWH)
// ============================================================================

const FALLBACK_COST_DATA = {
    summary: {
        totalSourceCols: 453,
        totalSelectedCols: 71,
        totalPrunedCols: 382,
        overallColReductionPct: 84.3,
        totalDirectCols: 37,
        totalTransformedCols: 48,
        totalSourceRows: 1149335,
        totalExtractedRows: 490244,
        rowReductionPct: 57.3,
        totalTimeV1: 259.8,
        totalTimeV2: 94.06,
        totalTimeV3: 43.4,
        totalTimeV4: 24.0,
        speedupV4VsV1: 10.8
    },
    costTable: [
        { TableName: 'Dim_User', SourceTables: 'user, cohort_members, cohort, role_assignments, role (5 bảng)', TotalSourceCols: 86, SelectedCols: 12, PrunedCols: 74, ColReductionPct: 86.05, DirectMappedCols: 7, TransformedCols: 4, SourceRowCount: 15420, ExtractedRowCount: 15280, LoadedRowCount: 15280, LiveDwhRows: 15280, ExtractTimeSec: 0.42, LoadTimeSec: 0.65, TotalTimeSec: 1.25, TimeV1Sec: 18.4, TimeV2Sec: 2.15, TimeV3Sec: 2.1, PeakRamKB: 2140.5 },
        { TableName: 'Dim_Course', SourceTables: 'course (1 bảng)', TotalSourceCols: 35, SelectedCols: 6, PrunedCols: 29, ColReductionPct: 82.86, DirectMappedCols: 4, TransformedCols: 2, SourceRowCount: 845, ExtractedRowCount: 844, LoadedRowCount: 844, LiveDwhRows: 844, ExtractTimeSec: 0.05, LoadTimeSec: 0.08, TotalTimeSec: 0.15, TimeV1Sec: 1.85, TimeV2Sec: 0.22, TimeV3Sec: 0.21, PeakRamKB: 320.0 },
        { TableName: 'Dim_Quiz', SourceTables: 'quiz (1 bảng + Lookup Dim_Course)', TotalSourceCols: 42, SelectedCols: 7, PrunedCols: 35, ColReductionPct: 83.33, DirectMappedCols: 3, TransformedCols: 4, SourceRowCount: 3120, ExtractedRowCount: 3120, LoadedRowCount: 3120, LiveDwhRows: 3120, ExtractTimeSec: 0.09, LoadTimeSec: 0.15, TotalTimeSec: 0.28, TimeV1Sec: 4.6, TimeV2Sec: 0.41, TimeV3Sec: 0.4, PeakRamKB: 540.2 },
        { TableName: 'Dim_Assign', SourceTables: 'assign (1 bảng + Lookup Dim_Course)', TotalSourceCols: 32, SelectedCols: 6, PrunedCols: 26, ColReductionPct: 81.25, DirectMappedCols: 3, TransformedCols: 3, SourceRowCount: 4250, ExtractedRowCount: 4250, LoadedRowCount: 4250, LiveDwhRows: 4250, ExtractTimeSec: 0.11, LoadTimeSec: 0.18, TotalTimeSec: 0.34, TimeV1Sec: 5.9, TimeV2Sec: 0.48, TimeV3Sec: 0.46, PeakRamKB: 610.8 },
        { TableName: 'Dim_Question', SourceTables: 'question (1 bảng)', TotalSourceCols: 25, SelectedCols: 5, PrunedCols: 20, ColReductionPct: 80.0, DirectMappedCols: 5, TransformedCols: 0, SourceRowCount: 68500, ExtractedRowCount: 68500, LoadedRowCount: 68500, LiveDwhRows: 68500, ExtractTimeSec: 1.15, LoadTimeSec: 1.92, TotalTimeSec: 3.52, TimeV1Sec: 142.5, TimeV2Sec: 5.8, TimeV3Sec: 5.75, PeakRamKB: 8420.0 },
        { TableName: 'Fact_Course_Grades', SourceTables: 'grade_grades, grade_items (2 bảng)', TotalSourceCols: 48, SelectedCols: 5, PrunedCols: 43, ColReductionPct: 89.58, DirectMappedCols: 2, TransformedCols: 8, SourceRowCount: 195400, ExtractedRowCount: 48650, LoadedRowCount: 48650, LiveDwhRows: 48650, ExtractTimeSec: 0.68, LoadTimeSec: 1.12, TotalTimeSec: 2.19, TimeV1Sec: 3.45, TimeV2Sec: 3.4, TimeV3Sec: 3.38, PeakRamKB: 3850.4 },
        { TableName: 'Fact_Quiz_Attempts', SourceTables: 'quiz_attempts, quiz (2 bảng)', TotalSourceCols: 58, SelectedCols: 10, PrunedCols: 48, ColReductionPct: 82.76, DirectMappedCols: 4, TransformedCols: 9, SourceRowCount: 112800, ExtractedRowCount: 112800, LoadedRowCount: 112800, LiveDwhRows: 112800, ExtractTimeSec: 1.42, LoadTimeSec: 2.45, TotalTimeSec: 4.75, TimeV1Sec: 7.8, TimeV2Sec: 7.65, TimeV3Sec: 7.6, PeakRamKB: 4920.0 },
        { TableName: 'Fact_Assign_Submissions', SourceTables: 'assign_submission, assign, assign_grades (3 bảng)', TotalSourceCols: 52, SelectedCols: 9, PrunedCols: 43, ColReductionPct: 82.69, DirectMappedCols: 3, TransformedCols: 10, SourceRowCount: 164000, ExtractedRowCount: 94200, LoadedRowCount: 94200, LiveDwhRows: 94200, ExtractTimeSec: 1.28, LoadTimeSec: 2.05, TotalTimeSec: 4.07, TimeV1Sec: 6.9, TimeV2Sec: 6.75, TimeV3Sec: 6.7, PeakRamKB: 4610.0 },
        { TableName: 'Fact_Question_Attempts', SourceTables: 'question_attempts, quiz_attempts, quiz, question_attempt_steps (4 bảng)', TotalSourceCols: 75, SelectedCols: 11, PrunedCols: 64, ColReductionPct: 85.33, DirectMappedCols: 6, TransformedCols: 8, SourceRowCount: 685000, ExtractedRowCount: 142600, LoadedRowCount: 142600, LiveDwhRows: 142600, ExtractTimeSec: 2.85, LoadTimeSec: 3.48, TotalTimeSec: 7.45, TimeV1Sec: 68.4, TimeV2Sec: 67.2, TimeV3Sec: 16.8, PeakRamKB: 6280.0 }
    ],
    readBenchmarks: [
        { Method: '1. Đọc toàn bộ cột (SELECT * - Nhiều cột)', ColumnsRead: '75/75 cột (100%)', CursorType: 'DictCursor', BatchStrategy: 'fetchall() (1 cục)', ReadTimeSec: 38.45, PeakRamMB: 412.6, Note: 'Đọc cả 64 cột dư thừa & LONGTEXT off-page vào Dict gây tràn RAM' },
        { Method: '2. Cắt giảm trường (Column Pruning - Ít cột)', ColumnsRead: '11/75 cột (14.7%)', CursorType: 'DictCursor', BatchStrategy: 'fetchall() (1 cục)', ReadTimeSec: 14.2, PeakRamMB: 148.3, Note: 'Cắt bỏ 85.3% cột rác giúp giảm ~64% thời gian đọc và ~64% RAM' },
        { Method: '3. Cắt giảm trường + Tuple Cursor', ColumnsRead: '11/75 cột (14.7%)', CursorType: 'Cursor (Tuple)', BatchStrategy: 'fetchall() (1 cục)', ReadTimeSec: 8.65, PeakRamMB: 52.4, Note: 'Thay Dict bằng Tuple giảm thêm 65% RAM do không băm key chuỗi' },
        { Method: '4. Cắt trường + Lọc sớm + SSCursor Lô 10.000 (v4)', ColumnsRead: '11/75 cột (14.7%)', CursorType: 'SSCursor (Stream Tuple)', BatchStrategy: 'fetchmany(10000)', ReadTimeSec: 2.85, PeakRamMB: 6.13, Note: 'Đọc luồng từng lô 10.000 dòng + lọc fraction < 0.5 ngay tại nguồn' }
    ],
    columnMappings: [
        { TargetTable: 'Dim_User', TargetColumn: 'SourceUserID', SourceExpression: 'u.id', MappingType: 'Ánh xạ trực tiếp (1-1)', Description: 'Khóa gốc từ bảng user' },
        { TargetTable: 'Dim_User', TargetColumn: 'Username', SourceExpression: 'u.username', MappingType: 'Ánh xạ trực tiếp (1-1)', Description: 'Tên đăng nhập Moodle' },
        { TargetTable: 'Dim_User', TargetColumn: 'IdNumber', SourceExpression: 'u.idnumber', MappingType: 'Ánh xạ trực tiếp (1-1)', Description: 'Mã số sinh viên DLU' },
        { TargetTable: 'Dim_User', TargetColumn: 'FullName', SourceExpression: "TRIM(CONCAT(u.lastname, ' ', u.firstname))", MappingType: 'Đọc & Biến đổi / Tính toán', Description: 'Ghép Họ + Tên và cắt khoảng trắng' },
        { TargetTable: 'Dim_User', TargetColumn: 'CohortName', SourceExpression: "COALESCE(c.name, 'Chưa phân lớp')", MappingType: 'Đọc & Biến đổi / Tính toán', Description: 'Gộp từ bảng cohort_members + cohort' },
        { TargetTable: 'Dim_User', TargetColumn: 'IsStudent', SourceExpression: 'SUM(role=student) > 0 AND SUM(role=staff) = 0', MappingType: 'Đọc & Biến đổi / Tính toán', Description: 'Xét vai trò từ bảng role_assignments' },
        { TargetTable: 'Dim_Course', TargetColumn: 'CourseName', SourceExpression: 'c.fullname', MappingType: 'Ánh xạ trực tiếp (1-1)', Description: 'Tên đầy đủ học phần' },
        { TargetTable: 'Dim_Course', TargetColumn: 'StartDate', SourceExpression: 'ts_to_dt(c.startdate)', MappingType: 'Đọc & Biến đổi / Tính toán', Description: 'Đổi Unix Timestamp -> DATETIME' },
        { TargetTable: 'Fact_Course_Grades', TargetColumn: 'RawGrade', SourceExpression: 'gg.finalgrade', MappingType: 'Ánh xạ trực tiếp (1-1)', Description: 'Điểm gốc bảng grade_grades' },
        { TargetTable: 'Fact_Course_Grades', TargetColumn: 'GradeScaled10', SourceExpression: 'ROUND((RawGrade/MaxGrade)*10, 2)', MappingType: 'Đọc & Biến đổi / Tính toán', Description: 'Quy đổi điểm về thang 10' },
        { TargetTable: 'Fact_Course_Grades', TargetColumn: 'LetterGrade', SourceExpression: 'calculate_letter_grade()', MappingType: 'Đọc & Biến đổi / Tính toán', Description: 'Xếp loại điểm chữ A, B, C, D, F' }
    ]
};

async function loadEtlStudioData() {
    try {
        const costRes = await ApiService.getEtlCostReport();
        if (costRes && costRes.success) {
            state.etlCostData = costRes.data;
        } else {
            state.etlCostData = FALLBACK_COST_DATA;
        }
    } catch (_) {
        state.etlCostData = FALLBACK_COST_DATA;
    }
    renderEtlCostStudio(state.etlCostData);

    try {
        const lineageRes = await ApiService.getMultiTableDemo();
        if (lineageRes && lineageRes.success) {
            state.lineageData = lineageRes.data;
            renderMultiTableLineage(state.lineageData);
        }
    } catch (_) {
        // Fallback nếu chưa kết nối DB
    }
}

function renderEtlCostStudio(data) {
    if (!data) return;
    const s = data.summary || FALLBACK_COST_DATA.summary;
    const costTable = data.costTable || [];
    const benchmarks = data.readBenchmarks || [];
    const mappings = data.columnMappings || [];

    if ($('etlKpiColReduction')) $('etlKpiColReduction').textContent = `${s.overallColReductionPct}%`;
    if ($('etlKpiColRatio')) $('etlKpiColRatio').textContent = `Giữ ${s.totalSelectedCols} / ${s.totalSourceCols} cột`;
    if ($('etlKpiMappingSplit')) $('etlKpiMappingSplit').textContent = `${s.totalDirectCols} / ${s.totalTransformedCols}`;
    if ($('etlKpiMappingSub')) $('etlKpiMappingSub').textContent = `${s.totalDirectCols} cột ánh xạ trực tiếp (1-1) · ${s.totalTransformedCols} cột đọc & biến đổi`;
    if ($('etlKpiSpeedup')) $('etlKpiSpeedup').textContent = `${s.speedupV4VsV1}x nhanh hơn`;
    if ($('etlKpiTimeCompare')) $('etlKpiTimeCompare').textContent = `${s.totalTimeV1}s → ${s.totalTimeV4}s`;

    const costBody = $('etlCostTableBody');
    if (costBody) {
        costBody.innerHTML = costTable.map(r => `
            <tr class="cost-row-clickable" data-table-name="${escapeHtml(r.TableName)}" title="Nhấn để lọc xem các trường của ${escapeHtml(r.TableName)}">
                <td><strong class="text-cyan">${escapeHtml(r.TableName)}</strong></td>
                <td><span class="text-muted-sm">${escapeHtml(r.SourceTables)}</span></td>
                <td><strong>${r.SelectedCols}</strong> / ${r.TotalSourceCols} cột</td>
                <td><span class="badge badge-success">-${Number(r.ColReductionPct).toFixed(1)}%</span></td>
                <td><span class="badge badge-info">${r.DirectMappedCols} trực tiếp</span> <span class="badge badge-purple">${r.TransformedCols} biến đổi</span></td>
                <td>${Number(r.SourceRowCount).toLocaleString('vi-VN')} → <strong>${Number(r.ExtractedRowCount).toLocaleString('vi-VN')}</strong> → <span class="text-success">${Number(r.LiveDwhRows ?? r.LoadedRowCount).toLocaleString('vi-VN')}</span></td>
                <td>${Number(r.ExtractTimeSec).toFixed(2)}s</td>
                <td>${Number(r.LoadTimeSec).toFixed(2)}s</td>
                <td><span class="text-muted-sm">${r.TimeV1Sec || '--'}s → ${r.TimeV2Sec || '--'}s → ${r.TimeV3Sec || '--'}s →</span> <strong class="text-success">${Number(r.TotalTimeSec).toFixed(2)}s</strong></td>
                <td>${(Number(r.PeakRamKB) / 1024).toFixed(2)} MB</td>
            </tr>
        `).join('');

        costBody.querySelectorAll('.cost-row-clickable').forEach(tr => {
            tr.addEventListener('click', () => {
                const tName = tr.dataset.tableName;
                const sel = $('etlMappingTableFilter');
                if (sel) {
                    sel.value = tName;
                    renderEtlMappingTable(mappings, tName);
                }
            });
        });
    }

    const benchBody = $('etlBenchmarkTableBody');
    if (benchBody) {
        benchBody.innerHTML = benchmarks.map((b, idx) => `
            <tr>
                <td>
                    <div class="assign-name">${escapeHtml(b.Method)}</div>
                    <div class="text-muted-sm" style="white-space:normal;">${escapeHtml(b.Note || '')}</div>
                </td>
                <td><span class="badge ${idx === 0 ? 'badge-danger' : 'badge-success'}">${escapeHtml(b.ColumnsRead)}</span></td>
                <td><code>${escapeHtml(b.CursorType)}</code><br><span class="text-muted-sm">${escapeHtml(b.BatchStrategy)}</span></td>
                <td><strong class="${idx === benchmarks.length - 1 ? 'text-success' : ''}">${b.ReadTimeSec}s</strong></td>
                <td><strong class="${idx === benchmarks.length - 1 ? 'text-cyan' : ''}">${b.PeakRamMB} MB</strong></td>
            </tr>
        `).join('');
    }

    const filterSel = $('etlMappingTableFilter');
    if (filterSel) {
        const uniqueTables = [...new Set(mappings.map(m => m.TargetTable))];
        filterSel.innerHTML = `<option value="all">Tất cả ${uniqueTables.length} bảng (${mappings.length} trường)</option>` +
            uniqueTables.map(t => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`).join('');
    }
    renderEtlMappingTable(mappings, filterSel ? filterSel.value : 'all');
    renderEtlCharts(costTable);
}

function renderEtlMappingTable(mappings, tableFilter = 'all') {
    const tbody = $('etlMappingTableBody');
    if (!tbody) return;
    const filtered = tableFilter === 'all' ? mappings : mappings.filter(m => m.TargetTable === tableFilter);
    tbody.innerHTML = filtered.map(m => {
        const isDirect = String(m.MappingType).includes('trực tiếp');
        return `
            <tr>
                <td><strong class="text-cyan">${escapeHtml(m.TargetTable)}</strong></td>
                <td><code>${escapeHtml(m.TargetColumn)}</code></td>
                <td>
                    <code>${escapeHtml(m.SourceExpression)}</code>
                    <div class="text-muted-sm" style="white-space:normal;">${escapeHtml(m.Description || '')}</div>
                </td>
                <td><span class="badge ${isDirect ? 'badge-info' : 'badge-purple'}">${escapeHtml(m.MappingType)}</span></td>
            </tr>
        `;
    }).join('');
}

function renderEtlCharts(costTable) {
    if (!costTable || !costTable.length) return;
    const th = chartThemeColors();
    const labels = costTable.map(r => r.TableName.replace('Fact_', 'F_').replace('Dim_', 'D_'));

    const canvasCol = $('etlColPruningChart');
    if (canvasCol) {
        if (etlColPruningChartInstance) etlColPruningChartInstance.destroy();
        etlColPruningChartInstance = new Chart(canvasCol.getContext('2d'), {
            type: 'bar',
            data: {
                labels,
                datasets: [
                    {
                        label: 'Cột Ánh xạ trực tiếp (1-1)',
                        data: costTable.map(r => Number(r.DirectMappedCols) || 0),
                        backgroundColor: th.cyan,
                        stack: 'cols'
                    },
                    {
                        label: 'Cột Đọc & Biến đổi (Derived)',
                        data: costTable.map(r => Number(r.TransformedCols) || 0),
                        backgroundColor: th.purple,
                        stack: 'cols'
                    },
                    {
                        label: 'Cột Rác đã cắt bỏ (Pruned)',
                        data: costTable.map(r => Number(r.PrunedCols) || 0),
                        backgroundColor: 'rgba(148, 163, 184, 0.28)',
                        stack: 'cols'
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { labels: { color: th.text, font: { size: 11 } } }
                },
                scales: {
                    x: { stacked: true, ticks: { color: th.text, font: { size: 10 } }, grid: { display: false } },
                    y: { stacked: true, ticks: { color: th.text }, grid: { color: th.grid }, title: { display: true, text: 'Số lượng trường (cột)', color: th.text } }
                }
            }
        });
    }

    const canvasTime = $('etlVersionTimeChart');
    if (canvasTime) {
        if (etlVersionTimeChartInstance) etlVersionTimeChartInstance.destroy();
        etlVersionTimeChartInstance = new Chart(canvasTime.getContext('2d'), {
            type: 'bar',
            data: {
                labels,
                datasets: [
                    {
                        label: 'v1 (MERGE từng dòng)',
                        data: costTable.map(r => Number(r.TimeV1Sec) || 0),
                        backgroundColor: th.danger
                    },
                    {
                        label: 'v2 (Staging + MERGE)',
                        data: costTable.map(r => Number(r.TimeV2Sec) || 0),
                        backgroundColor: th.amber
                    },
                    {
                        label: 'v3 (+ Lọc câu sai)',
                        data: costTable.map(r => Number(r.TimeV3Sec) || 0),
                        backgroundColor: th.cyan
                    },
                    {
                        label: 'v4 (+ SSCursor Lô 10k & Tối ưu SQL)',
                        data: costTable.map(r => Number(r.TotalTimeSec) || 0),
                        backgroundColor: th.success
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { labels: { color: th.text, font: { size: 11 } } }
                },
                scales: {
                    x: { ticks: { color: th.text, font: { size: 10 } }, grid: { display: false } },
                    y: { ticks: { color: th.text }, grid: { color: th.grid }, title: { display: true, text: 'Thời gian (giây)', color: th.text } }
                }
            }
        });
    }
}

function renderMultiTableLineage(data) {
    if (!data) return;
    const dims = data.sourceDims || {};
    const unified = data.unifiedTable || [];

    const u = (dims.tableA_DimUser && dims.tableA_DimUser[0]) || {};
    const c = (dims.tableB_DimCourse && dims.tableB_DimCourse[0]) || {};
    const a = (dims.tableC_DimAssign && dims.tableC_DimAssign[0]) || {};
    const q = (dims.tableD_DimQuiz && dims.tableD_DimQuiz[0]) || {};
    const d = (dims.tableE_DimDate && dims.tableE_DimDate[0]) || {};

    const container = $('dimSourceCardsContainer');
    if (container) {
        container.innerHTML = `
            <div class="dim-source-box" data-box-src="A">
                <h5><span class="src-tag src-a">Bảng A: Dim_User</span> <span>UserKey=${u.UserKey || '--'}</span></h5>
                <ul>
                    <li><code>IdNumber</code>: ${escapeHtml(u.IdNumber || u.Username || '--')}</li>
                    <li><code>FullName</code>: ${escapeHtml(u.FullName || '--')}</li>
                    <li><code>CohortName</code>: <strong>${escapeHtml(u.CohortName || '--')}</strong></li>
                    <li><code>Department</code>: ${escapeHtml(u.Department || '--')}</li>
                </ul>
            </div>
            <div class="dim-source-box" data-box-src="B">
                <h5><span class="src-tag src-b">Bảng B: Dim_Course</span> <span>${(dims.tableB_DimCourse || []).length} môn</span></h5>
                <ul>
                    <li><code>CourseKey</code>: ${escapeHtml(c.CourseKey || '--')}</li>
                    <li><code>CourseCode</code>: ${escapeHtml(c.CourseCode || c.CourseShortName || '--')}</li>
                    <li><code>CourseName</code>: <strong>${escapeHtml(c.CourseName || '--')}</strong></li>
                </ul>
            </div>
            <div class="dim-source-box" data-box-src="C">
                <h5><span class="src-tag src-c">Bảng C: Dim_Assign</span> <span>${(dims.tableC_DimAssign || []).length} bài tập</span></h5>
                <ul>
                    <li><code>AssignKey</code>: ${escapeHtml(a.AssignKey || '--')}</li>
                    <li><code>AssignName</code>: <strong>${escapeHtml(a.AssignName || '--')}</strong></li>
                    <li><code>MaxGrade</code>: ${escapeHtml(a.MaxGrade ?? 10)}</li>
                </ul>
            </div>
            <div class="dim-source-box" data-box-src="D">
                <h5><span class="src-tag src-d">Bảng D: Dim_Quiz</span> <span>${(dims.tableD_DimQuiz || []).length} quiz</span></h5>
                <ul>
                    <li><code>QuizKey</code>: ${escapeHtml(q.QuizKey || '--')}</li>
                    <li><code>QuizName</code>: <strong>${escapeHtml(q.QuizName || '--')}</strong></li>
                    <li><code>TimeLimit</code>: ${escapeHtml(q.TimeLimitMinutes ?? 0)} phút</li>
                </ul>
            </div>
            <div class="dim-source-box" data-box-src="E">
                <h5><span class="src-tag src-e">Bảng E: Dim_Date</span> <span>DateKey=${d.DateKey || '--'}</span></h5>
                <ul>
                    <li><code>FullDate</code>: ${escapeHtml(d.FullDate ? String(d.FullDate).slice(0, 10) : 'Học kỳ hiện tại')}</li>
                    <li><code>Quarter</code>: Quý ${escapeHtml(d.Quarter || '--')}</li>
                    <li><code>Year</code>: Năm <strong>${escapeHtml(d.Year || '2026')}</strong></li>
                </ul>
            </div>
        `;

        container.querySelectorAll('.dim-source-box').forEach(box => {
            box.addEventListener('click', () => highlightLineageSource(box.dataset.boxSrc));
        });
    }

    const tbody = $('unifiedLineageTableBody');
    if (tbody) {
        if (!unified.length) {
            tbody.innerHTML = emptyStateHTML('Chưa có dữ liệu hợp nhất', 'Hãy đăng nhập tài khoản sinh viên để xem bảng hợp nhất 5 Dim.');
        } else {
            tbody.innerHTML = unified.map(row => `
                <tr>
                    <td data-src-col="A"><code>${escapeHtml(row.A_IdNumber || '--')}</code></td>
                    <td data-src-col="A"><strong>${escapeHtml(row.A_FullName)}</strong></td>
                    <td data-src-col="A"><span class="badge badge-info">${escapeHtml(row.A_CohortName)}</span></td>
                    <td data-src-col="B"><code>${escapeHtml(row.B_CourseCode || '--')}</code></td>
                    <td data-src-col="B"><span class="course-name">${escapeHtml(row.B_CourseName)}</span></td>
                    <td data-src-col="C">${escapeHtml(row.C_SampleAssign || '(Không có BT)')}</td>
                    <td data-src-col="D">${escapeHtml(row.D_SampleQuiz || '(Không có Quiz)')}</td>
                    <td data-src-col="E">${escapeHtml(row.E_SyncDate)} · Q${escapeHtml(row.E_Quarter)}/${escapeHtml(row.E_Year)}</td>
                    <td data-src-col="F">${row.Fact_RawGrade ?? '--'} → <strong class="text-success">${Number(row.Fact_Grade10 || 0).toFixed(2)}</strong></td>
                    <td data-src-col="F"><span class="badge badge-success">${escapeHtml(row.Fact_Letter)}</span> ${escapeHtml(row.Fact_Classification)}</td>
                </tr>
            `).join('');
        }
    }

    // Điền sẵn dữ liệu vào Form Demo Đổi Dữ Liệu (Tab 3)
    if (u.CohortName && $('demoNewCohort') && !$('demoNewCohort').value) {
        $('demoNewCohort').value = u.CohortName.includes('(Đã đồng bộ)') ? u.CohortName.replace(' (Đã đồng bộ)', '') : `${u.CohortName} (Đã đồng bộ)`;
    }
    if (u.Department && $('demoNewDept') && !$('demoNewDept').value) {
        $('demoNewDept').value = u.Department;
    }

    const coursesList = dims.tableB_DimCourse || [];
    const fillCourseOpts = (selId) => {
        const sel = $(selId);
        if (!sel || !coursesList.length) return;
        sel.innerHTML = coursesList.map(cItem =>
            `<option value="${cItem.CourseKey}">[CourseKey=${cItem.CourseKey}] ${escapeHtml(cItem.CourseName)}</option>`
        ).join('');
    };
    fillCourseOpts('demoCourseSelect');
    fillCourseOpts('demoGradeCourseSelect');
    if (coursesList[0] && $('demoNewCourseName') && !$('demoNewCourseName').value) {
        $('demoNewCourseName').value = `${coursesList[0].CourseName} (Cập nhật MERGE)`;
    }
}

function highlightLineageSource(srcCode) {
    document.querySelectorAll('#lineageHighlightPills .pill').forEach(p => {
        p.classList.toggle('active', p.dataset.lineageSrc === srcCode);
    });
    document.querySelectorAll('#dimSourceCardsContainer .dim-source-box').forEach(b => {
        b.classList.toggle('active-highlight', srcCode !== 'all' && b.dataset.boxSrc === srcCode);
    });
    document.querySelectorAll('#unifiedLineageTable [data-src-col]').forEach(cell => {
        if (srcCode === 'all') {
            cell.classList.remove('col-dimmed', 'col-highlighted');
        } else if (cell.dataset.srcCol === srcCode) {
            cell.classList.remove('col-dimmed');
            cell.classList.add('col-highlighted');
        } else {
            cell.classList.remove('col-highlighted');
            cell.classList.add('col-dimmed');
        }
    });
}

async function handleLiveSyncSubmit(e) {
    e.preventDefault();
    const btn = $('btnRunLiveSync');
    const targetType = $('demoTargetType').value;
    const userKey = state.lineageData?.userKey || JSON.parse(localStorage.getItem('dlu_student_user') || '{}').userKey || 1;

    const payload = {
        targetType,
        userKey,
        courseKey: targetType === 'Dim_Course' ? $('demoCourseSelect')?.value : $('demoGradeCourseSelect')?.value,
        newCohortName: $('demoNewCohort')?.value,
        newDepartment: $('demoNewDept')?.value,
        newCourseName: $('demoNewCourseName')?.value,
        newRawGrade: $('demoNewRawGrade')?.value
    };

    if (btn) btn.disabled = true;
    try {
        const res = await ApiService.runLiveSyncDemo(payload);
        if (!res.success) {
            showToast(res.message || 'Không thể chạy đồng bộ demo.', 'error');
            return;
        }
        showToast(res.message, 'success');
        const d = res.data;
        const box = $('liveSyncResultBox');
        if (box && d) {
            const renderKv = (obj) => Object.entries(obj).map(([k, v]) =>
                `<div class="diff-kv"><span class="text-muted-sm">${escapeHtml(k)}:</span> <strong>${escapeHtml(v)}</strong></div>`
            ).join('');

            box.className = '';
            box.innerHTML = `
                <div class="alert-box" style="background:var(--success-soft);border:1px solid var(--color-success);color:var(--color-success);margin-bottom:12px;">
                    <i class="fa-solid fa-circle-check"></i>
                    <strong>${escapeHtml(res.message)}</strong><br>
                    <span>Bảng trung gian: <code>${escapeHtml(d.stagingTable)}</code> → Bảng đích: <code>${escapeHtml(d.targetTable)}</code> | Khóa bảo toàn: <code>${escapeHtml(d.surrogateKeyName)} = ${escapeHtml(d.surrogateKeyValue)}</code></span>
                </div>
                <div class="diff-comparison-grid">
                    <div class="diff-box before">
                        <h5 class="text-amber"><i class="fa-solid fa-clock-rotate-left"></i> TRƯỚC KHI ĐỒNG BỘ (Before)</h5>
                        ${renderKv(d.before)}
                    </div>
                    <div class="diff-box after">
                        <h5 class="text-success"><i class="fa-solid fa-bolt"></i> SAU KHI STAGING + MERGE (After)</h5>
                        ${renderKv(d.after)}
                    </div>
                </div>
            `;
        }

        // Cập nhật lại ngay Bảng hợp nhất 5 Dim và Dashboard chính của sinh viên
        if (targetType === 'Dim_User' && d?.after?.CohortName) {
            const uJson = localStorage.getItem('dlu_student_user');
            if (uJson) {
                const uObj = JSON.parse(uJson);
                uObj.cohortName = d.after.CohortName;
                uObj.department = d.after.Department;
                localStorage.setItem('dlu_student_user', JSON.stringify(uObj));
                initStudentProfile();
            }
        }
        await loadStudentData(true);
    } catch (err) {
        showToast('Lỗi khi gọi API đồng bộ: ' + err.message, 'error');
    } finally {
        if (btn) btn.disabled = false;
    }
}

function goToFilteredTab(tab, pillStatus) {
    switchTab(tab);
    setPill(tab === 'assignments' ? 'assignPills' : 'quizPills', pillStatus);
    $('actionCenter').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function debounce(fn, ms) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function bindEvents() {
    $('loginForm').addEventListener('submit', async (e) => { e.preventDefault(); await handleLogin(); });
    $('btnLogout').addEventListener('click', () => {
        if (confirm('Bạn có chắc chắn muốn đăng xuất?')) {
            ApiService.clearToken();
            checkAuthState();
        }
    });

    $('btnTheme').addEventListener('click', toggleTheme);

    $('btnRefresh').addEventListener('click', () => loadStudentData(false));
    setInterval(() => { if (ApiService.getToken()) loadStudentData(true); }, 5 * 60 * 1000);

    // Countdown tick: render lại danh sách bài tập mỗi 30s để "Còn lại" luôn chính xác
    setInterval(() => { if (state.deadlines.length) renderAssignments(); }, 30 * 1000);

    document.querySelectorAll('#actionCenter .tab').forEach(t => t.addEventListener('click', () => switchTab(t.dataset.tab)));

    bindPills('assignPills', 'assign');
    bindPills('quizPills', 'quiz');

    const onAssignSearch = debounce(() => { state.assign.search = $('assignSearch').value; renderAssignments(); }, 200);
    const onQuizSearch = debounce(() => { state.quiz.search = $('quizSearch').value; renderQuizzes(); }, 200);
    const onReviewSearch = debounce(() => { state.review.search = $('reviewSearch').value; renderWrongQuestions(); }, 200);
    $('assignSearch').addEventListener('input', onAssignSearch);
    $('quizSearch').addEventListener('input', onQuizSearch);
    $('reviewSearch').addEventListener('input', onReviewSearch);

    $('assignCourse').addEventListener('change', (e) => { state.assign.course = e.target.value; renderAssignments(); });
    $('assignSort').addEventListener('change', (e) => { state.assign.sort = e.target.value; renderAssignments(); });
    $('quizCourse').addEventListener('change', (e) => { state.quiz.course = e.target.value; renderQuizzes(); });
    $('quizSort').addEventListener('change', (e) => { state.quiz.sort = e.target.value; renderQuizzes(); });
    $('reviewCourseFilters').addEventListener('click', (e) => {
        const button = e.target.closest('button[data-review-course]');
        if (!button) return;
        state.review.course = button.dataset.reviewCourse;
        renderReviewCourseFilters();
        renderWrongQuestions();
    });

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

    $('kpiGpa').addEventListener('click', () => $('transcriptSection').scrollIntoView({ behavior: 'smooth', block: 'start' }));
    $('kpiUrgent').addEventListener('click', () => goToFilteredTab('assignments', 'pending'));
    $('kpiOnTime').addEventListener('click', () => goToFilteredTab('assignments', 'all'));
    $('kpiQuiz').addEventListener('click', () => goToFilteredTab('quizzes', 'all'));

    $('btnViewUrgent')?.addEventListener('click', () => goToFilteredTab('assignments', 'pending'));

    // Sự kiện cho Trung tâm ETL Studio & Demo Đa Bảng
    $('btnOpenEtlStudio')?.addEventListener('click', () => {
        $('etlStudioSection')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });

    $('btnLoginEtlPreview')?.addEventListener('click', () => {
        $('loginView').classList.add('hidden');
        $('dashboardView').classList.remove('hidden');
        $('btnCloseGuestEtl')?.classList.remove('hidden');
        loadEtlStudioData();
        setTimeout(() => $('etlStudioSection')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 100);
    });

    $('btnCloseGuestEtl')?.addEventListener('click', () => {
        $('btnCloseGuestEtl').classList.add('hidden');
        checkAuthState();
    });

    document.querySelectorAll('#etlStudioTabs .tab').forEach(btn => {
        btn.addEventListener('click', () => {
            const tab = btn.dataset.etlTab;
            document.querySelectorAll('#etlStudioTabs .tab').forEach(t => {
                t.classList.toggle('active', t.dataset.etlTab === tab);
                t.setAttribute('aria-selected', String(t.dataset.etlTab === tab));
            });
            $('etl-panel-cost')?.classList.toggle('active', tab === 'cost');
            $('etl-panel-lineage')?.classList.toggle('active', tab === 'lineage');
            $('etl-panel-livedemo')?.classList.toggle('active', tab === 'livedemo');
        });
    });

    $('etlMappingTableFilter')?.addEventListener('change', (e) => {
        const mappings = state.etlCostData?.columnMappings || FALLBACK_COST_DATA.columnMappings;
        renderEtlMappingTable(mappings, e.target.value);
    });

    $('lineageHighlightPills')?.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-lineage-src]');
        if (!btn) return;
        highlightLineageSource(btn.dataset.lineageSrc);
    });

    $('demoTargetType')?.addEventListener('change', (e) => {
        const v = e.target.value;
        $('demoFieldsDimUser')?.classList.toggle('hidden', v !== 'Dim_User');
        $('demoFieldsDimCourse')?.classList.toggle('hidden', v !== 'Dim_Course');
        $('demoFieldsFactGrade')?.classList.toggle('hidden', v !== 'Fact_Course_Grades');
    });

    $('liveEtlDemoForm')?.addEventListener('submit', handleLiveSyncSubmit);

    document.querySelectorAll('.kpi-clickable').forEach(card => {
        card.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                card.click();
            }
        });
    });

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

    document.addEventListener('keydown', (e) => {
        if (e.key !== '/') return;
        const tag = (document.activeElement && document.activeElement.tagName) || '';
        if (['INPUT', 'SELECT', 'TEXTAREA'].includes(tag)) return;
        e.preventDefault();
        const searchInputs = { assignments: 'assignSearch', quizzes: 'quizSearch', review: 'reviewSearch' };
        const input = $(searchInputs[state.activeTab] || 'assignSearch');
        input.focus();
        input.select();
    });
}

document.addEventListener('DOMContentLoaded', () => {
    applyTheme(localStorage.getItem('dlu_theme') || 'dark', false);
    bindEvents();
    checkAuthState();
});
