"""Run explicitly before deployment; never changes the teacher source files."""
import json
from app.teacher_workbook_index import build_index

if __name__ == "__main__":
    result = build_index()
    print(json.dumps(result, ensure_ascii=False))
    if result["errors"]:
        raise SystemExit(1)
