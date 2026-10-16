import { useState } from "react";
import { AlertTriangle, CheckCircle2, Download, LoaderCircle, Plus, Scale, ShieldCheck, Trash2 } from "lucide-react";
import { authorizedFetch, jsonRequest, request, streamJsonLines } from "./api";
import RiceDataVarietyDrawer from "./RiceDataVarietyDrawer";
import { conditionValueOptions, localizeConditionText } from "./conditionValuePresentation";

const MODE_LABELS = {
  conventional: "常规稻杂交",
  hybrid_three_line: "三系杂交稻配组",
  hybrid_two_line: "两系杂交稻配组",
};
const SCOPE_LABELS = {
  every_parent: "每个亲本",
  at_least_one: "至少一个亲本",
  female: "母本",
  male: "父本",
  pair: "亲本组合",
};
const FIELD_OPTIONS = [
  ["growth_duration_days", "全生育期", "天"],
  ["plant_height_cm", "株高", "厘米"],
  ["yield_relative_change_pct", "较对照增产", "%"],
  ["leaf_blast_grade", "叶瘟等级", "级"],
  ["panicle_blast_grade", "穗瘟等级", "级"],
  ["panicle_neck_blast_grade", "穗颈瘟等级", "级"],
  ["cold_tolerance_grade", "耐冷性等级", "级"],
  ["lodging_resistance", "抗倒伏", ""],
  ["head_rice_rate_pct", "整精米率", "%"],
  ["chalkiness_pct", "垩白度", "%"],
  ["amylose_content_pct", "直链淀粉含量", "%"],
  ["rice_type", "籼粳类型", ""],
  ["variety_kind", "品种类型", ""],
];

function GoalParsingProgress({ events, parsing }) {
  if (!events.length) return null;
  return <div className="goal-parsing-progress" role="status" aria-live="polite">
    <strong>{parsing ? "正在理解育种目标" : "育种目标理解过程"}</strong>
    <ol>{events.map((event, index) => {
      const active = parsing && index === events.length - 1;
      return <li className={active ? "active" : "complete"} key={`${event.stage}-${index}`}>
        <span>{active ? <LoaderCircle className="spin" size={15} /> : <CheckCircle2 size={15} />}</span>
        <div><b>{event.message}</b>{event.elapsed_seconds ? <small>当前已等待 {event.elapsed_seconds} 秒</small> : null}</div>
      </li>;
    })}</ol>
    {parsing && <p>大模型只负责把自然语言转换为条件草稿；系统随后还会校验字段、单位和阈值。请勿重复提交。</p>}
  </div>;
}

function editableValue(condition) {
  return Array.isArray(condition.value) ? condition.value.join("～") : String(condition.value ?? "");
}

function parsedValue(condition, value) {
  if (condition.operator === "between") {
    return value.split(/[～~,，]/).map((item) => Number(item.trim())).filter(Number.isFinite).slice(0, 2);
  }
  if (condition.operator === "in") return value.split(/[，,]/).map((item) => item.trim()).filter(Boolean);
  const number = Number(value);
  return value.trim() !== "" && Number.isFinite(number) ? number : value;
}

async function download(path, name) {
  const response = await authorizedFetch(path);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "导出失败");
  }
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url; anchor.download = name; anchor.click();
  URL.revokeObjectURL(url);
}

function ConditionEditor({ conditions, onChange }) {
  function update(index, patch) {
    onChange(conditions.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));
  }
  function add() {
    const index = conditions.length + 1;
    onChange([...conditions, {
      code: `custom_${index}`, label: "自定义生育期条件", field: "growth_duration_days",
      operator: "between", value: [120, 160], unit: "天", kind: "hard",
      scope: "every_parent", importance: 2, require_trusted_evidence: true,
      evidence_policy: "consistent",
    }]);
  }
  return <div className="parent-condition-editor">
    {conditions.map((condition, index) => {
      const valueOptions = conditionValueOptions(condition.field);
      return <article className="parent-condition-row" key={`${condition.code}-${index}`}>
      <div className="parent-condition-heading">
        <input value={condition.label} aria-label="条件名称" onChange={(event) => update(index, { label: event.target.value })} />
        <button type="button" className="icon-button" title="删除条件" onClick={() => onChange(conditions.filter((_, itemIndex) => itemIndex !== index))}><Trash2 size={15} /></button>
      </div>
      <div className="parameter-grid">
        <label>判断字段<select value={condition.field} onChange={(event) => {
          const selected = FIELD_OPTIONS.find(([code]) => code === event.target.value);
          const nextOptions = conditionValueOptions(event.target.value);
          update(index, {
            field: event.target.value,
            unit: selected?.[2] || "",
            ...(nextOptions.length ? { operator: "eq", value: nextOptions[0].value } : {}),
          });
        }}>{FIELD_OPTIONS.map(([code, label]) => <option value={code} key={code}>{label}</option>)}</select></label>
        <label>条件性质<select value={condition.kind} onChange={(event) => update(index, { kind: event.target.value })}><option value="hard">硬门槛</option><option value="preference">排序偏好</option></select></label>
        <label>作用对象<select value={condition.scope} onChange={(event) => update(index, { scope: event.target.value })}>{Object.entries(SCOPE_LABELS).map(([code, label]) => <option value={code} key={code}>{label}</option>)}</select></label>
        <label>比较方式<select value={condition.operator} onChange={(event) => update(index, { operator: event.target.value })}><option value="between">在范围内</option><option value="gte">大于等于</option><option value="lte">小于等于</option><option value="gt">大于</option><option value="lt">小于</option><option value="eq">等于</option><option value="in">属于集合</option><option value="contains">包含文字</option></select></label>
        <label>目标值{condition.unit ? `（${condition.unit}）` : ""}{valueOptions.length
          ? <select value={String(condition.value ?? "")} onChange={(event) => update(index, { value: event.target.value })}>{valueOptions.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select>
          : <input value={editableValue(condition)} onChange={(event) => update(index, { value: parsedValue(condition, event.target.value) })} placeholder={condition.operator === "between" ? "例如 120～160" : "填写目标值"} />}</label>
        {condition.kind === "preference" && <label>重要程度<select value={condition.importance || 2} onChange={(event) => update(index, { importance: Number(event.target.value) })}><option value={3}>关键</option><option value={2}>重要</option><option value={1}>一般</option></select></label>}
      </div>
      <label className="inline-check"><input type="checkbox" checked={condition.require_trusted_evidence !== false} onChange={(event) => update(index, { require_trusted_evidence: event.target.checked })} />只使用已核验或结构化可信证据</label>
      {valueOptions.length > 0 && <small className="condition-value-help">请选择中文选项，系统会自动匹配数据库，无需填写英文或字段代码。</small>}
    </article>;
    })}
    <button type="button" onClick={add}><Plus size={15} />新增自定义条件</button>
  </div>;
}

const EVIDENCE_STATE_LABELS = { pass: "满足", fail: "不满足", unknown: "证据不足" };
const CONDITION_KIND_LABELS = { hard: "硬门槛", preference: "排序偏好" };

function scoreText(value) {
  return value == null ? "—" : Number(value).toFixed(1);
}

function FiveTraitComparison({ evaluation, onOpenParent }) {
  if (!evaluation) return null;
  return <section className="pair-five-trait">
    <div className="pair-section-heading"><div><strong>双亲五性综合评价对照</strong><span>已覆盖 {evaluation.covered_dimension_count}/5 个维度</span></div><small>{evaluation.boundary}</small></div>
    <div className="pair-five-trait-table-wrap"><table><thead><tr><th>评价维度</th><th>{evaluation.female.name}</th><th>{evaluation.male.name}</th><th>组合证据说明</th></tr></thead><tbody>
      {evaluation.dimensions.map((dimension) => <tr key={dimension.code}><th>{dimension.label}</th><td>{scoreText(dimension.female_score)}</td><td>{scoreText(dimension.male_score)}</td><td>{dimension.evidence_text}</td></tr>)}
      <tr className="pair-comprehensive-row"><th>五性综合分</th><td>{scoreText(evaluation.female.comprehensive_score)}</td><td>{scoreText(evaluation.male.comprehensive_score)}</td><td>只有五项齐全时才形成单亲综合分；不计算组合或后代综合分。</td></tr>
    </tbody></table></div>
    <div className="parent-evaluation-summaries">
      {[evaluation.female, evaluation.male].map((parent) => <article key={parent.variety_id}><strong>{parent.name}</strong><span>已有 {parent.scored_dimension_count}/5 项</span><p>{parent.missing_dimensions?.length ? `缺失：${parent.missing_dimensions.join("、")}` : "五项数据齐全"}</p><button type="button" onClick={() => onOpenParent(parent)}>查看完整品种信息</button></article>)}
    </div>
  </section>;
}

function PairCard({ item, runId, onReviewed, onNotice }) {
  const [reviewing, setReviewing] = useState(false);
  const [selectedParent, setSelectedParent] = useState(null);
  async function review(decision) {
    setReviewing(true);
    try {
      await request(`/api/research/parent-selection/runs/${runId}/review`, jsonRequest("POST", {
        female_variety_id: item.female_parent.variety_id,
        male_variety_id: item.male_parent.variety_id,
        decision,
        comment: "",
      }));
      onReviewed(`${item.female_parent.variety_id}-${item.male_parent.variety_id}`, decision);
    } catch (error) {
      onNotice?.(error.message);
    } finally { setReviewing(false); }
  }
  return <><article className="recommendation-item parent-pair-card">
    <div className={`recommendation-rank tier-${String(item.tier).toLowerCase()}`}>{item.tier === "pending" ? "待核实" : item.tier === "excluded" ? "排除" : `${item.tier}级`}</div>
    <div>
      <h4>{item.female_parent.name} × {item.male_parent.name}</h4>
      <p><strong>目标匹配 {Math.round(item.target_match * 100)}%</strong><span>证据覆盖 {Math.round(item.evidence_coverage * 100)}%</span><span>可信度 {item.confidence}</span></p>
      <dl>
        <dt>母本贡献</dt><dd>{item.female_contributions?.join("；") || "暂无明确偏好证据"}</dd>
        <dt>父本贡献</dt><dd>{item.male_contributions?.join("；") || "暂无明确偏好证据"}</dd>
        <dt>亲缘结论</dt><dd>{item.kinship?.reasons?.join("；") || "—"}</dd>
        <dt>数据缺口</dt><dd>{item.data_gaps?.join("；") || "无关键缺口"}</dd>
        {item.exclusion_reasons?.length > 0 && <><dt>排除原因</dt><dd>{item.exclusion_reasons.join("；")}</dd></>}
      </dl>
      {item.tier !== "excluded" && <section className="pair-qualification"><strong>为什么这个组合符合条件</strong>{item.qualification_reasons?.length ? <ul>{item.qualification_reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul> : <p>当前没有可单独列出的硬门槛通过理由，请查看条件证据。</p>}{item.recommendation_reasons?.length > 0 && <><b>目标性状贡献</b><ul>{item.recommendation_reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul></>}</section>}
      <FiveTraitComparison evaluation={item.five_trait_evaluation} onOpenParent={setSelectedParent} />
      <details className="pair-condition-details"><summary>逐项查看硬门槛、偏好和证据来源</summary><div>{item.condition_outcomes?.map((condition) => <article key={condition.condition_code}><header><strong>{condition.label}</strong><span className={`condition-state state-${condition.state}`}>{CONDITION_KIND_LABELS[condition.kind] || "条件"} · {EVIDENCE_STATE_LABELS[condition.state] || condition.state}</span></header><p>{condition.reasons?.join("；")}</p>{condition.evidence?.length > 0 && <ul>{condition.evidence.map((evidence, index) => <li key={`${evidence.source_id}-${index}`}><b>{evidence.value}{evidence.unit || ""}</b>{evidence.region ? ` · ${evidence.region}` : ""}{evidence.year ? ` · ${evidence.year}年` : ""}{evidence.source_url && <a href={evidence.source_url} target="_blank" rel="noreferrer">查看来源</a>}</li>)}</ul>}</article>)}</div>{item.kinship?.common_ancestors?.length > 0 && <div className="pair-common-ancestors"><strong>共同祖先</strong><ul>{item.kinship.common_ancestors.map((ancestor) => <li key={ancestor.identity}>{ancestor.name}（母本侧第 {ancestor.female_depth} 代，父本侧第 {ancestor.male_depth} 代）</li>)}</ul></div>}</details>
      {item.tier !== "excluded" && <div className="parent-review-actions"><span>育种专家审核：</span><button disabled={reviewing} onClick={() => review("accepted")}>接受</button><button disabled={reviewing} onClick={() => review("held")}>暂缓</button><button disabled={reviewing} onClick={() => review("rejected")}>否决</button></div>}
    </div>
  </article>{selectedParent && <RiceDataVarietyDrawer parent={selectedParent} onClose={() => setSelectedParent(null)} />}</>;
}

export default function ParentSelectionWorkspace({ onNotice }) {
  const [mode, setMode] = useState("conventional");
  const [goal, setGoal] = useState("");
  const [targetRegion, setTargetRegion] = useState("云南");
  const [conditions, setConditions] = useState([]);
  const [goalDraftId, setGoalDraftId] = useState("");
  const [draftConfirmed, setDraftConfirmed] = useState(false);
  const [draftSummary, setDraftSummary] = useState("");
  const [unresolved, setUnresolved] = useState([]);
  const [assumptions, setAssumptions] = useState([]);
  const [kinship, setKinship] = useState({ depth: 3, exclude_through_depth: 3, require_complete_pedigree: false });
  const [candidateLimit, setCandidateLimit] = useState(60);
  const [pinned, setPinned] = useState("");
  const [excluded, setExcluded] = useState("");
  const [parsing, setParsing] = useState(false);
  const [parseProgress, setParseProgress] = useState([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [reviews, setReviews] = useState({});

  function invalidateDraft() {
    setGoalDraftId(""); setDraftConfirmed(false); setConditions([]);
    setDraftSummary(""); setUnresolved([]); setAssumptions([]); setResult(null); setParseProgress([]);
  }

  function changeMode(value) {
    setMode(value); invalidateDraft();
  }

  async function parseGoal() {
    if (goal.trim().length < 2) { setError("请先用自然语言填写本次育种目标。"); return; }
    setParsing(true); setError(""); setResult(null); setDraftConfirmed(false); setParseProgress([]);
    try {
      const payload = await streamJsonLines("/api/research/parent-selection/goal-drafts/stream", jsonRequest("POST", {
        mode, breeding_goal: goal, target_region: targetRegion,
      }), (event) => {
        if (!["progress", "heartbeat"].includes(event.type)) return;
        setParseProgress((current) => {
          if (event.type === "heartbeat" && current.at(-1)?.stage === event.stage) {
            return [...current.slice(0, -1), event];
          }
          return [...current, event];
        });
      });
      setGoalDraftId(payload.goal_draft_id);
      setTargetRegion(payload.target_region || "");
      setConditions((payload.conditions || []).map((item) => ({ ...item })));
      setDraftSummary(payload.summary || "");
      setUnresolved(payload.unresolved_requirements || []);
      setAssumptions(payload.assumptions || []);
    } catch (requestError) {
      setGoalDraftId(""); setConditions([]); setError(requestError.message);
    } finally { setParsing(false); }
  }

  function ids(value) {
    return [...new Set(value.split(/[，,\s]+/).map((item) => Number(item)).filter((item) => Number.isInteger(item) && item > 0))];
  }

  async function run() {
    if (!goalDraftId) { setError("请先让大模型理解本次育种目标。"); return; }
    if (!draftConfirmed) { setError("请核对结构化条件，并勾选确认后再执行推荐。"); return; }
    if (!conditions.length) { setError("至少需要一个经确认的可执行条件。"); return; }
    if (conditions.some((item) => item.operator === "between" && (!Array.isArray(item.value) || item.value.length !== 2))) { setError("范围条件必须填写两个有效边界值。"); return; }
    setRunning(true); setError(""); setResult(null); setReviews({});
    try {
      const payload = await request("/api/research/parent-selection/runs", jsonRequest("POST", {
        mode, breeding_goal: goal, goal_draft_id: goalDraftId, draft_confirmed: true,
        target_region: targetRegion, conditions,
        pinned_variety_ids: ids(pinned), excluded_variety_ids: ids(excluded),
        candidate_limit: Number(candidateLimit), result_limit: 10, kinship,
      }));
      setResult(payload);
    } catch (requestError) {
      setError(requestError.message);
    } finally { setRunning(false); }
  }

  return <div className="parent-selection-workspace">
    <section className="workspace-card parent-step">
      <header><span>步骤 1</span><h3>填写育种目标并由大模型理解</h3></header>
      <div className="parameter-grid">
        <label>育种路线<select value={mode} onChange={(event) => changeMode(event.target.value)}>{Object.entries(MODE_LABELS).map(([code, label]) => <option value={code} key={code}>{label}</option>)}</select></label>
        <label>目标地区（可在目标中说明）<input value={targetRegion} onChange={(event) => { setTargetRegion(event.target.value); invalidateDraft(); }} placeholder="例如：云南" /></label>
      </div>
      <label>自然语言育种目标<textarea value={goal} onChange={(event) => { setGoal(event.target.value); invalidateDraft(); }} placeholder="例如：面向云南籼型常规稻，生育期必须在135～155天，叶瘟不高于5级；优先选择较对照增产不低于3%，且至少一个亲本整精米率不低于55%的组合。" /></label>
      <p>请写清“必须达到”和“优先考虑”的性状；没有给出的数值阈值，大模型不会自行编造。</p>
      <GoalParsingProgress events={parseProgress} parsing={parsing} />
      {error && <div className="workspace-error"><AlertTriangle size={17} />{error}</div>}
      <button className="primary-button" disabled={parsing || goal.trim().length < 2} onClick={parseGoal}>{parsing ? <LoaderCircle className="spin" size={16} /> : <Scale size={16} />}{parsing ? "大模型正在理解目标…" : "让大模型理解育种目标"}</button>
    </section>
    {goalDraftId && <section className="workspace-card parent-step"><header><span>步骤 2</span><h3>核对大模型生成的条件草案</h3></header>{draftSummary && <p>{localizeConditionText(draftSummary)}</p>}{assumptions.length > 0 && <div className="workspace-warning"><AlertTriangle size={18} /><span>需确认的理解：{assumptions.map(localizeConditionText).join("；")}</span></div>}{unresolved.length > 0 && <div className="workspace-warning"><AlertTriangle size={18} /><span>尚不能执行的目标：{unresolved.map(localizeConditionText).join("；")}。请补充阈值后重新解析，或在下方手工新增条件。</span></div>}<ConditionEditor conditions={conditions} onChange={(items) => { setConditions(items); setDraftConfirmed(false); }} /><label className="inline-check parent-draft-confirm"><input type="checkbox" checked={draftConfirmed} disabled={!conditions.length} onChange={(event) => setDraftConfirmed(event.target.checked)} />我已核对育种路线、硬门槛、排序偏好、作用对象和阈值，同意按当前条件执行。</label></section>}
    {goalDraftId && <section className="workspace-card parent-step"><header><span>步骤 3</span><h3>设置候选范围和亲缘检查</h3></header><div className="parameter-grid"><label>自动召回候选数量<input type="number" min="2" max={mode === "conventional" ? 120 : 80} value={candidateLimit} onChange={(event) => setCandidateLimit(event.target.value)} /></label><label>检查祖先代数<select value={kinship.depth} onChange={(event) => { setKinship({ ...kinship, depth: Number(event.target.value), exclude_through_depth: Math.min(kinship.exclude_through_depth, Number(event.target.value)) }); setDraftConfirmed(false); }}><option value={3}>三代</option><option value={5}>五代</option></select></label><label>共同祖先排除范围<select value={kinship.exclude_through_depth} onChange={(event) => { setKinship({ ...kinship, exclude_through_depth: Number(event.target.value) }); setDraftConfirmed(false); }}><option value={2}>两代内</option><option value={3}>三代内</option>{kinship.depth === 5 && <option value={5}>五代内</option>}</select></label><label className="inline-check"><input type="checkbox" checked={kinship.require_complete_pedigree} onChange={(event) => { setKinship({ ...kinship, require_complete_pedigree: event.target.checked }); setDraftConfirmed(false); }} />系谱不完整时必须进入待核实</label></div><label>必评 RiceData 品种编号（可不填）<input value={pinned} onChange={(event) => setPinned(event.target.value)} placeholder="逗号分隔；必评仍不能绕过硬门槛" /></label><label>排除 RiceData 品种编号（可不填）<input value={excluded} onChange={(event) => setExcluded(event.target.value)} placeholder="逗号分隔" /></label></section>}
    {goalDraftId && <section className="workspace-card parent-step"><header><span>步骤 4</span><h3>执行确定性分析</h3></header><div className="workspace-warning"><ShieldCheck size={18} /><span>大模型只负责理解目标；品种筛选、三代/五代近亲判定和组合评分均由可复核规则完成。候选仅来自国家水稻数据中心。</span></div>{error && <div className="workspace-error"><AlertTriangle size={17} />{error}</div>}<button className="primary-button" disabled={running || !conditions.length || !draftConfirmed} onClick={run}>{running ? <LoaderCircle className="spin" size={16} /> : <Scale size={16} />}{running ? "正在检索与计算…" : "生成亲本推荐"}</button></section>}
    {result && <section className="workspace-card recommendation-results parent-step"><header><div><span>步骤 5</span><h3>查看结果并由育种专家确认</h3><p>{result.disclaimer}</p><p>候选 {result.candidate_count} 个，形成 {result.pair_count} 个组合；正式推荐 {result.formal_count}，待核实 {result.pending_count}，已排除 {result.excluded_count}。</p></div><div className="download-actions"><button onClick={() => download(`/api/research/parent-selection/runs/${result.run_id}/report.pdf`, "亲本推荐报告.pdf").catch((error) => onNotice?.(error.message))}><Download size={15} />PDF</button><button onClick={() => download(`/api/research/parent-selection/runs/${result.run_id}/result.csv`, "亲本推荐结果.csv").catch((error) => onNotice?.(error.message))}><Download size={15} />CSV</button></div></header>{result.selection_warning && <div className="workspace-warning"><AlertTriangle size={18} />{result.selection_warning}</div>}<h3>优先推荐</h3>{result.recommendations.length ? result.recommendations.map((item) => <PairCard key={`${item.female_parent.variety_id}-${item.male_parent.variety_id}`} item={item} runId={result.run_id} onNotice={onNotice} onReviewed={(key, value) => setReviews({ ...reviews, [key]: value })} />) : <p className="intelligence-empty">当前没有进入正式等级的组合。</p>}<details><summary>待核实组合（展示 {result.pending_verification.length}/{result.pending_count}）</summary>{result.pending_verification.map((item) => <PairCard key={`pending-${item.female_parent.variety_id}-${item.male_parent.variety_id}`} item={item} runId={result.run_id} onNotice={onNotice} onReviewed={(key, value) => setReviews({ ...reviews, [key]: value })} />)}</details><details><summary>已排除组合（展示 {result.excluded_combinations.length}/{result.excluded_count}）</summary>{result.excluded_combinations.map((item) => <PairCard key={`excluded-${item.female_parent.variety_id}-${item.male_parent.variety_id}`} item={item} runId={result.run_id} onNotice={onNotice} onReviewed={() => {}} />)}</details>{Object.keys(reviews).length > 0 && <div className="workspace-warning"><CheckCircle2 size={18} />已记录 {Object.keys(reviews).length} 个专家审核决定。</div>}</section>}
  </div>;
}
