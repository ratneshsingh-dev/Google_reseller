import os
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "credentials.json"

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/apps.order",
    "https://www.googleapis.com/auth/admin.directory.user",
    "https://www.googleapis.com/auth/apps.licensing",
]

ADMIN_EMAIL = "dinesh.h@supportnation.co.in"

creds = service_account.Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
delegated = creds.with_subject(ADMIN_EMAIL)

print("\n=== TEST 1: Reseller API ===")
try:
    reseller = build("reseller", "v1", credentials=delegated, cache_discovery=False)
    result = reseller.subscriptions().list(maxResults=5).execute()
    subs = result.get("subscriptions", [])
    print("SUCCESS - Reseller API working! Subscriptions:", len(subs))
    for s in subs[:3]:
        print("  Customer:", s.get("customerId"), "| Plan:", s.get("plan", {}).get("planName"))
except Exception as e:
    print("FAILED:", str(e))

print("\n=== TEST 2: Admin SDK ===")
try:
    admin = build("admin", "directory_v1", credentials=delegated, cache_discovery=False)
    result = admin.users().list(domain="supportnation.co.in", maxResults=5).execute()
    users = result.get("users", [])
    print("SUCCESS - Admin SDK working! Users:", len(users))
    for u in users[:3]:
        print("  User:", u.get("primaryEmail"))
except Exception as e:
    print("FAILED:", str(e))

print("\n=== DONE ===")
