"""Retired feature fails closed; shared datasets and rice SQL remain available."""
import os
from pathlib import Path
import unittest
import uuid
from unittest.mock import MagicMock

from pydantic import ValidationError
from sqlalchemy import create_engine, text

from app.published_data_query import (
    PublishedDataQuery, StructuredQueryRequest, execute_published_data_query,
    field_catalog_for_planner, template_catalog,
)


class RootRetirementTests(unittest.TestCase):
    def test_stale_scope_rejected_at_all_request_boundaries(self):
        from app.main import ResearchStructuredQueryRequest
        for model in (PublishedDataQuery, StructuredQueryRequest, ResearchStructuredQueryRequest):
            with self.assertRaises(ValidationError):
                model(scope="root_phenotype")

    def test_mutated_query_rejected_before_sql(self):
        session = MagicMock()
        stale = PublishedDataQuery(variety_ids=["example"]).model_copy(update={"scope": "root_phenotype"})
        with self.assertRaises(ValidationError):
            execute_published_data_query(session, stale, "example-project")
        session.execute.assert_not_called()

    def test_sql_and_model_metadata_contain_no_retired_feature(self):
        from app.main import Base, LEGACY_INTAKE_TEMPLATE_CODES, public_standard_field_catalog, TRAITS
        self.assertNotIn("root_phenotype_observation", Base.metadata.tables)
        self.assertEqual(LEGACY_INTAKE_TEMPLATE_CODES, frozenset({"rice_data_center"}))
        datasets = public_standard_field_catalog()["datasets"]
        self.assertEqual([d["scope"] for d in datasets], ["rice_phenotype"])
        self.assertEqual(len(datasets[0]["fields"]), len(TRAITS) + 1)
        self.assertTrue(all(f["scope"] == "rice_phenotype" for f in field_catalog_for_planner(TRAITS)))
        self.assertEqual({t["code"] for t in template_catalog()},
                         {"phenotype_by_variety", "phenotype_by_trait", "phenotype_filter"})
        self.assertTrue(all("root_phenotype" not in t["sql"] for t in template_catalog()))

    def test_rice_query_keeps_published_and_project_boundaries(self):
        session = MagicMock()
        session.execute.return_value.mappings.return_value.all.return_value = []
        result = execute_published_data_query(session, PublishedDataQuery(trait_codes=["plant_height"]), "project-1")
        self.assertEqual(result.template_code, "phenotype_by_trait")
        statement, params = session.execute.call_args.args
        self.assertIn("p.publish_status = 'published'", str(statement))
        self.assertIn("p.project_id = :project_id", str(statement))
        self.assertEqual(params["project_id"], "project-1")


@unittest.skipUnless(os.environ.get("TEST_RICEDATA_DSN"), "Set TEST_RICEDATA_DSN")
class RetirementMigrationTests(unittest.TestCase):
    """Migration executes only against rollback-only isolated test tables."""
    def setUp(self):
        self.engine = create_engine(os.environ["TEST_RICEDATA_DSN"])
        self.connection = self.engine.connect()
        self.transaction = self.connection.begin()
        self.schema = "retirement_test_" + uuid.uuid4().hex
        self.connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        for ddl in (
            "data_template (id text PRIMARY KEY, template_code text, target_table text, current_version_id text)",
            "template_version (id text PRIMARY KEY, template_id text REFERENCES {s}.data_template(id))",
            "source_review (id text PRIMARY KEY, template_version_id text)",
            "field_change_request (id text PRIMARY KEY, template_id text REFERENCES {s}.data_template(id))",
            "phenotype_observation (id text PRIMARY KEY, trait_code text)",
            "variety_basic (id text PRIMARY KEY)",
            "root_phenotype_observation (id text PRIMARY KEY)",
        ):
            self.connection.execute(text(f'CREATE TABLE "{self.schema}".' + ddl.format(s=self.schema)))
        self.connection.execute(text(f"INSERT INTO {self.schema}.data_template VALUES ('root','rice_root_phenotype','root_phenotype_observation','v1'), ('rice','rice_data_center','phenotype_observation','v2')"))
        self.connection.execute(text(f"INSERT INTO {self.schema}.template_version VALUES ('v1','root'),('v2','rice')"))
        self.connection.execute(text(f"INSERT INTO {self.schema}.phenotype_observation VALUES ('height','plant_height')"))
        self.connection.execute(text(f"INSERT INTO {self.schema}.variety_basic VALUES ('shared')"))
        script = Path(__file__).parents[2] / "deploy/ynaas-193/migrations/002_retire_root_phenotype.sql"
        # Strip only transaction delimiters; outer test transaction always rolls back.
        self.sql = script.read_text(encoding="utf-8").replace("BEGIN;", "").replace("COMMIT;", "").replace("public.", self.schema + ".")

    def tearDown(self):
        self.transaction.rollback()
        self.connection.close()
        self.engine.dispose()

    def migrate(self):
        self.connection.exec_driver_sql(self.sql)

    def test_cleanup_is_idempotent_and_preserves_shared_data(self):
        self.migrate()
        self.migrate()
        self.assertIsNone(self.connection.scalar(text(f"SELECT to_regclass('{self.schema}.root_phenotype_observation')")))
        for table in ("data_template", "template_version", "variety_basic", "phenotype_observation"):
            self.assertEqual(self.connection.scalar(text(f"SELECT count(*) FROM {self.schema}.{table}")), 1)

    def test_unexpected_root_data_aborts_without_touching_templates(self):
        self.connection.execute(text(f"INSERT INTO {self.schema}.root_phenotype_observation VALUES ('unexpected')"))
        with self.assertRaises(Exception), self.connection.begin_nested():
            self.migrate()
        self.assertEqual(self.connection.scalar(text(f"SELECT count(*) FROM {self.schema}.data_template")), 2)

    def test_shared_view_dependency_aborts_instead_of_cascading(self):
        self.connection.execute(text(f"CREATE VIEW {self.schema}.dependent_view AS SELECT * FROM {self.schema}.root_phenotype_observation"))
        with self.assertRaises(Exception), self.connection.begin_nested():
            self.migrate()
        self.assertEqual(self.connection.scalar(text(f"SELECT count(*) FROM {self.schema}.data_template")), 2)

    def test_related_intake_aborts_without_deleting_shared_sources(self):
        self.connection.execute(text(f"INSERT INTO {self.schema}.source_review VALUES ('intake','v1')"))
        with self.assertRaises(Exception), self.connection.begin_nested():
            self.migrate()
        self.assertEqual(self.connection.scalar(text(f"SELECT count(*) FROM {self.schema}.source_review")), 1)
