"""Check if the admin user has Gmail enabled and what licenses they have."""
from google.oauth2 import service_account
from googleapiclient.discovery import build

creds = service_account.Credentials.from_service_account_file(
    "credentials.json",
    scopes=["https://www.googleapis.com/auth/admin.directory.user"],
).with_subject("dinesh.h@supportnation.co.in")

svc = build("admin", "directory_v1", credentials=creds, cache_discovery=False)

try:
    user = svc.users().get(userKey="dinesh.h@supportnation.co.in").execute()
    print("User found!")
    print(f"Is Admin: {user.get('isAdmin')}")
    print(f"Suspended: {user.get('suspended')}")
    
    # Check if mail routing is set up / if they have a mailbox
    # Often, if Gmail is disabled, 'isMailboxSetup' is false or missing
    print(f"Is Mailbox Setup: {user.get('isMailboxSetup', 'Unknown')}")
except Exception as e:
    print(f"Error: {e}")
