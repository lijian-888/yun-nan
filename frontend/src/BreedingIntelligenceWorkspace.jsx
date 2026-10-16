import { useEffect, useState } from "react";
import { AlertTriangle, Download, FileSearch, LoaderCircle, Scale, ShieldCheck } from "lucide-react";
import { authorizedFetch, jsonRequest, request } from "./api";
import ParentSelectionWorkspace from "./ParentSelectionWorkspace";

const SECTION_LABELS = {
  basic: "种质基本信息",
  aliases: "别名",
  pedigree: "系谱",
  phenotype: "表型与试验观测",
  environment: "关联环境",
  genotype: "基因型",
  literature: "相关资料",
};

function DataTable({ rows }) {
  const normalized = (rows || []).filter((row) => row !== null && row !== undefined).map((row) => (
    typeof row === "object" ? row : { value: row }
  ));
  const columns = [...new Set(normalized.flatMap((row) => Object.keys(row)))].filter((key) => key !== "text").slice(0, 10);
  if (!normalized.length) return <p className="intelligence-empty">当前没有可用记录。</p>;
  return <div className="intelligence-table-wrap"><table className="intelligence-table"><thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{normalized.slice(0, 50).map((row, index) => <tr key={index}>{columns.map((column) => <td key={column}>{row[column] === null || row[column] === undefined ? "—" : typeof row[column] === "object" ? JSON.stringify(row[column]) : String(row[column])}</td>)}</tr>)}</tbody></table></div>;
}

async function download(path, fileName) {
  const response = await authorizedFetch(path);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || "下载失败");
  }
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  anchor.click();
  URL.revokeObjectURL(url);
}

export default function BreedingIntelligenceWorkspace({ onNotice }) {
  const [tab, setTab] = useState("analysis");
  const [materials, setMaterials] = useState([]);
  const [selectedMaterial, setSelectedMaterial] = useState("");
  const [analysis, setAnalysis] = useState(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const materialRows = await request("/api/research/intelligence/materials");
        setMaterials(materialRows);
        if (materialRows.length) setSelectedMaterial(materialRows[0].material_key);
      } catch (requestError) {
        setError(requestError.message || "无法读取院内种质证据。");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  async function runAnalysis() {
    if (!selectedMaterial) return;
    setRunning(true); setError(""); setAnalysis(null);
    try {
      setAnalysis(await request("/api/research/intelligence/material-analysis", jsonRequest("POST", { material_key: selectedMaterial })));
    } catch (requestError) {
      setError(requestError.message);
    } finally { setRunning(false); }
  }

  if (loading) return <div className="workspace-loading"><LoaderCircle className="spin" size={20} />正在读取种质证据…</div>;

  return <section className="intelligence-workspace">
    <header className="workspace-hero"><div><span>证据驱动 · 可追溯</span><h2>种质解析与亲本组合辅助推荐</h2><p>种质解析使用院内已导入证据；亲本推荐仅使用国家水稻数据中心种质。缺失内容不推断，推荐结果不是后代表现预测。</p></div><ShieldCheck size={30} /></header>
    <div className="workspace-tabs"><button className={tab === "analysis" ? "active" : ""} onClick={() => setTab("analysis")}><FileSearch size={16} />种质综合解析</button><button className={tab === "recommendation" ? "active" : ""} onClick={() => setTab("recommendation")}><Scale size={16} />亲本辅助推荐</button></div>
    {error && <div className="workspace-error"><AlertTriangle size={17} />{error}</div>}

    {tab === "analysis" ? <>
      <section className="workspace-card control-card"><label>选择院内种质材料<select value={selectedMaterial} onChange={(event) => setSelectedMaterial(event.target.value)}>{materials.map((item) => <option key={item.material_key} value={item.material_key}>{item.material_key} · {item.name}</option>)}</select></label><button className="primary-button" onClick={runAnalysis} disabled={!selectedMaterial || running}>{running ? <LoaderCircle className="spin" size={16} /> : <FileSearch size={16} />}生成综合解析</button></section>
      {!materials.length && <div className="workspace-empty">院内尚未导入种质资源，请先由数据处理员完成数据导入。</div>}
      {analysis && <>
        <section className="workspace-card analysis-summary"><div><small>综合解析运行 {analysis.run_id}</small><h3>{analysis.material_name}（{analysis.material_key}）</h3><p>{analysis.summary}</p></div><button onClick={() => download(`/api/research/intelligence/material-analysis/${analysis.run_id}/report.pdf`, `${analysis.material_key}-综合解析.pdf`).catch((downloadError) => onNotice?.(downloadError.message))}><Download size={16} />下载 PDF</button></section>
        {analysis.missing_categories?.length > 0 && <section className="workspace-warning"><AlertTriangle size={18} /><div><strong>缺失数据</strong><span>{analysis.missing_categories.join("、")}。系统未对这些内容生成结论。</span></div></section>}
        {Object.entries(analysis.sections || {}).map(([key, section]) => <section className="workspace-card evidence-section" key={key}><header><h3>{SECTION_LABELS[key] || key}</h3><span className={section.available ? "status-ready" : "status-missing"}>{section.available ? `${section.records?.length || 0} 条证据` : "缺失"}</span></header>{section.available ? <DataTable rows={section.records} /> : <p className="intelligence-empty">院内数据尚未导入或关联此类证据。</p>}</section>)}
        <section className="workspace-card"><h3>不确定性、异常与来源</h3><ul className="evidence-list">{analysis.uncertainties?.map((item) => <li key={item}>{item}</li>)}</ul><details><summary>查看 {analysis.sources?.length || 0} 条可追溯来源</summary><DataTable rows={analysis.sources} /></details></section>
      </>}
    </> : <ParentSelectionWorkspace onNotice={onNotice} />}
  </section>;
}
