"""
Tests for CSV bulk provisioning.

Covers:
9. CSV processing
"""

import os

from tests.conftest import wait_for_job_completion


class TestCsvProvisioning:
    """Single company CSV upload and processing tests."""

    def test_single_company_csv_upload(self, client):
        """Test: Single-company CSV upload creates job and provisions up to 5 users."""
        csv_content = (
            "company_name,primary_domain,alternate_email,contact_name,"
            "address_line1,locality,region,postal_code,country_code,"
            "plan,sku_id,license_count,initiated_by_email,"
            "econz_notification_email,first_name,last_name,personal_email\n"
            "Delta Corp,delta-corp-demo.com,admin@delta-alt.com,Delta Admin,"
            "1 Delta St,Bengaluru,KA,560001,IN,FLEXIBLE,SKU-BUSINESS-STANDARD,5,"
            "admin@delta-alt.com,econz@test.com,Alice,Smith,alice@g.com\n"
            "Delta Corp,delta-corp-demo.com,admin@delta-alt.com,Delta Admin,"
            "1 Delta St,Bengaluru,KA,560001,IN,FLEXIBLE,SKU-BUSINESS-STANDARD,5,"
            "admin@delta-alt.com,econz@test.com,Bob,Jones,bob@g.com\n"
            "Delta Corp,delta-corp-demo.com,admin@delta-alt.com,Delta Admin,"
            "1 Delta St,Bengaluru,KA,560001,IN,FLEXIBLE,SKU-BUSINESS-STANDARD,5,"
            "admin@delta-alt.com,econz@test.com,Charlie,Brown,charlie@g.com\n"
        )

        resp = client.post(
            "/api/v1/provision/csv",
            files={"file": ("test.csv", csv_content.encode(), "text/csv")},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "PENDING"
        assert data["company_name"] == "Delta Corp"
        assert data["primary_domain"] == "delta-corp-demo.com"
        assert data["employee_count"] == 3

        job_id = data["job_id"]
        status = wait_for_job_completion(client, job_id)
        assert status["status"] in ("COMPLETED", "PARTIAL_FAILURE")
        assert status["users_created"] == 3

    def test_simple_names_csv_with_form_fields(self, client):
        """Test: Simple names CSV with domain and plan passed via form fields."""
        csv_content = (
            "first_name,last_name,personal_email\n"
            "John,Doe,john.doe@gmail.com\n"
            "Jane,Smith,jane.smith@gmail.com\n"
        )

        resp = client.post(
            "/api/v1/provision/csv",
            files={"file": ("names.csv", csv_content.encode(), "text/csv")},
            data={
                "company_name": "Simple Corp",
                "primary_domain": "simple-corp.com",
                "alternate_email": "admin@simple-alt.com",
                "plan": "FLEXIBLE",
            },
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["company_name"] == "Simple Corp"
        assert data["primary_domain"] == "simple-corp.com"
        assert data["employee_count"] == 2

        status = wait_for_job_completion(client, data["job_id"])
        assert status["status"] == "COMPLETED"
        assert status["users_created"] == 2

    def test_csv_rejects_more_than_5_users(self, client):
        """Test: CSV with > 5 employees is rejected with 400 Bad Request."""
        csv_content = (
            "first_name,last_name,personal_email\n"
            "User1,Test,u1@g.com\n"
            "User2,Test,u2@g.com\n"
            "User3,Test,u3@g.com\n"
            "User4,Test,u4@g.com\n"
            "User5,Test,u5@g.com\n"
            "User6,Test,u6@g.com\n"  # 6th user violates max 5 limit
        )

        resp = client.post(
            "/api/v1/provision/csv",
            files={"file": ("too_many.csv", csv_content.encode(), "text/csv")},
            data={"primary_domain": "too-many.com"},
        )
        assert resp.status_code == 400
        assert "Maximum 5 employees" in resp.json()["detail"]

    def test_csv_non_csv_file_rejected(self, client):
        """Non-CSV file upload returns 400."""
        resp = client.post(
            "/api/v1/provision/csv",
            files={"file": ("test.txt", b"not csv", "text/plain")},
        )
        assert resp.status_code == 400

    def test_sample_csv_file(self, client):
        """Test the sample CSV file from data/."""
        csv_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "data",
            "sample_companies.csv",
        )
        if not os.path.exists(csv_path):
            return

        with open(csv_path, "rb") as f:
            resp = client.post(
                "/api/v1/provision/csv",
                files={"file": ("sample_companies.csv", f, "text/csv")},
            )
        assert resp.status_code == 202
        data = resp.json()
        assert data["employee_count"] == 5
        assert data["primary_domain"] == "alpha-systems.com"
