"""
E2E API-only test. No Firestore cleanup (server is already using it).
Creates Jithin as partner, provisions 2 TRIAL licences for a unique domain.
"""
import sys, time, datetime, requests

BASE = "http://localhost:8000"

TIMESTAMP = datetime.datetime.now().strftime("%m%d%H%M%S")
TEST_DOMAIN = f"jithin-test-{TIMESTAMP}.com"
print(f"Test domain: {TEST_DOMAIN}")
print()

# Step 1: Admin login
print("[1] Admin login...")
s = requests.Session()
r = s.post(f"{BASE}/api/v1/auth/dev-login")
assert r.status_code == 200, f"Admin login failed: {r.status_code} {r.text}"
print("    OK")

# Step 2: Create or reuse Jithin as channel partner
print("[2] Creating channel partner jithin.m@econz.net (cap=5)...")
r2 = s.post(f"{BASE}/api/v1/admin/resellers", json={
    "company_name": "Econz Wireless Pvt Ltd",
    "contact_name": "Jithin M",
    "contact_email": "jithin-new@econz.net",
    "max_licence_cap": 50,
    "role": "RESELLER_FULL",
    "access_methods": ["api", "csv"]
})
if r2.status_code == 409:
    # Already exists — get list, find RSL ID, regenerate secret
    print("    Already exists — fetching and regenerating secret...")
    r_list = s.get(f"{BASE}/api/v1/admin/resellers")
    resellers = r_list.json() if isinstance(r_list.json(), list) else r_list.json().get("resellers", [])
    existing = next((r for r in resellers if r.get("contact_email") == "jithin-new@econz.net"), None)
    if not existing:
        print(f"    Could not find existing reseller. Exiting.")
        sys.exit(1)
    RSL_ID = existing.get("reseller_id")
    r_regen = s.post(f"{BASE}/api/v1/admin/resellers/{RSL_ID}/regenerate-secret")
    if r_regen.status_code != 200:
        print(f"    Regenerate failed: {r_regen.status_code} {r_regen.text[:200]}")
        sys.exit(1)
    data = r_regen.json()
    CLIENT_ID = RSL_ID
    CLIENT_SECRET = data.get("plain_secret") or data.get("client_secret") or data.get("new_secret")
elif r2.status_code in (200, 201):
    data = r2.json()
    CLIENT_ID = data.get("reseller_id") or data.get("client_id")
    CLIENT_SECRET = data.get("plain_secret") or data.get("client_secret")
else:
    print(f"    FAILED: {r2.status_code} {r2.text[:400]}")
    sys.exit(1)
print(f"    client_id:     {CLIENT_ID}")
print(f"    client_secret: {CLIENT_SECRET}")

# Step 3: Get partner token
print("[3] Getting partner JWT token...")
r3 = requests.post(f"{BASE}/api/v1/reseller/auth/token", json={
    "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET
})
assert r3.status_code == 200, f"Token failed: {r3.status_code} {r3.text}"
PARTNER_TOKEN = r3.json()["access_token"]
H = {"Authorization": f"Bearer {PARTNER_TOKEN}"}
print("    OK")

# Step 4: Check quota
print("[4] Quota check...")
q = requests.get(f"{BASE}/api/v1/reseller/quota", headers=H).json()
print(f"    Cap={q.get('max_licence_cap')} Used={q.get('licences_used')} Remaining={q.get('licences_remaining')}")

# Step 5: Provision 2 licences
print(f"[5] Provisioning 20 TRIAL licences for {TEST_DOMAIN}...")
for attempt in range(1, 4):
    r5 = requests.post(f"{BASE}/api/v1/reseller/provision", headers=H, json={
        "company_name": "Jithin Test Corp",
        "primary_domain": TEST_DOMAIN,
        "admin_first_name": "Jithin",
        "admin_last_name": "Test",
        "alternate_email": "jithin.m@econz.net",
        "contact_name": "Jithin M",
        "admin_recovery_email": "jithin.m@econz.net",
        "postal_address": {
            "address_line1": "123 Test Street",
            "locality": "Kochi",
            "region": "KL",
            "postal_code": "682001",
            "country_code": "IN"
        },
        "plan": "TRIAL",
        "sku_id": "1010020027",
        "license_count": 20,
        "initiated_by_email": "jithin.m@econz.net",
        "econz_notification_email": "jithin.m@econz.net"
    })
    if r5.status_code in (200, 202):
        break
    print(f"    Attempt {attempt} failed: {r5.status_code} {r5.text[:200]}")
    if attempt < 3:
        time.sleep(5)
        # Use a fresh domain on retry
        TIMESTAMP2 = datetime.datetime.now().strftime("%m%d%H%M%S")
        TEST_DOMAIN = f"jithin-test-{TIMESTAMP2}.com"
        print(f"    Retrying with domain: {TEST_DOMAIN}")
else:
    sys.exit(1)
resp5 = r5.json()
JOB_ID = resp5["job_id"]
print(f"    Job ID: {JOB_ID}")
print(f"    {resp5.get('quota_summary', {}).get('message', '')}")

# Step 6: Poll until done
print(f"[6] Polling {JOB_ID} (up to 150s)...")
final = None
for i in range(30):
    time.sleep(5)
    r6 = requests.get(f"{BASE}/api/v1/reseller/provision/{JOB_ID}", headers=H)
    job = r6.json()
    status = job.get("status")
    done = [s["step_name"] for s in job.get("steps", []) if s["status"] == "COMPLETED"]
    failed = [s["step_name"] for s in job.get("steps", []) if s["status"] == "FAILED"]
    print(f"    [{(i+1)*5:3d}s] {status:12s} done={done} failed={failed}")
    if status in ("COMPLETED", "FAILED"):
        final = job
        break

print()
print("=" * 60)
if final:
    st = final.get("status")
    print(f"RESULT: {st}")
    print(f"  google_customer_id:     {final.get('google_customer_id')}")
    print(f"  google_subscription_id: {final.get('google_subscription_id')}")
    print(f"  users_created:          {final.get('users_created')}")
    print(f"  users_failed:           {final.get('users_failed')}")
    print(f"  email_status:           {final.get('email_status')}")
    if final.get("error_message"):
        print(f"  ERROR: {final.get('error_message')}")
    print()
    print("Steps:")
    for step in sorted(final.get("steps", []), key=lambda x: x.get("started_at","") or ""):
        print(f"  {step['step_name']:25s} {step['status']:12s} {step.get('details') or step.get('error_message') or ''}")
else:
    print("TIMED OUT after 150s")
print("=" * 60)
