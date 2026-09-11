"""Bounded, read-only access to the existing Yunnan rice reference schemas.

The model never supplies SQL.  This module recognizes a narrow set of intents,
executes fixed parameterized templates, and exposes only small evidence bundles
to the research agent.
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


REFERENCE_TABLES: dict[str, tuple[str, ...]] = {
    "ricedata": (
        "rice_variety",
        "rice_variety_approval",
        "rice_pedigree_snapshot",
        "rice_pedigree_node",
        "rice_pedigree_edge",
        "rice_gene",
        "rice_gene_symbol",
        "rice_gene_search_term",
        "rice_gene_ncbi_xref",
    ),
    "ncbi": (
        "gene",
        "gene_alias",
        "gene_annotation",
        "gene_go_annotation",
    ),
}

_VARIETY_MARKERS = ("品种", "材料", "审定", "系谱", "亲本", "父本", "母本", "选育", "栽培", "产量", "抗性")
_PEDIGREE_MARKERS = ("系谱", "亲本", "父本", "母本", "杂交组合", "亲缘")
_GENE_MARKERS = ("基因", "位点", "染色体", "注释", "功能", "qtl", "locus", "gene", "symbol", "go ")
_GENE_TOKEN = re.compile(r"(?<![A-Za-z0-9_.-])([A-Za-z][A-Za-z0-9_.-]{1,39})(?![A-Za-z0-9_.-])")
_GENE_STOP_WORDS = {
    "and", "gene", "genes", "go", "locus", "qtl", "rice", "symbol", "the",
}


def _reference_intents(question: str) -> tuple[bool, bool, bool]:
    normalized = (question or "").strip().lower()
    variety = any(marker in normalized for marker in _VARIETY_MARKERS)
    pedigree = any(marker in normalized for marker in _PEDIGREE_MARKERS)
    gene = any(marker in normalized for marker in _GENE_MARKERS)
    return variety or pedigree, pedigree, gene


def _gene_terms(question: str) -> list[str]:
    terms: list[str] = []
    for match in _GENE_TOKEN.finditer(question or ""):
        value = match.group(1).strip().lower()
        if value in _GENE_STOP_WORDS or value in terms:
            continue
        terms.append(value)
    return terms[:12]


def ensure_reference_read_access(session: Session, app_role: str) -> None:
    """Grant the API role SELECT on the explicit reference whitelist only."""
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", app_role):
        raise RuntimeError("Application database role is not a safe PostgreSQL identifier")
    for schema, table_names in REFERENCE_TABLES.items():
        schema_exists = session.scalar(
            text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :schema"),
            {"schema": schema},
        )
        if not schema_exists:
            continue
        session.execute(text(f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA {schema} FROM {app_role}"))
        session.execute(text(f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA {schema} FROM {app_role}"))
        session.execute(text(f"REVOKE ALL PRIVILEGES ON SCHEMA {schema} FROM {app_role}"))
        session.execute(text(f"GRANT USAGE ON SCHEMA {schema} TO {app_role}"))
        for table_name in table_names:
            relation = session.scalar(
                text("SELECT to_regclass(:relation)"),
                {"relation": f"{schema}.{table_name}"},
            )
            if relation:
                session.execute(text(f"GRANT SELECT ON TABLE {schema}.{table_name} TO {app_role}"))


def _rows(session: Session, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [dict(row) for row in session.execute(text(sql), params or {}).mappings().all()]


def _variety_evidence(session: Session, question: str, include_pedigree: bool) -> dict[str, Any]:
    varieties = _rows(session, """
        SELECT variety_id, source_variety_id, variety_name, trial_names, former_names,
               variety_type, parentage_text, breeder_text, applicant_text, source_url
        FROM ricedata.rice_variety
        WHERE variety_name IS NOT NULL
          AND (
              strpos(lower(:question), lower(variety_name)) > 0
              OR EXISTS (
                  SELECT 1 FROM unnest(COALESCE(trial_names, ARRAY[]::text[]) ||
                                       COALESCE(former_names, ARRAY[]::text[])) AS alias_name
                  WHERE alias_name <> '' AND strpos(lower(:question), lower(alias_name)) > 0
              )
          )
        ORDER BY char_length(variety_name) DESC, variety_id
        LIMIT 8
    """, {"question": question})
    total = session.scalar(text("SELECT count(*) FROM ricedata.rice_variety")) or 0
    if not varieties:
        varieties = _rows(session, """
            SELECT variety_id, source_variety_id, variety_name, trial_names, former_names,
                   variety_type, parentage_text, breeder_text, applicant_text, source_url
            FROM ricedata.rice_variety
            ORDER BY variety_id
            LIMIT 10
        """)
    variety_ids = [row["variety_id"] for row in varieties]
    approvals = _rows(session, """
        SELECT variety_id, approval_no, approval_year, approval_type, approval_region,
               approval_authority, applicant_text, breeder_text, variety_source_text,
               variety_type_text, yield_performance_text, cultivation_text,
               suitable_area_text, approval_opinion_text, info_source
        FROM ricedata.rice_variety_approval
        WHERE variety_id = ANY(CAST(:variety_ids AS bigint[]))
        ORDER BY variety_id, approval_year DESC NULLS LAST, approval_id
        LIMIT 24
    """, {"variety_ids": variety_ids}) if variety_ids else []

    pedigrees: list[dict[str, Any]] = []
    if include_pedigree and variety_ids:
        pedigrees = _rows(session, """
            WITH RECURSIVE latest_snapshot AS (
                SELECT DISTINCT ON (variety_id)
                       pedigree_snapshot_id, variety_id, source_url, fully_expanded,
                       node_count, edge_count, max_depth
                FROM ricedata.rice_pedigree_snapshot
                WHERE variety_id = ANY(CAST(:variety_ids AS bigint[]))
                ORDER BY variety_id, crawled_at DESC, pedigree_snapshot_id DESC
            ), walk AS (
                SELECT s.variety_id, s.pedigree_snapshot_id, s.source_url,
                       s.fully_expanded, n.pedigree_node_id, n.material_name,
                       n.node_type, n.linked_variety_id, ARRAY[n.pedigree_node_id]::bigint[] AS path,
                       0 AS depth, NULL::varchar AS parent_role, NULL::varchar AS child_name
                FROM latest_snapshot s
                JOIN ricedata.rice_pedigree_node n
                  ON n.pedigree_snapshot_id = s.pedigree_snapshot_id AND n.is_root
              UNION ALL
                SELECT w.variety_id, w.pedigree_snapshot_id, w.source_url,
                       w.fully_expanded, parent.pedigree_node_id, parent.material_name,
                       parent.node_type, parent.linked_variety_id,
                       w.path || parent.pedigree_node_id, w.depth + 1,
                       edge.parent_role, child.material_name
                FROM walk w
                JOIN ricedata.rice_pedigree_edge edge
                  ON edge.pedigree_snapshot_id = w.pedigree_snapshot_id
                 AND edge.child_node_id = w.pedigree_node_id
                JOIN ricedata.rice_pedigree_node parent
                  ON parent.pedigree_node_id = edge.parent_node_id
                JOIN ricedata.rice_pedigree_node child
                  ON child.pedigree_node_id = edge.child_node_id
                WHERE w.depth < 4 AND NOT parent.pedigree_node_id = ANY(w.path)
            )
            SELECT variety_id, source_url, fully_expanded, depth, child_name,
                   material_name, parent_role, node_type, linked_variety_id
            FROM walk
            ORDER BY variety_id, depth, material_name
            LIMIT 120
        """, {"variety_ids": variety_ids})
    return {
        "catalog_total": int(total),
        "match_mode": "question_exact_name_or_alias" if any(
            (row.get("variety_name") or "").lower() in question.lower() for row in varieties
        ) else "bounded_catalog_sample",
        "varieties": varieties,
        "approvals": approvals,
        "pedigrees": pedigrees,
    }


def _gene_evidence(session: Session, question: str) -> dict[str, Any]:
    terms = _gene_terms(question)
    total = session.scalar(text("SELECT count(*) FROM ricedata.rice_gene")) or 0
    if terms:
        genes = _rows(session, """
            WITH matches AS (
                SELECT DISTINCT rice_gene_id
                FROM ricedata.rice_gene_search_term
                WHERE normalized_term = ANY(CAST(:terms AS text[]))
                LIMIT 12
            )
            SELECT g.rice_gene_id, g.gene_name_annotation, g.gene_symbol_raw,
                   g.ncbi_locus_raw, g.source_url,
                   COALESCE(array_agg(DISTINCT s.symbol) FILTER (WHERE s.symbol IS NOT NULL), ARRAY[]::varchar[]) AS symbols,
                   x.ncbi_gene_id, x.ncbi_locus, x.ncbi_url, x.resolution_status,
                   n.primary_symbol, n.description, n.tax_name, n.gene_type,
                   n.chromosomes, n.locus_tag
            FROM matches m
            JOIN ricedata.rice_gene g ON g.rice_gene_id = m.rice_gene_id
            LEFT JOIN ricedata.rice_gene_symbol s ON s.rice_gene_id = g.rice_gene_id
            LEFT JOIN ricedata.rice_gene_ncbi_xref x ON x.rice_gene_id = g.rice_gene_id
            LEFT JOIN ncbi.gene n ON n.ncbi_gene_id = x.ncbi_gene_id
            GROUP BY g.rice_gene_id, x.ncbi_gene_id, x.ncbi_locus, x.ncbi_url,
                     x.resolution_status, n.primary_symbol, n.description, n.tax_name,
                     n.gene_type, n.chromosomes, n.locus_tag
            ORDER BY g.rice_gene_id
            LIMIT 12
        """, {"terms": terms})
    else:
        genes = _rows(session, """
            SELECT g.rice_gene_id, g.gene_name_annotation, g.gene_symbol_raw,
                   g.ncbi_locus_raw, g.source_url, ARRAY[]::varchar[] AS symbols,
                   NULL::bigint AS ncbi_gene_id, NULL::varchar AS ncbi_locus,
                   NULL::text AS ncbi_url, NULL::varchar AS resolution_status,
                   NULL::varchar AS primary_symbol, NULL::text AS description,
                   NULL::text AS tax_name, NULL::varchar AS gene_type,
                   ARRAY[]::text[] AS chromosomes, NULL::varchar AS locus_tag
            FROM ricedata.rice_gene g ORDER BY g.rice_gene_id LIMIT 8
        """)
    ncbi_ids = sorted({row["ncbi_gene_id"] for row in genes if row.get("ncbi_gene_id")})
    aliases = _rows(session, """
        SELECT ncbi_gene_id, alias_type, alias_text
        FROM ncbi.gene_alias
        WHERE ncbi_gene_id = ANY(CAST(:ncbi_ids AS bigint[]))
        ORDER BY ncbi_gene_id, alias_order
        LIMIT 48
    """, {"ncbi_ids": ncbi_ids}) if ncbi_ids else []
    annotations = _rows(session, """
        SELECT ncbi_gene_id, assembly_accession, assembly_name, annotation_name,
               genomic_accession_version, sequence_name, genomic_begin, genomic_end, orientation
        FROM ncbi.gene_annotation
        WHERE ncbi_gene_id = ANY(CAST(:ncbi_ids AS bigint[]))
        ORDER BY ncbi_gene_id, annotation_order, location_order
        LIMIT 24
    """, {"ncbi_ids": ncbi_ids}) if ncbi_ids else []
    go_annotations = _rows(session, """
        SELECT ncbi_gene_id, aspect, go_id, go_name, evidence_code, qualifier, assigned_by
        FROM ncbi.gene_go_annotation
        WHERE ncbi_gene_id = ANY(CAST(:ncbi_ids AS bigint[]))
        ORDER BY ncbi_gene_id, go_id
        LIMIT 48
    """, {"ncbi_ids": ncbi_ids}) if ncbi_ids else []
    return {
        "catalog_total": int(total),
        "normalized_query_terms": terms,
        "match_mode": "exact_normalized_identifier" if terms else "bounded_catalog_sample",
        "genes": genes,
        "aliases": aliases,
        "annotations": annotations,
        "go_annotations": go_annotations,
    }


def build_ynaas_database_evidence(session: Session, question: str) -> tuple[str, list[dict[str, Any]]]:
    """Return model context and UI cards for relevant existing database data."""
    variety_requested, pedigree_requested, gene_requested = _reference_intents(question)
    if not (variety_requested or gene_requested):
        return "", []
    payload: dict[str, Any] = {
        "source": "ynaas_existing_postgresql",
        "access": "read_only_fixed_parameterized_templates",
        "question": question.strip(),
        "limitations": [
            "Results are bounded and may be a catalog sample when no exact identifier is present.",
            "Pedigree traversal is limited to four generations and 120 nodes.",
            "Absence from this evidence bundle does not prove absence from the complete database.",
        ],
    }
    cards: list[dict[str, Any]] = []
    if variety_requested:
        payload["variety_and_pedigree"] = _variety_evidence(session, question, pedigree_requested)
        result = payload["variety_and_pedigree"]
        cards.append({
            "priority": 1,
            "type": "ynaas_existing_database",
            "title": "云南既有品种与系谱数据库",
            "detail": f"只读固定模板；本轮返回 {len(result['varieties'])} 个品种、{len(result['approvals'])} 条审定信息、{len(result['pedigrees'])} 个系谱节点。",
        })
    if gene_requested:
        payload["genes"] = _gene_evidence(session, question)
        result = payload["genes"]
        cards.append({
            "priority": 1,
            "type": "ynaas_existing_database",
            "title": "云南既有水稻基因数据库",
            "detail": f"只读固定模板；本轮返回 {len(result['genes'])} 条基因记录、{len(result['go_annotations'])} 条 GO 注释。",
        })
    return json.dumps(payload, ensure_ascii=False, default=str), cards
