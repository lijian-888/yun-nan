// Independent of the Python index reader: verify source identities and saved values with SheetJS.
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const XLSX = require("xlsx");
const samples = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const root = process.argv[3];
const books = new Map();
let checked = 0;
for (const sample of samples) {
  if (!books.has(sample.file)) books.set(sample.file, XLSX.readFile(path.join(root, sample.file), { dense: true, cellFormula: true, cellNF: true }));
  const book = books.get(sample.file);
  const sheet = book.Sheets[sample.sheet];
  assert.ok(sheet, `${sample.file}: ${sample.sheet} missing`);
  const location = XLSX.utils.decode_cell(sample.cell);
  const cell = sheet[location.r]?.[location.c];
  assert.ok(cell, `${sample.file}: ${sample.cell} missing`);
  if (cell.t === "n") {
    assert.equal(Number(sample.raw_value), Number(cell.v), `${sample.file}: ${sample.sheet}!${sample.cell} numeric mismatch`);
  } else {
    assert.equal(sample.value, String(cell.w ?? cell.v ?? "").trim(), `${sample.file}: ${sample.sheet}!${sample.cell} text mismatch`);
  }
  for (const identity of sample.identities) {
    const p = XLSX.utils.decode_cell(identity.cell);
    const identityCell = sheet[p.r]?.[p.c];
    if (identityCell) assert.equal(identity.value, String(identityCell.w ?? identityCell.v ?? "").trim(), `${sample.file}: ${sample.sheet}!${identity.cell} material identity display mismatch`);
  }
  checked++;
}
console.log(JSON.stringify({ result: "PASS", independently_checked_cells: checked, files: books.size }));
