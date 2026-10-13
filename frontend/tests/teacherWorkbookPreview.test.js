import assert from "node:assert/strict";
import { test } from "node:test";
import * as XLSX from "xlsx";
import { readWorkbook, sheetPage } from "../src/teacherWorkbookPreview.js";

for (const bookType of ["xlsx", "biff8"]) {
  test(`read-only ${bookType} preview preserves sheets, formatted values and page bounds`, () => {
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
    const firstPage = sheetPage(parsed, "考种数据");
    assert.equal(firstPage.rows.length, 100);
    assert.equal(firstPage.rowPages, 2);
    assert.equal(firstPage.rows[0].values[0], "材料");
    assert.equal(firstPage.rows[0].values[2], "25%");
    const secondPage = sheetPage(parsed, "考种数据", 1, 0);
    assert.equal(secondPage.rows.length, 5);
    assert.equal(secondPage.rows[0].number, 101);
    assert.equal(sheetPage(parsed, "试验记录").rows[1].values[0], "记录");
  });
}

test("wide worksheets can be read beyond the first column page", () => {
  const workbook = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.aoa_to_sheet([Array.from({ length: 27 }, (_, i) => `列${i + 1}`)]), "宽表");
  const parsed = readWorkbook(XLSX.write(workbook, { bookType: "xlsx", type: "array" }));
  const first = sheetPage(parsed, "宽表", 0, 0);
  const second = sheetPage(parsed, "宽表", 0, 1);
  assert.equal(first.columns.length, 25);
  assert.deepEqual(second.columns, ["Z", "AA"]);
  assert.deepEqual(second.rows[0].values, ["列26", "列27"]);
});
