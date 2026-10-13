import assert from "node:assert/strict";
import { test } from "node:test";
import * as XLSX from "xlsx";
import { readWorkbook, sheetDetails, cellText, visibleSheetWindow } from "../src/teacherWorkbookPreview.js";

const geometry = { rowHeight: 36, columnWidth: 180, headerHeight: 36, rowNumberWidth: 60, overscan: 4 };

for (const bookType of ["xlsx", "biff8"]) {
  test(`read-only ${bookType} preview preserves every row, sheet and formatted value`, () => {
    const workbook = XLSX.utils.book_new();
    const rows = Array.from({ length: 105 }, (_, i) => [i === 0 ? "材料" : `品种${i}`, i === 0 ? "株高" : i]);
    const first = XLSX.utils.aoa_to_sheet(rows);
    first.C1 = { t: "n", v: 0.25, z: "0%" };
    first["!ref"] = "A1:C105";
    XLSX.utils.book_append_sheet(workbook, first, "考种数据");
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.aoa_to_sheet([["另一张表"], ["记录"]]), "试验记录");
    const bytes = XLSX.write(workbook, { bookType, type: "array" });
    const parsed = readWorkbook(bytes);
    assert.deepEqual(parsed.SheetNames, ["考种数据", "试验记录"]);
    const details = sheetDetails(parsed, "考种数据");
    assert.equal(details.totalRows, 105);
    assert.equal(details.totalColumns, 3);
    assert.equal(cellText(details.sheet, 0, 0), "材料");
    assert.equal(cellText(details.sheet, 0, 2), "25%");
    assert.equal(cellText(details.sheet, 104, 0), "品种104");
    assert.equal(cellText(sheetDetails(parsed, "试验记录").sheet, 1, 0), "记录");
  });
}

test("wide worksheets scroll continuously through the last row and column with bounded rendering", () => {
  const workbook = XLSX.utils.book_new();
  const sheet = XLSX.utils.aoa_to_sheet([["开头"]]);
  sheet["ALL1000"] = { t: "s", v: "最后一项" };
  sheet["!ref"] = "A1:ALL1000";
  XLSX.utils.book_append_sheet(workbook, sheet, "宽表");
  const parsed = readWorkbook(XLSX.write(workbook, { bookType: "xlsx", type: "array" }));
  const details = sheetDetails(parsed, "宽表");
  const viewport = { top: 0, left: 0, width: 960, height: 500 };
  const first = visibleSheetWindow(details, viewport, geometry);
  assert.equal(first.rows[0].index, 0);
  assert.equal(first.columns[0].label, "A");
  assert.ok(first.rows.length * first.columns.length < 500);
  const last = visibleSheetWindow(details, { ...viewport, top: 36 + details.totalRows * 36 - 500, left: 60 + details.totalColumns * 180 - 960 }, geometry);
  assert.equal(last.rows.at(-1).index, 999);
  assert.equal(last.columns.at(-1).label, "ALL");
  assert.equal(cellText(details.sheet, last.rows.at(-1).index, last.columns.at(-1).index), "最后一项");
  const middle = visibleSheetWindow(details, { ...viewport, top: 500 * 36, left: 500 * 180 }, geometry);
  assert.ok(middle.rows.some(row => row.index === 500));
  assert.ok(middle.columns.some(column => column.index === 500));
});

test("formatted trailing blanks are trimmed without losing zeroes, internal blanks or merged headings", () => {
  const workbook = XLSX.utils.book_new();
  const sheet = XLSX.utils.aoa_to_sheet([["标题"], [], ["", 0]]);
  sheet["!ref"] = "A1:XFD10000";
  sheet["!merges"] = [XLSX.utils.decode_range("A1:D1")];
  XLSX.utils.book_append_sheet(workbook, sheet, "格式范围过大");
  const details = sheetDetails(workbook, "格式范围过大");
  assert.equal(details.totalRows, 3);
  assert.equal(details.totalColumns, 4);
  assert.equal(cellText(details.sheet, 2, 1), "0");
  assert.equal(cellText(details.sheet, 1, 1), "");
});

test("empty sheets keep an empty scroll window", () => {
  const details = sheetDetails({ Sheets: { empty: {} } }, "empty");
  const visible = visibleSheetWindow(details, { top: 0, left: 0, width: 960, height: 500 }, geometry);
  assert.equal(details.totalRows, 0);
  assert.deepEqual(visible, { rows: [], columns: [] });
});
