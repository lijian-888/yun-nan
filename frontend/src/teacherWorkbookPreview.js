import * as XLSX from "xlsx";

export function readWorkbook(bytes) {
  const workbook = XLSX.read(bytes, {
    type: "array",
    dense: true,
    cellFormula: false,
    cellHTML: false,
    cellStyles: false,
    bookVBA: false,
  });
  if (!workbook.SheetNames?.length) throw new Error("文件中没有可显示的工作表。");
  return workbook;
}

export function sheetDetails(workbook, sheetName) {
  const sheet = workbook.Sheets[sheetName];
  if (!sheet) throw new Error("未找到该工作表。");
  let lastRow = -1;
  let lastColumn = -1;
  const include = (cell, row, column) => {
    if (cell && (cell.v != null && cell.v !== "" || cell.w || cell.f)) {
      lastRow = Math.max(lastRow, row);
      lastColumn = Math.max(lastColumn, column);
    }
  };
  if (Array.isArray(sheet)) {
    sheet.forEach((row, r) => row?.forEach((cell, c) => include(cell, r, c)));
  } else {
    for (const address of Object.keys(sheet)) {
      if (address.startsWith("!")) continue;
      const { r, c } = XLSX.utils.decode_cell(address);
      include(sheet[address], r, c);
    }
  }
  // Formatting can extend !ref to XFD. Keep meaningful cells, internal blanks,
  // original coordinates and any merged area anchored by a meaningful cell.
  for (const merge of sheet["!merges"] || []) {
    if (cellText(sheet, merge.s.r, merge.s.c) !== "") {
      lastRow = Math.max(lastRow, merge.e.r);
      lastColumn = Math.max(lastColumn, merge.e.c);
    }
  }
  return { sheet, totalRows: lastRow + 1, totalColumns: lastColumn + 1 };
}

export function cellText(sheet, row, column) {
  const cell = Array.isArray(sheet) ? sheet[row]?.[column] : sheet[XLSX.utils.encode_cell({ r: row, c: column })];
  return String(cell?.w ?? (cell?.v == null ? "" : cell.v));
}

export function visibleSheetWindow(details, viewport, geometry) {
  const { rowHeight, columnWidth, headerHeight, rowNumberWidth, overscan = 3 } = geometry;
  const firstRow = Math.max(0, Math.floor(Math.max(0, viewport.top - headerHeight) / rowHeight) - overscan);
  const firstColumn = Math.max(0, Math.floor(Math.max(0, viewport.left - rowNumberWidth) / columnWidth) - overscan);
  const endRow = Math.min(details.totalRows, Math.ceil((viewport.top + viewport.height) / rowHeight) + overscan);
  const endColumn = Math.min(details.totalColumns, Math.ceil((viewport.left + viewport.width) / columnWidth) + overscan);
  return {
    columns: Array.from({ length: Math.max(0, endColumn - firstColumn) }, (_, i) => ({ index: firstColumn + i, label: XLSX.utils.encode_col(firstColumn + i) })),
    rows: Array.from({ length: Math.max(0, endRow - firstRow) }, (_, i) => ({ index: firstRow + i })),
  };
}
