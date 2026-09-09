"""Test if reseller can create admin user in customer domain via Directory API.

Tests multiple approaches:
1. Direct user creation (skip the GET check)
2. Using customer-scoped impersonation
"""
import os
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Use the most recent customer domain
CUSTOMER_DOMAIN = "testdemo-b8f5ea.com"
ADMIN_EMAIL = "admin@testdemo-b8f5ea.com"
ADMIN_PASSWORD = "TempPass#2026!"

RESELLER_ADMIN = "dinesh.h@supportnation.co.in"
CREDENTIALS_FILE = "credentials.json"

SCOPES = [
    "https://www.googleapis.com/auth/admin.directory.user",
]

print(f"\n{'='*70}")
print(f"  TESTING ADMIN USER CREATION IN CUSTOMER DOMAIN")
print(f"  Domain: {CUSTOMER_DOMAIN}")
print(f"  Admin:  {ADMIN_EMAIL}")
print(f"{'='*70}\n")

# --- Approach 1: Impersonate reseller admin, create user directly ---
print("[1] Attempting: Impersonate reseller admin -> create user in customer domain")
try:
    creds = service_account.Credentials.from_service_account_file(
        CREDENTIALS_FILE, scopes=SCOPES
    ).with_subject(RESELLER_ADMIN)

    svc = build("admin", "directory_v1", credentials=creds, cache_discovery=False)

    body = {
        "primaryEmail": ADMIN_EMAIL,
        "name": {
            "givenName": "Admin",
            "familyName": "User",
        },
        "password": ADMIN_PASSWORD,
        "changePasswordAtNextLogin": True,
        "orgUnitPath": "/",
    }

    result = svc.users().insert(body=body).execute()
    print(f"    SUCCESS! User created: {result.get('primaryEmail')}")
    print(f"    Google User ID: {result.get('id')}")
    print(f"    Is Admin: {result.get('isAdmin')}")

    # Try to make them a super admin
    print("\n    Making user a Super Admin...")
    try:
        svc.users().makeAdmin(userKey=ADMIN_EMAIL, body={"status": True}).execute()
        print("    SUCCESS! User is now Super Admin")
    except HttpError as e:
        print(f"    Make admin failed: {e.resp.status} - {e._get_reason()}")

except HttpError as e:
    print(f"    FAILED: {e.resp.status} - {e._get_reason()}")
    print(f"    Details: {e.content.decode()[:300]}")
except Exception as e:
    print(f"    ERROR: {type(e).__name__}: {e}")

print(f"\n{'='*70}")
print("  DONE")
print(f"{'='*70}\n")
