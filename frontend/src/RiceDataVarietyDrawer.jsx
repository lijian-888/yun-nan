import { useEffect, useState } from "react";
import { ExternalLink, LoaderCircle, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { request } from "./api";

const safeLink = (value) => /^https?:\/\//i.test(value || "") ? value : undefined;

export default function RiceDataVarietyDrawer({ parent, onClose }) {
  const [detail, setDetail] = useState(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const control = new AbortController();
    setBusy(true); setError(""); setDetail(null);
    request(`/api/research/variety-search/ricedata/${parent.variety_id}`, { signal: control.signal })
      .then(setDetail)
      .catch((reason) => {
        if (reason?.name !== "AbortError") setError(reason.message || "品种档案读取失败");
      })
      .finally(() => setBusy(false));
    return () => control.abort();
  }, [parent.variety_id]);

  return <div className="parent-detail-backdrop" onMouseDown={onClose}>
    <aside className="parent-detail-drawer" onMouseDown={(event) => event.stopPropagation()} aria-label={`${parent.name}完整品种档案`}>
      <header>
        <div><span>国家水稻数据中心品种档案</span><h2>{parent.name}</h2><p>品种编号 {parent.variety_id}</p></div>
        <button type="button" className="icon-button" aria-label="关闭品种档案" onClick={onClose}><X size={18} /></button>
      </header>
      {busy && <div className="parent-detail-state"><LoaderCircle className="spin" size={20} />正在读取完整档案…</div>}
      {error && <div className="workspace-warning">{error}</div>}
      {detail && <div className="parent-detail-content">
        <section><h3>基础信息</h3><dl>
          <dt>品种名称</dt><dd>{detail.record.name}</dd>
          <dt>别名</dt><dd>{detail.record.aliases?.join("、") || "未记载"}</dd>
          <dt>品种类型</dt><dd>{detail.record.variety_type || "未记载"}</dd>
          <dt>亲本来源</dt><dd>{detail.record.parentage || "未记载"}</dd>
          <dt>选育单位</dt><dd>{detail.record.breeder || "未记载"}</dd>
          <dt>申请单位</dt><dd>{detail.record.applicant || "未记载"}</dd>
        </dl>{safeLink(detail.record.source_url) && <a className="parent-source-link" href={detail.record.source_url} target="_blank" rel="noreferrer"><ExternalLink size={14} />查看国家水稻数据中心原始页面</a>}</section>
        <section><h3>审定、性状、产量与适宜地区</h3><div className="assistant-markdown parent-dossier-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{
          table: ({ children }) => <div className="parent-table-scroll"><table>{children}</table></div>,
          a: ({ href, children }) => <a href={safeLink(href)} target="_blank" rel="noreferrer">{children}</a>,
        }}>{detail.content || "当前档案暂无可展示内容。"}</ReactMarkdown></div></section>
        <section><h3>原始来源与证据</h3>{detail.evidence?.length ? detail.evidence.map((evidence, index) => <details key={`${evidence.title}-${index}`}>
          <summary>{evidence.title || `来源 ${index + 1}`}</summary>
          {safeLink(evidence.url) && <a className="parent-source-link" href={evidence.url} target="_blank" rel="noreferrer"><ExternalLink size={13} />查看来源网页</a>}
          {evidence.excerpts?.map((excerpt, excerptIndex) => <div className="parent-evidence-excerpt" key={excerptIndex}><strong>{excerpt.title}</strong><p>{excerpt.text}</p></div>)}
        </details>) : <p>当前档案没有单独列出的来源摘录。</p>}</section>
      </div>}
    </aside>
  </div>;
}
