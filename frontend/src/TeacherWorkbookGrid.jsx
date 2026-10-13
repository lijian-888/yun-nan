import { useEffect, useMemo, useRef, useState } from "react";
import { cellText, sheetDetails, visibleSheetWindow } from "./teacherWorkbookPreview";

const geometry = { rowHeight: 36, columnWidth: 180, headerHeight: 36, rowNumberWidth: 60, overscan: 4 };

export default function TeacherWorkbookGrid({ workbook, sheetName }) {
  const details = useMemo(() => sheetDetails(workbook, sheetName), [workbook, sheetName]);
  const scroller = useRef(null);
  const [viewport, setViewport] = useState({ top: 0, left: 0, width: 1200, height: 600 });
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    const element = scroller.current;
    if (!element) return;
    let frame = 0;
    const measure = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => setViewport({ top: element.scrollTop, left: element.scrollLeft, width: element.clientWidth, height: element.clientHeight }));
    };
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    element.addEventListener("scroll", measure, { passive: true });
    measure();
    return () => { cancelAnimationFrame(frame); observer.disconnect(); element.removeEventListener("scroll", measure); };
  }, []);

  const window = visibleSheetWindow(details, viewport, geometry);
  const width = geometry.rowNumberWidth + details.totalColumns * geometry.columnWidth;
  const height = geometry.headerHeight + details.totalRows * geometry.rowHeight;

  return <>
    <div className="teacher-file-sheet-summary">{sheetName} · {details.totalRows.toLocaleString()} 行 × {details.totalColumns.toLocaleString()} 列<span>横向、纵向滚动查看整表</span></div>
    <div className="teacher-file-cell-value" aria-live="polite"><span>{selected?.address || "单元格内容"}</span><div>{selected ? selected.value || "（空白）" : "点击单元格可查看完整内容"}</div></div>
    <div ref={scroller} className="teacher-file-grid-scroll" role="grid" aria-label={`${sheetName}，只读工作表`} aria-readonly="true" aria-rowcount={details.totalRows + 1} aria-colcount={details.totalColumns + 1} tabIndex={0}>
      {!details.totalRows ? <p className="teacher-file-empty-sheet">这个工作表没有可显示的单元格。</p> : <div className="teacher-file-grid-canvas" style={{ width, height }}>
        <div className="teacher-file-grid-header" role="row" aria-rowindex={1} style={{ height: geometry.headerHeight, width }}>
          {window.columns.map(column => <div className="teacher-file-grid-heading" role="columnheader" aria-colindex={column.index + 2} key={column.index} style={{ left: geometry.rowNumberWidth + column.index * geometry.columnWidth, width: geometry.columnWidth }}>{column.label}</div>)}
          <div className="teacher-file-grid-corner" role="columnheader" aria-label="行号" style={{ width: geometry.rowNumberWidth }}>#</div>
        </div>
        {window.rows.map(row => <div className="teacher-file-grid-row" role="row" aria-rowindex={row.index + 2} key={row.index} style={{ top: geometry.headerHeight + row.index * geometry.rowHeight, height: geometry.rowHeight, width }}>
          {window.columns.map(column => {
            const value = cellText(details.sheet, row.index, column.index);
            const address = `${column.label}${row.index + 1}`;
            const choose = () => setSelected({ address, value });
            return <div className={`teacher-file-grid-cell${selected?.address === address ? " is-selected" : ""}`} role="gridcell" aria-colindex={column.index + 2} aria-selected={selected?.address === address} key={column.index} title={value} tabIndex={0} onClick={choose} onFocus={choose} style={{ left: geometry.rowNumberWidth + column.index * geometry.columnWidth, width: geometry.columnWidth }}>{value}</div>;
          })}
          <div className="teacher-file-grid-row-number" role="rowheader" aria-colindex={1} style={{ width: geometry.rowNumberWidth }}>{row.index + 1}</div>
        </div>)}
      </div>}
    </div>
  </>;
}
