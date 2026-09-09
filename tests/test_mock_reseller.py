"""
Tests for the Mock Reseller API.

Tests customer and subscription CRUD operations through the mock HTTP endpoints.
"""

import pytest


class TestMockResellerAPI:
    """Mock Reseller API endpoint tests."""

    def test_create_customer(self, client):
        """Create a customer via mock API."""
        payload = {
            "customerDomain": "example-test.com",
            "customerType": "domain",
            "postalAddress": {
                "contactName": "Test User",
                "organizationName": "Example Corp",
                "addressLine1": "123 Main St",
                "locality": "City",
                "region": "ST",
                "postalCode": "12345",
                "countryCode": "US",
            },
            "alternateEmail": "alt@example.net",
        }
        resp = client.post("/mock/reseller/v1/customers", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["kind"] == "reseller#customer"
        assert data["customerId"].startswith("CUST-")
        assert data["customerDomain"] == "example-test.com"
        assert data["customerDomainVerified"] is True

    def test_get_customer(self, client):
        """Create then retrieve a customer."""
        payload = {
            "customerDomain": "get-test.com",
            "customerType": "domain",
            "postalAddress": {
                "contactName": "Get User",
                "organizationName": "Get Corp",
                "addressLine1": "456 Test Ave",
                "locality": "Town",
                "region": "ST",
                "postalCode": "67890",
                "countryCode": "US",
            },
            "alternateEmail": "get@example.net",
        }
        create_resp = client.post("/mock/reseller/v1/customers", json=payload)
        customer_id = create_resp.json()["customerId"]

        get_resp = client.get(f"/mock/reseller/v1/customers/{customer_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["customerId"] == customer_id

    def test_duplicate_customer_returns_409(self, client):
        """Creating duplicate customer returns 409."""
        payload = {
            "customerDomain": "dupe-test.com",
            "customerType": "domain",
            "postalAddress": {
                "contactName": "Dupe User",
                "organizationName": "Dupe Corp",
                "addressLine1": "789 Dupe St",
                "locality": "Dupeville",
                "region": "ST",
                "postalCode": "11111",
                "countryCode": "US",
            },
            "alternateEmail": "dupe@example.net",
        }
        client.post("/mock/reseller/v1/customers", json=payload)
        resp2 = client.post("/mock/reseller/v1/customers", json=payload)
        assert resp2.status_code == 409

    def test_customer_not_found(self, client):
        """Getting non-existent customer returns 404."""
        resp = client.get("/mock/reseller/v1/customers/CUST-NONEXIST")
        assert resp.status_code == 404

    def test_create_subscription(self, client):
        """Create a subscription for a customer."""
        # First create customer
        cust_resp = client.post(
            "/mock/reseller/v1/customers",
            json={
                "customerDomain": "sub-test.com",
                "customerType": "domain",
                "postalAddress": {
                    "contactName": "Sub User",
                    "organizationName": "Sub Corp",
                    "addressLine1": "1 Sub St",
                    "locality": "Subville",
                    "region": "ST",
                    "postalCode": "22222",
                    "countryCode": "US",
                },
                "alternateEmail": "sub@example.net",
            },
        )
        customer_id = cust_resp.json()["customerId"]

        sub_resp = client.post(
            f"/mock/reseller/v1/customers/{customer_id}/subscriptions",
            json={
                "skuId": "SKU-BUSINESS-STANDARD",
                "plan": {"planName": "FLEXIBLE"},
                "seats": {"numberOfSeats": 10},
                "customerDomain": "sub-test.com",
            },
        )
        assert sub_resp.status_code == 200
        data = sub_resp.json()
        assert data["kind"] == "reseller#subscription"
        assert data["subscriptionId"].startswith("SUB-")
        assert data["status"] == "ACTIVE"
        assert data["seats"]["numberOfSeats"] == 10

    def test_change_seats(self, client):
        """Change seats on a subscription."""
        # Setup
        cust = client.post(
            "/mock/reseller/v1/customers",
            json={
                "customerDomain": "seats-test.com",
                "customerType": "domain",
                "postalAddress": {
                    "contactName": "Seats User",
                    "organizationName": "Seats Corp",
                    "addressLine1": "1 Seat St",
                    "locality": "Seatville",
                    "region": "ST",
                    "postalCode": "33333",
                    "countryCode": "US",
                },
                "alternateEmail": "seats@example.net",
            },
        ).json()
        cid = cust["customerId"]

        sub = client.post(
            f"/mock/reseller/v1/customers/{cid}/subscriptions",
            json={
                "skuId": "SKU-BUSINESS-STANDARD",
                "plan": {"planName": "FLEXIBLE"},
                "seats": {"numberOfSeats": 5},
            },
        ).json()
        sid = sub["subscriptionId"]

        # Change seats
        resp = client.post(
            f"/mock/reseller/v1/customers/{cid}/subscriptions/{sid}/changeSeats",
            json={"seats": {"numberOfSeats": 20}},
        )
        assert resp.status_code == 200
        assert resp.json()["seats"]["numberOfSeats"] == 20

    def test_change_plan(self, client):
        """Change plan on a subscription."""
        cust = client.post(
            "/mock/reseller/v1/customers",
            json={
                "customerDomain": "plan-test.com",
                "customerType": "domain",
                "postalAddress": {
                    "contactName": "Plan User",
                    "organizationName": "Plan Corp",
                    "addressLine1": "1 Plan St",
                    "locality": "Planville",
                    "region": "ST",
                    "postalCode": "44444",
                    "countryCode": "US",
                },
                "alternateEmail": "plan@example.net",
            },
        ).json()
        cid = cust["customerId"]

        sub = client.post(
            f"/mock/reseller/v1/customers/{cid}/subscriptions",
            json={
                "skuId": "SKU-BUSINESS-STANDARD",
                "plan": {"planName": "FLEXIBLE"},
                "seats": {"numberOfSeats": 5},
            },
        ).json()
        sid = sub["subscriptionId"]

        resp = client.post(
            f"/mock/reseller/v1/customers/{cid}/subscriptions/{sid}/changePlan",
            json={"planName": "ANNUAL_MONTHLY_PAY"},
        )
        assert resp.status_code == 200
        assert resp.json()["plan"]["planName"] == "ANNUAL_MONTHLY_PAY"

    def test_list_subscriptions(self, client):
        """List subscriptions for a customer."""
        cust = client.post(
            "/mock/reseller/v1/customers",
            json={
                "customerDomain": "list-subs-test.com",
                "customerType": "domain",
                "postalAddress": {
                    "contactName": "List User",
                    "organizationName": "List Corp",
                    "addressLine1": "1 List St",
                    "locality": "Listville",
                    "region": "ST",
                    "postalCode": "55555",
                    "countryCode": "US",
                },
                "alternateEmail": "list@example.net",
            },
        ).json()
        cid = cust["customerId"]

        client.post(
            f"/mock/reseller/v1/customers/{cid}/subscriptions",
            json={
                "skuId": "SKU-BUSINESS-STANDARD",
                "plan": {"planName": "FLEXIBLE"},
                "seats": {"numberOfSeats": 5},
            },
        )

        resp = client.get(f"/mock/reseller/v1/customers/{cid}/subscriptions")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
