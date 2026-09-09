import os
import google.auth.transport.requests
from google.oauth2 import service_account

# Load from your existing credentials.json
CREDENTIALS_FILE = "credentials.json"
# The admin email you want to impersonate
ADMIN_EMAIL = "Admin@supportnation.co.in"

# We need scopes for Reseller API, Directory API (Users), and Gmail
SCOPES = [
    "https://www.googleapis.com/auth/apps.order",
    "https://www.googleapis.com/auth/admin.directory.user",
    "https://www.googleapis.com/auth/gmail.send"
]

def get_dwd_token():
    print(f"Loading credentials from {CREDENTIALS_FILE}...")
    credentials = service_account.Credentials.from_service_account_file(
        CREDENTIALS_FILE, scopes=SCOPES
    )
    
    print(f"Impersonating {ADMIN_EMAIL}...")
    delegated_credentials = credentials.with_subject(ADMIN_EMAIL)
    
    print("Fetching token from Google...")
    request = google.auth.transport.requests.Request()
    delegated_credentials.refresh(request)
    
    print("\n" + "="*50)
    print("YOUR BEARER TOKEN FOR POSTMAN (Valid for 1 hour):")
    print("="*50)
    print(delegated_credentials.token)
    print("="*50 + "\n")

if __name__ == "__main__":
    get_dwd_token()
