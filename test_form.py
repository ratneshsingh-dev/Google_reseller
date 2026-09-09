import httpx
import asyncio
import uuid
import time

payload = {
    'company_name': 'Test Company Demo',
    'primary_domain': f'testdemo-{uuid.uuid4().hex[:6]}.com',
    'alternate_email': 'ratnesh.s@econz.net',
    'contact_name': 'John Demo',
    'postal_address': {
        'address_line1': '123 Tech Park',
        'locality': 'Bengaluru',
        'region': 'Karnataka',
        'postal_code': '560001',
        'country_code': 'IN'
    },
    'plan': 'TRIAL',
    'sku_id': 'Google-Workspace-Business-Standard',
    'license_count': 1,
    'initiated_by_email': 'dinesh.h@supportnation.co.in',
    'econz_notification_email': 'ratnesh.s@econz.net',
    'admin_first_name': 'John',
    'admin_last_name': 'Demo',
    'admin_recovery_email': 'ratnesh.s@econz.net'
}

async def run_provisioning():
    async with httpx.AsyncClient(timeout=30) as client:
        print(f"Submitting job for domain {payload['primary_domain']}...")
        r = await client.post(
            'http://127.0.0.1:8000/api/v1/provision', 
            json=payload, 
            headers={'Idempotency-Key': f'req-auto-{uuid.uuid4().hex[:6]}'}
        )
        if r.status_code != 202:
            print('Submission failed:', r.text)
            return
        
        job_id = r.json().get('job_id')
        print(f'Job started: {job_id}')
        
        while True:
            r2 = await client.get(f'http://127.0.0.1:8000/api/v1/provision/{job_id}')
            job = r2.json()
            status = job.get('status')
            print(f'Status: {status}')
            if status in ('COMPLETED', 'FAILED'):
                print(f'\nFinal Error: {job.get("error_message")}')
                for step in job.get('steps', []):
                    print(f'- {step.get("step_name")}: {step.get("status")}')
                break
            time.sleep(2)

asyncio.run(run_provisioning())
