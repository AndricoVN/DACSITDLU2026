/**
 * api.js
 * Quản lý các lệnh gọi HTTP REST API kèm JWT Authentication
 */
const API_BASE_URL = 'api';

const ApiService = {
    getToken() {
        return localStorage.getItem('dlu_student_token');
    },

    setToken(token) {
        localStorage.setItem('dlu_student_token', token);
    },

    clearToken() {
        localStorage.removeItem('dlu_student_token');
        localStorage.removeItem('dlu_student_user');
    },

    getAuthHeaders() {
        const token = this.getToken();
        return {
            'Content-Type': 'application/json',
            'Authorization': token ? `Bearer ${token}` : ''
        };
    },

    async login(username, password) {
        const response = await fetch(`${API_BASE_URL}/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });
        return await response.json();
    },

    async getSummary() {
        const response = await fetch(`${API_BASE_URL}/student/summary`, {
            headers: this.getAuthHeaders()
        });
        return await response.json();
    },

    async getDeadlines() {
        const response = await fetch(`${API_BASE_URL}/student/deadlines`, {
            headers: this.getAuthHeaders()
        });
        return await response.json();
    },

    async getCourses() {
        const response = await fetch(`${API_BASE_URL}/student/courses`, {
            headers: this.getAuthHeaders()
        });
        return await response.json();
    },

    async getQuizzes() {
        const response = await fetch(`${API_BASE_URL}/student/quizzes`, {
            headers: this.getAuthHeaders()
        });
        return await response.json();
    },

    async getWrongQuestions(courseKey = 'all') {
        const qs = courseKey && courseKey !== 'all' ? `?courseKey=${encodeURIComponent(courseKey)}` : '';
        const response = await fetch(`${API_BASE_URL}/student/wrong-questions${qs}`, {
            headers: this.getAuthHeaders()
        });
        return await response.json();
    }
};