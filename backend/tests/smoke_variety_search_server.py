"""Read-only integration probe, using the deployed API's least-privilege role."""
import json
import os
from time import monotonic
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.variety_search import search_catalog, search_varieties, variety_detail, VarietySearchRequest

SessionLocal = sessionmaker(bind=create_engine(os.environ['DATABASE_URL']))


def main():
    with SessionLocal() as session:
        print(json.dumps(search_catalog(session), ensure_ascii=False))
        cases = [dict(keyword="南粳9212"), dict(keyword="宁9212", fuzzy=False), dict(keyword="D优130"),
                 dict(keyword="先农8号"), dict(region="云南", approval_year=2006), dict(keyword="不存在的品种ZX999"),
                 dict(page=2), dict(sort="latest"), dict(source="institute", has_phenotype=True),
                 dict(traits=[dict(trait_code="plant_height_cm", operator="gte", value=120)]),
                 dict(traits=[dict(trait_code="amylose_content_pct", operator="gte", value=15), dict(trait_code="amylose_content_pct", operator="lte", value=20)])]
        cases.extend([dict(traits=[dict(trait_code=code, operator="gte", value=value) for code, value in [
            ("plant_height_cm", 100), ("growth_duration_days", 100), ("seed_setting_rate_pct", 80),
            ("thousand_grain_weight_g", 20), ("amylose_content_pct", 15), ("yield_kg_per_mu", 500)]]),
            dict(keyword="D优130", region="福建", traits=[dict(trait_code="amylose_content_pct", operator="eq", value=24.1)]),
            dict(keyword="D优130", region="四川", traits=[dict(trait_code="amylose_content_pct", operator="eq", value=24.1)])])
        for case in cases:
            start = monotonic()
            result = search_varieties(session, VarietySearchRequest(**case))
            print(json.dumps({"query": case, "seconds": round(monotonic()-start, 3), "total": result['total'], "names": [r['name'] for r in result['records'][:3]]}, ensure_ascii=False))
            if not case.get('region') and case.get('keyword') in ('南粳9212', '宁9212', 'D优130', '先农8号'):
                assert result['total'] > 0
                detail = variety_detail(session, 'ricedata', str(result['records'][0]['id']))
                assert detail and detail['content'] and detail['evidence']
            if case.get('keyword') == 'D优130' and case.get('region'):
                assert result['total'] == (1 if case['region'] == '福建' else 0)
            if case.get('keyword') == '不存在的品种ZX999':
                assert result['total'] == 0
            if case.get('source') == 'institute':
                record = result['records'][0]
                detail = variety_detail(session, 'institute', record['id'])
                assert detail['observation_total'] > 0
        session.rollback()


if __name__ == '__main__':
    main()
