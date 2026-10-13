"""Read saved spreadsheet cells without executing formulas or changing files."""
from __future__ import annotations

import csv
import posixpath
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from openpyxl.styles.numbers import BUILTIN_FORMATS, is_date_format
from openpyxl.utils.cell import coordinate_to_tuple, get_column_letter
from openpyxl.utils.datetime import from_excel

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


@dataclass
class SourceCell:
    text: str
    raw: str = ""
    formula: bool = False
    error: bool = False


@dataclass
class SourceSheet:
    name: str
    cells: dict[tuple[int, int], SourceCell]
    merges: list[tuple[int, int, int, int]]
    _row_merges: dict = field(default_factory=dict, repr=False)

    def at(self, row: int, col: int, *, merged: bool = False) -> SourceCell:
        cell = self.cells.get((row, col))
        if cell is None and merged:
            if row not in self._row_merges:
                self._row_merges[row] = [merge for merge in self.merges if merge[0] <= row <= merge[2]]
            for top, left, bottom, right in self._row_merges[row]:
                if top <= row <= bottom and left <= col <= right:
                    cell = self.cells.get((top, left))
                    break
        return cell or SourceCell("")


def address(row: int, col: int) -> str:
    return f"{get_column_letter(col)}{row}"


def _number_text(raw: str, fmt: str, epoch=None) -> str:
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return raw
    if is_date_format(fmt):
        try:
            date = from_excel(float(value), epoch=epoch) if epoch else from_excel(float(value))
            return date.isoformat(sep=" ") if hasattr(date, "date") else date.isoformat()
        except (ValueError, OverflowError):
            return raw
    section = fmt.split(";")[0]
    # Only apply explicit simple display precision. Never guess units/scales.
    simple = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.|[_*].', "", section).strip()
    if re.fullmatch(r"[#,0]+(?:\.[0#]+)?%?", simple):
        if "%" in simple:
            value *= 100
        decimals = len(simple.split(".")[1].rstrip("%")) if "." in simple else 0
        if re.fullmatch(r"0{2,}", simple):
            return f"{value:0{len(simple)}.0f}"
        return f"{value:.{decimals}f}" + ("%" if "%" in simple else "")
    if (fmt.casefold() == "general" or fmt == "@") and value == value.to_integral_value():
        return str(int(value))
    return raw


def _xlsx(path: Path):
    with ZipFile(path) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            with archive.open("xl/sharedStrings.xml") as stream:
                for _, node in ET.iterparse(stream, events=("end",)):
                    if node.tag == NS + "si":
                        strings.append("".join(t.text or "" for t in node.iter(NS + "t")))
                        node.clear()
        formats = {**BUILTIN_FORMATS}
        styles = []
        if "xl/styles.xml" in archive.namelist():
            style_root = ET.fromstring(archive.read("xl/styles.xml"))
            for item in style_root.findall(f"{NS}numFmts/{NS}numFmt"):
                formats[int(item.attrib["numFmtId"])] = item.attrib["formatCode"]
            styles = [formats.get(int(item.attrib.get("numFmtId", "0")), "General")
                      for item in style_root.findall(f"{NS}cellXfs/{NS}xf")]
        rels = {item.attrib["Id"]: item.attrib["Target"]
                for item in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
                if item.attrib.get("TargetMode") != "External"}
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        from openpyxl.utils.datetime import CALENDAR_MAC_1904, CALENDAR_WINDOWS_1900
        props = workbook.find(NS + "workbookPr")
        epoch = CALENDAR_MAC_1904 if props is not None and props.attrib.get("date1904") in {"1", "true"} else CALENDAR_WINDOWS_1900
        for item in workbook.findall(f"{NS}sheets/{NS}sheet"):
            target = rels[item.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]]
            member = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
            cells, merges = {}, []
            with archive.open(member) as stream:
                for _, node in ET.iterparse(stream, events=("end",)):
                    if node.tag == NS + "c":
                        row, col = coordinate_to_tuple(node.attrib["r"])
                        raw = node.findtext(NS + "v") or ""
                        kind = node.attrib.get("t", "n")
                        formula = node.find(NS + "f") is not None
                        if kind == "s":
                            value = strings[int(raw)] if raw else ""
                        elif kind == "inlineStr":
                            value = "".join(t.text or "" for t in node.iter(NS + "t"))
                        elif kind == "n" and raw:
                            style = int(node.attrib.get("s", "0"))
                            value = _number_text(raw, styles[style] if style < len(styles) else "General", epoch)
                        else:
                            value = raw
                        if value or formula:
                            cells[(row, col)] = SourceCell(value, raw, formula, kind == "e")
                        node.clear()
                    elif node.tag == NS + "mergeCell":
                        start, end = node.attrib["ref"].split(":")
                        top, left = coordinate_to_tuple(start)
                        bottom, right = coordinate_to_tuple(end)
                        merges.append((top, left, bottom, right))
                        node.clear()
                    elif node.tag == NS + "row":
                        node.clear()
            yield SourceSheet(item.attrib["name"], cells, merges)


def _xls(path: Path):
    import xlrd
    book = xlrd.open_workbook(str(path), on_demand=True, formatting_info=True)
    try:
        for name in book.sheet_names():
            sheet = book.sheet_by_name(name)
            cells = {}
            for row in range(sheet.nrows):
                for col, cell in enumerate(sheet.row(row)):
                    if cell.ctype in {xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK}:
                        continue
                    raw = str(cell.value)
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        value = xlrd.xldate_as_datetime(cell.value, book.datemode).isoformat(sep=" ")
                    elif cell.ctype == xlrd.XL_CELL_NUMBER:
                        fmt = book.format_map[book.xf_list[cell.xf_index].format_key].format_str
                        value = _number_text(raw, fmt)
                    elif cell.ctype == xlrd.XL_CELL_ERROR:
                        value = xlrd.error_text_from_code.get(cell.value, "#ERROR")
                    else:
                        value = raw
                    cells[(row + 1, col + 1)] = SourceCell(value, raw, error=cell.ctype == xlrd.XL_CELL_ERROR)
            merges = [(r1 + 1, c1 + 1, r2, c2) for r1, r2, c1, c2 in sheet.merged_cells]
            yield SourceSheet(name, cells, merges)
            book.unload_sheet(name)
    finally:
        book.release_resources()


def read_source_workbook(path: Path):
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        yield from _xlsx(path)
    elif suffix == ".xls":
        # Some exported .xls files actually contain OOXML.
        with path.open("rb") as stream:
            is_zip = stream.read(2) == b"PK"
        yield from _xlsx(path) if is_zip else _xls(path)
    elif suffix in {".csv", ".tsv"}:
        data = path.read_bytes()
        try:
            content = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            content = data.decode("gb18030")
        rows = csv.reader(content.splitlines(), delimiter="\t" if suffix == ".tsv" else ",")
        cells = {(r, c): SourceCell(value, value) for r, row in enumerate(rows, 1)
                 for c, value in enumerate(row, 1) if value}
        yield SourceSheet("数据", cells, [])
    else:
        raise ValueError("仅对 Excel/CSV/TSV 表格建立数值索引。")
