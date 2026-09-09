/**
 * cp-api-guide.js — Channel Partner Portal: API Integration Guide Tab
 *
 * Contains:
 *  - populateApiGuide() — inject credentials and curl/python examples
 *
 * This tab shows the partner their credentials and ready-to-copy code
 * snippets for integrating the provisioning API into their own system.
 *
 * To debug API guide issues:
 *  - If credentials show "—", the login session may not have saved
 *    _resellerId or _partnerEmail correctly (check cp-auth.js).
 *  - If code samples show wrong URLs, check window.location.origin.
 */

'use strict';


// =============================================================================
// POPULATE API GUIDE
// =============================================================================

function populateApiGuide() {
  const origin = window.location.origin;

  // Inject credentials into the read-only fields
  const clientIdEl  = document.getElementById('api-client-id');
  const emailEl     = document.getElementById('api-email');
  const tokenUrlEl  = document.getElementById('api-token-url');

  if (clientIdEl)  clientIdEl.value  = _resellerId   || '—';
  if (emailEl)     emailEl.value     = _partnerEmail  || '—';
  if (tokenUrlEl)  tokenUrlEl.value  = `${origin}/api/v1/reseller/auth/token`;

  // ----- Step 1: Get Access Token -----
  const getTokenEl = document.getElementById('code-get-token');
  if (getTokenEl) {
    getTokenEl.textContent =
`import requests

# Step 1: Get access token (valid 24 hours)
resp = requests.post("${origin}/api/v1/reseller/auth/token", json={
    "client_id":     "${_resellerId || 'RSL-XXXXXXXX'}",
    "client_secret": "sec_your_secret_here"       # provided by your admin
})
token = resp.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}`;
  }

  // ----- Step 2: Provision a Company -----
  const provisionEl = document.getElementById('code-provision');
  if (provisionEl) {
    provisionEl.textContent =
`# Step 2: Provision a company workspace
resp = requests.post("${origin}/api/v1/reseller/provision",
    headers=headers,
    json={
        "company_name":       "Client Corp",
        "primary_domain":     "clientcorp.com",
        "admin_first_name":   "John",
        "admin_last_name":    "Doe",
        "alternate_email":    "john@example.com",
        "contact_name":       "John Doe",
        "admin_recovery_email": "john.alt@example.com",
        "postal_address": {
            "address_line1": "123 Tech Park",
            "locality":      "Mumbai",
            "region":        "MH",
            "postal_code":   "400001",
            "country_code":  "IN"
        },
        "plan":           "TRIAL",         # TRIAL / FLEXIBLE / ANNUAL_MONTHLY_PAY
        "sku_id":         "1030040059",    # Business Standard SKU
        "license_count":  5,
        "initiated_by_email":       "${_partnerEmail || 'you@company.com'}",
        "econz_notification_email": "${_partnerEmail || 'you@company.com'}"
    }
)
job = resp.json()
print("Job ID:", job["job_id"])   # e.g. JOB-A1B2C3D4`;
  }

  // ----- Step 3: Poll Job Status -----
  const statusEl = document.getElementById('code-job-status');
  if (statusEl) {
    statusEl.textContent =
`# Step 3: Poll until status is COMPLETED or FAILED
import time

job_id = job["job_id"]
while True:
    resp   = requests.get(
        f"${origin}/api/v1/reseller/provision/{job_id}",
        headers=headers
    )
    status = resp.json()["status"]
    print("Status:", status)

    if status in ("COMPLETED", "FAILED"):
        print(resp.json())
        break

    time.sleep(5)   # check every 5 seconds`;
  }
}
