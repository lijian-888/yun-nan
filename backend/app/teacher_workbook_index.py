"""Rebuildable source-cell index; no inferred cross-file material identities."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import unicodedata
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path

from .teacher_source_files import TEACHER_FOLDERS, _root, _resolve
from .teacher_workbook_reader import read_source_workbook, address

INDEX_VERSION = "teacher-cells-2"
SUFFIXES = {".xlsx", ".xls", ".csv", ".tsv"}
YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
IDENTITY = re.compile(r"^(?:20\d{2}(?:年)?|\d{2}年)?(?:材料|品种|品系|株系)?(?:名称|名|编号|号|原号|原编号)$|^(?:穗号|原始穗号|编辑后穗号|品种|材料|品系|name|cultivarname|accession|组合名称)$", re.I)
CONDITIONS = re.compile(r"地点|试验点|点位|基地|处理|重复|测定日期|观测日期|测量日期|试验年份|观测年份|测定年份|^年份$|季节|备注|世代")
METRICS = {
    "株高": ("株高", "平均株高", "plantheight"),
    "穗长": ("穗长", "平均穗长", "paniclelength"),
    "千粒重": ("千粒重", "千粒重量", "千粒质量"),
    "结实率": ("结实率",),
    "直链淀粉含量": ("直链淀粉含量", "直链淀粉", "amylose"),
    "蛋白质含量": ("蛋白质含量", "蛋白质", "蛋白含量"),
    "生育期": ("全生育期", "生育期", "生育天数"),
    "穗数": ("穗数", "有效穗数", "单株有效穗", "每丛穗数", "有效穗数/穴"),
    "每穗实粒数": ("每穗实粒数", "穗实粒数"),
    "实粒数": ("实粒数", "实粒", "总实粒数", "总实粒"),
    "空秕粒数": ("空秕数", "空瘪数", "空枇数", "空瘪粒", "总空粒", "总空瘪数"),
    "穗总粒数": ("穗总粒数", "穗粒数", "总粒数", "每穗粒数"),
    "实粒重": ("实粒重", "总实粒重", "单株考种"),
    "产量": ("产量", "亩产", "平均产量", "实际产量", "理论产量", "小区产量", "小区重量", "折合亩产", "全收", "收获"),
    "粒长": ("粒长",), "粒宽": ("粒宽",), "长宽比": ("长宽比",),
    "出糙率": ("出糙率", "糙米率"), "精米率": ("精米率",),
    "整精米率": ("整精米率",), "食味值": ("食味值",),
    "水分": ("水分", "水份"), "枝梗数": ("枝梗数",),
    "叶瘟": ("叶瘟", "叶瘟级别"), "穗瘟": ("穗瘟", "穗瘟级别", "yx穗瘟"),
    "稻瘟病": ("稻瘟病",), "抗倒伏": ("抗倒伏", "倒伏"),
}


def normalized(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value))).casefold()


def metric_code(header: str) -> str | None:
    text = normalized(header)
    # Header recognition is bounded; narrative descriptions are not measured columns.
    if len(text) > 45:
        return None
    text = YEAR.sub("", text).replace("年", "")
    text = re.sub(r"\([^)]*\)", "", text).strip("%％").rstrip("1234567890ⅠⅡⅢⅣⅤ")
    for code, aliases in METRICS.items():
        if text in aliases:
            return code
    return None


def generic_metric(header: str) -> str:
    return re.sub(r"\([^)]*\)", "", YEAR.sub("", normalized(header))).strip("%· ")


def _unit(header: str, value: str) -> str:
    if value.endswith("%"):
        return "%"
    label = normalized(header)
    # '千粒重' contains 粒 but does not state its unit. Only explicit units count.
    explicit = " ".join(re.findall(r"\(([^)]*)\)", label))
    if "%" in label:
        explicit += " %"
    unit = re.search(r"公斤/亩|千克/亩|kg/亩|kg/mu|厘米|毫米|cm|mm|kg|公斤|千克|(?<![a-z])g(?![a-z])|克|%|粒|天|(?<![a-z])d(?![a-z])|级", explicit)
    if not unit:
        return ""
    return {"厘米": "cm", "毫米": "mm", "公斤": "kg", "千克": "kg", "克": "g", "公斤/亩": "kg/亩", "千克/亩": "kg/亩", "d": "天"}.get(unit.group(), unit.group())


def index_path() -> Path:
    return Path(os.getenv("YNAAS_TEACHER_INDEX_PATH", str(Path(os.getenv("RESEARCH_STORAGE_DIR", "/data/research")) / "teacher-source-index.sqlite")))


def source_files(root: Path):
    for teacher in TEACHER_FOLDERS:
        folder = root / teacher
        if not folder.is_dir() or folder.is_symlink():
            continue
        for directory, dirs, files in os.walk(folder, followlinks=False):
            dirs[:] = [name for name in dirs if not name.startswith((".", "~$")) and not (Path(directory) / name).is_symlink()]
            for name in sorted(files):
                file = Path(directory) / name
                if not name.startswith((".", "~$")) and not file.is_symlink() and file.suffix.lower() in SUFFIXES:
                    yield file


def _unique_year(value: str):
    years = set(YEAR.findall(value))
    return int(next(iter(years))) if len(years) == 1 else None


def _sheet_records(sheet, relative: str):
    actual_rows = {}
    for (row, col), cell in sheet.cells.items():
        actual_rows.setdefault(row, {})[col] = cell.text.strip()
    row_numbers = sorted(actual_rows)
    max_col = max((c for _, c in sheet.cells), default=0)
    headers = None
    skip_until = 0
    count = 0
    for row in row_numbers:
        if row <= skip_until:
            continue
        # Data rows never need a full sheet-width header scan.
        possible_header = any(metric_code(value) for value in actual_rows[row].values())
        current = ({col: sheet.at(row, col, merged=True).text.strip() for col in range(1, max_col + 1)}
                   if possible_header else {})
        identity_cols = [col for col, value in current.items() if IDENTITY.fullmatch(normalized(value))]
        metrics = {col: metric_code(value) for col, value in current.items() if metric_code(value)}
        uncertain_identity = set()
        if metrics and not identity_cols and row <= 10:
            for col, label in current.items():
                if label:
                    continue
                samples = [sheet.at(r, col).text.strip() for r in row_numbers if row < r <= row + 10 and sheet.at(r, col).text.strip()]
                if len(samples) >= 2 and all(len(value) <= 80 and re.search(r"[A-Za-z]", value) and re.search(r"\d", value) for value in samples):
                    identity_cols.append(col)
                    uncertain_identity.add(col)
                    current[col] = f"无标题列 {address(row,col).rstrip(str(row))}（材料标识待确认）"
        if identity_cols and metrics:
            # Combine a following units/repetition subheader and merged parents.
            labels = dict(current)
            lower = {col: sheet.at(row + 1, col, merged=True).text.strip() for col in range(1, max_col + 1)}
            has_subheader = (not any(lower.get(col) for col in identity_cols) and any(
                re.fullmatch(r"[ⅠⅡⅢⅣⅤIVX12345]+|平均|均值|\(?cm\)?|\(?g\)?|\(?%\)?|20\d{2}年?", normalized(value), re.I)
                for col, value in lower.items() if col in metrics and value))
            if has_subheader:
                for col in labels:
                    if lower[col] and lower[col] != labels[col]:
                        labels[col] = f"{labels[col]} · {lower[col]}"
                skip_until = row + 1
            conditions = [col for col, value in current.items() if CONDITIONS.search(value)]
            # Once a measured table is established, retain its other explicitly
            # named columns too. Their original meanings are not remapped by AI.
            for col, label in current.items():
                if label and col not in identity_cols and col not in conditions and col not in metrics:
                    if normalized(label) not in {"序号", "组合", "世代或来源", "来源", "栽插要求"}:
                        metrics[col] = generic_metric(label)
            headers = (identity_cols, metrics, labels, conditions, row, uncertain_identity)
            continue
        if headers is None:
            continue
        identity_cols, metrics, labels, condition_cols, header_row, uncertain_identity = headers
        identities = [{"header": labels[col], "value": sheet.at(row, col, merged=True).text.strip(), "cell": address(row, col), "basis": "unlabeled" if col in uncertain_identity else "source_header"}
                      for col in identity_cols if sheet.at(row, col, merged=True).text.strip()]
        identities = [item for item in identities if len(item["value"]) <= 250 and not IDENTITY.fullmatch(normalized(item["value"]))]
        if not identities:
            continue
        context = {labels[col]: sheet.at(row, col, merged=True).text.strip() for col in condition_cols if sheet.at(row, col, merged=True).text.strip()}
        explicit_years = {int(year) for key, value in context.items()
                          if re.search(r"年份|测定日期|测量日期|观测日期", key) for year in YEAR.findall(value)}
        sheet_year = _unique_year(sheet.name)
        file_year = _unique_year(Path(relative).name)
        path_year = _unique_year(relative)
        for col, metric in metrics.items():
            label = labels[col]
            cell = sheet.at(row, col)
            column_year = _unique_year(label)
            year = column_year or (next(iter(explicit_years)) if len(explicit_years) == 1 else None) or sheet_year or file_year or path_year
            year_basis = "指标表头" if column_year else "行内年份/日期" if explicit_years else "工作表名称" if sheet_year else "文件名称" if file_year else "文件目录" if path_year else "未注明"
            if len(explicit_years) > 1:
                year, year_basis = None, "行内年份冲突"
            value = cell.text.strip()
            status = "error" if cell.error else "formula_uncached" if cell.formula and not value else "missing" if not value or value in {"无", "未测", "未测定", "-", "—", "/", "NA", "N/A"} else "formula_cached" if cell.formula else "source_value"
            fact_id = hashlib.sha256(f"{relative}\0{sheet.name}\0{address(row,col)}".encode()).hexdigest()[:24]
            yield {"id": fact_id, "file": relative, "sheet": sheet.name, "cell": address(row, col),
                   "row": row, "metric": metric, "header": label, "header_cell": address(header_row, col),
                   "identities": identities, "year": year, "year_basis": year_basis, "conditions": context,
                   "value": value, "raw_value": cell.raw, "unit": _unit(label, value), "status": status}
            count += 1


def build_index(root: Path | None = None, target: Path | None = None) -> dict:
    root = (root or _root()).resolve()
    target = target or index_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=target.name + ".", suffix=".building", dir=target.parent)
    os.close(fd)
    temporary = Path(name)
    files, sheets, facts, errors = 0, 0, 0, []
    with closing(sqlite3.connect(temporary)) as db:
        db.executescript("""
          CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE files(path TEXT PRIMARY KEY,sha256 TEXT,mtime_ns INTEGER,size INTEGER,status TEXT,detail TEXT);
          CREATE TABLE facts(id TEXT PRIMARY KEY,file TEXT,sheet TEXT,cell TEXT,row_number INTEGER,
            metric TEXT,year INTEGER,status TEXT,body TEXT);
          CREATE TABLE names(name TEXT,fact_id TEXT,PRIMARY KEY(name,fact_id));
          CREATE TABLE mentions(name TEXT,file TEXT,sheet TEXT,cell TEXT,PRIMARY KEY(name,file,sheet,cell));
          CREATE INDEX facts_scope ON facts(metric,year,file,sheet);
          CREATE INDEX names_lookup ON names(name);
        """)
        for file in source_files(root):
            relative = file.relative_to(root).as_posix()
            stat = file.stat()
            with file.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            files += 1
            try:
                for sheet in read_source_workbook(file):
                    sheets += 1
                    db.executemany("INSERT OR IGNORE INTO mentions VALUES(?,?,?,?)", [
                        (normalized(cell.text), relative, sheet.name, address(r, c))
                        for (r, c), cell in sheet.cells.items()
                        if cell.text.strip() and len(cell.text) <= 250 and not re.fullmatch(r"[\d\s./%+?—-]+", cell.text)
                        and not cell.error])
                    for fact in _sheet_records(sheet, relative):
                        fact["file_hash"] = digest
                        db.execute("INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?)", (fact["id"], relative, sheet.name, fact["cell"], fact["row"], fact["metric"], fact["year"], fact["status"], json.dumps(fact, ensure_ascii=False)))
                        db.executemany("INSERT OR IGNORE INTO names VALUES(?,?)", [(normalized(item["value"]), fact["id"]) for item in fact["identities"]])
                        facts += 1
                db.execute("INSERT INTO files VALUES(?,?,?,?,?,?)", (relative, digest, stat.st_mtime_ns, stat.st_size, "indexed", ""))
            except Exception as exc:
                # Keep an explicit failure in the catalog. It must never become 'no data'.
                db.execute("DELETE FROM names WHERE fact_id IN (SELECT id FROM facts WHERE file=?)", (relative,))
                db.execute("DELETE FROM facts WHERE file=?", (relative,))
                db.execute("DELETE FROM mentions WHERE file=?", (relative,))
                error = {"file": relative, "error": type(exc).__name__, "detail": str(exc)[:180]}
                errors.append(error)
                db.execute("INSERT INTO files VALUES(?,?,?,?,?,?)", (relative, digest, stat.st_mtime_ns, stat.st_size, "failed", error["detail"]))
        summary = {"version": INDEX_VERSION, "files": files, "sheets": sheets,
                   "facts": db.execute("SELECT count(*) FROM facts").fetchone()[0],
                   "files_with_measurement_headers": db.execute("SELECT count(DISTINCT file) FROM facts").fetchone()[0],
                   "sheets_with_measurement_headers": db.execute("SELECT count(*) FROM (SELECT file,sheet FROM facts GROUP BY file,sheet)").fetchone()[0],
                   "status_counts": dict(db.execute("SELECT status,count(*) FROM facts GROUP BY status")),
                   "errors": errors, "built_at": datetime.now(timezone.utc).isoformat()}
        db.executemany("INSERT INTO metadata VALUES(?,?)", [("version", INDEX_VERSION), ("summary", json.dumps(summary, ensure_ascii=False))])
        db.commit()
    os.replace(temporary, target)
    return summary


def open_index(target: Path | None = None):
    path = target or index_path()
    if not path.is_file():
        raise ValueError("老师原始文件检索索引尚未建立，暂不能核验表格数值。")
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    if db.execute("SELECT value FROM metadata WHERE key='version'").fetchone()[0] != INDEX_VERSION:
        db.close()
        raise ValueError("老师原始文件检索索引需要更新。")
    return db


def index_summary():
    with closing(open_index()) as db:
        return json.loads(db.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()[0])


def verify_manifest(db, root: Path | None = None):
    root = root or _root()
    actual = {file.relative_to(root).as_posix(): (file.stat().st_mtime_ns, file.stat().st_size) for file in source_files(root)}
    expected = {row["path"]: (row["mtime_ns"], row["size"]) for row in db.execute("SELECT * FROM files")}
    if actual != expected:
        raise ValueError("老师文件目录已变化，需更新检索索引后再核验数值。")


def verify_sources(facts: list[dict], root: Path | None = None):
    root = root or _root()
    grouped = {}
    for fact in facts:
        grouped.setdefault(fact["file"], []).append(fact)
    for relative, items in grouped.items():
        file = _resolve(root, relative, file_only=True)
        with file.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if any(digest != fact["file_hash"] for fact in items):
            raise ValueError("原文件已变化，当前索引与原文件不一致，请更新索引后再查询。")
        pending = {(fact["sheet"], fact["cell"]): fact for fact in items}
        for sheet in read_source_workbook(file):
            for key, fact in list(pending.items()):
                if sheet.name != fact["sheet"]:
                    continue
                from openpyxl.utils.cell import coordinate_to_tuple
                row, col = coordinate_to_tuple(fact["cell"])
                if sheet.at(row, col).text.strip() != fact["value"]:
                    raise ValueError("索引数值与原单元格不一致，未返回数值。")
                del pending[key]
        if pending:
            raise ValueError("原工作表已不存在，未返回数值。")
