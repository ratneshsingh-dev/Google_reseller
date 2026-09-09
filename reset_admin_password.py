"""Reset the password for the newly created admin so the user can log in."""
from google.oauth2 import service_account
from googleapiclient.discovery import build

creds = service_account.Credentials.from_service_account_file(
    "credentials.json",
    scopes=["https://www.googleapis.com/auth/admin.directory.user"],
).with_subject("dinesh.h@supportnation.co.in")

svc = build("admin", "directory_v1", credentials=creds, cache_discovery=False)

user_email = "ratnesh.singh@my-new-demo-123.com"
new_password = "DemoAdmin123!"

try:
    svc.users().update(
        userKey=user_email,
        body={"password": new_password, "changePasswordAtNextLogin": False}
    ).execute()
    print(f"SUCCESS: Password for {user_email} reset to: {new_password}")
except Exception as e:
    print(f"Error resetting password: {e}")
