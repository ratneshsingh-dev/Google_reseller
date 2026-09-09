/**
 * cp-quota.js — Channel Partner Portal: Licence Quota Tab
 *
 * Contains:
 *  - loadQuota(showInTab) — fetch quota from server and update UI
 *
 * API endpoint used:
 *  GET /api/v1/reseller/quota  → returns licences_used, max_licence_cap, etc.
 *
 * This module is also called by cp-core.js on login (showApp) to populate
 * the navbar quota pill and get the access_methods list.
 *
 * To debug:
 *  - Check network tab for GET /api/v1/reseller/quota
 *  - If quota shows wrong numbers, check the reseller document in Firestore.
 */

'use strict';


// =============================================================================
// LOAD QUOTA
// =============================================================================

/**
 * @param {boolean} showInTab - If true, render the full quota breakdown in the
 *   Quota tab. If false (default), only update the navbar pill.
 */
async function loadQuota(showInTab = false) {
  try {
    const resp = await apiFetch('/reseller/quota');
    const q    = await resp.json();

    // Store latest quota data in shared state (used by showApp for access_methods)
    _partnerData = q;

    // Always update the navbar quota pill
    document.getElementById('nav-used').textContent  = q.licences_used    ?? '—';
    document.getElementById('nav-cap').textContent   = q.max_licence_cap  ?? '—';
    document.getElementById('brand-company').textContent = q.company_name || 'Channel Partner';

    // Only render the full tab breakdown when the Quota tab is open
    if (!showInTab) return;

    const pct       = q.utilization_percent ?? 0;
    const remaining = q.licences_remaining  ?? 0;
    const isWarn    = pct >= 80; // Turn progress bar red when >= 80% used

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
      <div class="quota-row">
        <span class="quota-row-label">Licences Remaining</span>
        <span class="quota-row-val" style="color:${isWarn ? 'var(--warning)' : 'var(--text-1)'}">
          ${remaining}
        </span>
      </div>
      <div class="quota-bar-bg">
        <div class="quota-bar-fill ${isWarn ? 'warn' : ''}" style="width:${pct}%"></div>
      </div>
      <div class="quota-pct">${pct.toFixed(1)}% used &nbsp;·&nbsp; ${remaining} remaining</div>`;

  } catch (err) {
    // Silently fail on navbar pill updates; log for tab view
    if (showInTab) {
      const el = document.getElementById('quota-detail');
      if (el) el.innerHTML = `<div style="color:#fca5a5;">❌ Failed to load quota: ${esc(err.message)}</div>`;
    }
  }
}
