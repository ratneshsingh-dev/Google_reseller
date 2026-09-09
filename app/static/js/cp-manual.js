/**
 * cp-manual.js — Channel Partner Portal: Manual Provisioning Tab
 *
 * Contains:
 *  - initManualForm()        — auto-fill partner email on form load
 *  - fillManualSampleData()  — populate form with sample data for testing
 *  - resetManualForm()       — clear all form fields
 *  - submitManualProvision() — collect form data and POST to /reseller/provision
 *  - pollManualJob()         — poll job status until COMPLETED or FAILED
 *
 * API endpoints used:
 *  POST /api/v1/reseller/provision             → create job
 *  GET  /api/v1/reseller/provision/{job_id}    → poll status
 *
 * To debug manual form issues:
 *  - Check the network tab for POST /api/v1/reseller/provision
 *  - The error box (id="manual-error-box") shows validation errors
 *  - Common errors:
 *      403 → licence cap exceeded
 *      422 → missing required field (check payload in network tab)
 *      409 → domain already provisioned
 */

'use strict';


// =============================================================================
// FORM INIT — auto-fill partner email when the form is first shown
// =============================================================================

/**
 * Called from cp-core.js showApp() after login.
 * Auto-fills the read-only notification email from the logged-in session.
 */
function initManualForm() {
  const emailEl = document.getElementById('mf-init-email');
  if (emailEl && _partnerEmail) {
    emailEl.value = _partnerEmail;
  }
}


// =============================================================================
// FILL SAMPLE DATA
// =============================================================================

function fillManualSampleData() {
  const today = new Date();
  const stamp = `${today.getFullYear()}${String(today.getMonth()+1).padStart(2,'0')}${String(today.getDate()).padStart(2,'0')}`;

  const set = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };

  set('mf-company-name',    'NovaTech Solutions Pvt Ltd');
  set('mf-primary-domain',  `novatech-demo-${stamp}.com`);
  set('mf-alternate-email', 'founder@novatech-alt.com');
  set('mf-contact-name',    'Arjun Mehta');
  set('mf-address',         '78 Innovation Park, Sector 21');
  set('mf-locality',        'Gurugram');
  set('mf-postal',          '122016');
  set('mf-country',         'IN');
  set('mf-plan',            'TRIAL');
  set('mf-sku',             '1010020028'); // Business Standard
  set('mf-license-count',   '5');
  set('mf-admin-first',     'Arjun');
  set('mf-admin-last',      'Admin');
  set('mf-admin-recovery',  'arjun.alt@gmail.com');
  // init email stays as partner's login email (read-only)
}


// =============================================================================
// RESET FORM
// =============================================================================

function resetManualForm() {
  const form = document.getElementById('manual-provision-form');
  if (form) form.reset();

  // Re-fill the read-only notification email after reset
  const emailEl = document.getElementById('mf-init-email');
  if (emailEl && _partnerEmail) emailEl.value = _partnerEmail;

  // Hide result box and errors
  const errBox    = document.getElementById('manual-error-box');
  const resultBox = document.getElementById('manual-job-result');
  if (errBox)    errBox.style.display    = 'none';
  if (resultBox) resultBox.style.display = 'none';
}


// =============================================================================
// FORM SUBMISSION
// =============================================================================

async function submitManualProvision() {
  const btn       = document.getElementById('btn-manual-provision');
  const errBox    = document.getElementById('manual-error-box');
  const resultBox = document.getElementById('manual-job-result');

  // Reset UI
  errBox.style.display    = 'none';
  resultBox.style.display = 'none';

  // --- Validate required fields ---
  const adminFirst  = (document.getElementById('mf-admin-first')?.value  || '').trim();
  const adminLast   = (document.getElementById('mf-admin-last')?.value   || '').trim();
  const companyName = (document.getElementById('mf-company-name')?.value || '').trim();
  const domain      = (document.getElementById('mf-primary-domain')?.value || '').trim().toLowerCase();

  if (!companyName || !domain) {
    errBox.textContent   = 'Company name and primary domain are required.';
    errBox.style.display = 'block';
    return;
  }
  if (!adminFirst || !adminLast) {
    errBox.textContent   = 'Admin first name and last name are required.';
    errBox.style.display = 'block';
    return;
  }

  // --- Build request payload ---
  const payload = {
    company_name:    companyName,
    primary_domain:  domain,
    alternate_email: (document.getElementById('mf-alternate-email')?.value || '').trim().toLowerCase(),
    contact_name:    (document.getElementById('mf-contact-name')?.value    || '').trim(),
    postal_address: {
      address_line1: (document.getElementById('mf-address')?.value  || '').trim(),
      locality:      (document.getElementById('mf-locality')?.value || '').trim(),
      region:        'KA',  // Default region; extend form if needed
      postal_code:   (document.getElementById('mf-postal')?.value   || '').trim(),
      country_code:  (document.getElementById('mf-country')?.value  || '').trim().toUpperCase(),
    },
    plan:             document.getElementById('mf-plan')?.value          || 'TRIAL',
    sku_id:           document.getElementById('mf-sku')?.value           || '1010020028',
    license_count:    parseInt(document.getElementById('mf-license-count')?.value, 10) || 5,
    // Both notification fields use the logged-in partner's email
    initiated_by_email:       (document.getElementById('mf-init-email')?.value || '').trim() || _partnerEmail,
    econz_notification_email: _partnerEmail,
    admin_first_name:    adminFirst,
    admin_last_name:     adminLast,
    admin_recovery_email: (document.getElementById('mf-admin-recovery')?.value || '').trim() || null,
  };

  // --- Submit ---
  // Silently generate a unique idempotency key per submit to prevent double-submissions
  // No need to expose this in the UI — the UUID handles it automatically
  const idempotencyKey = `cp-manual-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

  btn.disabled    = true;
  btn.textContent = '⏳ Creating domain & admin account...';

  try {
    const resp = await apiFetch('/reseller/provision', {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();

    if (!resp.ok) {
      const detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
      throw new Error(detail || 'Provisioning request failed');
    }

    // --- Show live job status card ---
    const adminEmail = `${adminFirst.toLowerCase()}.${adminLast.toLowerCase()}@${domain}`;
    showToast(`Job ${data.job_id} created! Admin: ${adminEmail}`, 'ok');

    resultBox.innerHTML = `
      <div class="manual-result-card success">
        <div class="manual-result-header">
          <span class="manual-result-title">✅ Job Submitted: ${esc(data.job_id)}</span>
          <span class="job-status-badge job-PENDING" id="mf-job-status">PENDING</span>
        </div>
        <div class="manual-result-body" id="mf-job-body">
          Provisioning <strong>${esc(companyName)}</strong> (${esc(domain)})...<br>
          Admin account: <strong>${esc(adminEmail)}</strong>
        </div>
      </div>`;
    resultBox.style.display = 'block';

    // Start polling for completion
    pollManualJob(data.job_id, companyName);

  } catch (err) {
    errBox.textContent   = `Error: ${err.message}`;
    errBox.style.display = 'block';
    showToast(err.message, 'error');
  } finally {
    btn.disabled    = false;
    btn.textContent = '🚀 Provision Domain & Admin Account';
  }
}


// =============================================================================
// JOB POLLING
// =============================================================================

/**
 * Poll job status every 5 seconds until COMPLETED, FAILED, or max attempts reached.
 * Updates the live status card shown above the form.
 */
async function pollManualJob(jobId, companyName) {
  const MAX_ATTEMPTS    = 30;  // 30 × 5s = 2.5 minutes max wait
  const POLL_INTERVAL_MS = 5000;

  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
    await new Promise(r => setTimeout(r, POLL_INTERVAL_MS));

    try {
      const resp = await apiFetch(`/reseller/provision/${jobId}`);
      const data = await resp.json();

      const statusEl = document.getElementById('mf-job-status');
      const bodyEl   = document.getElementById('mf-job-body');

      if (statusEl) {
        statusEl.textContent = data.status;
        statusEl.className   = `job-status-badge job-${data.status}`;
      }

      if (data.status === 'COMPLETED') {
        if (bodyEl) {
          bodyEl.innerHTML = `
            ✅ <strong>${esc(companyName)}</strong> provisioned successfully!<br>
            Customer ID:     <strong>${esc(data.google_customer_id      || '—')}</strong><br>
            Subscription ID: <strong>${esc(data.google_subscription_id  || '—')}</strong><br>
            Email Status:    <strong>${esc(data.email_status            || '—')}</strong>`;
        }
        // Refresh companies list and quota pill now that a new company exists
        loadCompanies();
        loadQuota();
        return;
      }

      if (data.status === 'FAILED') {
        if (bodyEl) {
          bodyEl.innerHTML = `❌ Provisioning failed: ${esc(data.error_message || 'Unknown error')}`;
        }
        return;
      }

    } catch (_) {
      // Ignore poll errors silently — the status card just won't update this tick
    }
  }
}
