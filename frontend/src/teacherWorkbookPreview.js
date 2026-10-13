import * as XLSX from "xlsx";

export const PREVIEW_ROWS = 100;
export const PREVIEW_COLUMNS = 25;

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

export function sheetPage(workbook, sheetName, rowPage = 0, columnPage = 0) {
  const sheet = workbook.Sheets[sheetName];
  if (!sheet) throw new Error("未找到该工作表。");
  if (!sheet["!ref"]) return { rows: [], columns: [], rowPages: 1, columnPages: 1, totalRows: 0, totalColumns: 0, rowPage: 0, columnPage: 0 };

  const range = XLSX.utils.decode_range(sheet["!ref"]);
  const totalRows = range.e.r - range.s.r + 1;
  const totalColumns = range.e.c - range.s.c + 1;
  const rowPages = Math.max(1, Math.ceil(totalRows / PREVIEW_ROWS));
  const columnPages = Math.max(1, Math.ceil(totalColumns / PREVIEW_COLUMNS));
  const safeRowPage = Math.min(Math.max(0, rowPage), rowPages - 1);
  const safeColumnPage = Math.min(Math.max(0, columnPage), columnPages - 1);
  const firstRow = range.s.r + safeRowPage * PREVIEW_ROWS;
  const firstColumn = range.s.c + safeColumnPage * PREVIEW_COLUMNS;
  const columns = Array.from({ length: Math.min(PREVIEW_COLUMNS, range.e.c - firstColumn + 1) }, (_, i) => XLSX.utils.encode_col(firstColumn + i));
  const rows = Array.from({ length: Math.min(PREVIEW_ROWS, range.e.r - firstRow + 1) }, (_, i) => {
    const number = firstRow + i + 1;
    const values = columns.map((_, j) => {
      const row = firstRow + i;
      const column = firstColumn + j;
      const cell = Array.isArray(sheet) ? sheet[row]?.[column] : sheet[XLSX.utils.encode_cell({ r: row, c: column })];
      return cell?.w ?? (cell?.v == null ? "" : String(cell.v));
    });
    return { number, values };
  });
  return { rows, columns, rowPages, columnPages, totalRows, totalColumns, rowPage: safeRowPage, columnPage: safeColumnPage };
}
