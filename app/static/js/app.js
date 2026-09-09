/**
 * Google Workspace Automated Provisioning Frontend Application
 */

const API_BASE = '/api/v1';
let currentPollingJobId = null;
let pollInterval = null;
let loggedInUser = null;

// Preset sample data
const PRESET_COMPANY = {
  company_name: 'Econz Tech Solutions Pvt Ltd',
  primary_domain: 'econz-tech.com',
  alternate_email: 'founder@econz-alt.com',
  contact_name: 'Ratnesh Admin',
  address_line1: 'Econz Innovation Hub, MG Road',
  locality: 'Bengaluru',
  region: 'KA',
  postal_code: '560001',
  country_code: 'IN',
  plan: 'FLEXIBLE',
  sku_id: 'SKU-BUSINESS-STANDARD',
  license_count: 5,
  initiated_by_email: '',
  econz_notification_email: 'econz-notify@example.net',
  employees: [
    { first_name: '', last_name: '', personal_email: '' }
  ]
};

// Initial state
let employees = [...PRESET_COMPANY.employees];

// ---------- Authentication ----------

/**
 * Called by Google Identity Services after the user signs in.
 * Receives a credential (JWT) that we send to our backend for verification.
 */
async function handleGoogleCredentialResponse(response) {
  try {
    const res = await fetch(`${API_BASE}/auth/google`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ credential: response.credential }),
    });

    if (!res.ok) {
      const err = await res.json();
      showToast(err.detail || 'Login failed', 'error');
      return;
    }

    const user = await res.json();
    onLoginSuccess(user);
  } catch (e) {
    showToast('Login failed: ' + e.message, 'error');
  }
}

/** Show the app after successful login. */
function onLoginSuccess(user) {
  loggedInUser = user;

  // Hide login overlay, show app
  document.getElementById('login-overlay').classList.add('hidden');
  document.getElementById('main-navbar').style.display = '';
  document.getElementById('main-content').style.display = '';

  // Show user info in navbar
  const badge = document.getElementById('user-badge');
  const avatar = document.getElementById('user-avatar');
  const displayName = document.getElementById('user-display-name');
  const logoutBtn = document.getElementById('btn-logout');

  if (user.picture) {
    avatar.src = user.picture;
  } else {
    avatar.src = 'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSIjMzRkMzk5Ij48cGF0aCBkPSJNMTIgMkM2LjQ4IDIgMiA2LjQ4IDIgMTJzNC40OCAxMCAxMCAxMCAxMC00LjQ4IDEwLTEwUzE3LjUyIDIgMTIgMnptMCAzYzEuNjYgMCAzIDEuMzQgMyAzcy0xLjM0IDMtMyAzLTMtMS4zNC0zLTMgMS4zNC0zIDMtM3ptMCAxNC4yYy0yLjUgMC00LjcxLTEuMjgtNi0zLjIyLjAzLTEuOTkgNC0zLjA4IDYtMy4wOCAxLjk5IDAgNS45NyAxLjA5IDYgMy4wOC0xLjI5IDEuOTQtMy41IDMuMjItNiAzLjIyeiIvPjwvc3ZnPg==';
  }
  displayName.textContent = user.name || user.email;
  badge.style.display = 'flex';
  logoutBtn.style.display = '';

  // Auto-fill the Initiator Email field with the logged-in user's email
  const initEmailInput = document.getElementById('input-init-email');
  if (initEmailInput && user.email) {
    initEmailInput.value = user.email;
  }

  showToast(`Welcome, ${user.given_name || user.name}! 👋`, 'success');
}

/** Handle logout button click. */
async function handleLogout() {
  try {
    await fetch(`${API_BASE}/auth/logout`, { method: 'POST' });
  } catch (e) { /* ignore */ }

  loggedInUser = null;

  // Show login overlay, hide app
  document.getElementById('login-overlay').classList.remove('hidden');
  document.getElementById('main-navbar').style.display = 'none';
  document.getElementById('main-content').style.display = 'none';
  document.getElementById('user-badge').style.display = 'none';
  document.getElementById('btn-logout').style.display = 'none';

  showToast('Logged out successfully', 'info');
}

/** Initialize Google Sign-In button. */
async function initGoogleSignIn() {
  try {
    // Fetch the client ID from backend
    const res = await fetch(`${API_BASE}/auth/client-id`);
    const data = await res.json();

    if (!data.client_id) {
      document.getElementById('google-signin-btn').innerHTML =
        '<p style="color: #ef4444; font-size: 0.85rem;">OAuth Client ID not configured. Check .env</p>';
      return;
    }

    // Wait for GIS library to load
    const waitForGIS = () => new Promise((resolve) => {
      if (window.google && window.google.accounts) { resolve(); return; }
      const check = setInterval(() => {
        if (window.google && window.google.accounts) { clearInterval(check); resolve(); }
      }, 100);
    });

    await waitForGIS();

    google.accounts.id.initialize({
      client_id: data.client_id,
      callback: handleGoogleCredentialResponse,
      auto_select: false,
    });

    google.accounts.id.renderButton(
      document.getElementById('google-signin-btn'),
      {
        theme: 'filled_blue',
        size: 'large',
        shape: 'pill',
        text: 'signin_with',
        width: 280,
      }
    );
  } catch (e) {
    console.error('Failed to init Google Sign-In:', e);
  }
}

/** Check if user has an existing session on page load. */
async function checkExistingSession() {
  try {
    const res = await fetch(`${API_BASE}/auth/me`);
    if (res.ok) {
      const user = await res.json();
      onLoginSuccess(user);
      return true;
    }
  } catch (e) { /* not logged in */ }
  return false;
}

// ---------- DOMContentLoaded ----------

document.addEventListener('DOMContentLoaded', async () => {
  initTabs();
  initForm();
  initCsvUploader();
  initJobMonitor();
  renderEmployeeRows();
  checkHealth();

  // Auth: check existing session first, then init sign-in button
  const hasSession = await checkExistingSession();
  if (!hasSession) {
    initGoogleSignIn();
  }
});

// Toast notification helper
function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  
  let icon = 'ℹ️';
  if (type === 'success') icon = '✅';
  if (type === 'error') icon = '❌';

  toast.innerHTML = `<span>${icon}</span> <span>${message}</span>`;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(100%)';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// Health check & header status
async function checkHealth() {
  try {
    const res = await fetch('/health');
    const data = await res.json();
    if (data.status === 'healthy') {
      const engineBadge = document.getElementById('engine-badge-text');
      if (engineBadge) {
        engineBadge.textContent = `Adapter: ${data.adapter.toUpperCase()}`;
      }
    }
  } catch (err) {
    console.error('Health check failed', err);
  }
}

// Tab Switching
function initTabs() {
  const tabBtns = document.querySelectorAll('.tab-btn');
  tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      tabBtns.forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

      btn.classList.add('active');
      const targetId = btn.getAttribute('data-target');
      const target = document.getElementById(targetId);
      if (target) target.classList.add('active');

      if (targetId === 'tab-companies') {
        loadCompaniesList();
      }
    });
  });
}

// Switch tab programmatically
function switchTab(tabId) {
  const btn = document.querySelector(`[data-target="${tabId}"]`);
  if (btn) btn.click();
}

// ==========================================================================
// 1. Manual Form Provisioning (Enterprise: Admin-Only)
// ==========================================================================
function initForm() {
  const form = document.getElementById('provision-form');
  const resetBtn = document.getElementById('btn-reset-form');
  const fillPresetBtn = document.getElementById('btn-fill-preset');

  if (fillPresetBtn) {
    fillPresetBtn.addEventListener('click', () => {
      document.getElementById('input-company-name').value = PRESET_COMPANY.company_name;
      document.getElementById('input-primary-domain').value = PRESET_COMPANY.primary_domain;
      document.getElementById('input-alternate-email').value = PRESET_COMPANY.alternate_email;
      document.getElementById('input-contact-name').value = PRESET_COMPANY.contact_name;
      document.getElementById('input-address').value = PRESET_COMPANY.address_line1;
      document.getElementById('input-locality').value = PRESET_COMPANY.locality;
      document.getElementById('input-region').value = PRESET_COMPANY.region;
      document.getElementById('input-postal').value = PRESET_COMPANY.postal_code;
      document.getElementById('input-country').value = PRESET_COMPANY.country_code;
      document.getElementById('select-plan').value = PRESET_COMPANY.plan;
      document.getElementById('select-sku').value = PRESET_COMPANY.sku_id;
      document.getElementById('input-license-count').value = PRESET_COMPANY.license_count;
      document.getElementById('input-init-email').value = loggedInUser ? loggedInUser.email : PRESET_COMPANY.initiated_by_email;
      document.getElementById('input-econz-email').value = PRESET_COMPANY.econz_notification_email;
      document.getElementById('input-admin-first').value = 'Ratnesh';
      document.getElementById('input-admin-last').value = 'Admin';
      document.getElementById('input-admin-recovery').value = 'ratnesh@gmail.com';
      showToast('Sample company data loaded', 'info');
    });
  }

  if (resetBtn) {
    resetBtn.addEventListener('click', () => {
      form.reset();
      if (loggedInUser) {
        document.getElementById('input-init-email').value = loggedInUser.email;
      }
    });
  }

  if (form) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      await submitProvisionForm();
    });
  }
}

function renderEmployeeRows() {
  // No longer needed — kept as no-op for backward compat
}

async function submitProvisionForm() {
  const submitBtn = document.getElementById('btn-submit-provision');
  const errorBox = document.getElementById('form-error-box');
  errorBox.style.display = 'none';

  const adminFirst = document.getElementById('input-admin-first').value.trim();
  const adminLast = document.getElementById('input-admin-last').value.trim();

  if (!adminFirst || !adminLast) {
    showToast('Admin first name and last name are required', 'error');
    return;
  }

  const payload = {
    company_name: document.getElementById('input-company-name').value.trim(),
    primary_domain: document.getElementById('input-primary-domain').value.trim().toLowerCase(),
    alternate_email: document.getElementById('input-alternate-email').value.trim().toLowerCase(),
    contact_name: document.getElementById('input-contact-name').value.trim(),
    postal_address: {
      address_line1: document.getElementById('input-address').value.trim(),
      locality: document.getElementById('input-locality').value.trim(),
      region: document.getElementById('input-region').value.trim(),
      postal_code: document.getElementById('input-postal').value.trim(),
      country_code: document.getElementById('input-country').value.trim().toUpperCase(),
    },
    plan: document.getElementById('select-plan').value,
    sku_id: document.getElementById('select-sku').value,
    license_count: parseInt(document.getElementById('input-license-count').value, 10),
    initiated_by_email: document.getElementById('input-init-email').value.trim().toLowerCase(),
    econz_notification_email: document.getElementById('input-econz-email').value.trim().toLowerCase(),
    admin_first_name: adminFirst,
    admin_last_name: adminLast,
    admin_recovery_email: document.getElementById('input-admin-recovery').value.trim() || null,
  };

  const domain = payload.primary_domain;
  const adminEmail = `${adminFirst.toLowerCase()}.${adminLast.toLowerCase()}@${domain}`;

  const idempotencyKey = document.getElementById('input-idempotency-key').value.trim();
  const headers = { 'Content-Type': 'application/json' };
  if (idempotencyKey) {
    headers['Idempotency-Key'] = idempotencyKey;
  }

  submitBtn.disabled = true;
  submitBtn.innerHTML = '⏳ Creating domain & admin account...';

  try {
    const res = await fetch(`${API_BASE}/provision`, {
      method: 'POST',
      headers: headers,
      body: JSON.stringify(payload)
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || data.error || 'Failed to submit request');
    }

    showToast(`Job ${data.job_id} initiated! Creating admin: ${adminEmail}`, 'success');
    
    // Jump to Monitor tab
    switchTab('tab-monitor');
    startJobPolling(data.job_id);

  } catch (err) {
    errorBox.textContent = `Error: ${err.message}`;
    errorBox.style.display = 'block';
    showToast(err.message, 'error');
  } finally {
    submitBtn.disabled = false;
    submitBtn.innerHTML = '🚀 Provision Domain & Admin Account';
  }
}

// ==========================================================================
// 2. Single-Company CSV Upload
// ==========================================================================
let parsedCsvEmployees = [];
let selectedCsvFile = null;

function initCsvUploader() {
  const dropzone = document.getElementById('csv-dropzone');
  const fileInput = document.getElementById('csv-file-input');
  const form = document.getElementById('csv-upload-form');

  if (dropzone && fileInput) {
    dropzone.addEventListener('click', () => fileInput.click());

    dropzone.addEventListener('dragover', (e) => {
      e.preventDefault();
      dropzone.classList.add('dragover');
    });

    dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));

    dropzone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropzone.classList.remove('dragover');
      if (e.dataTransfer.files.length > 0) {
        handleCsvFileSelect(e.dataTransfer.files[0]);
      }
    });

    fileInput.addEventListener('change', (e) => {
      if (e.target.files.length > 0) {
        handleCsvFileSelect(e.target.files[0]);
      }
    });
  }

  if (form) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      await submitCsvProvision();
    });
  }
}

function handleCsvFileSelect(file) {
  if (!file.name.endsWith('.csv')) {
    showToast('Please select a valid .csv file', 'error');
    return;
  }
  selectedCsvFile = file;
  document.getElementById('csv-file-name').textContent = `Selected: ${file.name} (${(file.size / 1024).toFixed(1)} KB)`;

  const reader = new FileReader();
  reader.onload = (e) => {
    parseCsvContent(e.target.result);
  };
  reader.readAsText(file);
}

function parseCsvContent(text) {
  const previewCard = document.getElementById('csv-preview-card');
  const tableBody = document.getElementById('csv-preview-body');
  const csvWarning = document.getElementById('csv-limit-warning');
  const submitBtn = document.getElementById('btn-submit-csv');

  parsedCsvEmployees = [];
  tableBody.innerHTML = '';
  csvWarning.style.display = 'none';

  const lines = text.split(/\r?\n/).filter(line => line.trim());
  if (lines.length <= 1) {
    showToast('CSV file is empty or has no data rows', 'error');
    return;
  }

  const headers = lines[0].split(',').map(h => h.trim().toLowerCase().replace(/^["']|["']$/g, ''));
  
  for (let i = 1; i < lines.length; i++) {
    const row = lines[i].split(',').map(c => c.trim().replace(/^["']|["']$/g, ''));
    if (row.length === 0 || !row[0]) continue;

    let first = '';
    let last = '';
    let email = '';

    headers.forEach((h, colIdx) => {
      const val = row[colIdx] || '';
      if (h === 'first_name' || h === 'given_name') first = val;
      else if (h === 'last_name' || h === 'family_name') last = val;
      else if (h === 'name' || h === 'full_name') {
        const parts = val.split(' ');
        first = parts[0];
        last = parts.slice(1).join(' ') || 'User';
      }
      else if (h === 'personal_email' || h === 'email') email = val;
    });

    if (first || last) {
      parsedCsvEmployees.push({
        first_name: first || 'User',
        last_name: last || 'Workspace',
        personal_email: email || '-'
      });
    }
  }

  previewCard.style.display = 'block';

  if (parsedCsvEmployees.length > 5) {
    csvWarning.style.display = 'block';
    csvWarning.innerHTML = `⚠️ <strong>Limit Exceeded:</strong> Found ${parsedCsvEmployees.length} users. The system allows a maximum of <strong>5 users</strong> per provisioning request. Please edit the CSV.`;
    submitBtn.disabled = true;
  } else {
    submitBtn.disabled = false;
  }

  parsedCsvEmployees.forEach((emp, idx) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><strong>#${idx + 1}</strong></td>
      <td>${escapeHtml(emp.first_name)}</td>
      <td>${escapeHtml(emp.last_name)}</td>
      <td><code>${escapeHtml(emp.personal_email)}</code></td>
    `;
    tableBody.appendChild(tr);
  });
}

async function submitCsvProvision() {
  if (!selectedCsvFile) {
    showToast('Please select a CSV file first', 'error');
    return;
  }
  if (parsedCsvEmployees.length > 5) {
    showToast('Cannot submit CSV with more than 5 users. Please reduce to 5 or fewer.', 'error');
    return;
  }

  const submitBtn = document.getElementById('btn-submit-csv');
  const formData = new FormData();
  formData.append('file', selectedCsvFile);

  const domainVal = document.getElementById('csv-input-domain').value.trim();
  const companyVal = document.getElementById('csv-input-company').value.trim();
  const planVal = document.getElementById('csv-select-plan').value;

  if (domainVal) formData.append('primary_domain', domainVal);
  if (companyVal) formData.append('company_name', companyVal);
  if (planVal) formData.append('plan', planVal);

  submitBtn.disabled = true;
  submitBtn.innerHTML = '⏳ Uploading & Provisioning...';

  try {
    const res = await fetch(`${API_BASE}/provision/csv`, {
      method: 'POST',
      body: formData
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || data.error || 'CSV provisioning failed');
    }

    showToast(`Single-company job ${data.job_id} launched for ${data.company_name}!`, 'success');
    switchTab('tab-monitor');
    startJobPolling(data.job_id);

  } catch (err) {
    showToast(err.message, 'error');
  } finally {
    submitBtn.disabled = false;
    submitBtn.innerHTML = '🚀 Upload & Provision Company';
  }
}

// Download Sample CSV helper
function downloadSampleCsv(type) {
  let content = '';
  let filename = 'sample_company_5users.csv';

  if (type === 'names') {
    filename = 'sample_names_5users.csv';
    content = 'first_name,last_name,personal_email\n' +
      'Rahul,Sharma,rahul.sharma@gmail.com\n' +
      'Priya,Patel,priya.patel@gmail.com\n' +
      'Amit,Kumar,amit.kumar@gmail.com\n' +
      'Ananya,Verma,ananya.verma@gmail.com\n' +
      'Vikram,Singh,vikram.singh@gmail.com\n';
  } else {
    filename = 'sample_single_company.csv';
    content = 'company_name,primary_domain,alternate_email,contact_name,address_line1,locality,region,postal_code,country_code,plan,sku_id,license_count,initiated_by_email,econz_notification_email,first_name,last_name,personal_email\n' +
      'Econz Tech Innovations,econz-tech.com,admin@econz-alt.com,Ratnesh Admin,Econz Tower MG Road,Bengaluru,KA,560001,IN,FLEXIBLE,SKU-BUSINESS-STANDARD,5,ratnesh@econz-alt.com,econz-notifications@example.net,Rahul,Sharma,rahul.sharma@gmail.com\n' +
      'Econz Tech Innovations,econz-tech.com,admin@econz-alt.com,Ratnesh Admin,Econz Tower MG Road,Bengaluru,KA,560001,IN,FLEXIBLE,SKU-BUSINESS-STANDARD,5,ratnesh@econz-alt.com,econz-notifications@example.net,Priya,Patel,priya.patel@gmail.com\n' +
      'Econz Tech Innovations,econz-tech.com,admin@econz-alt.com,Ratnesh Admin,Econz Tower MG Road,Bengaluru,KA,560001,IN,FLEXIBLE,SKU-BUSINESS-STANDARD,5,ratnesh@econz-alt.com,econz-notifications@example.net,Amit,Kumar,amit.kumar@gmail.com\n';
  }

  const blob = new Blob([content], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.setAttribute('href', url);
  link.setAttribute('download', filename);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

// ==========================================================================
// 3. Real-Time Job Monitor
// ==========================================================================
function initJobMonitor() {
  const searchBtn = document.getElementById('btn-search-job');
  const jobInput = document.getElementById('input-search-job');

  if (searchBtn && jobInput) {
    searchBtn.addEventListener('click', () => {
      const jobId = jobInput.value.trim();
      if (jobId) startJobPolling(jobId);
    });
    jobInput.addEventListener('keypress', (e) => {
      if (e.key === 'Enter') {
        const jobId = jobInput.value.trim();
        if (jobId) startJobPolling(jobId);
      }
    });
  }
}

function startJobPolling(jobId) {
  if (pollInterval) clearInterval(pollInterval);
  currentPollingJobId = jobId;
  
  const searchInput = document.getElementById('input-search-job');
  if (searchInput) searchInput.value = jobId;

  fetchJobStatus(jobId);
  pollInterval = setInterval(() => {
    fetchJobStatus(jobId);
  }, 1500);
}

async function fetchJobStatus(jobId) {
  try {
    const res = await fetch(`${API_BASE}/provision/${jobId}`);
    if (!res.ok) {
      if (res.status === 404) {
        showToast(`Job ${jobId} not found`, 'error');
        clearInterval(pollInterval);
        return;
      }
      throw new Error('Failed to retrieve job');
    }

    const data = await res.json();
    renderJobDetails(data);

    // Stop polling on terminal states
    if (['COMPLETED', 'PARTIAL_FAILURE', 'FAILED'].includes(data.status)) {
      clearInterval(pollInterval);
      pollInterval = null;
    }
  } catch (err) {
    console.error('Polling error', err);
  }
}

function renderJobDetails(job) {
  const container = document.getElementById('monitor-results');
  if (!container) return;
  container.style.display = 'block';

  // Status Badge
  const statusPill = document.getElementById('monitor-status-pill');
  statusPill.className = `status-pill ${job.status}`;
  statusPill.textContent = job.status;

  // Metadata
  document.getElementById('monitor-job-id').textContent = job.job_id;
  document.getElementById('monitor-company').textContent = job.company_name || '-';
  document.getElementById('monitor-domain').textContent = job.primary_domain || '-';
  document.getElementById('monitor-cust-id').textContent = job.google_customer_id || 'Pending creation...';
  document.getElementById('monitor-sub-id').textContent = job.google_subscription_id || 'Pending creation...';
  document.getElementById('monitor-plan').textContent = job.plan || '-';
  document.getElementById('monitor-seats').textContent = `${job.licensed_seats || 0} seats`;
  document.getElementById('monitor-users-stat').textContent = `Created: ${job.users_created} | Existing: ${job.users_existing} | Failed: ${job.users_failed}`;
  document.getElementById('monitor-email-stat').textContent = job.email_status || 'Pending';

  // Steps Stepper
  const stepsContainer = document.getElementById('monitor-stepper');
  stepsContainer.innerHTML = '';

  const stepOrder = [
    { name: 'VALIDATE_INPUT', label: '1. Input Validation' },
    { name: 'CREATE_CUSTOMER', label: '2. Create Google Reseller Customer' },
    { name: 'CREATE_SUBSCRIPTION', label: '3. Order Google Workspace Subscription' },
    { name: 'CREATE_USERS', label: '4. Provision Directory Users (Admin SDK)' },
    { name: 'SEND_EMAILS', label: '5. Send Provisioning Notification Emails' }
  ];

  stepOrder.forEach(stepDef => {
    let foundStep = (job.steps || []).find(s => s.step_name === stepDef.name);
    // Fallback for Admin creation flow
    if (!foundStep && stepDef.name === 'CREATE_USERS') {
      foundStep = (job.steps || []).find(s => s.step_name === 'CREATE_ADMIN_USER');
    }

    const status = foundStep ? foundStep.status : 'PENDING';
    const details = foundStep ? (foundStep.details || '') : 'Awaiting execution...';

    const item = document.createElement('div');
    item.className = `step-item ${status}`;
    item.innerHTML = `
      <div class="step-icon">${status === 'COMPLETED' ? '✓' : status === 'IN_PROGRESS' ? '↻' : status === 'FAILED' ? '✕' : '○'}</div>
      <div class="step-info">
        <div class="step-name">${stepDef.label}</div>
        <div class="step-details">${escapeHtml(details)}</div>
      </div>
      <div>
        <span class="status-pill ${status}" style="font-size:0.7rem; padding: 0.15rem 0.5rem;">${status}</span>
      </div>
    `;
    stepsContainer.appendChild(item);
  });

  // Provisioned Employees Cards
  const empGrid = document.getElementById('monitor-employees-grid');
  empGrid.innerHTML = '';

  if (job.employees && job.employees.length > 0) {
    job.employees.forEach(emp => {
      const card = document.createElement('div');
      card.className = 'emp-result-card';
      card.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:flex-start;">
          <div class="emp-result-name">${escapeHtml(emp.first_name)} ${escapeHtml(emp.last_name)}</div>
          <span class="status-pill ${emp.status}" style="font-size:0.7rem; padding: 0.15rem 0.5rem;">${emp.status}</span>
        </div>
        <div class="emp-result-email">✉ ${escapeHtml(emp.corporate_email)}</div>
        <div class="emp-result-id">Google User ID: <code>${emp.google_user_id || 'N/A'}</code></div>
        ${emp.temporary_password ? `<div class="emp-result-id" style="color:#4ade80;">🔑 Password: <code style="background:rgba(74,222,128,0.15); color:#4ade80; padding:2px 8px; border-radius:4px; font-size:0.95em;">${escapeHtml(emp.temporary_password)}</code></div>` : ''}
        ${emp.personal_email ? `<div class="emp-result-id">Recovery: ${escapeHtml(emp.personal_email)}</div>` : ''}
      `;
      empGrid.appendChild(card);
    });
  } else {
    empGrid.innerHTML = `<div style="color:var(--text-muted); font-size:0.88rem;">Users will appear here once provisioned.</div>`;
  }
}

// ==========================================================================
// 4. CSV Provisioning Logic
// ==========================================================================
let currentCsvFile = null;

document.addEventListener('DOMContentLoaded', () => {
  const csvFileInput = document.getElementById('csv-file-input');
  const csvDropzone = document.getElementById('csv-dropzone');
  const csvUploadForm = document.getElementById('csv-upload-form');

  if (csvFileInput && csvDropzone && csvUploadForm) {
    // Click to upload
    csvDropzone.addEventListener('click', () => csvFileInput.click());

    // Drag and drop
    csvDropzone.addEventListener('dragover', (e) => {
      e.preventDefault();
      csvDropzone.style.borderColor = 'var(--primary)';
      csvDropzone.style.background = 'rgba(99, 102, 241, 0.05)';
    });
    csvDropzone.addEventListener('dragleave', (e) => {
      e.preventDefault();
      csvDropzone.style.borderColor = 'var(--border-focus)';
      csvDropzone.style.background = 'var(--bg-input)';
    });
    csvDropzone.addEventListener('drop', (e) => {
      e.preventDefault();
      csvDropzone.style.borderColor = 'var(--border-focus)';
      csvDropzone.style.background = 'var(--bg-input)';
      if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        handleCsvFileSelect(e.dataTransfer.files[0]);
      }
    });

    // File input change
    csvFileInput.addEventListener('change', (e) => {
      if (e.target.files && e.target.files.length > 0) {
        handleCsvFileSelect(e.target.files[0]);
      }
    });

    // Form submit
    csvUploadForm.addEventListener('submit', handleCsvSubmit);
  }
});

function handleCsvFileSelect(file) {
  if (!file.name.endsWith('.csv')) {
    showToast('Please upload a valid .csv file', 'error');
    return;
  }
  
  currentCsvFile = file;
  document.getElementById('csv-file-name').textContent = `Selected: ${file.name} (${(file.size / 1024).toFixed(1)} KB)`;
  
  // Read and preview
  const reader = new FileReader();
  reader.onload = (e) => {
    const text = e.target.result;
    parseCsvAndPreview(text);
  };
  reader.readAsText(file);
}

function parseCsvAndPreview(csvText) {
  const lines = csvText.split(/\r?\n/).filter(line => line.trim() !== '');
  if (lines.length < 2) {
    showToast('CSV must contain a header row and at least one data row', 'error');
    return;
  }

  const headers = lines[0].split(',').map(h => h.trim().toLowerCase());
  const rows = lines.slice(1).map(line => {
    const values = line.split(',');
    const obj = {};
    headers.forEach((h, i) => {
      obj[h] = values[i] ? values[i].trim() : '';
    });
    return obj;
  });

  // Populate company meta if present in the first row
  const firstRow = rows[0];
  if (firstRow) {
    if (firstRow.company_name) document.getElementById('csv-input-company').value = firstRow.company_name;
    if (firstRow.primary_domain || firstRow.domain) document.getElementById('csv-input-domain').value = firstRow.primary_domain || firstRow.domain;
    if (firstRow.plan) document.getElementById('csv-select-plan').value = firstRow.plan;
  }

  // Populate Preview Table
  const tbody = document.getElementById('csv-preview-body');
  if (tbody) tbody.innerHTML = '';
  
  let validUsers = 0;
  rows.forEach((row, i) => {
    if (i >= 5) return; // UI limitation warning
    const fName = row.admin_first_name || row.first_name || '';
    const lName = row.admin_last_name || row.last_name || '';
    const email = row.admin_recovery_email || row.personal_email || row.email || '';
    
    if (fName || lName || email) {
      validUsers++;
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${i + 1}</td>
        <td><strong>${escapeHtml(fName)}</strong></td>
        <td><strong>${escapeHtml(lName)}</strong></td>
        <td><code style="font-size:0.85rem;">${escapeHtml(email)}</code></td>
      `;
      tbody.appendChild(tr);
    }
  });

  document.getElementById('csv-preview-card').style.display = 'block';
  const warningEl = document.getElementById('csv-limit-warning');
  if (rows.length > 5) {
    warningEl.style.display = 'block';
    warningEl.innerHTML = `⚠️ This CSV contains <strong>${rows.length}</strong> rows, but this demo endpoint only provisions up to 5 users.`;
  } else {
    warningEl.style.display = 'none';
  }
  
  // Enable the submit button now that a valid CSV is loaded
  const submitBtn = document.getElementById('btn-submit-csv');
  if (submitBtn) submitBtn.disabled = false;
}

async function handleCsvSubmit(e) {
  e.preventDefault();
  
  if (!currentCsvFile) {
    showToast('Please select a CSV file first', 'error');
    return;
  }

  const formData = new FormData();
  formData.append('file', currentCsvFile);
  
  const company = document.getElementById('csv-input-company').value;
  const domain = document.getElementById('csv-input-domain').value;
  const plan = document.getElementById('csv-select-plan').value;
  
  if (company) formData.append('company_name', company);
  if (domain) formData.append('primary_domain', domain);
  if (plan) formData.append('plan', plan);
  
  // Use currently logged in user as initiator
  if (loggedInUser && loggedInUser.email) {
    formData.append('initiated_by_email', loggedInUser.email);
  }
  
  const submitBtn = e.target.querySelector('button[type="submit"]');
  const originalText = submitBtn.innerHTML;
  submitBtn.disabled = true;
  submitBtn.innerHTML = '🔄 Uploading and Processing...';

  try {
    const res = await fetch(`${API_BASE}/provision/csv`, {
      method: 'POST',
      body: formData
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Upload failed');

    showToast(data.message || 'Provisioning job started', 'success');
    
    // Switch to monitoring tab
    document.getElementById('input-search-job').value = data.job_id;
    switchTab('tab-monitor');
    startJobPolling(data.job_id);

  } catch (err) {
    showToast(`Error: ${err.message}`, 'error');
  } finally {
    submitBtn.disabled = false;
    submitBtn.innerHTML = originalText;
  }
}

// ==========================================================================
// 5. Provisioned Companies Explorer
// ==========================================================================
async function loadCompaniesList() {
  const tableBody = document.getElementById('companies-table-body');
  if (!tableBody) return;
  tableBody.innerHTML = `<tr><td colspan="10" style="text-align:center; color:var(--text-muted);">Loading companies...</td></tr>`;

  try {
    const res = await fetch(`${API_BASE}/companies`);
    const data = await res.json();

    if (!data || data.length === 0) {
      tableBody.innerHTML = `<tr><td colspan="10" style="text-align:center; color:var(--text-muted); padding:2rem;">No companies provisioned yet. Use the Provisioning form to onboard your first workspace.</td></tr>`;
      return;
    }

    tableBody.innerHTML = '';

    // No need to fetch details individually, the /companies endpoint now returns full CompanyDetailResponse
    const details = data;

    details.forEach(comp => {
      const planLabel = comp.plan || 'N/A';
      const seatsLabel = comp.seats ? `${comp.seats} seats` : 'N/A';
      const subStatus = comp.subscription_status || 'N/A';
      const subPill = comp.subscription_status
        ? `<span class="status-pill ${comp.subscription_status.toUpperCase() === 'ACTIVE' ? 'COMPLETED' : 'PENDING'}" style="font-size:0.72rem;">${comp.subscription_status}</span>`
        : '<span style="color:var(--text-muted);font-size:0.8rem;">N/A</span>';
        
      const emailStatus = comp.email_status || 'NOT SENT';
      const emailPill = emailStatus === 'SENT' 
        ? '<span style="color:#34d399; font-size:0.85rem;">✅ Sent</span>'
        : emailStatus === 'FAILED'
          ? '<span style="color:#ef4444; font-size:0.85rem;">❌ Failed</span>'
          : `<span style="color:var(--text-muted); font-size:0.8rem;">${emailStatus}</span>`;

      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><strong>${escapeHtml(comp.company_name)}</strong></td>
        <td><code>${escapeHtml(comp.primary_domain)}</code></td>
        <td><code style="font-size:0.78rem;">${comp.google_customer_id || '<span style="color:var(--text-muted)">N/A</span>'}</code></td>
        <td><span style="font-size:0.82rem; color:#818cf8; font-weight:600;">${escapeHtml(planLabel)}</span></td>
        <td><span style="font-size:0.82rem; color:#34d399;">${escapeHtml(seatsLabel)}</span></td>
        <td>${subPill}</td>
        <td>${emailPill}</td>
        <td><span class="status-pill ${comp.status}">${comp.status}</span></td>
        <td style="font-size:0.8rem; color:var(--text-secondary);">${new Date(comp.created_at).toLocaleString()}</td>
        <td>
          <button class="btn btn-secondary" style="padding:0.35rem 0.8rem; font-size:0.78rem;" onclick="viewCompanyDirectory('${comp.company_id}', '${escapeHtml(comp.company_name)}')">👥 View Directory</button>
        </td>
      `;
      tableBody.appendChild(tr);
    });
  } catch (err) {
    tableBody.innerHTML = `<tr><td colspan="10" style="color:var(--danger); text-align:center;">Failed to load companies: ${err.message}</td></tr>`;
  }
}

async function viewCompanyDirectory(companyId, companyName) {
  try {
    const res = await fetch(`${API_BASE}/companies/${companyId}/users`);
    const users = await res.json();

    let userListHtml = '';
    if (users.length === 0) {
      userListHtml = '<p style="color:var(--text-muted)">No users found for this company.</p>';
    } else {
      userListHtml = `
        <table class="data-table" style="margin-top:1rem;">
          <thead>
            <tr>
              <th>Name</th>
              <th>Corporate Email</th>
              <th>Personal / Recovery Email</th>
              <th>Temp Password</th>
              <th>Google User ID</th>
              <th>Role</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            ${users.map(u => `
              <tr>
                <td><strong>${escapeHtml(u.first_name)} ${escapeHtml(u.last_name)}</strong></td>
                <td><code>${escapeHtml(u.corporate_email || 'N/A')}</code></td>
                <td style="font-size:0.82rem; color:var(--text-secondary);">${escapeHtml(u.personal_email || '—')}</td>
                <td>
                  ${u.temporary_password
                    ? `<code style="background:rgba(74,222,128,0.15); color:#4ade80; padding:2px 8px; border-radius:4px; font-size:0.85em;">🔑 ${escapeHtml(u.temporary_password)}</code>`
                    : '<span style="color:var(--text-muted);font-size:0.8rem;">—</span>'}
                </td>
                <td><code style="font-size:0.75rem;">${u.google_user_id || 'N/A'}</code></td>
                <td>
                  ${u.is_admin
                    ? '<span style="background:rgba(99,102,241,0.2);color:#818cf8;padding:2px 8px;border-radius:999px;font-size:0.75rem;font-weight:600;">👑 Admin</span>'
                    : '<span style="background:rgba(148,163,184,0.1);color:#94a3b8;padding:2px 8px;border-radius:999px;font-size:0.75rem;">Employee</span>'}
                </td>
                <td><span class="status-pill ${u.status}">${u.status}</span></td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;
    }

    const modalBody = document.getElementById('directory-modal-content');
    document.getElementById('directory-modal-title').textContent = `📁 Directory: ${companyName}`;
    modalBody.innerHTML = userListHtml;

    document.getElementById('directory-modal').style.display = 'flex';

  } catch (err) {
    showToast(`Failed to load directory: ${err.message}`, 'error');
  }
}

function closeDirectoryModal() {
  document.getElementById('directory-modal').style.display = 'none';
}

// Utility: Escape HTML
function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
