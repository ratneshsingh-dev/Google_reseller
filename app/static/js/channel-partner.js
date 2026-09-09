/**
 * Channel Partner Portal — JavaScript
 * Handles: login, company listing, CSV upload, API guide, quota display
 */

'use strict';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let _token = null;
let _resellerId = null;
let _partnerEmail = null;
let _partnerData = null;  // quota response
let _csvRows = [];

const BASE = '/api/v1';
const PORTAL_KEY = 'cp_session';

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
window.addEventListener('DOMContentLoaded', async () => {
  // Load Google OAuth client_id from backend and set it on the GIS div
  try {
    const r = await fetch('/api/v1/config/oauth-client-id');
    if (r.ok) {
      const { client_id } = await r.json();
      if (client_id) {
        document.getElementById('g_id_onload')?.setAttribute('data-client_id', client_id);
      }
    }
  } catch (_) {}

  const saved = sessionStorage.getItem(PORTAL_KEY);
  if (saved) {
    try {
      const { token, resellerId, email } = JSON.parse(saved);
      _token = token; _resellerId = resellerId; _partnerEmail = email;
      showApp();
      return;
    } catch (_) {}
  }
  // Show login screen
  document.getElementById('login-email')?.focus();
  document.addEventListener('keydown', e => {
    if (e.key === 'Enter' && document.getElementById('login-screen').style.display !== 'none') {
      doLogin();
    }
  });
});

// ---------------------------------------------------------------------------
// Google Sign-In Callback
// ---------------------------------------------------------------------------
async function handleGoogleLogin(response) {
  const errEl = document.getElementById('login-error');
  errEl.style.display = 'none';

  try {
    const resp = await fetch(`${BASE}/reseller/auth/google-login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id_token: response.credential }),
    });
    const data = await resp.json();

    if (!resp.ok) {
      errEl.textContent = data.detail || 'Google login failed. Your email may not be registered.';
      errEl.style.display = 'block';
      return;
    }

    _token = data.access_token;
    _resellerId = data.reseller_id;
    // Get email from JWT payload (middle part, base64 decoded)
    try {
      const payload = JSON.parse(atob(data.access_token.split('.')[1]));
      _partnerEmail = payload.email || '';
    } catch (_) { _partnerEmail = ''; }

    sessionStorage.setItem(PORTAL_KEY, JSON.stringify({
      token: _token, resellerId: _resellerId, email: _partnerEmail
    }));
    showApp();
  } catch (err) {
    errEl.textContent = 'Connection error. Is the server running?';
    errEl.style.display = 'block';
  }
}

// ---------------------------------------------------------------------------
// Login
// ---------------------------------------------------------------------------
async function doLogin() {
  const email = document.getElementById('login-email').value.trim();
  const secret = document.getElementById('login-secret').value.trim();
  const errEl = document.getElementById('login-error');
  const btnText = document.getElementById('login-btn-text');
  const spinner = document.getElementById('login-spinner');

  errEl.style.display = 'none';

  if (!email || !secret) {
    errEl.textContent = 'Please enter your email and client secret.';
    errEl.style.display = 'block';
    return;
  }

  btnText.style.display = 'none';
  spinner.style.display = 'block';
  document.getElementById('btn-login').disabled = true;

  try {
    const resp = await fetch(`${BASE}/reseller/auth/email-login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, client_secret: secret }),
    });
    const data = await resp.json();

    if (!resp.ok) {
      errEl.textContent = data.detail || 'Login failed. Check your credentials.';
      errEl.style.display = 'block';
      return;
    }

    _token = data.access_token;
    _resellerId = data.reseller_id;
    _partnerEmail = email;

    sessionStorage.setItem(PORTAL_KEY, JSON.stringify({
      token: _token, resellerId: _resellerId, email: _partnerEmail
    }));

    showApp();
  } catch (err) {
    errEl.textContent = 'Connection error. Is the server running?';
    errEl.style.display = 'block';
  } finally {
    btnText.style.display = 'block';
    spinner.style.display = 'none';
    document.getElementById('btn-login').disabled = false;
  }
}

function doLogout() {
  sessionStorage.removeItem(PORTAL_KEY);
  _token = null; _resellerId = null; _partnerEmail = null;
  document.getElementById('app-screen').style.display = 'none';
  document.getElementById('login-screen').style.display = 'flex';
  document.getElementById('login-email').value = '';
  document.getElementById('login-secret').value = '';
}

// ---------------------------------------------------------------------------
// Show App After Login
// ---------------------------------------------------------------------------
async function showApp() {
  document.getElementById('login-screen').style.display = 'none';
  document.getElementById('app-screen').style.display = 'block';
  document.getElementById('nav-email').textContent = _partnerEmail || '—';

  // Load quota first (gives us access_methods from the server)
  await loadQuota();

  // Show tabs based on access_methods returned by quota endpoint
  const methods = (_partnerData?.access_methods || ['API']);
  document.getElementById('tab-manual').style.display = methods.includes('MANUAL') ? 'block' : 'none';
  document.getElementById('tab-csv').style.display = methods.includes('CSV') ? 'block' : 'none';
  document.getElementById('tab-api').style.display = methods.includes('API') ? 'block' : 'none';

  // Load companies
  loadCompanies();
  populateApiGuide();
}

// ---------------------------------------------------------------------------
// API Helpers
// ---------------------------------------------------------------------------
async function apiFetch(path, opts = {}) {
  const resp = await fetch(`${BASE}${path}`, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${_token}`,
      ...(opts.headers || {}),
    },
  });
  if (resp.status === 401) { doLogout(); throw new Error('Session expired'); }
  return resp;
}

// ---------------------------------------------------------------------------
// Tab Switching
// ---------------------------------------------------------------------------
function switchTab(name) {
  document.querySelectorAll('.cp-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.style.display = 'none');

  document.getElementById(`tab-${name}`).classList.add('active');
  document.getElementById(`tab-content-${name}`).style.display = 'block';

  if (name === 'companies') loadCompanies();
  if (name === 'quota')     loadQuota(true);
}

// ---------------------------------------------------------------------------
// Companies Tab
// ---------------------------------------------------------------------------
async function loadCompanies() {
  const el = document.getElementById('companies-list');
  el.innerHTML = '<div class="loading-state"><div class="spinner"></div><p>Loading...</p></div>';

  try {
    const resp = await apiFetch('/reseller/companies');
    const data = await resp.json();

    if (!resp.ok || !Array.isArray(data) || data.length === 0) {
      el.innerHTML = `
        <div class="empty-state" style="grid-column:1/-1;">
          <div class="empty-icon">🏢</div>
          <p>No companies provisioned yet.</p>
          <p style="margin-top:0.4rem; font-size:0.78rem;">Use the CSV Upload or API tab to get started.</p>
        </div>`;
      return;
    }

    el.innerHTML = data.map(c => `
      <div class="company-card">
        <div class="company-card-header">
          <div class="company-avatar">${(c.company_name || '?')[0].toUpperCase()}</div>
          <div>
            <div class="company-name">${esc(c.company_name || '—')}</div>
            <div class="company-domain">${esc(c.primary_domain || c.domain || '—')}</div>
          </div>
        </div>
        <div class="company-meta">
          <span class="meta-badge ${c.status === 'ACTIVE' ? 'badge-active' : ''}">
            ${esc(c.status || 'ACTIVE')}
          </span>
          <span class="meta-badge ${c.plan === 'TRIAL' ? 'badge-trial' : 'badge-flexible'}">
            ${esc(c.plan || 'TRIAL')}
          </span>
        </div>
        <div class="company-seats">🪑 ${c.licensed_seats || c.license_count || '—'} licences</div>
      </div>
    `).join('');
  } catch (err) {
    el.innerHTML = `<div class="empty-state" style="grid-column:1/-1;color:#fca5a5;">Failed to load companies: ${esc(err.message)}</div>`;
  }
}

// ---------------------------------------------------------------------------
// Quota Tab
// ---------------------------------------------------------------------------
async function loadQuota(showInTab = false) {
  try {
    const resp = await apiFetch('/reseller/quota');
    const q = await resp.json();
    _partnerData = q;

    // Update navbar pill
    document.getElementById('nav-used').textContent = q.licences_used ?? '—';
    document.getElementById('nav-cap').textContent  = q.max_licence_cap ?? '—';
    document.getElementById('brand-company').textContent = q.company_name || 'Channel Partner';

    if (!showInTab) return;

    const pct = q.utilization_percent ?? 0;
    const remaining = q.licences_remaining ?? 0;
    const isWarn = pct >= 80;
    document.getElementById('quota-detail').innerHTML = `
      <div class="quota-row">
        <span class="quota-row-label">Company</span>
        <span class="quota-row-val">${esc(q.company_name || '—')}</span>
      </div>
      <div class="quota-row">
        <span class="quota-row-label">Licences Used</span>
        <span class="quota-row-val" style="color:var(--success)">${q.licences_used ?? 0}</span>
      </div>
      <div class="quota-row">
        <span class="quota-row-label">Maximum Cap</span>
        <span class="quota-row-val">${q.max_licence_cap ?? 0}</span>
      </div>
      <div class="quota-bar-bg">
        <div class="quota-bar-fill ${isWarn ? 'warn' : ''}" style="width:${pct}%"></div>
      </div>
      <div class="quota-pct">${pct}% used &nbsp;·&nbsp; ${remaining} remaining</div>`;
  } catch (_) {}
}

// ---------------------------------------------------------------------------
// CSV Upload Tab
// ---------------------------------------------------------------------------
const REQUIRED_COLS = [
  'company_name','primary_domain','admin_first_name','admin_last_name',
  'alternate_email','contact_name','admin_recovery_email',
  'address_line1','locality','region','postal_code','country_code',
  'plan','sku_id','license_count'
];

function downloadTemplate() {
  const header = REQUIRED_COLS.join(',');
  const example = [
    'NovaTech Solutions Pvt Ltd','novatechsolutions.com','Arjun','Mehta',
    'arjun.mehta@example.com','Arjun Mehta','arjun.alt@example.com',
    '78 Innovation Park Sector 21','Gurugram','HR','122016','IN',
    'TRIAL','1030040059','5'
  ].join(',');
  const blob = new Blob([header + '\n' + example], { type: 'text/csv' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'provision_template.csv';
  a.click();
}

function handleDrop(event) {
  event.preventDefault();
  document.getElementById('upload-area').classList.remove('drag-over');
  const file = event.dataTransfer.files[0];
  if (file) processFile(file);
}

function handleFileSelect(event) {
  const file = event.target.files[0];
  if (file) processFile(file);
}

function processFile(file) {
  if (!file.name.endsWith('.csv')) {
    showError('upload-error', 'Please upload a .csv file.');
    return;
  }
  const reader = new FileReader();
  reader.onload = e => parseCSV(e.target.result, file.name);
  reader.readAsText(file);
}

function parseCSV(text, filename) {
  const lines = text.trim().split('\n').map(l => l.trim()).filter(Boolean);
  if (lines.length < 2) {
    showError('upload-error', 'CSV must have a header row and at least one data row.');
    return;
  }

  const headers = lines[0].split(',').map(h => h.trim().toLowerCase().replace(/\s+/g, '_'));

  // Check required columns
  const missing = REQUIRED_COLS.filter(c => !headers.includes(c));
  if (missing.length) {
    showError('upload-error', `Missing columns: ${missing.join(', ')}`);
    return;
  }

  _csvRows = lines.slice(1).map(line => {
    const vals = line.split(',').map(v => v.trim());
    const row = {};
    headers.forEach((h, i) => row[h] = vals[i] || '');
    return row;
  });

  document.getElementById('upload-error').style.display = 'none';
  document.getElementById('upload-filename').textContent = `✓ ${filename} (${_csvRows.length} rows)`;
  document.getElementById('upload-filename').style.display = 'block';

  renderPreview(_csvRows.slice(0, 5), headers);
  document.getElementById('btn-provision-csv').style.display = 'block';
}

function renderPreview(rows, headers) {
  const previewEl = document.getElementById('csv-preview');
  const tableEl = document.getElementById('preview-table');
  document.getElementById('preview-count').textContent = `Preview — first ${rows.length} of ${_csvRows.length} rows`;

  const cols = ['company_name','primary_domain','admin_first_name','plan','license_count'];
  tableEl.innerHTML = `
    <thead><tr>${cols.map(c => `<th>${c}</th>`).join('')}</tr></thead>
    <tbody>${rows.map(r => `<tr>${cols.map(c => `<td>${esc(r[c] || '')}</td>`).join('')}</tr>`).join('')}</tbody>
  `;
  previewEl.style.display = 'block';
}

function clearFile() {
  _csvRows = [];
  document.getElementById('upload-filename').style.display = 'none';
  document.getElementById('csv-preview').style.display = 'none';
  document.getElementById('btn-provision-csv').style.display = 'none';
  document.getElementById('upload-error').style.display = 'none';
  document.getElementById('csv-file-input').value = '';
}

async function submitCSV() {
  if (!_csvRows.length) return;
  const btn = document.getElementById('btn-provision-csv');
  btn.disabled = true; btn.textContent = '⏳ Provisioning...';

  const jobsEl = document.getElementById('csv-jobs');
  jobsEl.innerHTML = '';

  let success = 0, failed = 0;

  for (const row of _csvRows) {
    try {
      const payload = {
        company_name: row.company_name,
        primary_domain: row.primary_domain,
        admin_first_name: row.admin_first_name,
        admin_last_name: row.admin_last_name,
        alternate_email: row.alternate_email,
        contact_name: row.contact_name,
        admin_recovery_email: row.admin_recovery_email,
        postal_address: {
          address_line1: row.address_line1,
          locality: row.locality,
          region: row.region,
          postal_code: row.postal_code,
          country_code: row.country_code,
        },
        plan: row.plan,
        sku_id: row.sku_id,
        license_count: parseInt(row.license_count) || 1,
        initiated_by_email: _partnerEmail,
        econz_notification_email: _partnerEmail,
      };

      const resp = await apiFetch('/reseller/provision', {
        method: 'POST', body: JSON.stringify(payload),
      });
      const data = await resp.json();

      if (resp.ok) {
        success++;
        jobsEl.innerHTML += jobCard(row.company_name, data.job_id, 'PENDING');
        // Poll status
        setTimeout(() => pollJob(data.job_id, row.company_name), 3000);
      } else {
        failed++;
        jobsEl.innerHTML += jobCard(row.company_name, '—', 'FAILED', data.detail);
      }
    } catch (err) {
      failed++;
      jobsEl.innerHTML += jobCard(row.company_name, '—', 'FAILED', err.message);
    }
  }

  showToast(`Submitted: ${success} succeeded, ${failed} failed`, success > 0 ? 'ok' : 'error');
  btn.textContent = '✅ Submitted';
  setTimeout(() => { btn.disabled = false; btn.textContent = '🚀 Provision All Companies'; }, 4000);
}

async function pollJob(jobId, companyName) {
  try {
    const resp = await apiFetch(`/reseller/provision/${jobId}`);
    const data = await resp.json();
    const card = document.getElementById(`job-${jobId}`);
    if (card) {
      card.querySelector('.job-status-badge').textContent = data.status;
      card.querySelector('.job-status-badge').className = `job-status-badge job-${data.status}`;
      if (data.status === 'COMPLETED') {
        card.querySelector('.job-body').textContent =
          `✅ ${companyName} provisioned | Customer: ${data.google_customer_id}`;
      }
    }
  } catch (_) {}
}

function jobCard(company, jobId, status, detail = '') {
  const safeId = jobId.replace(/[^a-z0-9-]/gi, '');
  return `
    <div class="job-card" id="job-${safeId}">
      <div class="job-header">
        <span class="job-id">${esc(company)} · ${esc(jobId)}</span>
        <span class="job-status-badge job-${status}">${status}</span>
      </div>
      <div class="job-body">${esc(detail) || 'Processing...'}</div>
    </div>`;
}

// ---------------------------------------------------------------------------
// API Guide Tab
// ---------------------------------------------------------------------------
function populateApiGuide() {
  const origin = window.location.origin;
  document.getElementById('api-client-id').value  = _resellerId || '—';
  document.getElementById('api-email').value       = _partnerEmail || '—';
  document.getElementById('api-token-url').value   = `${origin}/api/v1/reseller/auth/token`;

  document.getElementById('code-get-token').textContent =
`import requests

# Get access token (valid 24 hours)
resp = requests.post("${origin}/api/v1/reseller/auth/token", json={
    "client_id": "${_resellerId || 'RSL-XXXXXXXX'}",
    "client_secret": "sec_your_secret_here"
})
token = resp.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}`;

  document.getElementById('code-provision').textContent =
`# Provision a company
resp = requests.post("${origin}/api/v1/reseller/provision",
    headers=headers, json={
        "company_name": "Client Corp",
        "primary_domain": "clientcorp.com",
        "admin_first_name": "John",
        "admin_last_name": "Doe",
        "alternate_email": "john@example.com",
        "contact_name": "John Doe",
        "admin_recovery_email": "john.alt@example.com",
        "postal_address": {"address_line1": "...", "locality": "Mumbai",
                           "region": "MH", "postal_code": "400001", "country_code": "IN"},
        "plan": "TRIAL",
        "sku_id": "1030040059",
        "license_count": 5,
        "initiated_by_email": "${_partnerEmail || 'you@company.com'}",
        "econz_notification_email": "${_partnerEmail || 'you@company.com'}"
    })
job = resp.json()
print("Job ID:", job["job_id"])`;

  document.getElementById('code-job-status').textContent =
`# Poll job status
resp = requests.get(
    "${origin}/api/v1/reseller/provision/{job_id}",
    headers=headers
)
print(resp.json())
# status: PENDING → COMPLETED / FAILED`;
}

// ---------------------------------------------------------------------------
// Manual Provision Tab
// ---------------------------------------------------------------------------
async function submitManualProvision() {
  const btn = document.getElementById('btn-manual-provision');
  const errBox = document.getElementById('manual-error-box');
  const resultBox = document.getElementById('manual-job-result');
  errBox.style.display = 'none';
  resultBox.style.display = 'none';

  const adminFirst = (document.getElementById('mf-admin-first').value || '').trim();
  const adminLast  = (document.getElementById('mf-admin-last').value || '').trim();

  if (!adminFirst || !adminLast) {
    errBox.textContent = 'Admin first name and last name are required.';
    errBox.style.display = 'block';
    return;
  }

  const companyName = (document.getElementById('mf-company-name').value || '').trim();
  const domain      = (document.getElementById('mf-primary-domain').value || '').trim().toLowerCase();

  if (!companyName || !domain) {
    errBox.textContent = 'Company name and primary domain are required.';
    errBox.style.display = 'block';
    return;
  }

  const payload = {
    company_name: companyName,
    primary_domain: domain,
    alternate_email: (document.getElementById('mf-alternate-email').value || '').trim().toLowerCase(),
    contact_name: (document.getElementById('mf-contact-name').value || '').trim(),
    postal_address: {
      address_line1: (document.getElementById('mf-address').value || '').trim(),
      locality: (document.getElementById('mf-locality').value || '').trim(),
      region: 'KA',
      postal_code: (document.getElementById('mf-postal').value || '').trim(),
      country_code: (document.getElementById('mf-country').value || '').trim().toUpperCase(),
    },
    plan: document.getElementById('mf-plan').value,
    sku_id: document.getElementById('mf-sku').value,
    license_count: parseInt(document.getElementById('mf-license-count').value, 10) || 5,
    initiated_by_email: _partnerEmail,
    econz_notification_email: _partnerEmail,
    admin_first_name: adminFirst,
    admin_last_name: adminLast,
    admin_recovery_email: (document.getElementById('mf-admin-recovery').value || '').trim() || null,
  };

  btn.disabled = true;
  btn.textContent = '⏳ Creating domain & admin account...';

  try {
    const resp = await apiFetch('/reseller/provision', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    const data = await resp.json();

    if (!resp.ok) {
      const detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
      throw new Error(detail || 'Provisioning request failed');
    }

    const adminEmail = `${adminFirst.toLowerCase()}.${adminLast.toLowerCase()}@${domain}`;
    showToast(`Job ${data.job_id} created! Admin: ${adminEmail}`, 'ok');

    // Show result card and poll for status
    resultBox.innerHTML = `
      <div class="manual-result-card success">
        <div class="manual-result-header">
          <span class="manual-result-title">✅ Job Submitted: ${esc(data.job_id)}</span>
          <span class="job-status-badge job-PENDING" id="mf-job-status">PENDING</span>
        </div>
        <div class="manual-result-body" id="mf-job-body">
          Provisioning <strong>${esc(companyName)}</strong> (${esc(domain)})...<br>
          Admin: <strong>${esc(adminEmail)}</strong>
        </div>
      </div>`;
    resultBox.style.display = 'block';

    // Poll job status
    pollManualJob(data.job_id, companyName);

    // Refresh quota in navbar
    loadQuota();

  } catch (err) {
    errBox.textContent = `Error: ${err.message}`;
    errBox.style.display = 'block';
    showToast(err.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '🚀 Provision Domain & Admin Account';
  }
}

async function pollManualJob(jobId, companyName) {
  const maxAttempts = 30;
  for (let i = 0; i < maxAttempts; i++) {
    await new Promise(r => setTimeout(r, 5000)); // wait 5s
    try {
      const resp = await apiFetch(`/reseller/provision/${jobId}`);
      const data = await resp.json();
      const statusEl = document.getElementById('mf-job-status');
      const bodyEl = document.getElementById('mf-job-body');
      if (statusEl) {
        statusEl.textContent = data.status;
        statusEl.className = `job-status-badge job-${data.status}`;
      }
      if (data.status === 'COMPLETED') {
        if (bodyEl) {
          bodyEl.innerHTML = `
            ✅ <strong>${esc(companyName)}</strong> provisioned successfully!<br>
            Customer ID: <strong>${esc(data.google_customer_id || '—')}</strong><br>
            Subscription ID: <strong>${esc(data.google_subscription_id || '—')}</strong><br>
            Email Status: <strong>${esc(data.email_status || '—')}</strong>`;
        }
        // Refresh companies list
        loadCompanies();
        loadQuota();
        return;
      }
      if (data.status === 'FAILED') {
        if (bodyEl) bodyEl.innerHTML = `❌ Provisioning failed: ${esc(data.error_message || 'Unknown error')}`;
        return;
      }
    } catch (_) {}
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function showError(elId, msg) {
  const el = document.getElementById(elId);
  el.textContent = msg; el.style.display = 'block';
}

function copyField(id) {
  const el = document.getElementById(id);
  navigator.clipboard.writeText(el.value).then(() => showToast('Copied!', 'ok'));
}

function esc(str) {
  return String(str)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

let _toastTimer;
function showToast(msg, type = 'ok') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `cp-toast show ${type}`;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove('show'), 3500);
}
