"""Quick script to verify if the newly created user is a super admin."""
from google.oauth2 import service_account
from googleapiclient.discovery import build

creds = service_account.Credentials.from_service_account_file(
    "credentials.json",
    scopes=["https://www.googleapis.com/auth/admin.directory.user"],
).with_subject("dinesh.h@supportnation.co.in")

svc = build("admin", "directory_v1", credentials=creds, cache_discovery=False)

user_email = "john.demo@testdemo-317f52.com"

print(f"\n{'='*70}")
print(f"  VERIFYING USER: {user_email}")
print(f"{'='*70}\n")

try:
    user = svc.users().get(userKey=user_email).execute()
    print(f"  Found user: {user.get('primaryEmail')}")
    print(f"  Google User ID: {user.get('id')}")
    print(f"  Is Admin: {user.get('isAdmin')}")
    print(f"  Creation Time: {user.get('creationTime')}")
except Exception as e:
    print(f"  Error: {e}")

print(f"\n{'='*70}")
print("  DONE")
print(f"{'='*70}\n")
