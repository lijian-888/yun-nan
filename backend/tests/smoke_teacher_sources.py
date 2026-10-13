"""Read-only real-file probe; writes only a caller-selected comparison report."""
import argparse
import json
from pathlib import Path

from app.teacher_file_query import TeacherFileQuery, query_teacher_records
from app.teacher_workbook_index import open_index, index_summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--comparison-output")
    args = parser.parse_args()
    cases = [
        ({"material":"HD218","metric":"株高","year":2024,"teacher":"黄平老师"}, "83", "H4"),
        ({"material":"DT1","metric":"株高","year":2023,"file":"2023勐海考种数据(1).xlsx"}, "96", "C3"),
        ({"material":"C1-83","metric":"株高","year":2023,"file":"2023勐海考种数据(1).xlsx"}, "96", "C3"),
        ({"material":"DT1","metric":"直链淀粉含量","year":2022,"file":"2023勐海考种数据(1).xlsx"}, "0.1408", "Q3"),
        ({"material":"DT1","metric":"直链淀粉含量","year":2023,"file":"2023勐海考种数据(1).xlsx"}, "0.1222", "R3"),
        ({"material":"BSDD1","metric":"千粒重","year":2025,"teacher":"袁老师","sheet":"1"}, "28.52", "I2"),
    ]
    for query, value, cell in cases:
        result = query_teacher_records(TeacherFileQuery(**query))
        assert result["status"] == "found", result
        assert len(result["records"]) == 1, result
        row = result["records"][0]
        assert row["value"] == value and row["cell"] == cell, row
        print(json.dumps({"query":query,"value":value,"cell":cell,"result":"PASS"},ensure_ascii=False))
    ambiguity = query_teacher_records(TeacherFileQuery(material="DT1",metric="株高",year=2024))
    assert ambiguity["status"] == "ambiguous" and not ambiguity["records"], ambiguity
    absent = query_teacher_records(TeacherFileQuery(material="不存在材料YNAAS999999",metric="株高",year=2024))
    assert absent["status"] == "not_found" and not absent["records"], absent
    print(json.dumps({"ambiguous_count":ambiguity["count"],"not_found":"PASS","catalog":index_summary()},ensure_ascii=False))
    if args.comparison_output:
        db = open_index()
        try:
            samples = [json.loads(row[0]) for row in db.execute("""SELECT body FROM (
                SELECT body, row_number() OVER (PARTITION BY file,sheet ORDER BY row_number,cell) AS position,
                count(*) OVER (PARTITION BY file,sheet) AS total FROM facts WHERE status IN ('source_value','formula_cached')
              ) WHERE position=1 OR position=total OR position=CAST(total/2 AS integer)""")]
        finally:
            db.close()
        Path(args.comparison_output).write_text(json.dumps(samples,ensure_ascii=False),encoding="utf-8")
        print(f"Independent-reader comparison samples: {len(samples)}")


if __name__ == "__main__":
    main()
