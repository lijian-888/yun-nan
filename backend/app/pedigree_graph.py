"""Read-only RiceData pedigree snapshots. No inferred or institute relationships."""
from __future__ import annotations

import logging
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .variety_search import VarietySearchRequest, compile_search

logger = logging.getLogger(__name__)
GROUP_NAMES = {"indica": "籼稻", "japonica": "粳稻", "mixed": "籼粳交", "other": "其他 / 类型未记载"}
GROUP_SQL = """CASE WHEN strpos(COALESCE(v.variety_type,''),'籼粳')>0 THEN 'mixed'
    WHEN v.variety_type LIKE '籼%' THEN 'indica' WHEN v.variety_type LIKE '粳%' THEN 'japonica'
    ELSE 'other' END"""
LATEST_SQL = """SELECT DISTINCT ON (variety_id) variety_id,pedigree_snapshot_id,node_count,edge_count,max_depth,fully_expanded,crawled_at
    FROM agent_query.ricedata__rice_pedigree_snapshot WHERE source_verified ORDER BY variety_id,crawled_at DESC,pedigree_snapshot_id DESC"""


def rows(session, sql, params=None):
    return [dict(row) for row in session.execute(text(sql), params or {}).mappings()]


def public_url(value):
    if not value:
        return None
    parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password else None


def catalog(session):
    session.execute(text("SET LOCAL statement_timeout='8s'"))
    groups = rows(session, f"""WITH latest AS ({LATEST_SQL})
        SELECT {GROUP_SQL} AS code,count(*) AS count,
          count(*) FILTER(WHERE s.edge_count>0) AS with_pedigree
        FROM agent_query.ricedata__rice_variety v LEFT JOIN latest s USING(variety_id)
        GROUP BY 1""")
    by_code = {row["code"]: row for row in groups}
    groups = [{"code": code, "name": name, "count": by_code.get(code, {}).get("count", 0),
               "with_pedigree": by_code.get(code, {}).get("with_pedigree", 0)} for code, name in GROUP_NAMES.items()]
    return {"total": sum(g["count"] for g in groups), "with_pedigree": sum(g["with_pedigree"] for g in groups),
            "groups": groups, "source": "国家水稻数据中心", "scope": "目录分组不是亲缘关系；系谱仅展示已保存的来源记录。"}


def list_varieties(session, keyword="", group="", only_pedigree=False, page=1, page_size=30):
    session.execute(text("SET LOCAL statement_timeout='8s'"))
    _, where, params = compile_search(VarietySearchRequest(keyword=keyword, page=page, page_size=page_size))
    if group:
        where += f" AND ({GROUP_SQL})=:group"
        params["group"] = group
    if only_pedigree:
        where += " AND s.edge_count>0"
    relation = f"WITH latest AS ({LATEST_SQL})"
    join = "FROM agent_query.ricedata__rice_variety v LEFT JOIN latest s USING(variety_id)"
    total = session.scalar(text(f"{relation} SELECT count(*) {join} WHERE {where}"), params)
    records = rows(session, f"""{relation} SELECT v.variety_id AS id,v.variety_name AS name,v.variety_type,
        v.source_variety_id AS source_id,COALESCE(v.trial_names,ARRAY[]::text[]) || COALESCE(v.former_names,ARRAY[]::text[]) AS aliases,
        s.node_count,s.edge_count,s.max_depth,s.fully_expanded,
        CASE WHEN s.pedigree_snapshot_id IS NULL THEN 'pending_verification' ELSE 'verified' END AS pedigree_status
        {join} WHERE {where}
        ORDER BY CASE WHEN lower(v.variety_name)=lower(:exact) THEN 0 ELSE 1 END,v.variety_name,v.variety_id
        LIMIT :limit OFFSET :offset""", params)
    return {"records": records, "total": total, "page": page, "page_size": page_size,
            "has_more": page*page_size<total, "keyword": keyword, "group": group}


def serialize_graph(variety, snapshot, nodes, edges):
    """Snapshot node identities are retained: same names do NOT imply same material."""
    ids = {node["id"] for node in nodes}
    valid = [edge for edge in edges if edge["child_id"] in ids and edge["parent_id"] in ids]
    roots = [node["id"] for node in nodes if node["is_root"]]
    adjacency = {}
    for edge in valid:
        adjacency.setdefault(edge["child_id"], []).append(edge["parent_id"])
    reachable, pending = set(roots), list(roots)
    while pending:
        for parent in adjacency.get(pending.pop(), []):
            if parent not in reachable:
                reachable.add(parent)
                pending.append(parent)
    warnings = []
    if len(valid) != len(edges):
        warnings.append("部分来源连线的端点缺失，已排除这些无效连线，未补造节点。")
    if snapshot and not snapshot["fully_expanded"]:
        warnings.append("来源系谱尚未完整采集，一键展开仅展示当前已收录的内容。")
    if any(node.get("was_expandable") and node.get("expansion_succeeded") is not True for node in nodes):
        warnings.append("部分来源节点曾可展开但未成功采集，不把已收录内容称为完整祖先史。")
    if snapshot and (snapshot["node_count"] != len(nodes) or snapshot["edge_count"] != len(edges)):
        warnings.append("来源统计与实际保存的节点或连线数量不一致，请结合原始页面核对。")
    if nodes and not roots:
        warnings.append("来源未标注起点节点，不能可靠分层；可查看全部已存节点。")
    if len(reachable) < len(nodes):
        warnings.append("部分来源节点未连接到当前品种；一键展开时会保留展示，但不认定它们是该品种的祖先。")
    degree = {node_id: 0 for node_id in ids}
    for edge in valid:
        degree[edge["parent_id"]] += 1
    queue = [node_id for node_id, value in degree.items() if value == 0]
    visited = 0
    while queue:
        visited += 1
        for parent in adjacency.get(queue.pop(), []):
            degree[parent] -= 1
            if degree[parent] == 0:
                queue.append(parent)
    if visited < len(ids):
        warnings.append("来源关系中存在环路，图中保留原始连线供核对，不视为已确认的育种过程。")
    if not valid:
        warnings.append("未收录可展示的亲本关系，不表示该品种没有亲本。")
    for node in nodes:
        node["source_url"] = public_url(node.get("source_url"))
    variety["source_url"] = public_url(variety.get("source_url"))
    return {"variety": variety, "snapshot": snapshot, "nodes": nodes, "edges": valid, "root_ids": roots,
            "node_count": len(nodes), "edge_count": len(valid), "unconnected_count": len(nodes)-len(reachable),
            "warnings": warnings, "scope": "系谱层级不等于F1、F2选育世代。节点仅按来源身份保留，不按同名合并。"}


def pedigree_graph(session, variety_id):
    session.execute(text("SET LOCAL statement_timeout='8s'"))
    varieties = rows(session, """SELECT variety_id AS id,variety_name AS name,variety_type,parentage_text AS parentage,
        breeder_text AS breeder,source_variety_id AS source_id,source_url
        FROM agent_query.ricedata__rice_variety WHERE variety_id=:id""", {"id": variety_id})
    if not varieties:
        return None
    snapshots = rows(session, """SELECT pedigree_snapshot_id AS id,variety_id,node_count,edge_count,max_depth,
        fully_expanded,crawled_at,source_url FROM agent_query.ricedata__rice_pedigree_snapshot
        WHERE variety_id=:id AND source_verified ORDER BY crawled_at DESC,pedigree_snapshot_id DESC LIMIT 1""", {"id": variety_id})
    if not snapshots:
        result=serialize_graph(varieties[0], None, [], [])
        result["warnings"]=["该品种系谱正在按原站重新完整采集和校验，暂不展示未经核验的旧关系；不表示没有亲本。"]
        return result
    snapshot = snapshots[0]
    params = {"snapshot": snapshot["id"]}
    nodes = rows(session, """SELECT pedigree_node_id AS id,material_name AS name,node_type AS kind,
        linked_variety_id AS variety_id,generation_depth AS recorded_depth,is_root,was_expandable,expansion_succeeded,
        source_href AS source_url FROM agent_query.ricedata__rice_pedigree_node WHERE pedigree_snapshot_id=:snapshot
        ORDER BY generation_depth,pedigree_node_id LIMIT 5001""", params)
    edges = rows(session, """SELECT pedigree_edge_id AS id,child_node_id AS child_id,parent_node_id AS parent_id,
        parent_role AS role,display_order FROM agent_query.ricedata__rice_pedigree_edge WHERE pedigree_snapshot_id=:snapshot
        ORDER BY display_order NULLS LAST,pedigree_edge_id LIMIT 10001""", params)
    if len(nodes)>5000 or len(edges)>10000:
        raise HTTPException(409, "该品种系谱超过安全展示上限，本次未截断后冒充完整系谱。请联系管理员核对。")
    snapshot["source_url"] = public_url(snapshot["source_url"])
    return serialize_graph(varieties[0], snapshot, nodes, edges)


def create_pedigree_router(require_user, get_session):
    router = APIRouter(prefix="/api/research/pedigree", dependencies=[Depends(require_user)])

    def guarded(call, session, *args):
        try:
            return call(session, *args)
        except SQLAlchemyError as exc:
            session.rollback()
            logger.warning("RiceData pedigree query failed: %s", type(exc).__name__)
            raise HTTPException(503, "服务器系谱查询未完成，请稍后重试；不能据此认为系谱资料不存在。") from exc

    @router.get("/catalog")
    def get_catalog(session=Depends(get_session)):
        return guarded(catalog, session)

    @router.get("/varieties")
    def get_varieties(q: str=Query(default="",max_length=120), group: Literal["","indica","japonica","mixed","other"]="",
                      only_pedigree: bool=False, page: int=Query(default=1,ge=1,le=10000),
                      page_size: int=Query(default=30,ge=1,le=100), session=Depends(get_session)):
        return guarded(list_varieties, session, q.strip(), group, only_pedigree, page, page_size)

    @router.get("/varieties/{variety_id}/graph")
    def get_graph(variety_id: int, session=Depends(get_session)):
        if variety_id is None or not 0<variety_id<=2**63-1:
            raise HTTPException(422, "无效品种编号")
        result = guarded(pedigree_graph, session, variety_id)
        if result is None:
            raise HTTPException(404, "未找到国家水稻数据中心的对应品种。")
        return result

    return router
