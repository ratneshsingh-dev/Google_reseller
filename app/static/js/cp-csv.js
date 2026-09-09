/**
 * cp-csv.js — Channel Partner Portal: CSV Bulk Upload Tab
 *
 * Contains:
 *  - downloadTemplate()  — download a sample CSV file
 *  - handleDrop()        — drag-and-drop file handler
 *  - handleFileSelect()  — file input change handler
 *  - processFile()       — validate file type and start reading
 *  - parseCSV()          — parse CSV text, validate columns
 *  - renderPreview()     — show first 5 rows in a preview table
 *  - clearFile()         — reset the upload area
 *  - submitCSV()         — submit each row as a provisioning job
 *  - pollJob()           — poll a single job until complete/failed
 *  - jobCard()           — render a job status card HTML string
 *
 * API endpoint used:
 *  POST /api/v1/reseller/provision  → one call per CSV row
 *  GET  /api/v1/reseller/provision/{job_id}  → poll status
 *
 * To debug CSV issues:
 *  - Check the preview table renders correctly after upload
 *  - Check the network tab for POST /api/v1/reseller/provision calls
 *  - Common error: "Missing columns" → check CSV header spelling exactly
 */

'use strict';


// =============================================================================
// REQUIRED CSV COLUMNS
// =============================================================================

const REQUIRED_COLS = [
  'company_name', 'primary_domain', 'admin_first_name', 'admin_last_name',
  'alternate_email', 'contact_name', 'admin_recovery_email',
  'address_line1', 'locality', 'region', 'postal_code', 'country_code',
  'plan', 'sku_id', 'license_count',
];

// Holds parsed rows from the uploaded CSV
let _csvRows = [];


// =============================================================================
// TEMPLATE DOWNLOAD
// =============================================================================

function downloadTemplate() {
  const header  = REQUIRED_COLS.join(',');
  const example = [
    'NovaTech Solutions Pvt Ltd', 'novatechsolutions.com', 'Arjun', 'Mehta',
    'arjun.mehta@example.com', 'Arjun Mehta', 'arjun.alt@example.com',
    '78 Innovation Park Sector 21', 'Gurugram', 'HR', '122016', 'IN',
    'TRIAL', '1030040059', '5',
  ].join(',');

  const blob = new Blob([header + '\n' + example], { type: 'text/csv' });
  const a    = document.createElement('a');
  a.href     = URL.createObjectURL(blob);
  a.download = 'provision_template.csv';
  a.click();
}


// =============================================================================
// FILE HANDLING
// =============================================================================

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

  // Validate all required columns are present
  const missing = REQUIRED_COLS.filter(c => !headers.includes(c));
  if (missing.length) {
    showError('upload-error', `Missing required columns: ${missing.join(', ')}`);
    return;
  }

  // Parse data rows
  _csvRows = lines.slice(1).map(line => {
    const vals = line.split(',').map(v => v.trim());
    const row  = {};
    headers.forEach((h, i) => row[h] = vals[i] || '');
    return row;
  });

  // Update UI
  document.getElementById('upload-error').style.display    = 'none';
  document.getElementById('upload-filename').textContent   = `✓ ${filename} (${_csvRows.length} rows)`;
  document.getElementById('upload-filename').style.display = 'block';

  renderPreview(_csvRows.slice(0, 5), headers);
  document.getElementById('btn-provision-csv').style.display = 'block';
}

function renderPreview(rows, headers) {
  const previewEl = document.getElementById('csv-preview');
  const tableEl   = document.getElementById('preview-table');
  document.getElementById('preview-count').textContent =
    `Preview — first ${rows.length} of ${_csvRows.length} rows`;

  // Show only key columns in preview
  const cols = ['company_name', 'primary_domain', 'admin_first_name', 'plan', 'license_count'];
  tableEl.innerHTML = `
    <thead><tr>${cols.map(c => `<th>${c}</th>`).join('')}</tr></thead>
    <tbody>${rows.map(r => `<tr>${cols.map(c => `<td>${esc(r[c] || '')}</td>`).join('')}</tr>`).join('')}</tbody>
  `;
  previewEl.style.display = 'block';
}

function clearFile() {
  _csvRows = [];
  document.getElementById('upload-filename').style.display    = 'none';
  document.getElementById('csv-preview').style.display        = 'none';
  document.getElementById('btn-provision-csv').style.display  = 'none';
  document.getElementById('upload-error').style.display       = 'none';
  document.getElementById('csv-file-input').value             = '';
}


// =============================================================================
// CSV SUBMISSION
// =============================================================================

async function submitCSV() {
  if (!_csvRows.length) return;

  const btn    = document.getElementById('btn-provision-csv');
  const jobsEl = document.getElementById('csv-jobs');

  btn.disabled    = true;
  btn.textContent = '⏳ Provisioning...';
  jobsEl.innerHTML = '';

  let success = 0, failed = 0;

  // Submit one job per CSV row (sequentially to avoid rate-limiting)
  for (const row of _csvRows) {
    try {
      const payload = {
        company_name:         row.company_name,
        primary_domain:       row.primary_domain,
        admin_first_name:     row.admin_first_name,
        admin_last_name:      row.admin_last_name,
        alternate_email:      row.alternate_email,
        contact_name:         row.contact_name,
        admin_recovery_email: row.admin_recovery_email,
        postal_address: {
          address_line1: row.address_line1,
          locality:      row.locality,
          region:        row.region,
          postal_code:   row.postal_code,
          country_code:  row.country_code,
        },
        plan:                     row.plan,
        sku_id:                   row.sku_id,
        license_count:            parseInt(row.license_count) || 1,
        initiated_by_email:       _partnerEmail,
        econz_notification_email: _partnerEmail,
      };

      const resp = await apiFetch('/reseller/provision', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      const data = await resp.json();

      if (resp.ok) {
        success++;
        jobsEl.innerHTML += jobCard(row.company_name, data.job_id, 'PENDING');
        // Poll status after 3 seconds
        setTimeout(() => pollJob(data.job_id, row.company_name), 3000);
      } else {
        failed++;
        const detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
        jobsEl.innerHTML += jobCard(row.company_name, '—', 'FAILED', detail);
      }
    } catch (err) {
      failed++;
      jobsEl.innerHTML += jobCard(row.company_name, '—', 'FAILED', err.message);
    }
  }

  showToast(`Submitted: ${success} succeeded, ${failed} failed`, success > 0 ? 'ok' : 'error');
  btn.textContent = '✅ All Submitted';
  setTimeout(() => {
    btn.disabled = false;
    btn.textContent = '🚀 Provision All Companies';
  }, 4000);

  // Refresh quota after bulk submission
  loadQuota();
}


// =============================================================================
// JOB POLLING & RENDERING
// =============================================================================

/** Poll a single job once, updating its card in the UI. */
async function pollJob(jobId, companyName) {
  try {
    const resp = await apiFetch(`/reseller/provision/${jobId}`);
    const data = await resp.json();
    const card = document.getElementById(`job-${jobId}`);

    if (card) {
      const badge = card.querySelector('.job-status-badge');
      const body  = card.querySelector('.job-body');

      badge.textContent = data.status;
      badge.className   = `job-status-badge job-${data.status}`;

      if (data.status === 'COMPLETED') {
        body.textContent = `✅ ${companyName} provisioned | Customer: ${data.google_customer_id || '—'}`;
      } else if (data.status === 'FAILED') {
        body.textContent = `❌ Failed: ${data.error_message || 'Unknown error'}`;
      }
    }
  } catch (_) {
    // Silently ignore poll errors — the card just won't update
  }
}

/** Return an HTML string for a job status card. */
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
