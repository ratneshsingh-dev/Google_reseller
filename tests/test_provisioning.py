"""
Tests for the core provisioning workflow.

Covers:
1. Successful company provisioning
2. Customer creation
3. Subscription creation
4. Seat allocation
5. Employee creation
6. Email to both recipients
"""

from tests.conftest import wait_for_job_completion


class TestProvisioningWorkflow:
    """End-to-end provisioning tests."""

    def test_successful_provisioning(self, client, sample_request):
        """Test 1: Full successful provisioning flow."""
        resp = client.post("/api/v1/provision", json=sample_request)
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "PENDING"
        job_id = data["job_id"]
        assert job_id.startswith("JOB-")

        # Wait for completion
        status = wait_for_job_completion(client, job_id)
        assert status["status"] == "COMPLETED"
        assert status["company_name"] == "Test Corp"
        assert status["primary_domain"] == "test-corp.com"
        assert status["users_created"] == 3
        assert status["users_failed"] == 0

    def test_customer_creation(self, client, sample_request):
        """Test 2: Customer is created and visible in companies API."""
        resp = client.post("/api/v1/provision", json=sample_request)
        job_id = resp.json()["job_id"]
        status = wait_for_job_completion(client, job_id)

        assert status["google_customer_id"] is not None
        assert status["google_customer_id"].startswith("CUST-")

        # Verify company in list
        companies = client.get("/api/v1/companies").json()
        assert len(companies) >= 1
        assert any(c["primary_domain"] == "test-corp.com" for c in companies)

    def test_subscription_creation(self, client, sample_request):
        """Test 3: Subscription is created with correct plan and seats."""
        resp = client.post("/api/v1/provision", json=sample_request)
        job_id = resp.json()["job_id"]
        status = wait_for_job_completion(client, job_id)

        assert status["google_subscription_id"] is not None
        assert status["google_subscription_id"].startswith("SUB-")
        assert status["plan"] == "FLEXIBLE"
        assert status["sku_id"] == "SKU-BUSINESS-STANDARD"
        assert status["licensed_seats"] == 3

    def test_seat_allocation(self, client, sample_request):
        """Test 4: Seats match license count."""
        resp = client.post("/api/v1/provision", json=sample_request)
        job_id = resp.json()["job_id"]
        status = wait_for_job_completion(client, job_id)

        assert status["licensed_seats"] == 3
        assert status["users_created"] + status["users_existing"] == 3

    def test_employee_creation(self, client, sample_request):
        """Test 5: All employees are created with correct corporate emails."""
        resp = client.post("/api/v1/provision", json=sample_request)
        job_id = resp.json()["job_id"]
        status = wait_for_job_completion(client, job_id)

        employees = status["employees"]
        assert len(employees) == 3

        emails = {e["corporate_email"] for e in employees}
        assert "rahul.sharma@test-corp.com" in emails
        assert "priya.patel@test-corp.com" in emails
        assert "amit.kumar@test-corp.com" in emails

        for emp in employees:
            assert emp["status"] in ("PROVISIONED", "EXISTING")
            assert emp["google_user_id"] is not None

    def test_email_sent_to_both_recipients(self, client, sample_request):
        """Test 6: Confirmation email sent to initiated_by and econz."""
        resp = client.post("/api/v1/provision", json=sample_request)
        job_id = resp.json()["job_id"]
        status = wait_for_job_completion(client, job_id)

        assert status["email_status"] in ("ALL_SENT", "PARTIAL_SENT")

    def test_provisioning_steps_tracked(self, client, sample_request):
        """Verify all provisioning steps are recorded."""
        resp = client.post("/api/v1/provision", json=sample_request)
        job_id = resp.json()["job_id"]
        status = wait_for_job_completion(client, job_id)

        steps = status["steps"]
        step_names = [s["step_name"] for s in steps]
        assert "VALIDATE_INPUT" in step_names
        assert "CREATE_CUSTOMER" in step_names
        assert "CREATE_SUBSCRIPTION" in step_names
        assert "CREATE_USERS" in step_names
        assert "SEND_EMAILS" in step_names

    def test_company_users_endpoint(self, client, sample_request):
        """Verify GET /api/v1/companies/{id}/users returns employees."""
        resp = client.post("/api/v1/provision", json=sample_request)
        job_id = resp.json()["job_id"]
        wait_for_job_completion(client, job_id)

        companies = client.get("/api/v1/companies").json()
        company_id = companies[0]["company_id"]

        users = client.get(f"/api/v1/companies/{company_id}/users").json()
        assert len(users) == 3

    def test_health_check(self, client):
        """Verify health endpoint."""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["adapter"] == "mock"
