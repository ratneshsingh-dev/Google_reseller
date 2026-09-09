/* ==========================================================================
   Reseller Admin Dashboard — JavaScript
   Handles auth, API calls, UI state management
   ========================================================================== */

'use strict';

// ---------------------------------------------------------------------------
// Constants & State
// ---------------------------------------------------------------------------

const API_BASE = '/api/v1/admin';
let allResellers = [];
let selectedReseller = null;
let userEmail = null;

const ROLE_PERMISSIONS = {
  RESELLER_FULL: [
    { label: 'Provision company (create licence)',   allowed: true  },
    { label: 'Bulk CSV provisioning',               allowed: true  },
    { label: 'View own job status',                 allowed: true  },
    { label: 'View own quota',                      allowed: true  },
    { label: 'View own provisioned companies',      allowed: true  },
    { label: 'View other resellers\' data',         allowed: false },
    { label: 'Access admin panel',                  allowed: false },
    { label: 'Create / manage resellers',           allowed: false },
  ],
  RESELLER_READONLY: [
    { label: 'Provision company (create licence)',   allowed: false },
    { label: 'Bulk CSV provisioning',               allowed: false },
    { label: 'View own job status',                 allowed: true  },
    { label: 'View own quota',                      allowed: true  },
    { label: 'View own provisioned companies',      allowed: true  },
    { label: 'View other resellers\' data',         allowed: false },
    { label: 'Access admin panel',                  allowed: false },
    { label: 'Create / manage resellers',           allowed: false },
  ],
};

// ---------------------------------------------------------------------------
// Google Auth
// ---------------------------------------------------------------------------

async function initGoogleSignIn() {
  if (!window.google) { setTimeout(initGoogleSignIn, 300); return; }
  
  // Fetch client_id from backend config
  let clientId = window.__GOOGLE_CLIENT_ID__ || '';
  try {
    const r = await fetch('/api/v1/config/oauth-client-id');
    if (r.ok) {
      const data = await r.json();
      if (data.client_id) clientId = data.client_id;
    }
  } catch (_) {}

  google.accounts.id.initialize({
    client_id: clientId,
    callback: handleCredentialResponse,
    auto_select: true,
  });
  google.accounts.id.renderButton(
    document.getElementById('google-signin-btn'),
    { theme: 'filled_black', size: 'large', shape: 'pill', width: 280 }
  );
  // Check existing session
  checkExistingSession();
}

async function checkExistingSession() {
  try {
    const res = await fetch('/api/v1/auth/me', { credentials: 'include' });
    if (res.ok) {
      const data = await res.json();
      onLoginSuccess(data);
    }
  } catch (_) {}
}

async function devLogin() {
  try {
    const res = await fetch('/api/v1/auth/dev-login', {
      method: 'POST',
      credentials: 'include'
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Dev login failed');
    onLoginSuccess(data);
  } catch (err) {
    showToast('Dev login failed: ' + err.message, 'error');
  }
}


async function handleCredentialResponse(response) {
  try {
    const res = await fetch('/api/v1/auth/google', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ credential: response.credential }),
    });
    const data = await res.json();
    if (res.status === 403) {
      // Non-admin tried to log in — show clear error on login card
      const loginCard = document.querySelector('.login-card');
      let errDiv = document.getElementById('admin-login-error');
      if (!errDiv) {
        errDiv = document.createElement('div');
        errDiv.id = 'admin-login-error';
        errDiv.style.cssText = 'margin:1rem 0;padding:1rem;background:rgba(239,68,68,0.15);border:1px solid #ef4444;border-radius:8px;color:#fca5a5;font-size:0.9rem;text-align:center;';
        loginCard.appendChild(errDiv);
      }
      errDiv.innerHTML = `🚫 <strong>Access Denied</strong><br>${data.detail || 'Only authorized admins can access this panel.'}`;
      return;
    }
    if (!res.ok) throw new Error(data.detail || 'Auth failed');
    onLoginSuccess(data);
  } catch (err) {
    showToast('Login failed: ' + err.message, 'error');
  }
}

function onLoginSuccess(userData) {
  userEmail = userData.email || '';
  document.getElementById('login-overlay').style.display = 'none';
  document.getElementById('app').style.display = 'block';
  document.getElementById('user-display-name').textContent = userData.name || userData.given_name || userEmail;
  const avatar = document.getElementById('user-avatar');
  if (userData.picture) {
    avatar.src = userData.picture;
    avatar.style.display = 'block';
  }
  loadDashboard();
}

function handleLogout() {
  fetch('/api/v1/auth/logout', { method: 'POST', credentials: 'include' }).finally(() => {
    document.cookie = 'session_user=; Max-Age=0';
    window.location.reload();
  });
}

// ---------------------------------------------------------------------------
// API Helpers
// ---------------------------------------------------------------------------

async function apiGet(path) {
  const res = await fetch(API_BASE + path, { credentials: 'include' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

async function apiPost(path, body) {
  const res = await fetch(API_BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

async function apiPatch(path, body) {
  const res = await fetch(API_BASE + path, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

async function apiDelete(path) {
  const res = await fetch(API_BASE + path, { method: 'DELETE', credentials: 'include' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

// ---------------------------------------------------------------------------
// Load Dashboard
// ---------------------------------------------------------------------------

async function loadDashboard() {
  try {
    const summary = await apiGet('/dashboard/summary');
    updateStats(summary);
    allResellers = summary.resellers || [];
    renderResellerList(allResellers);
  } catch (err) {
    showToast('Failed to load dashboard: ' + err.message, 'error');
    document.getElementById('resellers-loading').style.display = 'none';
    document.getElementById('resellers-empty').style.display = 'flex';
  }
}

function updateStats(summary) {
  document.getElementById('stat-total-val').textContent = summary.total_resellers ?? 0;
  document.getElementById('stat-active-val').textContent = summary.active_resellers ?? 0;
  document.getElementById('stat-suspended-val').textContent = summary.suspended_resellers ?? 0;
  document.getElementById('stat-licences-val').textContent = (summary.total_licences_provisioned ?? 0).toLocaleString();
  document.getElementById('stat-cap-val').textContent = (summary.total_licence_cap ?? 0).toLocaleString();
  document.getElementById('stat-util-val').textContent = (summary.overall_utilization_percent ?? 0).toFixed(1) + '%';
}

// ---------------------------------------------------------------------------
// Render Reseller List
// ---------------------------------------------------------------------------

function renderResellerList(resellers) {
  const container = document.getElementById('reseller-list');
  const loading = document.getElementById('resellers-loading');
  const empty = document.getElementById('resellers-empty');

  loading.style.display = 'none';

  // Remove old cards (keep loading/empty elements)
  [...container.querySelectorAll('.reseller-card')].forEach(el => el.remove());

  if (!resellers.length) {
    empty.style.display = 'flex';
    return;
  }
  empty.style.display = 'none';

  resellers.forEach(r => {
    const card = document.createElement('div');
    card.className = 'reseller-card' + (selectedReseller?.reseller_id === r.reseller_id ? ' selected' : '');
    card.id = 'card-' + r.reseller_id;
    card.onclick = () => selectReseller(r);

    const pct = r.max_licence_cap > 0 ? (r.licences_used / r.max_licence_cap) * 100 : 0;
    const warnClass = pct >= 80 ? 'warn' : '';
    const roleClass = r.role === 'RESELLER_FULL' ? 'full' : 'readonly';
    const statusClass = r.status.toLowerCase();

    card.innerHTML = `
      <div class="reseller-card-header">
        <div class="reseller-avatar">${r.company_name[0].toUpperCase()}</div>
        <div>
          <div class="reseller-card-name">${escHtml(r.company_name)}</div>
          <div class="reseller-card-email">${escHtml(r.contact_email)}</div>
        </div>
      </div>
      <div class="reseller-card-badges">
        <span class="role-badge ${roleClass}">${r.role}</span>
        <span class="status-badge ${statusClass}">${r.status}</span>
      </div>
      <div class="reseller-card-quota">
        <div class="mini-quota-text">${r.licences_used.toLocaleString()} / ${r.max_licence_cap.toLocaleString()} licences used</div>
        <div class="mini-quota-bar"><div class="mini-quota-fill ${warnClass}" style="width:${Math.min(pct,100).toFixed(1)}%"></div></div>
      </div>
    `;
    container.appendChild(card);
  });
}

function filterResellers() {
  const q = document.getElementById('reseller-search').value.toLowerCase();
  const statusFilter = document.getElementById('status-filter').value;
  const filtered = allResellers.filter(r => {
    const matchQ = r.company_name.toLowerCase().includes(q) || r.contact_email.toLowerCase().includes(q);
    const matchStatus = !statusFilter || r.status === statusFilter;
    return matchQ && matchStatus;
  });
  renderResellerList(filtered);
}

// ---------------------------------------------------------------------------
// Select Reseller → Detail Panel
// ---------------------------------------------------------------------------

function selectReseller(r) {
  selectedReseller = r;

  // Update selected card highlight
  document.querySelectorAll('.reseller-card').forEach(c => c.classList.remove('selected'));
  const card = document.getElementById('card-' + r.reseller_id);
  if (card) card.classList.add('selected');

  // Show detail content
  document.getElementById('detail-empty').style.display = 'none';
  document.getElementById('detail-content').style.display = 'block';

  // Fill header
  document.getElementById('detail-avatar').textContent = r.company_name[0].toUpperCase();
  document.getElementById('detail-company').textContent = r.company_name;
  document.getElementById('detail-email').textContent = r.contact_email;

  const roleClass = r.role === 'RESELLER_FULL' ? 'full' : 'readonly';
  const statusClass = r.status.toLowerCase();
  document.getElementById('detail-role-badge').textContent = r.role;
  document.getElementById('detail-role-badge').className = 'role-badge ' + roleClass;
  document.getElementById('detail-status-badge').textContent = r.status;
  document.getElementById('detail-status-badge').className = 'status-badge ' + statusClass;

  // Suspend button label
  const btnSuspend = document.getElementById('btn-suspend');
  btnSuspend.textContent = r.status === 'SUSPENDED' ? '▶ Activate' : '⏸ Suspend';
  btnSuspend.className = r.status === 'SUSPENDED' ? 'btn btn-sm btn-primary' : 'btn btn-sm btn-warning';

  // Quota bar
  const pct = r.max_licence_cap > 0 ? (r.licences_used / r.max_licence_cap) * 100 : 0;
  document.getElementById('detail-quota-numbers').textContent = `${r.licences_used.toLocaleString()} / ${r.max_licence_cap.toLocaleString()}`;
  const bar = document.getElementById('detail-quota-bar');
  bar.style.width = Math.min(pct, 100).toFixed(1) + '%';
  bar.className = 'quota-bar-fill' + (pct >= 80 ? ' warn' : '');
  document.getElementById('detail-remaining').textContent = `${r.licences_remaining.toLocaleString()} remaining`;
  document.getElementById('detail-percent').textContent = pct.toFixed(1) + '%';

  // Info grid
  document.getElementById('detail-id').textContent = r.reseller_id;
  document.getElementById('detail-contact').textContent = r.contact_name || '—';
  document.getElementById('detail-role').textContent = r.role;
  document.getElementById('detail-created-by').textContent = r.created_by || '—';
  document.getElementById('detail-created-at').textContent = r.created_at ? fmtDate(r.created_at) : '—';
  document.getElementById('detail-last-call').textContent = r.last_api_call_at ? fmtDate(r.last_api_call_at) : 'Never';

  // Permissions
  renderPermissions(r.role);

  // Hide audit log
  document.getElementById('audit-log-section').style.display = 'none';
}

function renderPermissions(role) {
  const perms = ROLE_PERMISSIONS[role] || ROLE_PERMISSIONS.RESELLER_READONLY;
  const grid = document.getElementById('detail-perms-grid');
  grid.innerHTML = perms.map(p => `
    <div class="perm-item ${p.allowed ? 'allowed' : 'denied'}">
      <span class="perm-icon">${p.allowed ? '✅' : '❌'}</span>
      <span>${p.label}</span>
    </div>
  `).join('');
}

// ---------------------------------------------------------------------------
// Create Channel Partner Modal
// ---------------------------------------------------------------------------

function openCreateModal() {
  document.getElementById('cr-company').value = '';
  document.getElementById('cr-contact').value = '';
  document.getElementById('cr-email').value = '';
  document.getElementById('cr-cap').value = '100';
  // Reset access method chips — default API selected
  document.querySelectorAll('.method-chip').forEach(c => {
    c.classList.toggle('selected', c.dataset.method === 'API');
  });
  openModal('create-modal');
}

function toggleMethod(btn) {
  btn.classList.toggle('selected');
  // Ensure at least one is always selected
  const selected = document.querySelectorAll('.method-chip.selected');
  if (selected.length === 0) btn.classList.add('selected');
}

function getSelectedMethods() {
  return Array.from(document.querySelectorAll('.method-chip.selected'))
    .map(c => c.dataset.method);
}

async function createReseller() {
  const company = document.getElementById('cr-company').value.trim();
  const contact = document.getElementById('cr-contact').value.trim();
  const email = document.getElementById('cr-email').value.trim();
  const cap = parseInt(document.getElementById('cr-cap').value);
  const accessMethods = getSelectedMethods();

  if (!company || !contact || !email || !cap) {
    showToast('Please fill in all required fields', 'error'); return;
  }
  if (!isValidEmail(email)) {
    showToast('Please enter a valid email address', 'error'); return;
  }
  if (accessMethods.length === 0) {
    showToast('Please select at least one access method', 'error'); return;
  }

  try {
    const data = await apiPost('/resellers', {
      company_name: company,
      contact_name: contact,
      contact_email: email,
      role: 'RESELLER_FULL',
      max_licence_cap: cap,
      access_methods: accessMethods,
    });
    closeModal('create-modal');
    // Only show credentials if not MANUAL-only
    if (accessMethods.length === 1 && accessMethods[0] === 'MANUAL') {
      showToast(`Channel Partner "${company}" created (Manual access — no portal login needed).`, 'success');
    } else {
      showCredentials(data);
    }
    await loadDashboard();
    showToast(`Channel Partner "${company}" created successfully!`, 'success');
  } catch (err) {
    showToast('Failed to create partner: ' + err.message, 'error');
  }
}

function showCredentials(data) {
  const origin = window.location.origin;
  document.getElementById('creds-client-id').value = data.client_id;
  document.getElementById('creds-client-secret').value = data.client_secret;
  document.getElementById('creds-token-url').value = `${origin}/api/v1/reseller/auth/email-login`;

  const methods = (data.access_methods || ['API']).join(', ');
  document.getElementById('creds-code-sample').textContent =
`# Access Methods: ${methods}
# Portal Login (for CSV / web portal access):
# URL: ${origin}/static/channel-partner.html
# Email: ${data.contact_email}
# Client Secret: ${data.client_secret}

# API Access (direct integration):
import requests
response = requests.post("${origin}/api/v1/reseller/auth/token", json={
    "client_id": "${data.client_id}",
    "client_secret": "${data.client_secret}"
})
token = response.json()["access_token"]

# Provision a company
requests.post("${origin}/api/v1/reseller/provision",
    headers={"Authorization": f"Bearer {token}"},
    json={"company_name": "Client Corp", "license_count": 50, ...}
)`;

  openModal('creds-modal');
}

// ---------------------------------------------------------------------------
// Licence Cap
// ---------------------------------------------------------------------------

function openIncreaseLicenceModal() {
  if (!selectedReseller) return;
  document.getElementById('lm-current').value = selectedReseller.max_licence_cap.toLocaleString();
  document.getElementById('lm-new-cap').value = selectedReseller.max_licence_cap;
  openModal('licence-modal');
}

async function updateLicenceCap() {
  const newCap = parseInt(document.getElementById('lm-new-cap').value);
  if (!newCap || newCap < 1) {
    showToast('Please enter a valid licence cap', 'error'); return;
  }
  try {
    await apiPatch(`/resellers/${selectedReseller.reseller_id}`, { max_licence_cap: newCap });
    closeModal('licence-modal');
    showToast(`Licence cap updated to ${newCap.toLocaleString()}`, 'success');
    await refreshSelectedReseller();
  } catch (err) {
    showToast('Failed to update cap: ' + err.message, 'error');
  }
}

// ---------------------------------------------------------------------------
// Change Role
// ---------------------------------------------------------------------------

function openChangeRoleModal() {
  if (!selectedReseller) return;
  document.getElementById('rm-role').value = selectedReseller.role;
  updateChangeRolePreview();
  openModal('role-modal');
}

function updateChangeRolePreview() {
  const role = document.getElementById('rm-role').value;
  const perms = ROLE_PERMISSIONS[role] || [];
  document.getElementById('role-change-perms').innerHTML = perms.map(p => `
    <div class="role-perm-row ${p.allowed ? 'yes' : 'no'}">
      ${p.allowed ? '✓' : '✗'} ${p.label}
    </div>
  `).join('');
}

async function changeRole() {
  const newRole = document.getElementById('rm-role').value;
  try {
    await apiPatch(`/resellers/${selectedReseller.reseller_id}`, { role: newRole });
    closeModal('role-modal');
    showToast(`Role changed to ${newRole}`, 'success');
    await refreshSelectedReseller();
  } catch (err) {
    showToast('Failed to change role: ' + err.message, 'error');
  }
}

// ---------------------------------------------------------------------------
// Edit (inline update)
// ---------------------------------------------------------------------------

function editReseller() {
  if (!selectedReseller) return;
  document.getElementById('em-company').value = selectedReseller.company_name;
  document.getElementById('em-cap').value = selectedReseller.max_licence_cap;
  document.getElementById('em-role').value = selectedReseller.role;
  document.getElementById('em-status').value = selectedReseller.status;
  openModal('edit-modal');
}

async function saveEdit() {
  const cap = parseInt(document.getElementById('em-cap').value);
  const role = document.getElementById('em-role').value;
  const status = document.getElementById('em-status').value;

  try {
    await apiPatch(`/resellers/${selectedReseller.reseller_id}`, {
      max_licence_cap: cap, role, status
    });
    closeModal('edit-modal');
    showToast('Reseller updated successfully', 'success');
    await refreshSelectedReseller();
  } catch (err) {
    showToast('Failed to update: ' + err.message, 'error');
  }
}

// ---------------------------------------------------------------------------
// Suspend / Activate Toggle
// ---------------------------------------------------------------------------

async function toggleSuspend() {
  if (!selectedReseller) return;
  const isSuspended = selectedReseller.status === 'SUSPENDED';
  const newStatus = isSuspended ? 'ACTIVE' : 'SUSPENDED';
  const msg = isSuspended
    ? `Activate ${selectedReseller.company_name}?`
    : `Suspend ${selectedReseller.company_name}? Their API tokens will be blocked immediately.`;

  if (!confirm(msg)) return;

  try {
    await apiPatch(`/resellers/${selectedReseller.reseller_id}`, { status: newStatus });
    showToast(`Reseller ${isSuspended ? 'activated' : 'suspended'}`, isSuspended ? 'success' : 'warning');
    await refreshSelectedReseller();
  } catch (err) {
    showToast('Failed: ' + err.message, 'error');
  }
}

// ---------------------------------------------------------------------------
// Deactivate
// ---------------------------------------------------------------------------

async function confirmDeactivate() {
  if (!selectedReseller) return;
  if (!confirm(`Permanently deactivate "${selectedReseller.company_name}"? This cannot be undone from the UI.`)) return;
  try {
    await apiDelete(`/resellers/${selectedReseller.reseller_id}`);
    showToast('Reseller deactivated', 'warning');
    selectedReseller = null;
    document.getElementById('detail-empty').style.display = 'flex';
    document.getElementById('detail-content').style.display = 'none';
    await loadDashboard();
  } catch (err) {
    showToast('Failed to deactivate: ' + err.message, 'error');
  }
}

// ---------------------------------------------------------------------------
// Rotate Secret
// ---------------------------------------------------------------------------

async function rotateSecret() {
  if (!selectedReseller) return;
  if (!confirm(`Rotate credentials for "${selectedReseller.company_name}"?\n\nAll their active JWT tokens will be immediately invalidated and they must re-authenticate with the new secret.`)) return;

  try {
    const data = await apiPost(`/resellers/${selectedReseller.reseller_id}/regenerate-secret`, {});
    // Show new credentials
    document.getElementById('creds-client-id').value = data.reseller_id;
    document.getElementById('creds-client-secret').value = data.client_secret;
    document.getElementById('creds-token-url').value = `${window.location.origin}/api/v1/reseller/auth/token`;
    document.getElementById('creds-code-sample').textContent =
`# New credentials (old tokens are now invalid)
# Share these with the reseller securely

client_id     = "${data.reseller_id}"
client_secret = "${data.client_secret}"`;
    openModal('creds-modal');
    showToast('Secret rotated. Old tokens are now invalid.', 'warning');
  } catch (err) {
    showToast('Failed to rotate secret: ' + err.message, 'error');
  }
}

// ---------------------------------------------------------------------------
// Audit Log
// ---------------------------------------------------------------------------

async function viewAuditLog() {
  if (!selectedReseller) return;
  const section = document.getElementById('audit-log-section');
  section.style.display = 'block';
  section.scrollIntoView({ behavior: 'smooth' });
  document.getElementById('audit-log-container').innerHTML = '<div class="loading-state"><div class="spinner"></div><p>Loading audit log...</p></div>';

  try {
    const logs = await apiGet(`/resellers/${selectedReseller.reseller_id}/audit-log`);
    renderAuditLog(logs);
  } catch (err) {
    document.getElementById('audit-log-container').innerHTML = `<p style="color:var(--danger);padding:1rem;">Failed to load: ${err.message}</p>`;
  }
}

function renderAuditLog(logs) {
  if (!logs.length) {
    document.getElementById('audit-log-container').innerHTML = '<p style="color:var(--text-muted);padding:1rem;font-size:0.82rem;">No audit entries yet.</p>';
    return;
  }
  const rows = logs.map(l => {
    const statusClass = l.status === 'SUCCESS' ? 'success' : l.status === 'FAILED' ? 'failed' : 'denied';
    return `<tr>
      <td>${fmtDate(l.timestamp)}</td>
      <td><strong>${escHtml(l.action)}</strong></td>
      <td>${escHtml(l.resource_type || '—')}</td>
      <td>${l.licences_requested || 0}</td>
      <td><span class="audit-status ${statusClass}">${l.status}</span></td>
      <td style="color:var(--text-muted)">${escHtml(l.ip_address || '—')}</td>
    </tr>`;
  }).join('');

  document.getElementById('audit-log-container').innerHTML = `
    <table class="audit-table">
      <thead>
        <tr>
          <th>Time</th><th>Action</th><th>Resource</th><th>Licences</th><th>Status</th><th>IP</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

// ---------------------------------------------------------------------------
// Refresh selected reseller
// ---------------------------------------------------------------------------

async function refreshSelectedReseller() {
  await loadDashboard();
  if (selectedReseller) {
    const updated = allResellers.find(r => r.reseller_id === selectedReseller.reseller_id);
    if (updated) selectReseller(updated);
  }
}

// ---------------------------------------------------------------------------
// Modal helpers
// ---------------------------------------------------------------------------

function openModal(id) {
  document.getElementById(id).style.display = 'flex';
  requestAnimationFrame(() => document.getElementById(id).style.opacity = '1');
}

function closeModal(id) {
  document.getElementById(id).style.display = 'none';
}

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------

let toastTimer = null;

function showToast(msg, type = 'success') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `toast ${type} show`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = 'toast'; }, 3500);
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function copyField(id) {
  const el = document.getElementById(id);
  navigator.clipboard.writeText(el.value).then(() => showToast('Copied to clipboard!', 'success'));
}

function isValidEmail(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

function escHtml(str) {
  const d = document.createElement('div');
  d.textContent = str || '';
  return d.innerHTML;
}

function fmtDate(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })
      + ' ' + d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
  } catch (_) { return iso; }
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

// Inject Google client ID from meta tag if available
window.addEventListener('DOMContentLoaded', () => {
  // Try to read GOOGLE_CLIENT_ID from main app's config endpoint
  fetch('/api/v1/auth/config').then(r => r.json()).then(cfg => {
    window.__GOOGLE_CLIENT_ID__ = cfg.google_client_id || cfg.client_id || '';
  }).catch(() => {}).finally(() => {
    initGoogleSignIn();
  });

  // Init role previews
  updateRolePreview();
  updateChangeRolePreview();
});
