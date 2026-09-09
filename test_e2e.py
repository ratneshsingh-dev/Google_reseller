"""End-to-end pipeline test — submits provisioning and monitors status."""
import httpx, asyncio, uuid, time

DOMAIN = f"testdemo-{uuid.uuid4().hex[:6]}.com"
IDEMPOTENCY_KEY = f"req-e2e-{uuid.uuid4().hex[:8]}"

payload = {
    "company_name": "Test Company Demo",
    "primary_domain": DOMAIN,
    "alternate_email": "ratnesh.s@econz.net",
    "contact_name": "John Demo",
    "postal_address": {
        "address_line1": "123 Tech Park",
        "locality": "Bengaluru",
        "region": "Karnataka",
        "postal_code": "560001",
        "country_code": "IN",
    },
    "plan": "TRIAL",
    "sku_id": "Google-Workspace-Business-Standard",
    "license_count": 2,
    "initiated_by_email": "dinesh.h@supportnation.co.in",
    "econz_notification_email": "ratnesh.s@econz.net",
    "admin_first_name": "John",
    "admin_last_name": "Demo",
    "admin_recovery_email": "ratnesh.s@econz.net",
}

async def run():
    async with httpx.AsyncClient(timeout=60) as client:
        print(f"Domain: {DOMAIN} | Key: {IDEMPOTENCY_KEY}")
        r = await client.post(
            "http://127.0.0.1:8000/api/v1/provision",
            json=payload,
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
        )
        if r.status_code != 202:
            print(f"SUBMIT FAILED: {r.status_code} {r.text}")
            return
        job_id = r.json().get("job_id")
        print(f"Job: {job_id}")

        for i in range(60):
            time.sleep(3)
            r2 = await client.get(f"http://127.0.0.1:8000/api/v1/provision/{job_id}")
            job = r2.json()
            status = job.get("status")
            steps = " | ".join(f"{s.get('step_name')}:{s.get('status')}" for s in job.get("steps", []))
            print(f"[{i+1}] {status} | {steps}")

            if status in ("COMPLETED", "FAILED", "PARTIAL_FAILURE"):
                print(f"\n{'='*60}")
                print(f"RESULT: {status}")
                print(f"Customer: {job.get('google_customer_id')}")
                print(f"Subscription: {job.get('google_subscription_id')}")
                print(f"Plan: {job.get('plan')} | SKU: {job.get('sku_id')}")
                print(f"Seats: {job.get('licensed_seats')}")
                print(f"Users: created={job.get('users_created')} existing={job.get('users_existing')} failed={job.get('users_failed')}")
                print(f"Email: {job.get('email_status')}")
                if job.get("error_message"):
                    print(f"ERROR: {job['error_message']}")
                print(f"{'='*60}")
                break

asyncio.run(run())
