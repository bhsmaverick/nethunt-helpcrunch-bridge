// --- State Management ---
let currentSettings = {};
let foldersList = [];
let logsList = [];
let activeLogId = null;
let pollInterval = null;

// --- DOM Elements ---
const navButtons = document.querySelectorAll('.nav-btn');
const tabContents = document.querySelectorAll('.tab-content');
const pageTitle = document.getElementById('page-title');
const pageSubtitle = document.getElementById('page-subtitle');
const clockEl = document.getElementById('clock');
const toastContainer = document.getElementById('toast-container');

// Settings inputs
const hcApiKeyInput = document.getElementById('hc-api-key');
const hcSubdomainInput = document.getElementById('hc-subdomain');
const hcWebhookSecretInput = document.getElementById('hc-webhook-secret');
const nhApiEmailInput = document.getElementById('nh-api-email');
const nhApiKeyInput = document.getElementById('nh-api-key');
const nhBaseUrlInput = document.getElementById('nh-base-url');
const nhFolderContactsSelect = document.getElementById('nh-folder-contacts');
const nhFolderDealsSelect = document.getElementById('nh-folder-deals');
const syncPriorityInput = document.getElementById('sync-priority');
const fieldTgHcInput = document.getElementById('field-tg-hc');
const fieldTgNhInput = document.getElementById('field-tg-nh');
const checkboxUpdateNhChatLink = document.getElementById('update-nh-chat-link');
const groupNhLinkField = document.getElementById('nh-link-field-group');
const fieldLinkNhInput = document.getElementById('field-link-nh');

// Connection statuses
const hcConnStatusDot = document.querySelector('#hc-conn-status .status-dot');
const hcConnStatusText = document.querySelector('#hc-conn-status .status-text');
const nhConnStatusDot = document.querySelector('#nh-conn-status .status-dot');
const nhConnStatusText = document.querySelector('#nh-conn-status .status-text');

// Metrics
const metricTotal = document.getElementById('metric-total');
const metricMatches = document.getElementById('metric-matches');
const metricRate = document.getElementById('metric-rate');
const metricErrors = document.getElementById('metric-errors');

// Logs & Details
const logsContainer = document.getElementById('logs-container');
const logFilterStatus = document.getElementById('log-filter-status');
const btnRefreshLogs = document.getElementById('btn-refresh-logs');
const detailPlaceholder = document.getElementById('detail-placeholder');
const detailContent = document.getElementById('detail-content');
const detTime = document.getElementById('det-time');
const detEvent = document.getElementById('det-event');
const detUser = document.getElementById('det-user');
const detContact = document.getElementById('det-contact');
const detStatus = document.getElementById('det-status');
const detTrace = document.getElementById('det-trace');

// Webhook Simulation
const simulationForm = document.getElementById('simulation-form');
const generatedWebhookUrl = document.getElementById('generated-webhook-url');
const btnCopyWebhook = document.getElementById('btn-copy-webhook');

// --- Helper Functions ---
function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    
    let iconClass = 'fa-circle-info';
    if (type === 'success') iconClass = 'fa-circle-check';
    if (type === 'error') iconClass = 'fa-circle-exclamation';
    
    toast.innerHTML = `
        <i class="fa-solid ${iconClass}"></i>
        <span>${message}</span>
    `;
    
    toastContainer.appendChild(toast);
    
    setTimeout(() => {
        toast.style.animation = 'fadeOut 0.3s forwards ease';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// Clock logic
function updateClock() {
    const now = new Date();
    clockEl.textContent = now.toTimeString().split(' ')[0];
}
setInterval(updateClock, 1000);
updateClock();

// Tab switching
navButtons.forEach(btn => {
    btn.addEventListener('click', () => {
        const tabName = btn.dataset.tab;
        
        navButtons.forEach(b => b.classList.remove('active'));
        tabContents.forEach(c => c.classList.remove('active'));
        
        btn.classList.add('active');
        document.getElementById(`tab-${tabName}`).classList.add('active');
        
        // Update headers text
        if (tabName === 'dashboard') {
            pageTitle.textContent = 'Панель моніторингу';
            pageSubtitle.textContent = 'Перегляд синхронізації та системних метрик у реальному часі';
        } else if (tabName === 'settings') {
            pageTitle.textContent = 'Налаштування інтеграції';
            pageSubtitle.textContent = 'Керування підключенням до API NetHunt CRM та HelpCrunch';
        } else if (tabName === 'simulation') {
            pageTitle.textContent = 'Симуляція Webhook';
            pageSubtitle.textContent = 'Ручне тестування працездатності алгоритму пошуку угод';
        } else if (tabName === 'guide') {
            pageTitle.textContent = 'Інструкція підключення';
            pageSubtitle.textContent = 'Як інтегрувати цей міст з кабінетом HelpCrunch';
        }
    });
});

// Show/Hide NetHunt Link mapping inputs
checkboxUpdateNhChatLink.addEventListener('change', () => {
    if (checkboxUpdateNhChatLink.checked) {
        groupNhLinkField.classList.remove('hidden');
    } else {
        groupNhLinkField.classList.add('hidden');
    }
});

// Copy Webhook Link to Clipboard
btnCopyWebhook.addEventListener('click', () => {
    generatedWebhookUrl.select();
    document.execCommand('copy');
    showToast('Посилання на Webhook скопійовано!', 'success');
});

// --- API Calls ---

// 1. Fetch & Populate settings
async function loadSettings() {
    try {
        const res = await fetch('/api/settings');
        const settings = await res.json();
        currentSettings = settings;
        
        // Populate inputs
        hcApiKeyInput.value = settings.helpcrunch_api_key || '';
        hcSubdomainInput.value = settings.helpcrunch_subdomain || '';
        hcWebhookSecretInput.value = settings.helpcrunch_webhook_secret || '';
        nhApiEmailInput.value = settings.nethunt_api_email || '';
        nhApiKeyInput.value = settings.nethunt_api_key || '';
        nhBaseUrlInput.value = settings.nethunt_base_url || 'https://nethunt.co';
        syncPriorityInput.value = settings.sync_priority || 'email,phone,telegram';
        fieldTgHcInput.value = settings.telegram_field_hc || 'telegram';
        fieldTgNhInput.value = settings.telegram_field_nh || 'Telegram';
        fieldLinkNhInput.value = settings.nh_chat_link_field || 'HelpCrunch Chat Link';
        
        checkboxUpdateNhChatLink.checked = settings.update_nh_chat_link === 'true';
        if (checkboxUpdateNhChatLink.checked) {
            groupNhLinkField.classList.remove('hidden');
        } else {
            groupNhLinkField.classList.add('hidden');
        }
        
        // Formulate Webhook guide URL
        const origin = window.location.origin;
        generatedWebhookUrl.value = `${origin}/api/webhook`;
        
        // Try testing connection quietly
        testConnectionsQuietly();
        
    } catch (err) {
        console.error(err);
        showToast('Помилка завантаження конфігурації', 'error');
    }
}

// Quietly check connection status on page load
async function testConnectionsQuietly() {
    updateConnDot(hcConnStatusDot, hcConnStatusText, 'checking', 'Перевірка...');
    updateConnDot(nhConnStatusDot, nhConnStatusText, 'checking', 'Перевірка...');
    
    if (currentSettings.helpcrunch_api_key) {
        try {
            const res = await fetch('/api/test-helpcrunch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key: currentSettings.helpcrunch_api_key })
            });
            if (res.ok) {
                updateConnDot(hcConnStatusDot, hcConnStatusText, 'online', 'Активний');
            } else {
                updateConnDot(hcConnStatusDot, hcConnStatusText, 'offline', 'Помилка');
            }
        } catch {
            updateConnDot(hcConnStatusDot, hcConnStatusText, 'offline', 'Помилка');
        }
    } else {
        updateConnDot(hcConnStatusDot, hcConnStatusText, 'offline', 'Не налаштовано');
    }
    
    if (currentSettings.nethunt_api_email && currentSettings.nethunt_api_key) {
        try {
            const res = await fetch('/api/test-nethunt', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    email: currentSettings.nethunt_api_email,
                    key: currentSettings.nethunt_api_key,
                    base_url: currentSettings.nethunt_base_url
                })
            });
            if (res.ok) {
                updateConnDot(nhConnStatusDot, nhConnStatusText, 'online', 'Активний');
                // Load NetHunt folders lists
                await loadFoldersQuietly();
            } else {
                updateConnDot(nhConnStatusDot, nhConnStatusText, 'offline', 'Помилка');
            }
        } catch {
            updateConnDot(nhConnStatusDot, nhConnStatusText, 'offline', 'Помилка');
        }
    } else {
        updateConnDot(nhConnStatusDot, nhConnStatusText, 'offline', 'Не налаштовано');
    }
}

function updateConnDot(dotEl, textEl, status, text) {
    dotEl.className = `status-dot ${status}`;
    textEl.textContent = text;
}

// Load NetHunt Folders into options select
async function loadFoldersQuietly() {
    try {
        const res = await fetch('/api/nethunt/folders', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                email: currentSettings.nethunt_api_email,
                key: currentSettings.nethunt_api_key,
                base_url: currentSettings.nethunt_base_url
            })
        });
        if (res.ok) {
            foldersList = await res.json();
            populateFolderOptions();
        }
    } catch (err) {
        console.error("Error loading NetHunt folders:", err);
    }
}

function populateFolderOptions() {
    // Save selected options
    const selectedContact = currentSettings.nethunt_contacts_folder;
    const selectedDeals = currentSettings.nethunt_deals_folder;
    
    nhFolderContactsSelect.innerHTML = '<option value="">-- Виберіть папку контактів --</option>';
    nhFolderDealsSelect.innerHTML = '<option value="">-- Виберіть папку угод (Необов\'язково) --</option>';
    
    foldersList.forEach(folder => {
        const opt1 = document.createElement('option');
        opt1.value = folder.id;
        opt1.textContent = `${folder.name} (${folder.id})`;
        if (folder.id === selectedContact) opt1.selected = true;
        nhFolderContactsSelect.appendChild(opt1);
        
        const opt2 = document.createElement('option');
        opt2.value = folder.id;
        opt2.textContent = `${folder.name} (${folder.id})`;
        if (folder.id === selectedDeals) opt2.selected = true;
        nhFolderDealsSelect.appendChild(opt2);
    });
}

// 2. Test HelpCrunch Button Handler
document.getElementById('btn-test-hc').addEventListener('click', async () => {
    const key = hcApiKeyInput.value.strip ? hcApiKeyInput.value.strip() : hcApiKeyInput.value;
    if (!key) {
        showToast('Введіть API ключ для тесту', 'error');
        return;
    }
    
    const btn = document.getElementById('btn-test-hc');
    const origHtml = btn.innerHTML;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Тестування...';
    btn.disabled = true;
    
    try {
        const res = await fetch('/api/test-helpcrunch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key })
        });
        const data = await res.json();
        if (res.ok) {
            showToast('Підключення до HelpCrunch успішне!', 'success');
            updateConnDot(hcConnStatusDot, hcConnStatusText, 'online', 'Активний');
        } else {
            showToast(data.detail || 'Помилка підключення', 'error');
            updateConnDot(hcConnStatusDot, hcConnStatusText, 'offline', 'Помилка');
        }
    } catch {
        showToast('Помилка надсилання запиту до сервера', 'error');
    } finally {
        btn.innerHTML = origHtml;
        btn.disabled = false;
    }
});

// 3. Test NetHunt Button Handler
document.getElementById('btn-test-nh').addEventListener('click', async () => {
    const email = nhApiEmailInput.value.trim();
    const key = nhApiKeyInput.value.trim();
    const base_url = nhBaseUrlInput.value.trim();
    
    if (!email || !key) {
        showToast('Введіть Email та API ключ NetHunt', 'error');
        return;
    }
    
    const btn = document.getElementById('btn-test-nh');
    const origHtml = btn.innerHTML;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Тестування...';
    btn.disabled = true;
    
    try {
        const res = await fetch('/api/test-nethunt', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, key, base_url })
        });
        const data = await res.json();
        if (res.ok) {
            showToast('Підключення до NetHunt CRM успішне!', 'success');
            updateConnDot(nhConnStatusDot, nhConnStatusText, 'online', 'Активний');
            
            // Fetch and update folders select options immediately
            const fRes = await fetch('/api/nethunt/folders', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, key, base_url })
            });
            if (fRes.ok) {
                foldersList = await fRes.json();
                populateFolderOptions();
                showToast('Папки завантажені у налаштування', 'success');
            }
        } else {
            showToast(data.detail || 'Помилка підключення', 'error');
            updateConnDot(nhConnStatusDot, nhConnStatusText, 'offline', 'Помилка');
        }
    } catch {
        showToast('Помилка надсилання запиту до сервера', 'error');
    } finally {
        btn.innerHTML = origHtml;
        btn.disabled = false;
    }
});

// 4. Save Config Form Handler
document.getElementById('btn-save-settings').addEventListener('click', async () => {
    const payload = {
        helpcrunch_api_key: hcApiKeyInput.value.trim(),
        helpcrunch_subdomain: hcSubdomainInput.value.trim(),
        helpcrunch_webhook_secret: hcWebhookSecretInput.value.trim(),
        nethunt_api_email: nhApiEmailInput.value.trim(),
        nethunt_api_key: nhApiKeyInput.value.trim(),
        nethunt_contacts_folder: nhFolderContactsSelect.value,
        nethunt_deals_folder: nhFolderDealsSelect.value,
        nethunt_base_url: nhBaseUrlInput.value.trim(),
        sync_priority: syncPriorityInput.value.trim(),
        telegram_field_hc: fieldTgHcInput.value.trim(),
        telegram_field_nh: fieldTgNhInput.value.trim(),
        phone_field_nh: 'Phone', // Default standard CRM search parameter
        email_field_nh: 'Email', // Default
        update_nh_chat_link: checkboxUpdateNhChatLink.checked ? 'true' : 'false',
        nh_chat_link_field: fieldLinkNhInput.value.trim()
    };
    
    const saveStatus = document.getElementById('save-status-msg');
    saveStatus.textContent = 'Збереження...';
    saveStatus.className = 'save-status';
    
    try {
        const res = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (res.ok) {
            currentSettings = data.settings;
            saveStatus.textContent = 'Налаштування успішно збережено!';
            saveStatus.className = 'save-status success';
            showToast('Конфігурацію збережено!', 'success');
            setTimeout(() => { saveStatus.textContent = ''; }, 3000);
            
            // Refresh connection tests
            testConnectionsQuietly();
        } else {
            saveStatus.textContent = 'Помилка збереження: ' + data.detail;
            saveStatus.className = 'save-status error';
            showToast('Помилка збереження налаштувань', 'error');
        }
    } catch {
        saveStatus.textContent = 'Помилка надсилання запиту.';
        saveStatus.className = 'save-status error';
        showToast('Помилка зв\'язку з сервером', 'error');
    }
});

// 5. Simulation Form Submit
simulationForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const payload = {
        event: document.getElementById('sim-event').value,
        name: document.getElementById('sim-name').value,
        email: document.getElementById('sim-email').value,
        phone: document.getElementById('sim-phone').value,
        telegram: document.getElementById('sim-telegram').value,
        chat_id: parseInt(document.getElementById('sim-chat-id').value) || null
    };
    
    try {
        const res = await fetch('/api/simulate-webhook', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (res.ok) {
            showToast('Симуляцію webhook запущено в чергу!', 'success');
            // Switch to dashboard tab to watch the log
            setTimeout(() => {
                document.querySelector('.nav-btn[data-tab="dashboard"]').click();
                refreshMetricsAndLogs();
            }, 500);
        } else {
            showToast('Помилка симуляції: ' + data.detail, 'error');
        }
    } catch {
        showToast('Помилка підключення до сервера', 'error');
    }
});

// 6. Metrics & Logs Polling
async function refreshMetricsAndLogs() {
    // Fetch metrics
    try {
        const res = await fetch('/api/metrics');
        const metrics = await res.json();
        
        metricTotal.textContent = metrics.total_syncs || 0;
        metricMatches.textContent = metrics.matched_syncs || 0;
        metricRate.textContent = `${metrics.match_rate || 0}%`;
        metricErrors.textContent = metrics.errors || 0;
    } catch (e) {
        console.error("Error fetching metrics:", e);
    }
    
    // Fetch logs
    try {
        const statusFilter = logFilterStatus.value;
        const url = `/api/logs?limit=50${statusFilter ? `&status=${statusFilter}` : ''}`;
        const res = await fetch(url);
        const logs = await res.json();
        logsList = logs;
        
        renderLogs();
    } catch (e) {
        console.error("Error fetching logs:", e);
    }
}

function renderLogs() {
    if (logsList.length === 0) {
        logsContainer.innerHTML = `
            <div class="loading-state">
                <i class="fa-solid fa-folder-open"></i>
                Логів немає
            </div>
        `;
        return;
    }
    
    let html = '';
    logsList.forEach(log => {
        const isSelected = activeLogId === log.id ? 'active' : '';
        
        // Time parsing
        const timeStr = new Date(log.timestamp).toLocaleTimeString();
        
        let statusBadge = '';
        if (log.status === 'success') statusBadge = '<span class="badge success">Успішно</span>';
        else if (log.status === 'no_match') statusBadge = '<span class="badge no_match">Не знайдено</span>';
        else if (log.status === 'error') statusBadge = '<span class="badge error">Помилка</span>';
        
        html += `
            <div class="log-row ${isSelected}" onclick="selectLog(${log.id})">
                <div class="log-meta">
                    <span class="log-title">${log.customer_name}</span>
                    <span class="log-sub">${timeStr} • Email: ${log.customer_email || 'n/a'}</span>
                </div>
                <div class="log-status">
                    ${statusBadge}
                    <i class="fa-solid fa-chevron-right" style="font-size:0.8rem; color:var(--text-muted);"></i>
                </div>
            </div>
        `;
    });
    
    logsContainer.innerHTML = html;
    
    // Auto-update details view if selected log exists
    if (activeLogId) {
        const selectedLog = logsList.find(l => l.id === activeLogId);
        if (selectedLog) {
            updateDetailsPanel(selectedLog);
        }
    }
}

window.selectLog = function(logId) {
    activeLogId = logId;
    
    // Toggle active class visually
    const rows = document.querySelectorAll('.log-row');
    rows.forEach(row => row.classList.remove('active'));
    
    // Re-render to ensure selection highlights stay correct
    const selectedLog = logsList.find(l => l.id === logId);
    if (selectedLog) {
        updateDetailsPanel(selectedLog);
        
        // Find row manually and add active class to prevent fully re-rendering immediately
        // (better user experience, no blinking)
        const matchedRow = Array.from(rows).find(row => row.outerHTML.includes(selectedLog.customer_name) && row.outerHTML.includes(new Date(selectedLog.timestamp).toLocaleTimeString()));
        if (matchedRow) matchedRow.classList.add('active');
    }
};

function updateDetailsPanel(log) {
    detailPlaceholder.classList.add('hidden');
    detailContent.classList.remove('hidden');
    
    detTime.textContent = new Date(log.timestamp).toLocaleString();
    detEvent.textContent = log.event_type;
    detEvent.className = 'value badge success'; // Event reset style
    
    detUser.textContent = log.customer_name;
    
    const contactInfo = [];
    if (log.customer_email) contactInfo.push(`Email: ${log.customer_email}`);
    if (log.customer_phone) contactInfo.push(`Тел: ${log.customer_phone}`);
    detContact.textContent = contactInfo.join(' | ') || 'Контактних даних немає';
    
    detStatus.textContent = log.status === 'success' ? 'Успішно знайдено' : (log.status === 'no_match' ? 'Користувача немає в CRM' : 'Помилка обробки');
    detStatus.className = `value badge ${log.status}`;
    
    detTrace.textContent = log.details || '';
}

// Log refresh actions
btnRefreshLogs.addEventListener('click', refreshMetricsAndLogs);
logFilterStatus.addEventListener('change', refreshMetricsAndLogs);

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    loadSettings();
    refreshMetricsAndLogs();
    
    // Start Polling every 5 seconds
    pollInterval = setInterval(refreshMetricsAndLogs, 5000);
});
