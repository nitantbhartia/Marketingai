/**
 * ClaimCoach Dashboard JavaScript
 * Handles interactive functionality for article review dashboard
 */

// ── Configuration ──────────────────────────────────────────

const API_BASE = window.location.origin;
const REFRESH_INTERVAL = 30000; // 30 seconds

// ── Utility Functions ──────────────────────────────────────

function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;

    // Add to page
    let toastContainer = document.querySelector('.toast-container');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.className = 'toast-container';
        document.body.appendChild(toastContainer);
    }

    toastContainer.appendChild(toast);

    // Animate in
    setTimeout(() => toast.classList.add('show'), 10);

    // Remove after 4 seconds
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

function showLoading(button) {
    button.dataset.originalText = button.textContent;
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span> Loading...';
}

function hideLoading(button) {
    button.disabled = false;
    button.textContent = button.dataset.originalText;
}

async function apiRequest(url, method = 'GET', data = null, timeoutMs = 30000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    const options = {
        method,
        headers: {
            'Content-Type': 'application/json'
        },
        signal: controller.signal,
    };

    if (data) {
        options.body = JSON.stringify(data);
    }

    try {
        const response = await fetch(url, options);
        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.error || 'Request failed');
        }

        return result;
    } catch (error) {
        if (error.name === 'AbortError') {
            throw new Error('Request timed out');
        }
        console.error('API request failed:', error);
        throw error;
    } finally {
        clearTimeout(timer);
    }
}

// ── Modal Functions ────────────────────────────────────────

function openModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.style.display = 'flex';
        // Prevent body scroll
        document.body.style.overflow = 'hidden';
    }
}

function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.style.display = 'none';
        // Restore body scroll
        document.body.style.overflow = '';

        // Clear form fields
        const textarea = modal.querySelector('textarea');
        if (textarea) {
            textarea.value = '';
        }
    }
}

// Close modal on backdrop click
document.addEventListener('click', (e) => {
    if (e.target.classList.contains('modal')) {
        closeModal(e.target.id);
    }
});

// Close modal on Escape key
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        const modals = document.querySelectorAll('.modal');
        modals.forEach(modal => {
            if (modal.style.display === 'flex') {
                closeModal(modal.id);
            }
        });
    }
});

// ── Article Review Actions ─────────────────────────────────

function approveArticle() {
    openModal('approveModal');
}

async function submitApproval() {
    const notes = document.getElementById('approveNotes').value;
    const button = event.target;

    showLoading(button);

    try {
        const result = await apiRequest(
            `${API_BASE}/api/article/${articleId}/approve`,
            'POST',
            { notes }
        );

        showToast('✅ Article approved for publishing!', 'success');
        closeModal('approveModal');

        // Reload page after short delay
        setTimeout(() => window.location.reload(), 1500);

    } catch (error) {
        showToast(`Error approving article: ${error.message}`, 'error');
        hideLoading(button);
    }
}

function requestRevision() {
    openModal('revisionModal');
}

async function submitRevision() {
    const reason = document.getElementById('revisionReason').value;

    if (!reason.trim()) {
        showToast('Please describe what needs to be fixed', 'warning');
        return;
    }

    const button = event.target;
    showLoading(button);

    try {
        const result = await apiRequest(
            `${API_BASE}/api/article/${articleId}/reject`,
            'POST',
            { reason }
        );

        showToast('✏️ Revision request sent!', 'success');
        closeModal('revisionModal');

        // Reload page after short delay
        setTimeout(() => window.location.reload(), 1500);

    } catch (error) {
        showToast(`Error requesting revision: ${error.message}`, 'error');
        hideLoading(button);
    }
}

async function publishNow() {
    if (!confirm('Publish this article immediately?')) {
        return;
    }

    const button = event.target;
    showLoading(button);

    try {
        const result = await apiRequest(
            `${API_BASE}/api/article/${articleId}/publish`,
            'POST'
        );

        if (result.published_url) {
            showToast('🚀 Article published successfully!', 'success');

            // Reload page after short delay
            setTimeout(() => window.location.reload(), 1500);
        } else {
            showToast('Article queued for publishing', 'info');
        }

    } catch (error) {
        showToast(`Error publishing article: ${error.message}`, 'error');
        hideLoading(button);
    }
}

async function retryArticle() {
    if (!confirm('Send this article back to Quill for revision? Sage\'s feedback will be used to improve it.')) {
        return;
    }

    const button = event.target;
    showLoading(button);

    try {
        await apiRequest(
            `${API_BASE}/api/article/${articleId}/retry`,
            'POST'
        );

        showToast('Article sent back for revision', 'success');
        setTimeout(() => window.location.reload(), 1500);
    } catch (error) {
        showToast(`Error retrying article: ${error.message}`, 'error');
        hideLoading(button);
    }
}

async function promoteToSeed() {
    if (!confirm('Promote this article markdown into the seed library?')) {
        return;
    }
    const button = event.target;
    showLoading(button);
    try {
        const result = await apiRequest(
            `${API_BASE}/api/seed/promote/${articleId}`,
            'POST'
        );
        showToast(`Promoted to seed: ${result.file}`, 'success');
    } catch (error) {
        showToast(`Error promoting seed: ${error.message}`, 'error');
        hideLoading(button);
    }
}

// ── Pipeline Control Functions ─────────────────────────────

const PIPELINE_STEPS = ['scout', 'brief-topics', 'promote', 'quill', 'sage'];

function addLogEntry(message, type = 'info') {
    const logPanel = document.getElementById('pipelineLog');
    const logEntries = document.getElementById('logEntries');
    if (!logPanel || !logEntries) return;

    logPanel.style.display = 'block';

    const entry = document.createElement('div');
    entry.className = `log-entry log-${type}`;
    const time = new Date().toLocaleTimeString();
    entry.innerHTML = `<span class="log-time">[${time}]</span> ${message}`;
    logEntries.appendChild(entry);
    logEntries.scrollTop = logEntries.scrollHeight;
}

function clearLog() {
    const logEntries = document.getElementById('logEntries');
    const logPanel = document.getElementById('pipelineLog');
    if (logEntries) logEntries.innerHTML = '';
    if (logPanel) logPanel.style.display = 'none';
}

function setStepState(stepName, state) {
    // Map trigger name to step element id
    const idMap = { 'scout': 'step-scout', 'brief-topics': 'step-brief', 'promote': 'step-promote', 'quill': 'step-quill', 'sage': 'step-sage' };
    const el = document.getElementById(idMap[stepName]);
    if (!el) return;
    el.classList.remove('step-running', 'step-done', 'step-error');
    if (state) el.classList.add(`step-${state}`);
}

function pollJob(jobId, maxWaitMs = 200000) {
    // Poll /trigger/status/{jobId} until done, error, or timeout
    return new Promise((resolve, reject) => {
        const deadline = Date.now() + maxWaitMs;
        let pollCount = 0;

        const poll = async () => {
            if (Date.now() > deadline) {
                return reject(new Error(`Job ${jobId} timed out after ${Math.round(maxWaitMs / 1000)}s`));
            }
            pollCount++;
            try {
                const res = await apiRequest(`${API_BASE}/trigger/status/${jobId}`, 'GET', null, 10000);
                if (res.status === 'done') return resolve(res);
                if (res.status === 'error') return reject(new Error(res.error || 'Agent failed'));
                // Still running — poll again (slow down after first few polls)
                const delay = pollCount < 5 ? 2000 : 4000;
                setTimeout(poll, delay);
            } catch (e) {
                // Network error polling — retry with backoff
                setTimeout(poll, 5000);
            }
        };
        setTimeout(poll, 1500); // First poll after 1.5s
    });
}

function buildSummary(result) {
    if (result.promoted !== undefined) return `Promoted ${result.promoted} articles`;
    if (result.briefed !== undefined) return `Briefed ${result.briefed} topics`;
    if (result.result) {
        const r = result.result;
        if (r.status === 'idle') return `Idle — ${r.reason || 'no work to do'}`;
        if (r.title) return `Wrote: ${r.title} (${r.word_count || '?'} words)`;
        return typeof r === 'string' ? r : JSON.stringify(r).slice(0, 120);
    }
    return 'Done';
}

async function triggerStep(stepName, button) {
    if (button) showLoading(button);
    setStepState(stepName, 'running');
    addLogEntry(`Starting <strong>${stepName}</strong>...`);

    try {
        const triggerRes = await apiRequest(`${API_BASE}/trigger/${stepName}`);

        let result;
        if (triggerRes.job_id) {
            // Background job — poll for completion
            addLogEntry(`<strong>${stepName}</strong> running in background (job ${triggerRes.job_id})...`);
            result = await pollJob(triggerRes.job_id);
        } else {
            // Synchronous response (e.g. promote, brief-topics)
            result = triggerRes;
        }

        setStepState(stepName, 'done');
        const summary = buildSummary(result);
        addLogEntry(`<strong>${stepName}</strong> completed: ${summary}`, 'success');
        showToast(`${stepName} completed!`, 'success');
        return result;
    } catch (error) {
        setStepState(stepName, 'error');
        addLogEntry(`<strong>${stepName}</strong> failed: ${error.message}`, 'error');
        showToast(`${stepName} failed: ${error.message}`, 'error');
        throw error;
    } finally {
        if (button) hideLoading(button);
    }
}

async function runFullPipeline() {
    const runAllBtn = document.getElementById('runAllBtn');
    if (runAllBtn) showLoading(runAllBtn);

    // Reset all step states
    PIPELINE_STEPS.forEach(s => setStepState(s, null));
    clearLog();

    addLogEntry('Starting full pipeline...', 'info');

    for (const step of PIPELINE_STEPS) {
        try {
            await triggerStep(step, null);
        } catch (e) {
            addLogEntry(`Pipeline stopped at <strong>${step}</strong>. Fix the issue and retry.`, 'error');
            if (runAllBtn) hideLoading(runAllBtn);
            return;
        }
    }

    addLogEntry('Full pipeline completed! Refreshing dashboard...', 'success');
    showToast('Full pipeline completed!', 'success');
    if (runAllBtn) hideLoading(runAllBtn);

    // Refresh dashboard after a short delay to show new articles
    setTimeout(() => window.location.reload(), 2000);
}

function refreshDashboard() {
    window.location.reload();
}

// ── Content Display Functions ──────────────────────────────

function showFullContent() {
    const fullContent = document.getElementById('fullContent');
    const previewSection = document.querySelector('.review-section:has(.content-preview)');

    if (fullContent) {
        fullContent.style.display = 'block';

        // Hide preview section
        if (previewSection) {
            previewSection.style.display = 'none';
        }

        // Scroll to full content
        fullContent.scrollIntoView({ behavior: 'smooth' });
    }
}

// ── Dashboard Filters ──────────────────────────────────────

function filterArticles(status) {
    if (!status) {
        return;
    }
    // Update active filter button
    const filterButtons = document.querySelectorAll('.filter-btn[data-status]');
    filterButtons.forEach(btn => {
        if (btn.dataset.status === status) {
            btn.classList.add('active');
        } else {
            btn.classList.remove('active');
        }
    });

    // Reload page with filter
    const url = new URL(window.location);
    const product = url.searchParams.get('product');
    if (status === 'all') {
        url.searchParams.delete('status');
    } else {
        url.searchParams.set('status', status);
    }
    if (product) {
        url.searchParams.set('product', product);
    }
    window.location.href = url.toString();
}

// ── Auto-refresh Dashboard ─────────────────────────────────

let refreshTimer = null;

function startAutoRefresh() {
    // Only auto-refresh on dashboard page, not article review
    if (!window.location.pathname.includes('/article/')) {
        refreshTimer = setInterval(() => {
            // Silently reload page
            window.location.reload();
        }, REFRESH_INTERVAL);
    }
}

function stopAutoRefresh() {
    if (refreshTimer) {
        clearInterval(refreshTimer);
        refreshTimer = null;
    }
}

// Stop refresh when page is hidden
document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
        stopAutoRefresh();
    } else {
        startAutoRefresh();
    }
});

// ── Notifications ──────────────────────────────────────────

function checkNotifications() {
    // Could implement WebSocket or polling for real-time notifications
    // For now, just refresh the page periodically
}

// ── Keyboard Shortcuts ─────────────────────────────────────

document.addEventListener('keydown', (e) => {
    // Only on article review page
    if (!window.location.pathname.includes('/article/')) {
        return;
    }

    // Ctrl/Cmd + Enter = Approve
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault();
        approveArticle();
    }

    // Ctrl/Cmd + R = Request Revision
    if ((e.ctrlKey || e.metaKey) && e.key === 'r') {
        e.preventDefault();
        requestRevision();
    }
});

// ── Initialization ─────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    // Start auto-refresh on dashboard
    startAutoRefresh();

    // Initialize filter buttons
    const filterButtons = document.querySelectorAll('.filter-btn[data-status]');
    filterButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            const status = btn.dataset.status;
            filterArticles(status);
        });
    });

    // Set active filter based on URL
    const urlParams = new URLSearchParams(window.location.search);
    const currentStatus = urlParams.get('status') || 'all';
    const activeBtn = document.querySelector(`.filter-btn[data-status="${currentStatus}"]`);
    if (activeBtn) {
        activeBtn.classList.add('active');
    }

    console.log('ClaimCoach Dashboard initialized');
});

// ── Export for testing ─────────────────────────────────────

if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        approveArticle,
        requestRevision,
        publishNow,
        showFullContent,
        filterArticles,
        closeModal
    };
}
