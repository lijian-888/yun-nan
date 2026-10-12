import { useEffect, useState } from "react";
import { ArrowLeft, Download, FileText, Folder, LoaderCircle, Search } from "lucide-react";
import { authorizedFetch, request } from "./api";
import "./TeacherSourceFilesWorkspace.css";

const apiBase = "/api/research/teacher-source-files";
const formatSize = bytes => bytes == null ? "—" : bytes < 1024 ? `${bytes} B` : bytes < 1048576 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1048576).toFixed(1)} MB`;
const formatDate = value => value ? new Date(value).toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—";

export default function TeacherSourceFilesWorkspace() {
  const [path, setPath] = useState("");
  const [listing, setListing] = useState(null);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [opening, setOpening] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setBusy(true); setError(""); setListing(null); setFilter("");
    request(`${apiBase}/browse?path=${encodeURIComponent(path)}`, { signal: controller.signal })
      .then(data => { if (!controller.signal.aborted) setListing(data); })
      .catch(e => { if (!controller.signal.aborted) setError(e.message || "文件目录读取失败。"); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [path]);

  async function openFile(item) {
    if (opening) return;
    const previewWindow = item.can_preview ? window.open("", "_blank") : null;
    if (previewWindow) previewWindow.document.title = "正在打开原始文件";
    setOpening(item.path); setError("");
    try {
      const response = await authorizedFetch(`${apiBase}/file?path=${encodeURIComponent(item.path)}`);
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(body?.detail || "原始文件读取失败。");
      }
      const url = URL.createObjectURL(await response.blob());
      if (previewWindow && !previewWindow.closed) {
        previewWindow.location.replace(url);
      } else {
        const anchor = document.createElement("a");
        anchor.href = url; anchor.download = item.name; anchor.click();
      }
      setTimeout(() => URL.revokeObjectURL(url), 120000);
    } catch (e) {
      previewWindow?.close();
      setError(e.message || "原始文件读取失败。");
    } finally {
      setOpening("");
    }
  }

  const ancestors = path ? path.split("/") : [];
  const visible = (listing?.entries || []).filter(item => item.name.toLocaleLowerCase().includes(filter.trim().toLocaleLowerCase()));
  return <section className="teacher-files">
    <div className="teacher-files-intro"><h3>院内原始文件</h3><p>按老师文件夹浏览原始资料。这里只展示文件名、格式、大小和修改时间，不把文件内容当成已核验的结构化数据。</p></div>
    <nav className="teacher-files-crumbs" aria-label="文件目录位置"><button type="button" onClick={() => setPath("")}>老师数据</button>{ancestors.map((name, index) => <span key={`${name}-${index}`}> / <button type="button" onClick={() => setPath(ancestors.slice(0, index + 1).join("/"))}>{name}</button></span>)}</nav>
    <div className="teacher-files-toolbar"><label><Search size={16} /><input aria-label="筛选当前目录文件" placeholder="筛选当前目录中的文件或文件夹" value={filter} onChange={e => setFilter(e.target.value)} /></label>{path && <button className="secondary-button" type="button" onClick={() => setPath(ancestors.slice(0, -1).join("/"))}><ArrowLeft size={15} />返回上一级</button>}</div>
    {error && <p className="vs-error" role="alert">{error}</p>}
    {busy && <p role="status"><LoaderCircle className="spin" size={16} />正在读取文件目录…</p>}
    {listing && <><p className="teacher-files-count">当前目录 {listing.entries.length} 项{filter && `，筛选后 ${visible.length} 项`}</p><div className="teacher-files-table-scroll"><table><thead><tr><th>名称</th><th>类型</th><th>大小</th><th>修改时间</th><th>操作</th></tr></thead><tbody>{visible.map(item => <tr key={item.path}><td><button className="teacher-files-name" type="button" onClick={() => item.kind === "folder" ? setPath(item.path) : openFile(item)}>{item.kind === "folder" ? <Folder size={17} /> : <FileText size={17} />}{item.name}</button></td><td>{item.kind === "folder" ? "文件夹" : (item.extension || "文件").replace(/^\./, "").toUpperCase()}</td><td>{formatSize(item.size_bytes)}</td><td>{formatDate(item.modified_at)}</td><td><button className="teacher-files-action" type="button" disabled={opening === item.path} onClick={() => item.kind === "folder" ? setPath(item.path) : openFile(item)}>{item.kind === "folder" ? "进入" : opening === item.path ? <LoaderCircle className="spin" size={15} /> : item.can_preview ? "打开" : <><Download size={14} />下载</>}</button></td></tr>)}{!visible.length && <tr><td colSpan={5}>当前目录没有符合条件的文件。</td></tr>}</tbody></table></div><p className="teacher-files-note">图片、PDF、文本可在浏览器中打开；Word、Excel 等格式由浏览器下载后用本机软件查看。仅提供原始资料访问，不代表内容已经过数据清洗或科研审核。</p></>}
  </section>;
}
