// --- State Management ---
let currentSettings = {};
let foldersList = [];
let logsList = [];
let activeLogId = null;
let pollInterval = null;
let currentUsername = "";

// --- DOM Elements ---
const navButtons = document.querySelectorAll('.nav-btn');
const tabContents = document.querySelectorAll('.tab-content');
const pageTitle = document.getElementById('page-title');
const pageSubtitle = document.getElementById('page-subtitle');
const clockEl = document.getElementById('clock');
const toastContainer = document.getElementById('toast-container');

// Auth DOM Elements
const authOverlay = document.getElementById('auth-overlay');
const authRegisterScreen = document.getElementById('auth-register-screen');
const authSetup2faScreen = document.getElementById('auth-setup-2fa-screen');
const authLoginScreen = document.getElementById('auth-login-screen');
const authVerify2faScreen = document.getElementById('auth-verify-2fa-screen');
const authError = document.getElementById('auth-error');

const authRegisterForm = document.getElementById('auth-register-form');
const authSetup2faForm = document.getElementById('auth-setup-2fa-form');
const authLoginForm = document.getElementById('auth-login-form');
const authVerify2faForm = document.getElementById('auth-verify-2fa-form');

const qrCodeImg = document.getElementById('qr-code-img');
const qrSecretKey = document.getElementById('qr-secret-key');
const btnLogout = document.getElementById('btn-logout');
const btnFullSync = document.getElementById('btn-full-sync');

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
const fieldEmailNhInput = document.getElementById('field-email-nh');
const fieldPhoneNhInput = document.getElementById('field-phone-nh');
const fieldTgNhInput = document.getElementById('field-tg-nh');
const fieldInstaNhInput = document.getElementById('field-insta-nh');
const fieldHcIdNhInput = document.getElementById('field-hc-id-nh');
const checkboxUpdateNhChatLink = document.getElementById('update-nh-chat-link');
const groupNhLinkField = document.getElementById('nh-link-field-group');
const fieldLinkNhInput = document.getElementById('field-link-nh');

// UTM mappings
const utmSourceNhInput = document.getElementById('utm-source-nh');
const utmMediumNhInput = document.getElementById('utm-medium-nh');
const utmCampaignNhInput = document.getElementById('utm-campaign-nh');
const utmTermNhInput = document.getElementById('utm-term-nh');
const utmContentNhInput = document.getElementById('utm-content-nh');
const gclidNhInput = document.getElementById('gclid-nh');
const refererNhInput = document.getElementById('referer-nh');
const sourceNhInput = document.getElementById('source-nh');
const countryNhInput = document.getElementById('country-nh');
const cityNhInput = document.getElementById('city-nh');

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

// --- Authentication & 2FA Flow ---

function showAuthScreen(screen) {
    authRegisterScreen.classList.add('hidden');
    authSetup2faScreen.classList.add('hidden');
    authLoginScreen.classList.add('hidden');
    authVerify2faScreen.classList.add('hidden');
    authError.classList.add('hidden');
    
    if (screen === 'register') authRegisterScreen.classList.remove('hidden');
    else if (screen === 'setup_2fa') authSetup2faScreen.classList.remove('hidden');
    else if (screen === 'login') authLoginScreen.classList.remove('hidden');
    else if (screen === 'verify_2fa') authVerify2faScreen.classList.remove('hidden');
}

function showAuthError(msg) {
    authError.textContent = msg;
    authError.classList.remove('hidden');
}

// Check auth status on load
async function checkAuthStatus() {
    try {
        const res = await fetch('/api/auth/status');
        const data = await res.json();
        
        if (data.status === 'unregistered') {
            authOverlay.classList.remove('hidden');
            showAuthScreen('register');
        } else if (data.status === 'unauthenticated') {
            authOverlay.classList.remove('hidden');
            showAuthScreen('login');
        } else if (data.status === 'authenticated') {
            authOverlay.classList.add('hidden');
            currentUsername = data.username;
            initializeDashboard();
        }
    } catch (err) {
        console.error(err);
        showToast('Помилка перевірки авторизації', 'error');
    }
}

// 1. Submit Registration Form
authRegisterForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = document.getElementById('reg-username').value.trim();
    const password = document.getElementById('reg-password').value;
    
    try {
        const res = await fetch('/api/auth/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });
        const data = await res.json();
        
        if (res.ok) {
            currentUsername = data.username;
            // Display QR Code from backend-generated data URI
            qrCodeImg.src = data.qr_code_data_uri;
            qrSecretKey.textContent = data.twofa_secret;
            
            showAuthScreen('setup_2fa');
            showToast('Адміністратора створено! Налаштуйте 2FA', 'success');
        } else {
            showAuthError(data.detail || 'Помилка реєстрації');
        }
    } catch {
        showAuthError('Не вдалося зв\'язатися з сервером.');
    }
});

// 2. Submit Setup 2FA Confirm Form
authSetup2faForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const token = document.getElementById('setup-totp-token').value.trim();
    
    try {
        const res = await fetch('/api/auth/verify-2fa', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: currentUsername, token })
        });
        const data = await res.json();
        
        if (res.ok) {
            authOverlay.classList.add('hidden');
            showToast('Двофакторну автентифікацію успішно підключено!', 'success');
            initializeDashboard();
        } else {
            showAuthError(data.detail || 'Невірний 2FA код');
        }
    } catch {
        showAuthError('Не вдалося підтвердити 2FA.');
    }
});

// 3. Submit Login Form
authLoginForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = document.getElementById('login-username').value.trim();
    const password = document.getElementById('login-password').value;
    
    try {
        const res = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });
        const data = await res.json();
        
        if (res.ok) {
            currentUsername = username;
            if (data.status === 'setup_2fa') {
                // If 2FA scan wasn't complete
                qrCodeImg.src = data.qr_code_data_uri;
                qrSecretKey.textContent = data.twofa_secret;
                showAuthScreen('setup_2fa');
            } else if (data.status === 'require_2fa') {
                showAuthScreen('verify_2fa');
            }
        } else {
            showAuthError(data.detail || 'Невірне ім\'я користувача або пароль');
        }
    } catch {
        showAuthError('Не вдалося зв\'язатися з сервером.');
    }
});

// 4. Submit 2FA Code Login Verification Form
authVerify2faForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const token = document.getElementById('login-totp-token').value.trim();
    
    try {
        const res = await fetch('/api/auth/login-2fa', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: currentUsername, token })
        });
        const data = await res.json();
        
        if (res.ok) {
            authOverlay.classList.add('hidden');
            showToast('Вхід виконано!', 'success');
            initializeDashboard();
        } else {
            showAuthError(data.detail || 'Невірний 2FA код');
        }
    } catch {
        showAuthError('Помилка входу.');
    }
});

// 5. Logout Trigger
btnLogout.addEventListener('click', async () => {
    try {
        const res = await fetch('/api/auth/logout', { method: 'POST' });
        if (res.ok) {
            // Stop polling
            if (pollInterval) {
                clearInterval(pollInterval);
                pollInterval = null;
            }
            currentUsername = "";
            authOverlay.classList.remove('hidden');
            showAuthScreen('login');
            showToast('Ви вийшли з системи', 'info');
        }
    } catch {
        showToast('Помилка виходу', 'error');
    }
});

// --- API Helpers (Automatically handle 401 Unauthorized Session Expired) ---
async function secureFetch(url, options = {}) {
    const res = await fetch(url, options);
    if (res.status === 401) {
        if (pollInterval) {
            clearInterval(pollInterval);
            pollInterval = null;
        }
        authOverlay.classList.remove('hidden');
        showAuthScreen('login');
        showToast('Сесія завершилась. Увійдіть знову.', 'error');
        throw new Error('Unauthorized');
    }
    return res;
}

// --- Dashboard & Settings Logic ---

function initializeDashboard() {
    loadSettings();
    refreshMetricsAndLogs();
    
    // Start Polling every 5 seconds
    if (!pollInterval) {
        pollInterval = setInterval(refreshMetricsAndLogs, 5000);
    }
}

// Fetch & Populate settings
async function loadSettings() {
    try {
        const res = await secureFetch('/api/settings');
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
        fieldEmailNhInput.value = settings.email_field_nh || 'Email';
        fieldPhoneNhInput.value = settings.phone_field_nh || 'Phone';
        fieldTgNhInput.value = settings.telegram_field_nh || 'Telegram';
        fieldInstaNhInput.value = settings.instagram_field_nh || 'Instagram';
        fieldHcIdNhInput.value = settings.hc_id_field_nh || 'HelpCrunch ID';
        fieldLinkNhInput.value = settings.nh_chat_link_field || 'HelpCrunch Chat Link';
        
        // Populate UTM tracking inputs
        utmSourceNhInput.value = settings.utm_source_field_nh || 'utm_source';
        utmMediumNhInput.value = settings.utm_medium_field_nh || 'utm_medium';
        utmCampaignNhInput.value = settings.utm_campaign_field_nh || 'utm_campaign';
        utmTermNhInput.value = settings.utm_term_field_nh || 'utm_term';
        utmContentNhInput.value = settings.utm_content_field_nh || 'utm_content';
        gclidNhInput.value = settings.gclid_field_nh || 'gclid';
        refererNhInput.value = settings.referer_field_nh || 'Referer';
        sourceNhInput.value = settings.source_field_nh || 'Source';
        countryNhInput.value = settings.country_field_nh || 'Country';
        cityNhInput.value = settings.city_field_nh || 'City';
        
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
    }
}

// Quietly check connection status on page load
async function testConnectionsQuietly() {
    updateConnDot(hcConnStatusDot, hcConnStatusText, 'checking', 'Перевірка...');
    updateConnDot(nhConnStatusDot, nhConnStatusText, 'checking', 'Перевірка...');
    
    if (currentSettings.helpcrunch_api_key) {
        try {
            const res = await secureFetch('/api/test-helpcrunch', {
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
            const res = await secureFetch('/api/test-nethunt', {
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
        const res = await secureFetch('/api/nethunt/folders', {
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

    if (selectedContact) {
        loadFolderFields(selectedContact);
    }
}

// NetHunt field mapping dropdowns list
const nhFieldSelects = [
    { select: fieldEmailNhInput, settingKey: 'email_field_nh', label: 'Email' },
    { select: fieldPhoneNhInput, settingKey: 'phone_field_nh', label: 'Phone' },
    { select: fieldTgNhInput, settingKey: 'telegram_field_nh', label: 'Telegram' },
    { select: fieldInstaNhInput, settingKey: 'instagram_field_nh', label: 'Instagram' },
    { select: fieldHcIdNhInput, settingKey: 'hc_id_field_nh', label: 'HelpCrunch ID' },
    { select: fieldLinkNhInput, settingKey: 'nh_chat_link_field', label: 'Чат лінк' },
    { select: utmSourceNhInput, settingKey: 'utm_source_field_nh', label: 'UTM Source' },
    { select: utmMediumNhInput, settingKey: 'utm_medium_field_nh', label: 'UTM Medium' },
    { select: utmCampaignNhInput, settingKey: 'utm_campaign_field_nh', label: 'UTM Campaign' },
    { select: utmTermNhInput, settingKey: 'utm_term_field_nh', label: 'UTM Term' },
    { select: utmContentNhInput, settingKey: 'utm_content_field_nh', label: 'UTM Content' },
    { select: gclidNhInput, settingKey: 'gclid_field_nh', label: 'Gclid' },
    { select: refererNhInput, settingKey: 'referer_field_nh', label: 'Referer' },
    { select: sourceNhInput, settingKey: 'source_field_nh', label: 'Source' },
    { select: countryNhInput, settingKey: 'country_field_nh', label: 'Country' },
    { select: cityNhInput, settingKey: 'city_field_nh', label: 'City' }
];

async function loadFolderFields(folderId) {
    if (!folderId) {
        nhFieldSelects.forEach(item => {
            item.select.innerHTML = '<option value="">Спочатку виберіть папку контактів...</option>';
        });
        return;
    }

    // Show loading state
    nhFieldSelects.forEach(item => {
        item.select.innerHTML = '<option value="">Завантаження полів...</option>';
    });

    try {
        const email = nhApiEmailInput.value.trim();
        const key = nhApiKeyInput.value.trim();
        const base_url = nhBaseUrlInput.value.trim();

        const res = await secureFetch('/api/nethunt/folder-fields', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, key, base_url, folder_id: folderId })
        });

        if (res.ok) {
            const fields = await res.json();
            nhFieldSelects.forEach(item => {
                item.select.innerHTML = `<option value="">-- Виберіть поле для ${item.label} --</option>`;
                
                // Sort fields alphabetically by name
                const sortedFields = [...fields].sort((a, b) => a.name.localeCompare(b.name));
                
                sortedFields.forEach(f => {
                    const opt = document.createElement('option');
                    opt.value = f.name;
                    opt.textContent = f.name;
                    if (currentSettings[item.settingKey] === f.name) {
                        opt.selected = true;
                    }
                    item.select.appendChild(opt);
                });

                // Fallback: If saved value is not found in CRM and is not empty, add it to options and select it
                const savedVal = currentSettings[item.settingKey];
                if (savedVal && !fields.some(f => f.name === savedVal)) {
                    const opt = document.createElement('option');
                    opt.value = savedVal;
                    opt.textContent = `${savedVal} (Не знайдено в CRM)`;
                    opt.selected = true;
                    item.select.appendChild(opt);
                }
            });
        } else {
            showToast('Не вдалося завантажити поля папки NetHunt', 'error');
            nhFieldSelects.forEach(item => {
                item.select.innerHTML = '<option value="">Помилка завантаження полів</option>';
            });
        }
    } catch (err) {
        console.error("Error loading folder fields:", err);
        nhFieldSelects.forEach(item => {
            item.select.innerHTML = '<option value="">Помилка завантаження</option>';
        });
    }
}

// Listener for Contacts folder change
nhFolderContactsSelect.addEventListener('change', (e) => {
    loadFolderFields(e.target.value);
});

// Test HelpCrunch Button Handler
document.getElementById('btn-test-hc').addEventListener('click', async () => {
    const key = hcApiKeyInput.value.trim();
    if (!key) {
        showToast('Введіть API ключ для тесту', 'error');
        return;
    }
    
    const btn = document.getElementById('btn-test-hc');
    const origHtml = btn.innerHTML;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Тестування...';
    btn.disabled = true;
    
    try {
        const res = await secureFetch('/api/test-helpcrunch', {
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

// Test NetHunt Button Handler
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
        const res = await secureFetch('/api/test-nethunt', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, key, base_url })
        });
        const data = await res.json();
        if (res.ok) {
            showToast('Підключення до NetHunt CRM успішне!', 'success');
            updateConnDot(nhConnStatusDot, nhConnStatusText, 'online', 'Активний');
            
            // Fetch and update folders select options immediately
            const fRes = await secureFetch('/api/nethunt/folders', {
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

// Save Config Form Handler
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
        instagram_field_nh: fieldInstaNhInput.value.trim(),
        hc_id_field_nh: fieldHcIdNhInput.value,
        phone_field_nh: fieldPhoneNhInput.value,
        email_field_nh: fieldEmailNhInput.value,
        update_nh_chat_link: checkboxUpdateNhChatLink.checked ? 'true' : 'false',
        nh_chat_link_field: fieldLinkNhInput.value.trim(),
        
        // Include UTM mapping fields
        utm_source_field_nh: utmSourceNhInput.value.trim(),
        utm_medium_field_nh: utmMediumNhInput.value.trim(),
        utm_campaign_field_nh: utmCampaignNhInput.value.trim(),
        utm_term_field_nh: utmTermNhInput.value.trim(),
        utm_content_field_nh: utmContentNhInput.value.trim(),
        gclid_field_nh: gclidNhInput.value.trim(),
        referer_field_nh: refererNhInput.value.trim(),
        source_field_nh: sourceNhInput.value.trim(),
        country_field_nh: countryNhInput.value.trim(),
        city_field_nh: cityNhInput.value.trim()
    };
    
    const saveStatus = document.getElementById('save-status-msg');
    saveStatus.textContent = 'Збереження...';
    saveStatus.className = 'save-status';
    
    try {
        const res = await secureFetch('/api/settings', {
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

// Simulation Form Submit
simulationForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const payload = {
        event: document.getElementById('sim-event').value,
        name: document.getElementById('sim-name').value,
        email: document.getElementById('sim-email').value,
        phone: document.getElementById('sim-phone').value,
        telegram: document.getElementById('sim-telegram').value,
        chat_id: parseInt(document.getElementById('sim-chat-id').value) || null,
        // UTM parameters simulator attributes
        utm_source: document.getElementById('sim-utm-source').value || "",
        utm_medium: document.getElementById('sim-utm-medium').value || "",
        utm_campaign: document.getElementById('sim-utm-campaign').value || "",
        gclid: document.getElementById('sim-gclid').value || ""
    };
    
    try {
        const res = await secureFetch('/api/simulate-webhook', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (res.ok) {
            showToast('Симуляцію webhook запущено в чергу!', 'success');
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

// Metrics & Logs Polling
async function refreshMetricsAndLogs() {
    if (!currentUsername) return; // Wait until authenticated
    
    // Fetch metrics
    try {
        const res = await secureFetch('/api/metrics');
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
        const res = await secureFetch(url);
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
        const timeStr = new Date(log.timestamp).toLocaleTimeString();
        
        let statusBadge = '';
        if (log.level === 'warning') statusBadge = '<span class="badge warning">Застереження</span>';
        else if (log.status === 'success') statusBadge = '<span class="badge success">Успішно</span>';
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
    
    if (activeLogId) {
        const selectedLog = logsList.find(l => l.id === activeLogId);
        if (selectedLog) {
            updateDetailsPanel(selectedLog);
        }
    }
}

window.selectLog = function(logId) {
    activeLogId = logId;
    const rows = document.querySelectorAll('.log-row');
    rows.forEach(row => row.classList.remove('active'));
    
    const selectedLog = logsList.find(l => l.id === logId);
    if (selectedLog) {
        updateDetailsPanel(selectedLog);
        const matchedRow = Array.from(rows).find(row => row.outerHTML.includes(selectedLog.customer_name) && row.outerHTML.includes(new Date(selectedLog.timestamp).toLocaleTimeString()));
        if (matchedRow) matchedRow.classList.add('active');
    }
};

function updateDetailsPanel(log) {
    detailPlaceholder.classList.add('hidden');
    detailContent.classList.remove('hidden');
    
    detTime.textContent = new Date(log.timestamp).toLocaleString();
    detEvent.textContent = log.event_type;
    detEvent.className = 'value badge success';
    
    detUser.textContent = log.customer_name;
    
    const contactInfo = [];
    if (log.customer_email) contactInfo.push(`Email: ${log.customer_email}`);
    if (log.customer_phone) contactInfo.push(`Тел: ${log.customer_phone}`);
    detContact.textContent = contactInfo.join(' | ') || 'Контактних даних немає';
    
    detStatus.textContent = log.status === 'success' ? 'Успішно знайдено' : (log.status === 'no_match' ? 'Користувача немає в CRM' : 'Помилка обробки');
    detStatus.className = `value badge ${log.status}`;
    
    detTrace.textContent = log.details || '';
}

// Full sync trigger
async function triggerFullSync() {
    if (!btnFullSync) return;
    btnFullSync.disabled = true;
    btnFullSync.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Синхронізація...';
    
    try {
        const res = await secureFetch('/api/sync/full', { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
            showToast('Повна синхронізація запущена у фоні', 'success');
        } else {
            showToast(data.detail || 'Не вдалося запустити синхронізацію', 'error');
        }
    } catch (err) {
        showToast('Помилка запуску синхронізації', 'error');
    } finally {
        btnFullSync.disabled = false;
        btnFullSync.innerHTML = '<i class="fa-solid fa-rotate"></i> Повна синхронізація';
    }
}

if (btnFullSync) {
    btnFullSync.addEventListener('click', triggerFullSync);
}

// Log refresh actions
btnRefreshLogs.addEventListener('click', refreshMetricsAndLogs);
logFilterStatus.addEventListener('change', refreshMetricsAndLogs);

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    checkAuthStatus();
});
