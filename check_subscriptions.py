"""Verify reseller customers + subscriptions exist via API — the source of truth."""
from google.oauth2 import service_account
from googleapiclient.discovery import build

creds = service_account.Credentials.from_service_account_file(
    "credentials.json",
    scopes=["https://www.googleapis.com/auth/apps.order"],
).with_subject("dinesh.h@supportnation.co.in")

svc = build("reseller", "v1", credentials=creds, cache_discovery=False)

# List all customers we've created
print("=" * 70)
print("  RESELLER CUSTOMERS & SUBSCRIPTIONS (via Google Reseller API)")
print("  This is the source of truth — these exist in Google's system")
print("=" * 70)

# Get subscriptions
result = svc.subscriptions().list(maxResults=50).execute()
subs = result.get("subscriptions", [])

for i, s in enumerate(subs, 1):
    cid = s.get("customerId")
    domain = s.get("customerDomain")
    
    # Try to get full customer details
    try:
        customer = svc.customers().get(customerId=cid).execute()
        contact = customer.get("postalAddress", {}).get("contactName", "N/A")
        org = customer.get("postalAddress", {}).get("organizationName", "N/A")
        alt_email = customer.get("alternateEmail", "N/A")
    except Exception:
        contact = "N/A"
        org = "N/A"
        alt_email = "N/A"
    
    plan = s.get("plan", {}).get("planName", "N/A")
    seats = s.get("seats", {}).get("numberOfSeats", "N/A")
    licensed = s.get("seats", {}).get("licensedNumberOfSeats", "N/A")
    sku = s.get("skuId", "N/A")
    status = s.get("status", "N/A")
    sub_id = s.get("subscriptionId", "N/A")
    
    print(f"\n  [{i}] {domain}")
    print(f"      Customer ID   : {cid}")
    print(f"      Organization  : {org}")
    print(f"      Contact       : {contact}")
    print(f"      Alt Email     : {alt_email}")
    print(f"      Subscription  : {sub_id}")
    print(f"      SKU           : {sku}")
    print(f"      Plan          : {plan}")
    print(f"      Seats         : {seats} (licensed: {licensed})")
    print(f"      Status        : {status}")
    print(f"      {'*** ACTIVE ***' if status == 'ACTIVE' else '(Waiting for customer to complete setup)'}")

print(f"\n{'=' * 70}")
print(f"  Total: {len(subs)} subscriptions managed by your reseller account")
print(f"{'=' * 70}")
print(f"\n  NOTE: These subscriptions don't appear in YOUR admin console billing")
print(f"  because they belong to the CUSTOMER domains, not supportnation.co.in.")
print(f"  The Reseller API is the correct way to manage and view them.")
print(f"  The customers would see them in their own admin.google.com after setup.\n")
