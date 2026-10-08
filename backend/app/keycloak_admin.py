"""Narrow, server-side Keycloak user provisioning for the Yunnan trial.

Only the application backend holds the service-account secret.  The public
web client never receives an administrator credential or an Admin REST token.
"""

from __future__ import annotations

import os
from urllib.parse import quote, urlsplit

import httpx


class ProvisioningUnavailable(Exception):
    """The dedicated service account has not been configured."""


class ProvisioningConflict(Exception):
    """The username already exists in Keycloak."""


class ProvisioningError(Exception):
    """Keycloak rejected an operation; never include its response body."""


class KeycloakUserAdmin:
    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = os.getenv("KEYCLOAK_ADMIN_URL", "http://keycloak:8080/auth").rstrip("/")
        self.realm = os.getenv("KEYCLOAK_PROVISION_REALM", "rice-research").strip()
        self.client_id = os.getenv("KEYCLOAK_PROVISION_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("KEYCLOAK_PROVISION_CLIENT_SECRET", "").strip()
        self.researcher_role_id = os.getenv("KEYCLOAK_RESEARCHER_ROLE_ID", "").strip()
        if not self.client_id or not self.client_secret or not self.researcher_role_id:
            raise ProvisioningUnavailable("账号开通服务尚未配置，请联系系统运维人员。")
        self.client = httpx.Client(timeout=12, transport=transport)
        self.token: str | None = None

    def __enter__(self) -> "KeycloakUserAdmin":
        return self

    def __exit__(self, *_: object) -> None:
        self.client.close()

    @property
    def users_path(self) -> str:
        return f"/admin/realms/{quote(self.realm, safe='')}/users"

    def _access_token(self) -> str:
        if self.token:
            return self.token
        try:
            response = self.client.post(
                f"{self.base_url}/realms/{quote(self.realm, safe='')}/protocol/openid-connect/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
            )
            response.raise_for_status()
            self.token = response.json()["access_token"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise ProvisioningError("账号开通服务认证失败，请联系系统运维人员。") from exc
        return self.token

    def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        try:
            response = self.client.request(
                method,
                f"{self.base_url}{path}",
                headers={"Authorization": f"Bearer {self._access_token()}"},
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise ProvisioningError("身份服务暂不可用，请稍后重试。") from exc
        if response.status_code == 409:
            raise ProvisioningConflict("该登录账号已存在，请使用其他账号。")
        if response.status_code in (401, 403):
            raise ProvisioningError("账号开通服务权限不足，请联系系统运维人员。")
        if response.is_error:
            raise ProvisioningError("身份服务未完成账号操作，请联系系统运维人员。")
        return response

    def find_user(self, username: str) -> dict | None:
        response = self._request("GET", self.users_path, params={"username": username, "exact": "true"})
        return next((item for item in response.json() if item.get("username") == username), None)

    def _user_path(self, user_id: str) -> str:
        return f"{self.users_path}/{quote(user_id, safe='')}"

    def create_researcher(self, username: str, display_name: str, temporary_password: str) -> str:
        """Create a disabled user first; enable only after role and password exist."""
        response = self._request("POST", self.users_path, json={
            "username": username,
            "firstName": display_name,
            "enabled": False,
            "requiredActions": ["UPDATE_PASSWORD"],
        })
        location = response.headers.get("Location", "")
        user_id = urlsplit(location).path.rstrip("/").split("/")[-1] if location else ""
        if not user_id or user_id == "users":
            created = self.find_user(username)
            user_id = str(created.get("id", "")) if created else ""
        if not user_id:
            raise ProvisioningError("身份服务未返回新账号标识，请联系系统运维人员核查。")
        try:
            self._request("POST", f"{self._user_path(user_id)}/role-mappings/realm", json=[{
                "id": self.researcher_role_id, "name": "researcher",
            }])
            self._request("PUT", f"{self._user_path(user_id)}/reset-password", json={
                "type": "password", "value": temporary_password, "temporary": True,
            })
            self._request("PUT", self._user_path(user_id), json={"enabled": True})
        except (ProvisioningError, ProvisioningConflict, KeyError, ValueError) as exc:
            # This ID was just created by this request.  A failed compensation
            # leaves it disabled instead of granting a half-provisioned login.
            try:
                self._request("DELETE", self._user_path(user_id))
            except (ProvisioningError, ProvisioningConflict):
                pass
            raise ProvisioningError("账号开通未完成，请联系系统运维人员核查。") from exc
        return user_id

    def delete_new_user(self, user_id: str) -> None:
        """Compensate only a user created by the same failed application request."""
        self._request("DELETE", self._user_path(user_id))

    def set_enabled(self, user_id: str, username: str, enabled: bool) -> None:
        user = self._request("GET", self._user_path(user_id)).json()
        if user.get("username") != username:
            raise ProvisioningError("账号身份不一致，已拒绝修改。")
        self._request("PUT", self._user_path(user_id), json={"enabled": enabled})
        if not enabled:
            # Logout invalidates refresh sessions.  The application account
            # gate separately rejects already-issued access tokens.
            try:
                self._request("POST", f"{self._user_path(user_id)}/logout")
            except ProvisioningError:
                pass
