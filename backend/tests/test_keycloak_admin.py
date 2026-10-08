import json
import os
import unittest
from unittest.mock import patch

import httpx

from app.keycloak_admin import KeycloakUserAdmin, ProvisioningError, ProvisioningUnavailable


class KeycloakUserAdminTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "KEYCLOAK_ADMIN_URL": "http://keycloak.test/auth",
            "KEYCLOAK_PROVISION_REALM": "rice-research",
            "KEYCLOAK_PROVISION_CLIENT_ID": "provisioner",
            "KEYCLOAK_PROVISION_CLIENT_SECRET": "test-only-secret",
            "KEYCLOAK_RESEARCHER_ROLE_ID": "researcher-id",
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_create_researcher_assigns_only_researcher_and_temporary_password(self):
        requests = []

        def respond(request):
            requests.append(request)
            path = request.url.path
            if path.endswith("/token"):
                return httpx.Response(200, json={"access_token": "test-token"})
            if request.method == "POST" and path.endswith("/users"):
                payload = json.loads(request.content)
                self.assertFalse(payload["enabled"])
                self.assertEqual(payload["requiredActions"], ["UPDATE_PASSWORD"])
                return httpx.Response(201, headers={"Location": "http://keycloak.test/auth/admin/realms/rice-research/users/new-id"})
            if path.endswith("/role-mappings/realm"):
                self.assertEqual(json.loads(request.content), [{"id": "researcher-id", "name": "researcher"}])
                return httpx.Response(204)
            if path.endswith("/reset-password"):
                payload = json.loads(request.content)
                self.assertEqual(payload["value"], "temporary-test-password")
                self.assertTrue(payload["temporary"])
                return httpx.Response(204)
            if request.method == "PUT" and path.endswith("/users/new-id"):
                self.assertEqual(json.loads(request.content), {"enabled": True})
                return httpx.Response(204)
            raise AssertionError((request.method, path))

        with KeycloakUserAdmin(transport=httpx.MockTransport(respond)) as admin:
            subject = admin.create_researcher("ynaas.new", "新科研人员", "temporary-test-password")
        self.assertEqual(subject, "new-id")
        self.assertEqual(sum(request.url.path.endswith("/token") for request in requests), 1)

    def test_failed_provisioning_removes_new_disabled_user(self):
        deleted = []

        def respond(request):
            path = request.url.path
            if path.endswith("/token"):
                return httpx.Response(200, json={"access_token": "test-token"})
            if request.method == "POST" and path.endswith("/users"):
                return httpx.Response(201, headers={"Location": "http://keycloak.test/auth/admin/realms/rice-research/users/new-id"})
            if path.endswith("/role-mappings/realm"):
                return httpx.Response(403)
            if request.method == "DELETE" and path.endswith("/users/new-id"):
                deleted.append(path)
                return httpx.Response(204)
            raise AssertionError((request.method, path))

        with KeycloakUserAdmin(transport=httpx.MockTransport(respond)) as admin:
            with self.assertRaises(ProvisioningError):
                admin.create_researcher("ynaas.new", "新科研人员", "temporary-test-password")
        self.assertEqual(len(deleted), 1)

    def test_deactivation_logs_out_sessions(self):
        changes = []

        def respond(request):
            path = request.url.path
            if path.endswith("/token"):
                return httpx.Response(200, json={"access_token": "test-token"})
            if request.method == "GET" and path.endswith("/users/user-id"):
                return httpx.Response(200, json={"username": "ynaas.new"})
            if request.method == "PUT" and path.endswith("/users/user-id"):
                changes.append(json.loads(request.content))
                return httpx.Response(204)
            if request.method == "POST" and path.endswith("/users/user-id/logout"):
                changes.append("logout")
                return httpx.Response(204)
            raise AssertionError((request.method, path))

        with KeycloakUserAdmin(transport=httpx.MockTransport(respond)) as admin:
            admin.set_enabled("user-id", "ynaas.new", False)
        self.assertEqual(changes, [{"enabled": False}, "logout"])

    def test_missing_service_secret_fails_closed(self):
        with patch.dict(os.environ, {"KEYCLOAK_PROVISION_CLIENT_SECRET": ""}):
            with self.assertRaises(ProvisioningUnavailable):
                KeycloakUserAdmin()


if __name__ == "__main__":
    unittest.main()
