"""Rebuild supplemental facts for ALL RiceData approvals, in bounded batches.

Usage: python -m app.ricedata_trait_etl [--apply] [--batch-size 250]
Without --apply this is a read-only coverage audit. Set MIGRATION_DATABASE_URL
to a privileged PostgreSQL URL; the web application's read-only role is not
used for ETL. The command is idempotent and can be re-run after a source update.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row

from .ricedata_trait_facts import PARSER_VERSION, SOURCE_FIELDS, extract_narrative_facts


DDL = """
CREATE TABLE IF NOT EXISTS ricedata.rice_variety_narrative_fact (
    narrative_fact_id bigserial PRIMARY KEY,
    approval_id bigint NOT NULL REFERENCES ricedata.rice_variety_approval(approval_id),
    variety_id bigint NOT NULL REFERENCES ricedata.rice_variety(variety_id),
    trait_code varchar(80) NOT NULL,
    trait_name varchar(100) NOT NULL,
    value_numeric numeric,
    value_min numeric,
    value_max numeric,
    value_text varchar(200) NOT NULL,
    unit varchar(40) NOT NULL,
    qualifier varchar(30) NOT NULL DEFAULT '',
    source_field varchar(80) NOT NULL,
    source_start integer NOT NULL,
    source_excerpt text NOT NULL,
    source_text_hash char(64) NOT NULL,
    parser_version varchar(50) NOT NULL,
    review_status varchar(30) NOT NULL DEFAULT 'machine_extracted',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (approval_id, source_field, trait_code, source_start, parser_version)
);
CREATE INDEX IF NOT EXISTS ix_ricedata_narrative_variety_trait
  ON ricedata.rice_variety_narrative_fact(variety_id, trait_code, approval_id);
CREATE INDEX IF NOT EXISTS ix_ricedata_narrative_approval_trait
  ON ricedata.rice_variety_narrative_fact(approval_id, trait_code);
CREATE TABLE IF NOT EXISTS ricedata.rice_variety_narrative_parse_status (
    approval_id bigint PRIMARY KEY REFERENCES ricedata.rice_variety_approval(approval_id),
    variety_id bigint NOT NULL REFERENCES ricedata.rice_variety(variety_id),
    source_fields_scanned integer NOT NULL,
    fact_count integer NOT NULL,
    parse_status varchar(40) NOT NULL,
    source_text_hash char(64) NOT NULL,
    parser_version varchar(50) NOT NULL,
    processed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ricedata_narrative_status_variety
  ON ricedata.rice_variety_narrative_parse_status(variety_id, parse_status);
"""

INSERT = """
INSERT INTO ricedata.rice_variety_narrative_fact (
    approval_id, variety_id, trait_code, trait_name, value_numeric, value_min,
    value_max, value_text, unit, qualifier, source_field, source_start,
    source_excerpt, source_text_hash, parser_version, review_status
) VALUES (
    %(approval_id)s, %(variety_id)s, %(trait_code)s, %(trait_name)s,
    %(value_numeric)s, %(value_min)s, %(value_max)s, %(value_text)s,
    %(unit)s, %(qualifier)s, %(source_field)s, %(source_start)s,
    %(source_excerpt)s, %(source_text_hash)s, %(parser_version)s,
    %(review_status)s
) ON CONFLICT (approval_id, source_field, trait_code, source_start, parser_version)
DO NOTHING
"""

UPSERT_STATUS = """
INSERT INTO ricedata.rice_variety_narrative_parse_status (
    approval_id, variety_id, source_fields_scanned, fact_count, parse_status,
    source_text_hash, parser_version
) VALUES (
    %(approval_id)s, %(variety_id)s, %(source_fields_scanned)s,
    %(fact_count)s, %(parse_status)s, %(source_text_hash)s, %(parser_version)s
) ON CONFLICT (approval_id) DO UPDATE SET
    variety_id = EXCLUDED.variety_id,
    source_fields_scanned = EXCLUDED.source_fields_scanned,
    fact_count = EXCLUDED.fact_count,
    parse_status = EXCLUDED.parse_status,
    source_text_hash = EXCLUDED.source_text_hash,
    parser_version = EXCLUDED.parser_version,
    processed_at = now()
"""


def run(*, apply: bool, batch_size: int, variety_id: int | None = None) -> dict:
    dsn = os.environ.get("MIGRATION_DATABASE_URL", "").strip()
    if not dsn:
        raise SystemExit("MIGRATION_DATABASE_URL is required")
    # psycopg understands a PostgreSQL URL, not SQLAlchemy's +psycopg suffix.
    dsn = dsn.replace("postgresql+psycopg://", "postgresql://", 1)
    if urlparse(dsn).scheme not in {"postgresql", "postgres"}:
        raise SystemExit("Expected a PostgreSQL database URL")
    totals: Counter[str] = Counter()
    metric_totals: Counter[str] = Counter()
    seen_varieties: set[int] = set()
    last_id = 0
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        if apply:
            with conn.cursor() as cursor:
                cursor.execute(DDL)
            conn.commit()
        while True:
            with conn.cursor() as cursor:
                cursor.execute(
                    """SELECT approval_id, variety_id, yield_performance_text,
                              cultivation_text, suitable_area_text,
                              approval_opinion_text, variety_source_text
                       FROM ricedata.rice_variety_approval
                       WHERE approval_id > %s AND (%s::bigint IS NULL OR variety_id = %s)
                       ORDER BY approval_id LIMIT %s""",
                    (last_id, variety_id, variety_id, batch_size),
                )
                approvals = cursor.fetchall()
            if not approvals:
                break
            inserts: list[dict] = []
            status_rows: list[dict] = []
            approval_ids: list[int] = []
            for approval in approvals:
                last_id = approval["approval_id"]
                approval_ids.append(last_id)
                seen_varieties.add(approval["variety_id"])
                totals["approvals_scanned"] += 1
                text_parts: list[str] = []
                approval_fact_count = 0
                for field in SOURCE_FIELDS:
                    raw = approval[field]
                    if not raw:
                        continue
                    text_parts.append(f"{field}:{raw}")
                    totals["nonempty_source_fields"] += 1
                    fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                    for fact in extract_narrative_facts(field, raw):
                        fact.update({
                            "approval_id": last_id,
                            "variety_id": approval["variety_id"],
                            "source_text_hash": fingerprint,
                        })
                        inserts.append(fact)
                        approval_fact_count += 1
                        metric_totals[fact["trait_code"]] += 1
                status_rows.append({
                    "approval_id": last_id,
                    "variety_id": approval["variety_id"],
                    "source_fields_scanned": len(text_parts),
                    "fact_count": approval_fact_count,
                    "parse_status": "extracted" if approval_fact_count else (
                        "no_recognized_supplemental_numeric" if text_parts else "no_supplemental_source_text"),
                    "source_text_hash": hashlib.sha256("\n".join(text_parts).encode("utf-8")).hexdigest(),
                    "parser_version": PARSER_VERSION,
                })
            totals["facts_extracted"] += len(inserts)
            if apply:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """DELETE FROM ricedata.rice_variety_narrative_fact
                           WHERE approval_id = ANY(%s) AND parser_version = %s
                             AND review_status = 'machine_extracted'""",
                        (approval_ids, PARSER_VERSION),
                    )
                    if inserts:
                        cursor.executemany(INSERT, inserts)
                    cursor.executemany(UPSERT_STATUS, status_rows)
                conn.commit()
    return {**totals, "varieties_scanned": len(seen_varieties), "parser_version": PARSER_VERSION,
            "applied": apply, "by_trait": dict(metric_totals.most_common())}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write/rebuild machine-extracted facts")
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument("--variety-id", type=int)
    args = parser.parse_args()
    if args.batch_size < 1 or args.batch_size > 2000:
        parser.error("--batch-size must be between 1 and 2000")
    print(json.dumps(run(apply=args.apply, batch_size=args.batch_size, variety_id=args.variety_id), ensure_ascii=False))


if __name__ == "__main__":
    main()
