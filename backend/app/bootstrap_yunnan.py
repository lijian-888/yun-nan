"""Safe, repeatable native-PostgreSQL bootstrap for the Yunnan deployment."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import make_url

from . import main


def _non_public_row_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    with main.migration_engine.connect() as connection:
        tables = connection.execute(text("""
            SELECT schemaname, tablename
            FROM pg_tables
            WHERE schemaname NOT IN ('pg_catalog', 'information_schema', 'public')
            ORDER BY schemaname, tablename
        """)).all()
        quote = connection.dialect.identifier_preparer.quote
        for schema_name, table_name in tables:
            qualified_name = f"{quote(schema_name)}.{quote(table_name)}"
            counts[f"{schema_name}.{table_name}"] = int(
                connection.exec_driver_sql(f"SELECT count(*) FROM {qualified_name}").scalar_one()
            )
    return counts


def _public_inventory() -> dict[str, Any]:
    with main.migration_engine.connect() as connection:
        table_rows = connection.execute(text("""
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public'
            ORDER BY tablename
        """)).scalars().all()
        view_rows = connection.execute(text("""
            SELECT viewname
            FROM pg_views
            WHERE schemaname = 'public'
            ORDER BY viewname
        """)).scalars().all()
        rls_tables = int(connection.execute(text("""
            SELECT count(*)
            FROM pg_class AS class
            JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
            WHERE namespace.nspname = 'public'
              AND class.relkind = 'r'
              AND class.relrowsecurity
        """)).scalar_one())
        rls_policies = int(connection.execute(text("""
            SELECT count(*) FROM pg_policies WHERE schemaname = 'public'
        """)).scalar_one())
        demo_tables = (
            "variety_basic",
            "phenotype_observation",
            "trial_data_package",
            "breeding_material",
            "breeding_program",
        )
        quote = connection.dialect.identifier_preparer.quote
        demo_business_rows = {
            table_name: int(connection.exec_driver_sql(
                f"SELECT count(*) FROM public.{quote(table_name)}"
            ).scalar_one())
            for table_name in demo_tables
            if table_name in table_rows
        }
    return {
        "public_table_count": len(table_rows),
        "public_view_count": len(view_rows),
        "public_views": list(view_rows),
        "rls_table_count": rls_tables,
        "rls_policy_count": rls_policies,
        "demo_business_rows": demo_business_rows,
    }


def bootstrap(*, include_demo_data: bool, expected_database: str) -> dict[str, Any]:
    actual_database = make_url(main.MIGRATION_DATABASE_URL).database or ""
    if actual_database != expected_database:
        raise RuntimeError(
            f"Refusing to initialize database {actual_database!r}; expected {expected_database!r}."
        )

    before = _non_public_row_counts()
    main.initialize_database(include_demo_data=include_demo_data)
    after = _non_public_row_counts()
    if before != after:
        changed = sorted(set(before) | set(after))
        changed = [name for name in changed if before.get(name) != after.get(name)]
        raise RuntimeError(
            "Existing non-public table row counts changed: " + ", ".join(changed)
        )

    inventory = _public_inventory()
    if not include_demo_data and any(inventory["demo_business_rows"].values()):
        raise RuntimeError("Schema-only bootstrap unexpectedly created demo business rows.")
    return {
        "database": actual_database,
        "institution": main.INSTITUTION_NAME,
        "demo_data_enabled": include_demo_data,
        "non_public_table_count": len(before),
        "non_public_rows_preserved": sum(before.values()),
        **inventory,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expected-database",
        default=os.getenv("YUNNAN_EXPECTED_DATABASE", "ynaas_rice_ai"),
        help="Safety guard; bootstrap refuses any other database.",
    )
    parser.add_argument(
        "--include-demo-data",
        action="store_true",
        help="Explicitly import legacy demo rows (disabled by default).",
    )
    return parser.parse_args()


def main_cli() -> int:
    args = _parse_args()
    try:
        report = bootstrap(
            include_demo_data=args.include_demo_data,
            expected_database=args.expected_database,
        )
    except Exception as exc:
        # Do not print exception details: connection errors may embed a URL.
        print(f"Yunnan bootstrap failed ({type(exc).__name__}).", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
