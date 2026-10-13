import { useEffect, useId, useRef, useState } from "react";
import { ArrowLeft, Download, FileText, Folder, LoaderCircle, Search, X } from "lucide-react";
import { authorizedFetch, request } from "./api";
import { readWorkbook } from "./teacherWorkbookPreview";
import TeacherWorkbookGrid from "./TeacherWorkbookGrid";
import "./TeacherSourceFilesWorkspace.css";

const apiBase = "/api/research/teacher-source-files";
const workbookExtensions = new Set([".xlsx", ".xls", ".csv", ".tsv"]);
const imageExtensions = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp"]);
const previewLimit = 12 * 1024 * 1024;
const formatSize = bytes => bytes == null ? "—" : bytes < 1024 ? `${bytes} B` : bytes < 1048576 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1048576).toFixed(1)} MB`;
const formatDate = value => value ? new Date(value).toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—";

async function fileResponse(item, signal) {
  const response = await authorizedFetch(`${apiBase}/file?path=${encodeURIComponent(item.path)}`, { signal });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail || "原始文件读取失败。");
  }
  return response;
}

export default function TeacherSourceFilesWorkspace() {
  const [path, setPath] = useState("");
  const [listing, setListing] = useState(null);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [opening, setOpening] = useState("");
  const [downloading, setDownloading] = useState("");
  const [downloadError, setDownloadError] = useState("");
  const [preview, setPreview] = useState(null);
  const [sheetIndex, setSheetIndex] = useState(0);
  const previewDialog = useRef(null);
  const previewController = useRef(null);
  const previewUrl = useRef("");
  const previewTitleId = useId();

  useEffect(() => {
    const controller = new AbortController();
    setBusy(true); setError(""); setListing(null); setFilter("");
    request(`${apiBase}/browse?path=${encodeURIComponent(path)}`, { signal: controller.signal })
      .then(data => { if (!controller.signal.aborted) setListing(data); })
      .catch(e => { if (!controller.signal.aborted) setError(e.message || "文件目录读取失败。"); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [path]);

  useEffect(() => {
    if (preview && !previewDialog.current?.open) previewDialog.current?.showModal();
  }, [preview]);

  useEffect(() => () => {
    previewController.current?.abort();
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
  }, []);

  function closePreview() {
    previewDialog.current?.close();
  }

  function onPreviewClosed() {
    previewController.current?.abort();
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
    previewUrl.current = "";
    setPreview(null); setOpening(""); setDownloadError("");
    setSheetIndex(0);
  }

  async function openPreview(item) {
    if (opening) return;
    previewController.current?.abort();
    const controller = new AbortController();
    previewController.current = controller;
    setOpening(item.path); setError(""); setDownloadError(""); setSheetIndex(0);
    setPreview({ item, kind: "loading" });
    try {
      if (!item.can_preview || item.size_bytes > previewLimit) {
        const reason = item.size_bytes > previewLimit ? "文件较大，当前暂不支持站内预览。" : "该格式暂不支持可靠的站内预览。";
        setPreview({ item, kind: "unsupported", reason });
        return;
      }
      const response = await fileResponse(item, controller.signal);
      if (workbookExtensions.has(item.extension)) {
        const bytes = await response.arrayBuffer();
        if (!controller.signal.aborted) setPreview({ item, kind: "workbook", workbook: readWorkbook(bytes) });
      } else if (item.extension === ".txt") {
        const content = await response.text();
        if (!controller.signal.aborted) setPreview({ item, kind: "text", content });
      } else {
        const url = URL.createObjectURL(await response.blob());
        if (controller.signal.aborted) URL.revokeObjectURL(url);
        else {
          previewUrl.current = url;
          setPreview({ item, kind: imageExtensions.has(item.extension) ? "image" : "pdf", url });
        }
      }
    } catch (e) {
      if (!controller.signal.aborted) setPreview({ item, kind: "error", reason: e.message || "预览失败，可尝试下载原文件。" });
    } finally {
      if (!controller.signal.aborted) setOpening("");
    }
  }

  async function downloadFile(item) {
    if (downloading) return;
    setDownloading(item.path); setError(""); setDownloadError("");
    try {
      const response = await fileResponse(item);
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = item.name; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 120000);
    } catch (e) {
      const message = e.message || "下载失败，请稍后重试。";
      if (previewDialog.current?.open) setDownloadError(message);
      else setError(message);
    } finally {
      setDownloading("");
    }
  }

  const ancestors = path ? path.split("/") : [];
  const visible = (listing?.entries || []).filter(item => item.name.toLocaleLowerCase().includes(filter.trim().toLocaleLowerCase()));

  return <section className="teacher-files">
    <div className="teacher-files-intro"><h3>院内原始文件</h3><p>按老师文件夹浏览原始资料。点击文件名可在站内预览；原文件保持只读。</p></div>
    <nav className="teacher-files-crumbs" aria-label="文件目录位置"><button type="button" onClick={() => setPath("")}>老师数据</button>{ancestors.map((name, index) => <span key={`${name}-${index}`}> / <button type="button" onClick={() => setPath(ancestors.slice(0, index + 1).join("/"))}>{name}</button></span>)}</nav>
    <div className="teacher-files-toolbar"><label><Search size={16} /><input aria-label="筛选当前目录文件" placeholder="筛选当前目录中的文件或文件夹" value={filter} onChange={e => setFilter(e.target.value)} /></label>{path && <button className="secondary-button" type="button" onClick={() => setPath(ancestors.slice(0, -1).join("/"))}><ArrowLeft size={15} />返回上一级</button>}</div>
    {error && <p className="vs-error" role="alert">{error}</p>}
    {busy && <p role="status"><LoaderCircle className="spin" size={16} />正在读取文件目录…</p>}
    {listing && <><p className="teacher-files-count">当前目录 {listing.entries.length} 项{filter && `，筛选后 ${visible.length} 项`}</p><div className="teacher-files-table-scroll"><table><thead><tr><th>名称</th><th>类型</th><th>大小</th><th>修改时间</th><th>操作</th></tr></thead><tbody>{visible.map(item => <tr key={item.path}><td><button className="teacher-files-name" type="button" onClick={() => item.kind === "folder" ? setPath(item.path) : openPreview(item)}>{item.kind === "folder" ? <Folder size={17} /> : <FileText size={17} />}{item.name}</button></td><td>{item.kind === "folder" ? "文件夹" : (item.extension || "文件").replace(/^\./, "").toUpperCase()}</td><td>{formatSize(item.size_bytes)}</td><td>{formatDate(item.modified_at)}</td><td><div className="teacher-files-actions"><button className="teacher-files-action" type="button" disabled={opening === item.path} onClick={() => item.kind === "folder" ? setPath(item.path) : openPreview(item)}>{item.kind === "folder" ? "进入" : opening === item.path ? <LoaderCircle className="spin" size={15} /> : "预览"}</button>{item.kind === "file" && <button className="teacher-files-action" type="button" disabled={downloading === item.path} onClick={() => downloadFile(item)}>{downloading === item.path ? <LoaderCircle className="spin" size={15} /> : <Download size={14} />}下载</button>}</div></td></tr>)}{!visible.length && <tr><td colSpan={5}>当前目录没有符合条件的文件。</td></tr>}</tbody></table></div><p className="teacher-files-note">Excel 工作表、图片、PDF 和文本可在站内查看。预览只读取原文件，不修改内容；复杂格式、图表及公式计算以原文件为准。Word 文档暂需下载查看。</p></>}

    <dialog ref={previewDialog} className="teacher-file-preview" aria-labelledby={previewTitleId} onClose={onPreviewClosed}>
      {preview && <><header className="teacher-file-preview-header"><div><h2 id={previewTitleId}>{preview.item.name}</h2><p>{formatSize(preview.item.size_bytes)} · {formatDate(preview.item.modified_at)} · 只读预览</p></div><div><button className="secondary-button" type="button" onClick={() => downloadFile(preview.item)} disabled={!!downloading}><Download size={15} />下载原文件</button><button className="secondary-button" type="button" aria-label="关闭预览" onClick={closePreview}><X size={17} /></button></div></header>
        <div className="teacher-file-preview-body">
          {downloadError && <p className="vs-error" role="alert">{downloadError}</p>}
          {preview.kind === "loading" && <p role="status"><LoaderCircle className="spin" size={18} />正在读取原文件…</p>}
          {(preview.kind === "unsupported" || preview.kind === "error") && <p className="vs-error" role="alert">{preview.reason}</p>}
          {preview.kind === "image" && <img className="teacher-file-preview-image" src={preview.url} alt={preview.item.name} />}
          {preview.kind === "pdf" && <iframe className="teacher-file-preview-frame" src={preview.url} title={preview.item.name} />}
          {preview.kind === "text" && <pre className="teacher-file-preview-text">{preview.content}</pre>}
          {preview.kind === "workbook" && <><nav className="teacher-file-sheet-tabs" aria-label="工作表">{preview.workbook.SheetNames.map((name, index) => <button type="button" key={`${name}-${index}`} aria-pressed={index === sheetIndex} onClick={() => setSheetIndex(index)}>{name}</button>)}</nav>
            <TeacherWorkbookGrid key={sheetIndex} workbook={preview.workbook} sheetName={preview.workbook.SheetNames[sheetIndex]} />
          </>}
        </div>
      </>}
    </dialog>
  </section>;
}
