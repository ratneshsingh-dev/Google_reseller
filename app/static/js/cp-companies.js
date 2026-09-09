/**
 * cp-companies.js — Channel Partner Portal: My Companies Tab
 *
 * Contains:
 *  - loadCompanies() — fetch and render all companies for this partner
 *
 * API endpoint used:
 *  GET /api/v1/reseller/companies  → returns list of provisioned companies
 *
 * To debug:
 *  - Check network tab for GET /api/v1/reseller/companies
 *  - Common issue: company shows nothing → reseller_id may not be set on
 *    the company record. Run the fix script in scratch/patch_email.py.
 *  - Response is filtered by server to only include THIS partner's companies.
 */

'use strict';


// =============================================================================
// LOAD COMPANIES
// =============================================================================

async function loadCompanies() {
  const el = document.getElementById('companies-list');
  if (!el) return;

  el.innerHTML = '<div class="loading-state"><div class="spinner"></div><p>Loading companies...</p></div>';

  try {
    const resp = await apiFetch('/reseller/companies');
    const data = await resp.json();

    // Show empty state if no companies yet
    if (!resp.ok || !Array.isArray(data) || data.length === 0) {
      el.innerHTML = `
        <div class="empty-state" style="grid-column:1/-1;">
          <div class="empty-icon">🏢</div>
          <p>No companies provisioned yet.</p>
          <p style="margin-top:0.4rem; font-size:0.78rem;">
            Use the Manual, CSV, or API tab to get started.
          </p>
        </div>`;
      return;
    }

    // Render a card for each company
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
            ${esc(c.plan || '—')}
          </span>
        </div>
        <div class="company-seats">🪑 ${c.licensed_seats || c.license_count || '—'} licences</div>
        <div class="company-customer-id" style="font-size:0.72rem;color:var(--text-3);margin-top:0.3rem;font-family:monospace;">
          ${c.google_customer_id ? `Customer: ${esc(c.google_customer_id)}` : ''}
        </div>
      </div>
    `).join('');

  } catch (err) {
    el.innerHTML = `
      <div class="empty-state" style="grid-column:1/-1;color:#fca5a5;">
        ❌ Failed to load companies: ${esc(err.message)}
      </div>`;
  }
}
