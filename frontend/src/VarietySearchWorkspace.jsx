import { useEffect, useId, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Search, X, LoaderCircle, Download, Plus } from "lucide-react";
import { request, jsonRequest } from "./api";
import TeacherSourceFilesWorkspace from "./TeacherSourceFilesWorkspace";
import "./VarietySearchWorkspace.css";

const defaults = (source = "ricedata") => ({ source, keyword: "", field: "name", fuzzy: true, rice_type: "", kind: "", glutinous: false, upland: false, approved_only: false, region: "", approval_year: null, traits: [], has_phenotype: false, has_genotype: false, confirmed_only: false, sort: "name", page: 1, page_size: 20 });
const operators = [["gte", "不小于"], ["lte", "不超过"], ["gt", "大于"], ["lt", "小于"], ["eq", "等于"]];
const display = value => Array.isArray(value) ? value.join("、") || "—" : value ?? "—";
const csvCell = value => `"${String(value ?? "").replace(/^[\s]*[=+\-@]/, "'$&").replaceAll('"', '""')}"`;

export default function VarietySearchWorkspace() {
  const [catalog, setCatalog] = useState(null);
  const [draft, setDraft] = useState(defaults);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState(null);
  const [detailError, setDetailError] = useState("");
  const [detailBusy, setDetailBusy] = useState(false);
  const searchController = useRef(null);
  const detailController = useRef(null);
  const dialog = useRef(null);
  const detailBody = useRef(null);
  const backdropPressed = useRef(false);
  const detailTitleId = useId();
  const currentDetail = useRef(null);
  const national = draft.source === "ricedata";
  const patch = change => setDraft(value => ({ ...value, ...change }));

  useEffect(() => {
    const controller = new AbortController();
    request("/api/research/variety-search/catalog", { signal: controller.signal }).then(setCatalog).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => { controller.abort(); searchController.current?.abort(); detailController.current?.abort(); };
  }, []);

  async function search(query = draft) {
    searchController.current?.abort();
    const controller = new AbortController();
    searchController.current = controller;
    if (query.traits.some(item => String(item.value).trim() === "" || !Number.isFinite(Number(item.value)))) {
      setBusy(false); setError("请填写每条性状条件的有效数值，或移除空条件。"); return;
    }
    setBusy(true); setError(""); setResult(null);
    try {
      const payload = { ...query, traits: query.traits.map(item => ({ ...item, value: Number(item.value) })) };
      const response = await request("/api/research/variety-search/query", { ...jsonRequest("POST", payload), signal: controller.signal });
      if (!controller.signal.aborted) setResult(response);
    } catch (e) { if (!controller.signal.aborted) setError(e.message || "查询未完成，请重试。"); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }

  function switchSource(source) {
    searchController.current?.abort(); detailController.current?.abort();
    dialog.current?.close(); setDetail(null); setDetailBusy(false); setResult(null); setBusy(false); setError("");
    setDraft(defaults(source));
  }

  async function openDetail(source, id, page = 1) {
    detailController.current?.abort();
    const controller = new AbortController(); detailController.current = controller;
    currentDetail.current = { source, id };
    setDetail(null); setDetailError(""); setDetailBusy(true);
    if (!dialog.current.open) dialog.current.showModal();
    detailBody.current?.scrollTo({ top: 0, left: 0 });
    try {
      const response = await request(`/api/research/variety-search/${source}/${encodeURIComponent(id)}?page=${page}`, { signal: controller.signal });
      if (!controller.signal.aborted) setDetail(response);
    } catch (e) { if (!controller.signal.aborted) setDetailError(e.message); }
    finally { if (!controller.signal.aborted) setDetailBusy(false); }
  }

  function outsideDialog(event) {
    if (event.target !== event.currentTarget) return false;
    const bounds = event.currentTarget.getBoundingClientRect();
    return event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom;
  }

  function exportPage() {
    if (!result?.records.length) return;
    const nationalResult = result.source === "ricedata";
    const rows = [["名称", "别名", nationalResult ? "品种类型" : "材料编号", "亲本来源", nationalResult ? "审定地区" : "记录地点", "最近年份"], ...result.records.map(r => [r.name, (r.aliases || []).join("、"), nationalResult ? r.variety_type : r.id, r.parentage, nationalResult ? (r.regions || []).join("、") : r.locations, r.latest_year])];
    const url = URL.createObjectURL(new Blob(["\uFEFF" + rows.map(row => row.map(csvCell).join(",")).join("\r\n")], { type: "text/csv;charset=utf-8" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = `品种检索-第${result.page}页.csv`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  const fields = national ? [["name", "品种名及别名"], ["alias", "别名"], ["identifier", "来源品种编号"], ["parentage", "亲本来源"], ["breeder", "育种 / 申报单位"], ["approval_no", "审定编号"]] : [["name", "材料名及别名"], ["alias", "别名"], ["identifier", "材料编号"], ["parentage", "亲本来源"], ["location", "记录地点"]];
  const sourceTabs = <nav className="vs-sources" aria-label="数据来源"><button type="button" aria-pressed={national} onClick={() => switchSource("ricedata")}>国家水稻数据中心{catalog?.sources?.find(source => source.code === "ricedata")?.count != null && <small>{catalog.sources.find(source => source.code === "ricedata").count.toLocaleString()} 条</small>}</button><button type="button" aria-pressed={!national} onClick={() => switchSource("institute")}>院内原始文件</button></nav>;
  if (!national) return <section className="variety-search-workspace"><header><div><h2>院内原始文件</h2><p>浏览服务器上的老师文件夹及文件元数据，按需打开原始资料。</p></div></header>{sourceTabs}<TeacherSourceFilesWorkspace /></section>;
  return <section className="variety-search-workspace">
    <header><div><h2>品种与材料检索</h2><p>直接查询服务器数据，无需了解数据库或编写 SQL。点击名称查看详细资料。</p></div></header>
    {sourceTabs}
    <form noValidate onSubmit={e => { e.preventDefault(); search({ ...draft, page: 1 }); }}>
      <div className="vs-search-row"><select aria-label="检索字段" value={draft.field} onChange={e => patch({ field: e.target.value })}>{fields.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><input aria-label="检索关键词" maxLength={120} placeholder={national ? "输入品种名、别名或其他关键词，例如南粳9212" : "输入材料名、别名或材料编号"} value={draft.keyword} onChange={e => patch({ keyword: e.target.value })} /><button className="primary-button" disabled={busy} type="submit">{busy ? <LoaderCircle className="spin" size={16} /> : <Search size={16} />}检索</button><button className="secondary-button" type="button" onClick={() => { searchController.current?.abort(); setDraft(defaults(draft.source)); setResult(null); setError(""); setBusy(false); }}>重置</button></div>
      <div className="vs-filters"><label><input type="checkbox" checked={draft.fuzzy} onChange={e => patch({ fuzzy: e.target.checked })} />模糊检索</label>
        {national ? <><fieldset><legend>稻作类型</legend>{[["", "不限"], ["indica", "籼稻"], ["japonica", "粳稻"], ["mixed", "籼粳交"]].map(([value, label]) => <label key={value}><input name="rice-type" type="radio" value={value} checked={draft.rice_type === value} onChange={e => patch({ rice_type: e.target.value })} />{label}</label>)}</fieldset><select aria-label="品种类别" value={draft.kind} onChange={e => patch({ kind: e.target.value })}><option value="">品种类别不限</option><option value="inbred">常规稻</option><option value="hybrid">杂交稻</option><option value="sterile">不育系</option></select>{[["glutinous", "糯稻"], ["upland", "旱稻"], ["approved_only", "有审定 / 认定记录"]].map(([key, label]) => <label key={key}><input type="checkbox" checked={draft[key]} onChange={e => patch({ [key]: e.target.checked })} />{label}</label>)}<select aria-label="审定地区" value={draft.region} onChange={e => patch({ region: e.target.value })}><option value="">审定地区不限</option>{catalog?.regions.map(r => <option key={r.name}>{r.name}</option>)}</select><select aria-label="审定年份" value={draft.approval_year || ""} onChange={e => patch({ approval_year: e.target.value ? Number(e.target.value) : null })}><option value="">审定年份不限</option>{catalog?.approval_years.map(y => <option key={y}>{y}</option>)}</select></> : <>{[["has_phenotype", "有表型数据"], ["has_genotype", "有基因型数据"], ["confirmed_only", "仅身份无需复核的材料"]].map(([key, label]) => <label key={key}><input type="checkbox" checked={draft[key]} onChange={e => patch({ [key]: e.target.checked })} />{label}</label>)}</>}
        <select aria-label="排序" value={draft.sort} onChange={e => patch({ sort: e.target.value })}><option value="name">名称排序</option><option value="latest">最近年份优先</option></select><select aria-label="每页条数" value={draft.page_size} onChange={e => patch({ page_size: Number(e.target.value) })}>{[20, 50, 100].map(n => <option key={n} value={n}>每页 {n} 条</option>)}</select>
      </div>
      {national && <details className="vs-advanced"><summary>高级检索：按性状数值筛选{draft.traits.length ? `（${draft.traits.length} 条条件）` : ""}</summary><p>最多 6 条条件，必须在同一审定或资料记录内满足。不代表同一试验；约数及自动抽取值请结合原文复核。缺少该指标的记录不会命中。</p>{draft.traits.map((item, index) => <div className="vs-trait-row" key={index}><select aria-label={`条件${index + 1}性状`} value={item.trait_code} onChange={e => patch({ traits: draft.traits.map((t, i) => i === index ? { ...t, trait_code: e.target.value } : t) })}>{catalog?.traits.map(t => <option value={t.code} key={t.code}>{t.name}{t.unit && `（${t.unit}）`}</option>)}</select><select aria-label={`条件${index + 1}比较方式`} value={item.operator} onChange={e => patch({ traits: draft.traits.map((t, i) => i === index ? { ...t, operator: e.target.value } : t) })}>{operators.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select><input aria-label={`条件${index + 1}数值`} type="number" step="any" required value={item.value} onChange={e => patch({ traits: draft.traits.map((t, i) => i === index ? { ...t, value: e.target.value } : t) })} /><button type="button" className="secondary-button" aria-label={`删除条件${index + 1}`} onClick={() => patch({ traits: draft.traits.filter((_, i) => i !== index) })}><X size={15} /></button></div>)}<button className="secondary-button" type="button" disabled={!catalog || draft.traits.length >= 6} onClick={() => patch({ traits: [...draft.traits, { trait_code: catalog.traits[0].code, operator: "gte", value: "" }] })}><Plus size={15} />添加性状条件</button></details>}
    </form>
    {national && catalog && <section className="vs-regions"><h3>按审定地区快速检索</h3><p>点击地区立即检索，并保留当前关键词和其他条件。省份包括其下属地区记录。</p><div>{catalog.regions.map(r => <button type="button" key={r.name} className={draft.region === r.name ? "active" : ""} title={`${r.count.toLocaleString()} 条审定资料记录（非品种数）`} onClick={() => { const query = { ...draft, region: r.name, page: 1 }; setDraft(query); search(query); }}>{r.name}</button>)}</div></section>}
    {error && <p className="vs-error" role="alert">{error}</p>}
    {busy && <p role="status"><LoaderCircle className="spin" size={16} />正在查询服务器数据…</p>}
    {!result && !busy && !error && <p className="vs-empty">输入关键词开始检索；留空点击“检索”可浏览该来源全部记录。</p>}
    {result && <section className="vs-results"><div className="vs-result-heading"><div><h3>检索结果 · {result.total.toLocaleString()} 条</h3><p>{result.source === "ricedata" ? "国家水稻数据中心" : "院内共享材料"}{result.query.keyword && ` · ${result.query.keyword}`}{result.query.region && ` · ${result.query.region}`}{result.query.approval_year && ` · ${result.query.approval_year}年`}{result.query.traits.length > 0 && ` · ${result.query.traits.length}条性状条件`} · 第 {result.page} / {Math.max(1, Math.ceil(result.total / result.page_size))} 页</p></div><button type="button" className="secondary-button" disabled={!result.records.length} onClick={exportPage}><Download size={15} />导出本页</button></div>
      <div className="vs-table-scroll"><table><thead><tr><th>名称 / 别名</th><th>{result.source === "ricedata" ? "品种类型" : "材料编号"}</th><th>亲本来源</th><th>{result.source === "ricedata" ? "育种单位" : "数据情况"}</th><th>{result.source === "ricedata" ? "符合条件的审定资料" : "记录地点 / 年份"}</th></tr></thead><tbody>{result.records.map(r => <tr key={r.id}><td><button type="button" className="vs-name" onClick={() => openDetail(result.source, r.id)}>{r.name}</button><small>{[...new Set(r.aliases || [])].filter(a => a !== r.name).join("、")}</small></td><td>{result.source === "ricedata" ? display(r.variety_type) : r.id}</td><td>{display(r.parentage)}</td><td>{result.source === "ricedata" ? display(r.breeder) : <>{r.has_phenotype ? "有表型" : "无表型记录"} · {r.has_genotype ? "有基因型" : "无基因型记录"}{r.needs_review && <small className="vs-warning">材料身份待复核</small>}</>}</td><td>{result.source === "ricedata" ? <>{display(r.regions)}<small>{r.approval_count ? `${r.approval_count} 条 · 最近 ${r.latest_year || "年份未记载"}` : "暂无正式审定记录"}</small></> : <>{display(r.locations)}<small>{r.earliest_year || "—"} 至 {r.latest_year || "—"}</small></>}</td></tr>)}{!result.records.length && <tr><td colSpan={5}>未找到符合这些条件的记录。可减少条件或改用别名检索；未命中不等于原始资料不存在。</td></tr>}</tbody></table></div>
      <div className="vs-pagination"><button className="secondary-button" type="button" disabled={result.page <= 1 || busy} onClick={() => search({ ...result.query, page: result.page - 1 })}>上一页</button><span>本页 {result.records.length} 条 / 共 {result.total.toLocaleString()} 条</span><button className="secondary-button" type="button" disabled={!result.has_more || busy} onClick={() => search({ ...result.query, page: result.page + 1 })}>下一页</button></div>
    </section>}
    <dialog ref={dialog} className="vs-detail" aria-labelledby={detailTitleId} onClose={() => { detailController.current?.abort(); backdropPressed.current = false; }} onPointerDown={e => { backdropPressed.current = outsideDialog(e); }} onClick={e => { if (backdropPressed.current && outsideDialog(e)) dialog.current.close(); backdropPressed.current = false; }}><div className="vs-detail-heading"><h2 id={detailTitleId}>{detail?.record.name || "品种 / 材料详情"}</h2><button className="secondary-button" type="button" aria-label="关闭详情" onClick={() => dialog.current.close()}><X size={18} /></button></div><div className="vs-detail-body" ref={detailBody} tabIndex={0} role="region" aria-label="详细资料，可滚动">{detailBusy && <p role="status">正在读取详细资料…</p>}{detailError && <p className="vs-error" role="alert">{detailError}</p>}{detail && <>
      {detail.source === "ricedata" ? <><div className="assistant-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{ table: ({ children }) => <div className="vs-table-scroll"><table>{children}</table></div>, a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer">{children}</a> }}>{detail.content}</ReactMarkdown></div>{detail.evidence?.length > 0 && <details><summary>原文依据与来源</summary>{detail.evidence.map((e, i) => <div key={i}><strong>{e.title || e.name || `来源 ${i + 1}`}</strong>{e.url && <a href={e.url} target="_blank" rel="noreferrer">查看原始资料</a>}<p>{e.detail}</p>{(e.excerpts || []).map((excerpt, index) => <section key={index}><h4>{excerpt.title}</h4><p>{excerpt.text}</p></section>)}</div>)}</details>}</> : <><p>材料编号：{detail.record.id} · 别名：{display(detail.record.aliases)}</p><p>亲本来源：{display(detail.record.parentage)}</p>{detail.record.needs_review && <p className="vs-warning">该材料身份仍待复核，观测记录仅供核对，不宜作为已确认材料直接用于评价。</p>}<h3>表型观测 · {detail.observation_total.toLocaleString()} 条</h3><p>各次观测分别列出，不将不同年份、地点的数据合成单一数值。</p><div className="vs-table-scroll"><table><thead><tr><th>性状</th><th>观测值</th><th>单位</th><th>年份 / 地点</th><th>原始记录</th></tr></thead><tbody>{detail.observations.map((o, i) => <tr key={i}><td>{o.raw_header || "未记载性状名称"}</td><td>{o.value_numeric ?? o.value_text ?? o.raw_value ?? "—"}</td><td>{display(o.unit)}</td><td>{o.trial_year || "—"} / {display(o.observation_location)}</td><td><details><summary>查看溯源</summary><p>来源编号：{display(o.source_file_id)}<br />工作表：{display(o.sheet_name)}<br />行号：{display(o.row_number)}<br />原始值：{display(o.raw_value)}<br />质量状态：{display(o.quality_label)}</p></details></td></tr>)}{!detail.observations.length && <tr><td colSpan={5}>暂无该材料的表型观测记录。</td></tr>}</tbody></table></div><div className="vs-pagination"><button className="secondary-button" disabled={detail.observation_page <= 1} onClick={() => openDetail(currentDetail.current.source, currentDetail.current.id, detail.observation_page - 1)}>上一页</button><span>第 {detail.observation_page} 页 · 每页最多 50 条</span><button className="secondary-button" disabled={!detail.observation_has_more} onClick={() => openDetail(currentDetail.current.source, currentDetail.current.id, detail.observation_page + 1)}>下一页</button></div></>}
    </>}</div><footer className="vs-detail-footer"><span>按 Esc 或点击窗口外可关闭</span><button className="secondary-button" type="button" onClick={() => dialog.current.close()}>关闭窗口</button></footer></dialog>
  </section>;
}
