"""Direct, bounded searches over server-owned cultivar and shared-material data."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .ricedata_trait_facts import TRAITS, TRAIT_BY_CODE
from .ricedata_variety_overview import lookup_variety_overview
from .rice_query_presentation import UNIT_LABELS


REGIONS = ("国家", "云南", "浙江", "福建", "江西", "江苏", "安徽", "上海", "山东", "黑龙江", "吉林", "辽宁",
           "陕西", "山西", "甘肃", "宁夏", "新疆", "湖南", "河南", "湖北", "青海", "广东", "广西", "海南",
           "西藏", "四川", "贵州", "重庆", "河北", "内蒙古", "天津", "北京", "台湾", "香港", "澳门")
NUMERIC_TRAITS = {item.code: item for item in TRAITS}
NUMERIC_TRAITS.update({code: TRAIT_BY_CODE[code] for code in
    ("yield_kg_per_mu", "leaf_blast_grade", "panicle_blast_grade", "panicle_neck_blast_grade")})
OPS = {"gte": ">=", "gt": ">", "lte": "<=", "lt": "<", "eq": "="}


class TraitFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trait_code: str
    operator: Literal["gte", "gt", "lte", "lt", "eq"]
    value: float = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def known_trait(self):
        if self.trait_code not in NUMERIC_TRAITS:
            raise ValueError("不支持的性状筛选字段")
        return self


class VarietySearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source: Literal["ricedata", "institute"] = "ricedata"
    keyword: str = Field(default="", max_length=120)
    field: Literal["name", "alias", "identifier", "parentage", "breeder", "approval_no", "location"] = "name"
    fuzzy: bool = True
    rice_type: Literal["", "indica", "japonica", "mixed"] = ""
    kind: Literal["", "inbred", "hybrid", "sterile"] = ""
    glutinous: bool = False
    upland: bool = False
    approved_only: bool = False
    region: str = Field(default="", max_length=20)
    approval_year: int | None = Field(default=None, ge=1900, le=2200)
    traits: list[TraitFilter] = Field(default_factory=list, max_length=6)
    has_phenotype: bool = False
    has_genotype: bool = False
    confirmed_only: bool = False
    sort: Literal["name", "latest"] = "name"
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def source_fields(self):
        if self.region and self.region not in REGIONS:
            raise ValueError("请选择目录中的审定地区")
        if self.source == "institute":
            if self.field not in {"name", "alias", "identifier", "parentage", "location"}:
                raise ValueError("院内材料不支持该检索字段")
            if self.traits or self.region or self.approval_year or self.rice_type or self.kind or self.glutinous or self.upland or self.approved_only:
                raise ValueError("院内观测与公开审定资料须分别检索")
        elif self.field == "location" or self.has_phenotype or self.has_genotype or self.confirmed_only:
            raise ValueError("该筛选条件仅用于院内材料")
        return self


def _rows(session, sql, params=None):
    return [dict(row) for row in session.execute(text(sql), params or {}).mappings()]


def _pattern(value: str, fuzzy: bool) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%" if fuzzy else escaped


def _numeric_clause(items: list[TraitFilter], index: int, params: dict) -> str:
    # Bounds of one trait must match ONE observation, not two separate trials.
    params[f"trait{index}"] = items[0].trait_code
    for offset, item in enumerate(items):
        params[f"value{index}_{offset}"] = item.value
    if items[0].trait_code == "yield_kg_per_mu":
        comparison = " AND ".join(f"y.yield_kg_per_mu {OPS[item.operator]} :value{index}_{offset}" for offset, item in enumerate(items))
        return f"""a.approval_id IN (SELECT y.approval_id FROM agent_query.ricedata__rice_variety_yield_observation y
            WHERE y.review_status='accepted'
            AND {comparison})"""
    low, high = "COALESCE(f.value_min,f.value_numeric)", "COALESCE(f.value_max,f.value_numeric)"
    comparison = " AND ".join(
        f"({low}=:value{index}_{offset} AND {high}=:value{index}_{offset})" if item.operator == "eq"
        else f"{low if item.operator in {'gte','gt'} else high} {OPS[item.operator]} :value{index}_{offset}"
        for offset, item in enumerate(items))
    return f"""a.approval_id IN (SELECT f.approval_id FROM (
        SELECT s.approval_id,m.value_numeric,m.value_min,m.value_max
        FROM agent_query.ricedata__rice_variety_trait_measurement m
        JOIN agent_query.ricedata__rice_variety_trait_summary s ON s.trait_summary_id=m.trait_summary_id
        WHERE m.trait_code=:trait{index}
        UNION ALL
        SELECT n.approval_id,n.value_numeric,n.value_min,n.value_max
        FROM agent_query.ricedata__rice_variety_narrative_fact n
        WHERE n.trait_code=:trait{index}
          AND n.review_status IN ('machine_extracted','verified')
    ) f WHERE {comparison})"""


def compile_search(payload: VarietySearchRequest) -> tuple[str, str, dict]:
    """Identifiers/operators come only from constants; all user values are bound."""
    p = {"pattern": _pattern(payload.keyword, payload.fuzzy), "escape": "\\",
         "exact": payload.keyword, "limit": payload.page_size, "offset": (payload.page-1)*payload.page_size}
    clauses = []
    if payload.source == "institute":
        fields = {"name": "m.preferred_name", "identifier": "m.material_id", "parentage": "m.pedigree_normalized", "location": "m.locations"}
        alias = "EXISTS (SELECT 1 FROM agent_data.material_alias ma WHERE ma.material_id=m.material_id AND ma.raw_alias ILIKE :pattern ESCAPE :escape)"
        if payload.keyword:
            expression = fields.get(payload.field)
            clauses.append(alias if not expression else f"({expression} ILIKE :pattern ESCAPE :escape" +
                           (f" OR {alias}" if payload.field == "name" else "") + ")")
        for enabled, column in ((payload.has_phenotype, "has_phenotype"), (payload.has_genotype, "has_genotype")):
            if enabled:
                clauses.append(f"m.{column} IS TRUE")
        if payload.confirmed_only:
            clauses.append("m.needs_review IS FALSE")
        return "agent_query.core__material m", " AND ".join(clauses) or "TRUE", p

    fields = {"name": "v.variety_name", "identifier": "v.source_variety_id", "parentage": "v.parentage_text",
              "breeder": "COALESCE(v.breeder_text,'') || ' ' || COALESCE(v.applicant_text,'')"}
    # Some imported pages put aliases only inside the displayed name.
    alias = "EXISTS (SELECT 1 FROM unnest(COALESCE(v.trial_names,ARRAY[]::text[]) || COALESCE(v.former_names,ARRAY[]::text[])) alias_name WHERE alias_name ILIKE :pattern ESCAPE :escape)"
    inline_alias = """EXISTS (SELECT 1 FROM regexp_split_to_table(v.variety_name,'[（()）;；、]') WITH ORDINALITY AS names(part,position)
        WHERE position>1 AND btrim(part) ILIKE :pattern ESCAPE :escape)"""
    approval = []
    if payload.keyword:
        if payload.field == "approval_no":
            approval.append("a.approval_no ILIKE :pattern ESCAPE :escape")
        elif payload.field == "alias":
            clauses.append(f"({alias} OR {inline_alias})")
        else:
            expression = fields[payload.field]
            clause = f"({expression}) ILIKE :pattern ESCAPE :escape"
            if payload.field == "name":
                tokens = "" if payload.fuzzy else """ OR EXISTS (SELECT 1 FROM regexp_split_to_table(v.variety_name,'[（()）;；、]') AS part
                    WHERE btrim(part) ILIKE :pattern ESCAPE :escape)"""
                clause = f"({clause} OR {alias}{tokens})"
            if payload.field == "breeder":
                clause = f"({clause} OR EXISTS (SELECT 1 FROM agent_query.ricedata__rice_variety_approval b WHERE b.variety_id=v.variety_id AND (COALESCE(b.breeder_text,'') || ' ' || COALESCE(b.applicant_text,'')) ILIKE :pattern ESCAPE :escape))"
            clauses.append(clause)
    if payload.rice_type:
        p["rice_type"] = {"indica": "籼型%", "japonica": "粳型%", "mixed": "籼粳交%"}[payload.rice_type]
        clauses.append("v.variety_type LIKE :rice_type")
    for enabled, word in ((payload.glutinous, "糯"), (payload.upland, "旱")):
        if enabled:
            clauses.append(f"strpos(COALESCE(v.variety_type,''),'{word}')>0")
    if payload.kind:
        p["kind"] = {"inbred": "%常规%", "hybrid": "%杂交%", "sterile": "%不育系%"}[payload.kind]
        clauses.append("v.variety_type LIKE :kind")
    if payload.approved_only:
        approval.append("(a.approval_no IS NOT NULL OR a.approval_type IN ('审定','认定'))")
    if payload.region:
        p["region"] = payload.region + "%"
        approval.append("a.approval_region LIKE :region")
    if payload.approval_year:
        p["year"] = payload.approval_year
        approval.append("a.approval_year=:year")
    grouped = {}
    for item in payload.traits:
        grouped.setdefault(item.trait_code, []).append(item)
    approval.extend(_numeric_clause(items, index, p) for index, items in enumerate(grouped.values()))
    approval_where = " AND ".join(approval) or "TRUE"
    if approval:
        clauses.append(f"EXISTS (SELECT 1 FROM agent_query.ricedata__rice_variety_approval a WHERE a.variety_id=v.variety_id AND {approval_where})")
    p["approval_where"] = approval_where  # Internal SQL fragment, not a user-supplied string.
    return "agent_query.ricedata__rice_variety v", " AND ".join(clauses) or "TRUE", p


def search_varieties(session: Session, payload: VarietySearchRequest) -> dict:
    session.execute(text("SET LOCAL statement_timeout='8s'"))
    relation, where, params = compile_search(payload)
    total = session.scalar(text(f"SELECT count(*) FROM {relation} WHERE {where}"), params)
    if payload.source == "institute":
        order = "m.latest_year DESC NULLS LAST,m.preferred_name,m.material_id" if payload.sort == "latest" else "CASE WHEN lower(m.preferred_name)=lower(:exact) THEN 0 ELSE 1 END,m.preferred_name,m.material_id"
        records = _rows(session, f"""SELECT m.material_id AS id,m.preferred_name AS name,
            m.material_type,m.earliest_year,m.latest_year,m.pedigree_normalized AS parentage,m.locations,
            m.has_phenotype,m.has_genotype,m.needs_review,
            ARRAY(SELECT DISTINCT a.raw_alias FROM agent_data.material_alias a WHERE a.material_id=m.material_id ORDER BY a.raw_alias) AS aliases
            FROM {relation} WHERE {where} ORDER BY {order} LIMIT :limit OFFSET :offset""", params)
    else:
        approval_where = params["approval_where"]
        order = "ap.latest_year DESC NULLS LAST,v.variety_name,v.variety_id" if payload.sort == "latest" else "CASE WHEN lower(v.variety_name)=lower(:exact) THEN 0 ELSE 1 END,v.variety_name,v.variety_id"
        records = _rows(session, f"""SELECT v.variety_id AS id,v.source_variety_id AS source_id,v.variety_name AS name,
            v.variety_type,v.parentage_text AS parentage,v.breeder_text AS breeder,v.source_url,
            COALESCE(v.trial_names,ARRAY[]::text[]) || COALESCE(v.former_names,ARRAY[]::text[]) AS aliases,
            ap.approval_count,ap.latest_year,ap.regions,ap.approvals
            FROM {relation} LEFT JOIN LATERAL (
              SELECT count(*) AS approval_count,max(a.approval_year) AS latest_year,
                array_remove(array_agg(DISTINCT a.approval_region),NULL) AS regions,
                jsonb_agg(jsonb_build_object('id',a.approval_id,'year',a.approval_year,'region',a.approval_region,'number',a.approval_no)
                    ORDER BY a.approval_year DESC NULLS LAST,a.approval_id) AS approvals
              FROM agent_query.ricedata__rice_variety_approval a WHERE a.variety_id=v.variety_id AND {approval_where}
              AND (a.approval_no IS NOT NULL OR a.approval_year IS NOT NULL)
            ) ap ON TRUE WHERE {where} ORDER BY {order} LIMIT :limit OFFSET :offset""", params)
    return {"source": payload.source, "total": total, "page": payload.page, "page_size": payload.page_size,
            "has_more": payload.page*payload.page_size < total, "records": records,
            "query": payload.model_dump(), "condition_scope": "同一审定或资料记录" if payload.traits else None}


def search_catalog(session: Session) -> dict:
    session.execute(text("SET LOCAL statement_timeout='8s'"))
    regions = _rows(session, "SELECT approval_region AS region,count(*) AS count FROM agent_query.ricedata__rice_variety_approval WHERE approval_region IS NOT NULL GROUP BY approval_region")
    return {"sources": [
        {"code": "ricedata", "name": "国家水稻数据中心", "count": session.scalar(text("SELECT count(*) FROM agent_query.ricedata__rice_variety"))},
        {"code": "institute", "name": "院内共享材料", "count": session.scalar(text("SELECT count(*) FROM agent_query.core__material"))}],
        "regions": [{"name": region, "count": sum(item["count"] for item in regions if item["region"].startswith(region))}
                    for region in REGIONS if any(item["region"].startswith(region) for item in regions)],
        "approval_years": [row["year"] for row in _rows(session, "SELECT DISTINCT approval_year AS year FROM agent_query.ricedata__rice_variety_approval WHERE approval_year IS NOT NULL ORDER BY approval_year DESC")],
        "traits": [{"code": item.code, "name": item.name, "unit": item.unit} for item in NUMERIC_TRAITS.values()]}


def variety_detail(session: Session, source: str, identifier: str, page: int = 1) -> dict | None:
    session.execute(text("SET LOCAL statement_timeout='8s'"))
    if source == "ricedata":
        if not identifier.isdecimal() or not 0 < int(identifier) <= 2**63-1:
            return None
        records = _rows(session, """SELECT variety_id AS id,source_variety_id AS source_id,variety_name AS name,
            variety_type,parentage_text AS parentage,breeder_text AS breeder,applicant_text AS applicant,source_url,
            COALESCE(trial_names,ARRAY[]::text[]) || COALESCE(former_names,ARRAY[]::text[]) AS aliases
            FROM agent_query.ricedata__rice_variety WHERE variety_id=:id""", {"id": int(identifier)})
        if not records:
            return None
        overview = lookup_variety_overview(session, "全部表型数据", variety_id=int(identifier))
        return {"source": source, "record": records[0], "content": overview["content"], "evidence": overview["evidence"]}
    records = _rows(session, """SELECT material_id AS id,preferred_name AS name,pedigree_normalized AS parentage,
        locations,earliest_year,latest_year,has_phenotype,has_genotype,needs_review,
        ARRAY(SELECT DISTINCT a.raw_alias FROM agent_data.material_alias a WHERE a.material_id=m.material_id ORDER BY a.raw_alias) AS aliases
        FROM agent_query.core__material m WHERE material_id=:id""", {"id": identifier})
    if not records:
        return None
    total = session.scalar(text("SELECT count(*) FROM agent_data.phenotype WHERE material_id=:id"), {"id": identifier})
    observations = _rows(session, """SELECT raw_header,value_numeric,value_text,raw_value,unit,trial_year,
        observed_on,observation_location,quality_status,source_file_id,sheet_name,row_number
        FROM agent_data.phenotype WHERE material_id=:id
        ORDER BY trial_year NULLS LAST,observed_on NULLS LAST,phenotype_value_id LIMIT 50 OFFSET :offset""",
        {"id": identifier, "offset": (page-1)*50})
    for observation in observations:
        observation["unit"] = UNIT_LABELS.get(observation["unit"], observation["unit"])
        observation["quality_label"] = {"accepted": "通过数据规则校验（非专家确认）",
            "out_of_range": "超出预设范围，需复核"}.get(observation["quality_status"], "质量状态待核对")
    return {"source": source, "record": records[0], "observations": observations, "observation_total": total,
            "observation_page": page, "observation_has_more": page*50 < total}


def create_variety_search_router(require_user, get_session) -> APIRouter:
    router = APIRouter(prefix="/api/research/variety-search", dependencies=[Depends(require_user)])

    def guarded(call, session, *args):
        try:
            return call(session, *args)
        except SQLAlchemyError as exc:
            session.rollback()
            raise HTTPException(503, "服务器数据查询未完成，请稍后重试；这不表示数据库没有数据。") from exc

    @router.get("/catalog")
    def catalog(session: Session = Depends(get_session)):
        return guarded(search_catalog, session)

    @router.post("/query")
    def query(payload: VarietySearchRequest, session: Session = Depends(get_session)):
        return guarded(search_varieties, session, payload)

    @router.get("/{source}/{identifier}")
    def detail(source: Literal["ricedata", "institute"], identifier: str,
               page: int = Query(default=1, ge=1, le=10000), session: Session = Depends(get_session)):
        if len(identifier) > 120:
            raise HTTPException(422, "记录编号过长")
        result = guarded(variety_detail, session, source, identifier, page)
        if result is None:
            raise HTTPException(404, "未找到该来源中的对应记录。")
        return result

    return router
