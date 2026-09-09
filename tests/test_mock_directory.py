"""
Tests for the Mock Admin Directory API.

Covers user CRUD, duplicate handling, suspension, listing.
"""


class TestMockDirectoryAPI:
    """Mock Directory API endpoint tests."""

    def test_create_user(self, client):
        """Create a user via mock Directory API."""
        resp = client.post(
            "/mock/admin/directory/v1/users",
            json={
                "primaryEmail": "user1@dir-test.com",
                "name": {"givenName": "User", "familyName": "One"},
                "password": "TempPass123!",
                "changePasswordAtNextLogin": True,
                "suspended": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["kind"] == "admin#directory#user"
        assert data["id"].startswith("USER-")
        assert data["primaryEmail"] == "user1@dir-test.com"
        assert data["suspended"] is False

    def test_get_user(self, client):
        """Create then retrieve a user."""
        client.post(
            "/mock/admin/directory/v1/users",
            json={
                "primaryEmail": "getuser@dir-test.com",
                "name": {"givenName": "Get", "familyName": "User"},
                "password": "TempPass123!",
            },
        )
        resp = client.get(
            "/mock/admin/directory/v1/users/getuser@dir-test.com"
        )
        assert resp.status_code == 200
        assert resp.json()["primaryEmail"] == "getuser@dir-test.com"

    def test_duplicate_user_returns_409(self, client):
        """Test 6: Creating duplicate user returns 409."""
        payload = {
            "primaryEmail": "dupe@dir-test.com",
            "name": {"givenName": "Dupe", "familyName": "User"},
            "password": "TempPass123!",
        }
        client.post("/mock/admin/directory/v1/users", json=payload)
        resp2 = client.post("/mock/admin/directory/v1/users", json=payload)
        assert resp2.status_code == 409

    def test_user_not_found(self, client):
        """Getting non-existent user returns 404."""
        resp = client.get(
            "/mock/admin/directory/v1/users/nonexist@dir-test.com"
        )
        assert resp.status_code == 404

    def test_update_user(self, client):
        """Update user name."""
        client.post(
            "/mock/admin/directory/v1/users",
            json={
                "primaryEmail": "update@dir-test.com",
                "name": {"givenName": "Old", "familyName": "Name"},
                "password": "TempPass123!",
            },
        )
        resp = client.put(
            "/mock/admin/directory/v1/users/update@dir-test.com",
            json={"name": {"givenName": "New", "familyName": "Name"}},
        )
        assert resp.status_code == 200
        assert resp.json()["name"]["givenName"] == "New"

    def test_suspend_user(self, client):
        """Suspend a user."""
        client.post(
            "/mock/admin/directory/v1/users",
            json={
                "primaryEmail": "suspend@dir-test.com",
                "name": {"givenName": "Suspend", "familyName": "Me"},
                "password": "TempPass123!",
            },
        )
        resp = client.post(
            "/mock/admin/directory/v1/users/suspend@dir-test.com/suspend"
        )
        assert resp.status_code == 200
        assert resp.json()["suspended"] is True

    def test_delete_user(self, client):
        """Delete a user."""
        client.post(
            "/mock/admin/directory/v1/users",
            json={
                "primaryEmail": "delete@dir-test.com",
                "name": {"givenName": "Delete", "familyName": "Me"},
                "password": "TempPass123!",
            },
        )
        resp = client.delete(
            "/mock/admin/directory/v1/users/delete@dir-test.com"
        )
        assert resp.status_code == 204

        # Verify deleted
        resp2 = client.get(
            "/mock/admin/directory/v1/users/delete@dir-test.com"
        )
        assert resp2.status_code == 404

    def test_list_users_by_domain(self, client):
        """List users filtered by domain."""
        for i in range(3):
            client.post(
                "/mock/admin/directory/v1/users",
                json={
                    "primaryEmail": f"user{i}@listdomain.com",
                    "name": {"givenName": f"User{i}", "familyName": "Test"},
                    "password": "TempPass123!",
                },
            )
        # Add user from different domain
        client.post(
            "/mock/admin/directory/v1/users",
            json={
                "primaryEmail": "other@otherdomain.com",
                "name": {"givenName": "Other", "familyName": "Domain"},
                "password": "TempPass123!",
            },
        )

        resp = client.get(
            "/mock/admin/directory/v1/users?domain=listdomain.com"
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    def test_list_users_requires_domain(self, client):
        """Listing users without domain returns 400."""
        resp = client.get("/mock/admin/directory/v1/users")
        assert resp.status_code == 400
