/**
 * cp-core.js — Channel Partner Portal: Core State & Shared Utilities
 *
 * Contains:
 *  - Shared state (_token, _resellerId, etc.)
 *  - apiFetch() — authenticated HTTP helper
 *  - switchTab() — tab switching logic
 *  - showApp() — called after login to boot the portal
 *  - DOMContentLoaded init
 *  - esc() / showToast() / showError() — shared utilities
 *
 * To debug this module: open cp-core.js
 */

'use strict';

// =============================================================================
// SHARED STATE
// =============================================================================
// All other modules READ from these. Only cp-auth.js writes to them on login.

let _token       = null;  // JWT Bearer token
let _resellerId  = null;  // e.g. "RSL-202320B4"
let _partnerEmail = null; // e.g. "jithin.m@econz.net"
let _partnerData  = null; // Latest quota response from server

const BASE       = '/api/v1';
const PORTAL_KEY = 'cp_session'; // sessionStorage key


// =============================================================================
// INIT — runs on page load
// =============================================================================

window.addEventListener('DOMContentLoaded', async () => {
  // Load Google OAuth client_id from backend and inject into GIS div
  try {
    const r = await fetch('/api/v1/config/oauth-client-id');
    if (r.ok) {
      const { client_id } = await r.json();
      
      const initGoogle = () => {
        if (!window.google) { setTimeout(initGoogle, 300); return; }
        if (client_id) {
          google.accounts.id.initialize({
            client_id: client_id,
            callback: handleGoogleLogin,
            auto_select: true
          });
          google.accounts.id.renderButton(
            document.getElementById('google-signin-btn'),
            { theme: 'filled_black', size: 'large', shape: 'pill', width: 360 }
          );
        }
      };
      initGoogle();
    }
  } catch (_) {}

  // Restore session from sessionStorage (e.g. page refresh)
  const saved = sessionStorage.getItem(PORTAL_KEY);
  if (saved) {
    try {
      const { token, resellerId, email } = JSON.parse(saved);
      _token = token;
      _resellerId = resellerId;
      _partnerEmail = email;
      showApp();
      return;
    } catch (_) {}
  }

  // Show login screen — focus email field, allow Enter to submit
  document.getElementById('login-email')?.focus();
  document.addEventListener('keydown', e => {
    const loginScreen = document.getElementById('login-screen');
    if (e.key === 'Enter' && loginScreen?.style.display !== 'none') {
      doLogin();
    }
  });
});


// =============================================================================
// SHOW APP — called after successful login
// =============================================================================

async function showApp() {
  document.getElementById('login-screen').style.display = 'none';
  document.getElementById('app-screen').style.display  = 'block';
  document.getElementById('nav-email').textContent = _partnerEmail || '—';

  // Load quota first — response contains access_methods
  await loadQuota();

  // Show only the tabs this partner has permission to use
  const methods = (_partnerData?.access_methods || ['API']);
  document.getElementById('tab-manual').style.display = methods.includes('MANUAL') ? 'block' : 'none';
  document.getElementById('tab-csv').style.display    = methods.includes('CSV')    ? 'block' : 'none';
  document.getElementById('tab-api').style.display    = methods.includes('API')    ? 'block' : 'none';

  // Load initial data for both visible tabs
  loadCompanies();
  populateApiGuide();
  initManualForm(); // auto-fill partner email in the manual form
}


// =============================================================================
// API HELPER
// =============================================================================

/**
 * apiFetch — wrapper around fetch() that:
 *  - Adds the Authorization: Bearer token header
 *  - Automatically logs out on 401 (token expired/revoked)
 *
 * Usage: const resp = await apiFetch('/reseller/quota');
 *
 * To debug API calls: add console.log here or in each feature file.
 */
async function apiFetch(path, opts = {}) {
  const resp = await fetch(`${BASE}${path}`, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${_token}`,
      ...(opts.headers || {}),
    },
  });

  // Token expired or revoked → force logout
  if (resp.status === 401) {
    doLogout();
    throw new Error('Session expired. Please log in again.');
  }

  return resp;
}


// =============================================================================
// TAB SWITCHING
// =============================================================================

/**
 * switchTab — shows the correct tab content and triggers its data load.
 *
 * Tab names map to:
 *   'companies' → cp-companies.js → loadCompanies()
 *   'manual'    → cp-manual.js    → (form, no auto-load needed)
 *   'csv'       → cp-csv.js       → (drag-drop, no auto-load needed)
 *   'api'       → cp-api-guide.js → populateApiGuide()
 *   'quota'     → cp-quota.js     → loadQuota(true)
 */
function switchTab(name) {
  // Deactivate all tabs
  document.querySelectorAll('.cp-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.style.display = 'none');

  // Activate selected tab
  document.getElementById(`tab-${name}`).classList.add('active');
  document.getElementById(`tab-content-${name}`).style.display = 'block';

  // Trigger data load for tabs that need fresh data
  if (name === 'companies') loadCompanies();
  if (name === 'quota')     loadQuota(true);
}


// =============================================================================
// SHARED UTILITIES
// =============================================================================

/** Escape HTML to prevent XSS in rendered content. */
function esc(str) {
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/** Show an error message inside a named element. */
function showError(elId, msg) {
  const el = document.getElementById(elId);
  if (el) { el.textContent = msg; el.style.display = 'block'; }
}

/** Copy a text input's value to the clipboard. */
function copyField(id) {
  const el = document.getElementById(id);
  if (el) navigator.clipboard.writeText(el.value).then(() => showToast('Copied!', 'ok'));
}

/** Show a brief toast notification at the bottom of the screen. */
let _toastTimer;
function showToast(msg, type = 'ok') {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = msg;
  el.className = `cp-toast show ${type}`;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove('show'), 3500);
}
