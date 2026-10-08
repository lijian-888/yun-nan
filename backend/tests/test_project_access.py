import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.main import (
    DEFAULT_PROJECT_ID,
    INSTITUTION_ID,
    Institution,
    PermissionAudit,
    PlatformAccount,
    PlatformAccountCreate,
    PlatformAccountUpdate,
    ProjectCreate,
    ProjectMember,
    ResearchProject,
    accessible_projects,
    app,
    create_platform_account,
    create_project,
    platform_context,
    record_permission_audit,
    require_approved_platform_account,
    resolve_project_access,
    update_platform_account,
)


class ProjectAccessTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        for table in (
            Institution.__table__,
            PlatformAccount.__table__,
            ResearchProject.__table__,
            ProjectMember.__table__,
            PermissionAudit.__table__,
        ):
            table.create(self.engine)
        self.session = Session(self.engine)
        self.session.add(Institution(
            id=INSTITUTION_ID,
            institution_code="YNAAS",
            institution_name="云南省农业科学院",
            status="active",
        ))
        self.session.add_all([
            PlatformAccount(
                username="researcher.one",
                display_name="科研人员一",
                business_role="researcher",
                institution_id=INSTITUTION_ID,
                active=True,
            ),
            PlatformAccount(
                username="processor.one",
                display_name="数据处理员一",
                business_role="data_processor",
                institution_id=INSTITUTION_ID,
                active=True,
            ),
            PlatformAccount(
                username="researcher.two",
                display_name="科研人员二",
                business_role="researcher",
                institution_id=INSTITUTION_ID,
                active=True,
            ),
            PlatformAccount(
                username="fieldadmin.one",
                display_name="字段管理员一",
                business_role="field_admin",
                institution_id=INSTITUTION_ID,
                active=True,
            ),
            ResearchProject(
                id=DEFAULT_PROJECT_ID,
                project_code="YNAAS-P1",
                project_name="院内工作区",
                institution_id=INSTITUTION_ID,
                status="active",
                created_by="test",
            ),
            ResearchProject(
                id="00000000-0000-4000-8000-000000000012",
                project_code="YNAAS-P2",
                project_name="课题二",
                institution_id=INSTITUTION_ID,
                status="active",
                created_by="test",
            ),
        ])
        self.session.commit()
        self.researcher = CurrentUser(
            id="subject-researcher-one",
            username="researcher.one",
            display_name="科研人员一",
            roles=frozenset({"researcher"}),
        )
        self.processor = CurrentUser(
            id="subject-processor-one",
            username="processor.one",
            display_name="数据处理员一",
            roles=frozenset({"data_processor"}),
        )
        self.field_admin = CurrentUser(
            id="subject-field-admin-one",
            username="fieldadmin.one",
            display_name="字段管理员一",
            roles=frozenset({"field_admin"}),
        )

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_researcher_without_membership_enters_institution_workspace(self):
        projects = accessible_projects(self.session, self.researcher)
        self.assertEqual([item.project_code for item in projects], ["YNAAS-P1"])
        second = CurrentUser(
            id="subject-researcher-two",
            username="researcher.two",
            display_name="科研人员二",
            roles=frozenset({"researcher"}),
        )
        self.assertEqual(resolve_project_access(self.session, second).id, DEFAULT_PROJECT_ID)

    def test_researcher_cannot_select_other_workspace(self):
        with self.assertRaises(HTTPException) as raised:
            resolve_project_access(
                self.session,
                self.researcher,
                "00000000-0000-4000-8000-000000000012",
            )
        self.assertEqual(raised.exception.status_code, 403)

    def test_data_processor_sees_only_institution_workspace(self):
        projects = accessible_projects(self.session, self.processor)
        self.assertEqual({item.project_code for item in projects}, {"YNAAS-P1"})

    def test_context_exposes_institution_and_user_without_project_selector(self):
        context = platform_context(self.researcher, self.session)
        self.assertEqual(context["institution"]["code"], "YNAAS")
        self.assertEqual(context["user"]["username"], "researcher.one")
        self.assertNotIn("projects", context)
        self.assertNotIn("active_project_id", context)

    def test_project_management_routes_are_not_exposed(self):
        paths = {route.path for route in app.routes}
        self.assertNotIn("/api/projects", paths)
        self.assertNotIn("/api/projects/{project_id}", paths)
        self.assertNotIn("/api/projects/{project_id}/members", paths)
        self.assertNotIn("/api/projects/{project_id}/members/{username}", paths)

    def test_disabled_account_cannot_enter_workspace(self):
        account = self.session.get(PlatformAccount, "researcher.two")
        account.active = False
        self.session.commit()
        second = CurrentUser(
            id="subject-researcher-two",
            username="researcher.two",
            display_name="科研人员二",
            roles=frozenset({"researcher"}),
        )
        with self.assertRaises(HTTPException) as raised:
            resolve_project_access(self.session, second)
        self.assertEqual(raised.exception.status_code, 403)

    def test_disabled_account_is_rejected_before_any_secured_api(self):
        account = self.session.get(PlatformAccount, "researcher.two")
        account.active = False
        self.session.commit()
        second = CurrentUser(
            id="subject-researcher-two", username="researcher.two",
            display_name="科研人员二", roles=frozenset({"researcher"}),
        )
        with patch("app.main.SessionLocal", side_effect=lambda: Session(self.engine)):
            with self.assertRaises(HTTPException) as raised:
                require_approved_platform_account(second)
        self.assertEqual(raised.exception.status_code, 403)

    def test_unapproved_keycloak_user_cannot_enter_workspace(self):
        unknown = CurrentUser(
            id="new-subject", username="unknown.researcher",
            display_name="未获批用户", roles=frozenset({"researcher"}),
        )
        with self.assertRaises(HTTPException) as raised:
            resolve_project_access(self.session, unknown)
        self.assertEqual(raised.exception.status_code, 403)

    def test_field_admin_creates_researcher_without_project_membership(self):
        class FakeIdentity:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def create_researcher(self, username, display_name, temporary_password):
                self_username = username
                assert self_username == "ynaas.newuser"
                assert display_name == "新科研人员"
                assert len(temporary_password) >= 24
                return "new-keycloak-subject"

        with patch("app.main.KeycloakUserAdmin", return_value=FakeIdentity()):
            response = create_platform_account(
                PlatformAccountCreate(username="YNAAS.NewUser", display_name="新科研人员"),
                self.field_admin, self.session,
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers["cache-control"], "no-store")
        account = self.session.get(PlatformAccount, "ynaas.newuser")
        self.assertEqual(account.keycloak_subject, "new-keycloak-subject")
        self.assertEqual(account.business_role, "researcher")
        self.assertFalse(self.session.query(ProjectMember).filter_by(username=account.username).count())
        audit = self.session.query(PermissionAudit).filter_by(action="account_created").one()
        self.assertNotIn("temporary_password", str(audit.after_state))

    def test_field_admin_deactivation_calls_identity_service(self):
        account = self.session.get(PlatformAccount, "researcher.two")
        account.keycloak_subject = "subject-researcher-two"
        self.session.commit()
        calls = []

        class FakeIdentity:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def set_enabled(self, subject, username, enabled):
                calls.append((subject, username, enabled))

        with patch("app.main.KeycloakUserAdmin", return_value=FakeIdentity()):
            update_platform_account(
                "researcher.two", PlatformAccountUpdate(active=False),
                self.field_admin, self.session,
            )
        self.assertEqual(calls, [("subject-researcher-two", "researcher.two", False)])
        self.assertFalse(account.active)

    def test_permission_changes_create_queryable_audit_records(self):
        record_permission_audit(
            self.session,
            self.processor,
            "project_member_added",
            "project_member",
            "membership-test",
            project_id="00000000-0000-4000-8000-000000000011",
            after={"username": "researcher.one", "member_role": "researcher"},
        )
        self.session.commit()
        audit = self.session.query(PermissionAudit).one()
        self.assertEqual(audit.action, "project_member_added")
        self.assertEqual(audit.after_state["username"], "researcher.one")

    def test_create_project_sets_rls_context_before_seeding_knowledge_folders(self):
        events: list[str] = []

        def set_knowledge_context(session: Session, user: CurrentUser) -> None:
            events.append("knowledge_context")
            session.info["knowledge_is_admin"] = "true"
            session.info["research_owner_id"] = user.id

        def set_active_project(session: Session, project_id: str) -> None:
            events.append("project_context")
            session.info["active_project_id"] = project_id

        def seed_folders(session: Session, project_id: str) -> None:
            events.append("seed_folders")
            self.assertEqual(session.info["knowledge_is_admin"], "true")
            self.assertEqual(session.info["research_owner_id"], self.field_admin.id)
            self.assertEqual(session.info["active_project_id"], project_id)

        with (
            patch("app.main._set_knowledge_context", side_effect=set_knowledge_context),
            patch("app.main._set_active_project", side_effect=set_active_project),
            patch("app.main.seed_public_knowledge_folders", side_effect=seed_folders),
        ):
            result = create_project(
                ProjectCreate(
                    project_code="ynaas-rls-test",
                    project_name="课题创建 RLS 回归测试",
                    description="验证新课题目录初始化之前已设置 RLS 上下文。",
                ),
                self.field_admin,
                self.session,
            )

        self.assertEqual(result["project_code"], "YNAAS-RLS-TEST")
        self.assertEqual(events, ["knowledge_context", "project_context", "seed_folders"])


if __name__ == "__main__":
    unittest.main()
