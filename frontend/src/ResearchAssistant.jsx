import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowDown,
  BarChart3,
  Bot,
  CircleHelp,
  ChevronDown,
  Dna,
  FileText,
  FileDown,
  LoaderCircle,
  LogOut,
  MessageSquarePlus,
  Paperclip,
  Pause,
  Pencil,
  Play,
  Plus,
  GitBranch,
  Search,
  Scale,
  SendHorizontal,
  ShieldCheck,
  Sparkles,
  Square,
  Trash2,
  Upload,
  UserRound,
  X,
} from "lucide-react";
import { keycloak } from "./auth";
import { authorizedFetch, request } from "./api";
import KnowledgeLibrary from "./KnowledgeLibrary";
import ResultsLibrary from "./ResultsLibrary";
import GwasWorkspace from "./GwasWorkspace";
import GenotypeAssetsWorkspace from "./GenotypeAssetsWorkspace";
import SkillLibrary from "./SkillLibrary";
import BreedingIntelligenceWorkspace from "./BreedingIntelligenceWorkspace";
import TrialAnalysisWorkspace from "./TrialAnalysisWorkspace";
import VarietySearchWorkspace from "./VarietySearchWorkspace";
import PedigreeWorkspace from "./PedigreeWorkspace";

const AGENT_NAME = "隆耘 Agent 育种智能体";

function formatSize(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function localMessage(role, content) {
  return {
    id: `local-${crypto.randomUUID()}`,
    role,
    content,
    evidence: [],
    operation_state: [],
    created_at: new Date().toISOString(),
  };
}

function isVisionAttachment(item) {
  const fileName = item?.file_name || item?.name || "";
  return item?.parsing_status === "image_ready" || /\.(png|jpe?g|webp)$/i.test(fileName);
}

function parseSseBlock(block) {
  const event = block.match(/^event:\s*(.+)$/m)?.[1]?.trim() || "message";
  const data = block.match(/^data:\s*(.+)$/m)?.[1];
  if (!data) return null;
  try {
    return { event, data: JSON.parse(data) };
  } catch {
    return null;
  }
}

function stripGeneratedReportInstructions(content) {
  // Report download is a controlled platform action. Some model providers
  // append a second, malformed "PDF report instructions" table after the
  // research conclusion; keeping that table causes its traceability fields
  // to fall into the first column. The real download affordance is rendered
  // below from `report_available`, so this presentation-only tail is removed.
  return content.replace(
    /(?:\n|^)\s*(?:#{1,6}\s*)?(?:[一二三四五六七八九十]+[、.．]\s*)?关于\s*PDF\s*报告(?:文档|下载|生成|说明)?[\s\S]*$/i,
    "",
  ).trim();
}

function markdownForDisplay(content, streaming, suppressReportInstructions = false) {
  const originalContent = String(content || "");
  if (/<\/?think\b|<\/?tool\b|<\/?query\b|<\/?domains\b|<\/?function(?:_call)?\b|<tool_call\b|<analysis\b/i.test(originalContent)) {
    return "本轮回答因模型工具协议异常已被系统拦截，未作为有效科研结论保留。请重新提问。";
  }
  // Earlier MinerU imports contained Markdown-escaped `\\~` range markers.
  // Render them as a readable Chinese range marker in historical answers too.
  const normalizedContent = (suppressReportInstructions
    ? stripGeneratedReportInstructions(originalContent)
    : originalContent
  ).replace(/\\~/g, "～");
  if (!streaming) return normalizedContent;
  // During streaming, hide an unfinished bold marker until the closing marker
  // arrives. The persisted answer is always rendered from the original text.
  const boldMarkerCount = (normalizedContent.match(/\*\*/g) || []).length;
  return boldMarkerCount % 2 ? normalizedContent.replace(/\*\*([^*]*)$/, "$1") : normalizedContent;
}

function AssistantMarkdown({ content, streaming, suppressReportInstructions = false }) {
  const displayContent = markdownForDisplay(content, streaming, suppressReportInstructions);
  return <div className="assistant-markdown"><ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        h1: ({ children }) => <h3>{children}</h3>,
        h2: ({ children }) => <h3>{children}</h3>,
        h3: ({ children }) => <h4>{children}</h4>,
        // Preserve section spacing without showing model-generated `---` rules.
        hr: () => <div className="assistant-section-gap" aria-hidden="true" />,
        table: ({ children }) => <div className="assistant-table-scroll" tabIndex={0} role="region" aria-label="数据表"><table>{children}</table></div>,
        a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer">{children}</a>,
      }}
    >{displayContent}</ReactMarkdown></div>;
}

function ReportDownloadCard({ message, onDownload }) {
  const operation = (message.operation_state || []).find((item) => item?.state === "report_ready");
  const breedingReport = operation?.report_kind === "breeding_dossier";
  const title = breedingReport ? "品种选育报告（审定辅助草稿）" : "本轮科研分析报告";
  const description = breedingReport
    ? "报告仅汇总已发布试验表现和已标注的演示育种档案；正式申报前仍需以农科院原始材料复核。"
    : "报告仅使用本轮已确认的结构化数据、图表和证据来源生成，不采用模型自行推测的数值。";
  return <section className="assistant-report-card" aria-label="本轮报告下载">
    <div>
      <span>研究产物已准备</span>
      <strong>{title}</strong>
      <p>{description}</p>
    </div>
    <button className="report-download-button" type="button" onClick={onDownload}>
      <FileDown size={15} />下载 PDF 报告
    </button>
  </section>;
}



export default function ResearchAssistant({ platformContext }) {
  const [user, setUser] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [activeSessionId, setActiveSessionId] = useState("");
  const [messages, setMessages] = useState([]);
  const [attachments, setAttachments] = useState([]);
  const [composerAttachmentIds, setComposerAttachmentIds] = useState([]);
  const [draft, setDraft] = useState("");
  const [clarificationDraft, setClarificationDraft] = useState("");
  const [notice, setNotice] = useState("");
  const [businessPolicy, setBusinessPolicy] = useState(null);
  const [progress, setProgress] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [paused, setPaused] = useState(false);
  const [canPause, setCanPause] = useState(false);
  const [pauseChanging, setPauseChanging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [preview, setPreview] = useState(null);
  const [imagePreviewUrls, setImagePreviewUrls] = useState({});
  const [pendingImages, setPendingImages] = useState([]);
  const [isDragActive, setIsDragActive] = useState(false);
  const [workspace, setWorkspace] = useState("assistant");
  const [knowledgeScope, setKnowledgeScope] = useState("both");
  const [accountOpen, setAccountOpen] = useState(false);
  const [showLatestButton, setShowLatestButton] = useState(false);
  const fileInputRef = useRef(null);
  const composerTextareaRef = useRef(null);
  const chatLogRef = useRef(null);
  const followLatestRef = useRef(true);
  const imagePreviewUrlsRef = useRef({});
  const activeAiTaskIdRef = useRef("");
  const activeAiAbortRef = useRef(null);
  const retryRequestRef = useRef(null);

  const activeSession = sessions.find((item) => item.id === activeSessionId);
  const attachmentById = new Map(attachments.map((item) => [item.id, item]));
  const composerAttachments = attachments.filter((item) => composerAttachmentIds.includes(item.id));

  const isGenerating = sending || messages.some((item) => item.streaming);

  function isNearChatBottom(container) {
    return container.scrollHeight - container.scrollTop - container.clientHeight < 72;
  }

  function scrollChatToLatest() {
    const container = chatLogRef.current;
    if (!container) return;
    followLatestRef.current = true;
    setShowLatestButton(false);
    container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
  }

  function handleChatScroll(event) {
    const isNearBottom = isNearChatBottom(event.currentTarget);
    followLatestRef.current = isNearBottom;
    setShowLatestButton(!isNearBottom);
  }

  useEffect(() => {
    const container = chatLogRef.current;
    if (!container) return;
    if (followLatestRef.current) {
      container.scrollTop = container.scrollHeight;
      setShowLatestButton(false);
    } else if (isGenerating) {
      setShowLatestButton(true);
    }
  }, [messages, progress, isGenerating]);

  useEffect(() => {
    let cancelled = false;
    const imageAttachments = attachments.filter(isVisionAttachment);
    const activeIds = new Set(imageAttachments.map((item) => item.id));
    const missing = imageAttachments.filter((item) => !imagePreviewUrlsRef.current[item.id]);

    async function loadImagePreviews() {
      const loaded = await Promise.all(missing.map(async (item) => {
        try {
          const response = await authorizedFetch(`/api/research/attachments/${item.id}/image`);
          if (!response.ok) throw new Error("图片读取失败");
          return [item.id, URL.createObjectURL(await response.blob())];
        } catch {
          return null;
        }
      }));
      if (cancelled) {
        loaded.forEach((entry) => entry && URL.revokeObjectURL(entry[1]));
        return;
      }
      setImagePreviewUrls((current) => {
        const next = {};
        Object.entries(current).forEach(([id, url]) => {
          if (activeIds.has(id)) next[id] = url;
          else URL.revokeObjectURL(url);
        });
        loaded.forEach((entry) => {
          if (entry) next[entry[0]] = entry[1];
        });
        imagePreviewUrlsRef.current = next;
        return next;
      });
    }

    void loadImagePreviews();
    return () => { cancelled = true; };
  }, [attachments]);

  useEffect(() => () => {
    Object.values(imagePreviewUrlsRef.current).forEach((url) => URL.revokeObjectURL(url));
  }, []);

  async function loadConversation(sessionId) {
    if (!sessionId) return;
    const [messageList, attachmentList] = await Promise.all([
      request(`/api/research/sessions/${sessionId}/messages`),
      request(`/api/research/sessions/${sessionId}/attachments`),
    ]);
    followLatestRef.current = true;
    setShowLatestButton(false);
    setMessages(messageList);
    setClarificationDraft("");
    setAttachments(attachmentList);
    const sentAttachmentIds = new Set(messageList.flatMap((message) => (message.evidence || [])
      .filter((item) => item.type === "message_attachment" && item.attachment_id)
      .map((item) => item.attachment_id)));
    // Legacy files created before message-bound attachments stay visible once,
    // so the researcher can explicitly send them with the next question.
    setComposerAttachmentIds(attachmentList
      .filter((item) => !sentAttachmentIds.has(item.id))
      .map((item) => item.id));
    setActiveSessionId(sessionId);
  }

  async function loadSessions(preferredId = "", { reloadConversation = true } = {}) {
    const sessionList = await request("/api/research/sessions");
    setSessions(sessionList);
    const targetId = preferredId || (sessionList.some((item) => item.id === activeSessionId) ? activeSessionId : sessionList[0]?.id);
    if (targetId && reloadConversation) await loadConversation(targetId);
    return sessionList;
  }

  async function createSession(title = "新会话") {
    const session = await request("/api/research/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    await loadSessions(session.id);
    return session;
  }

  useEffect(() => {
    (async () => {
      try {
        const currentUser = await request("/api/research/me");
        setUser(currentUser);
        request("/api/research/business-data/catalog").then(setBusinessPolicy).catch(() => setBusinessPolicy(null));
        const currentSessions = await loadSessions();
        if (!currentSessions.length) await createSession();
      } catch (error) {
        setNotice(error.message);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  async function renameSession(targetSession) {
    if (!targetSession) return;
    const title = window.prompt("会话名称", targetSession.title);
    if (!title?.trim()) return;
    try {
      await request(`/api/research/sessions/${targetSession.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title.trim() }),
      });
      await loadSessions(activeSessionId || targetSession.id);
    } catch (error) {
      setNotice(error.message);
    }
  }

  async function deleteSession(targetSession) {
    if (!targetSession || !window.confirm(`删除“${targetSession.title}”及其私有附件、解析文本和上下文？此操作不可恢复。`)) return;
    try {
      await request(`/api/research/sessions/${targetSession.id}`, { method: "DELETE" });
      const remaining = await request("/api/research/sessions");
      setSessions(remaining);
      if (targetSession.id === activeSessionId) {
        if (remaining[0]) await loadConversation(remaining[0].id);
        else await createSession();
      }
    } catch (error) {
      setNotice(error.message);
    }
  }

  async function uploadFiles(files) {
    if (!files.length || !activeSessionId) return;
    const pending = files.filter(isVisionAttachment).map((file) => ({
      id: `pending-${crypto.randomUUID()}`,
      file_name: file.name,
      image_url: URL.createObjectURL(file),
    }));
    if (pending.length) setPendingImages((items) => [...items, ...pending]);
    setUploading(true);
    setNotice("");
    try {
      const uploadedIds = [];
      for (const file of files) {
        const form = new FormData();
        form.append("file", file);
        const created = await request(`/api/research/sessions/${activeSessionId}/attachments`, { method: "POST", body: form });
        uploadedIds.push(created.id);
      }
      const attachmentList = await request(`/api/research/sessions/${activeSessionId}/attachments`);
      setAttachments(attachmentList);
      setComposerAttachmentIds((current) => [...new Set([...current, ...uploadedIds])]);
    } catch (error) {
      setNotice(error.message);
    } finally {
      setUploading(false);
      setPendingImages((items) => items.filter((item) => !pending.some((pendingItem) => pendingItem.id === item.id)));
      pending.forEach((item) => URL.revokeObjectURL(item.image_url));
    }
  }

  async function uploadFileInput(event) {
    const files = Array.from(event.target.files || []);
    event.target.value = "";
    await uploadFiles(files);
  }

  async function pasteImages(event) {
    const items = Array.from(event.clipboardData?.items || []);
    const clipboardFiles = Array.from(event.clipboardData?.files || [])
      .filter((file) => file.type.startsWith("image/"));
    const files = (clipboardFiles.length ? clipboardFiles : items
      .filter((item) => item.type.startsWith("image/"))
      .map((item, index) => {
        const blob = item.getAsFile();
        if (!blob) return null;
        const extension = blob.type === "image/png" ? "png" : blob.type === "image/webp" ? "webp" : "jpg";
        return new File([blob], `粘贴图片-${Date.now()}-${index + 1}.${extension}`, { type: blob.type });
      })
      .filter(Boolean));
    if (!files.length) return;
    event.preventDefault();
    setNotice(`已从剪贴板读取 ${files.length} 张图片，正在本地保存；提问时将原图直接交给当前配置的大模型进行视觉分析。`);
    await uploadFiles(files);
  }

  async function removeAttachment(attachmentId) {
    try {
      await request(`/api/research/attachments/${attachmentId}`, { method: "DELETE" });
      setComposerAttachmentIds((items) => items.filter((item) => item !== attachmentId));
      await loadConversation(activeSessionId);
    } catch (error) {
      setNotice(error.message);
    }
  }

  async function showPreview(attachment) {
    if (isVisionAttachment(attachment)) {
      const imageUrl = imagePreviewUrls[attachment.id];
      if (!imageUrl) {
        setNotice("图片预览正在加载，请稍候再点击查看。");
        return;
      }
      setPreview({
        file_name: attachment.file_name,
        parsing_status: "image_ready",
        image_url: imageUrl,
        parser_warnings: attachment.parser_warnings || [],
      });
      return;
    }
    try {
      setPreview(await request(`/api/research/attachments/${attachment.id}/preview`));
    } catch (error) {
      setNotice(error.message);
    }
  }

  function dropFiles(event) {
    event.preventDefault();
    setIsDragActive(false);
    const files = Array.from(event.dataTransfer?.files || []);
    if (files.length) void uploadFiles(files);
  }

  function applyTrialAnalysisPrompt(prompt) {
    setDraft(prompt);
    window.requestAnimationFrame(() => composerTextareaRef.current?.focus());
  }

  function openResearchSkill(skill) {
    setWorkspace(skill.workspace);
    if (skill.suggested_question) {
      setDraft(skill.suggested_question);
      window.requestAnimationFrame(() => composerTextareaRef.current?.focus());
    }
    setNotice(`${skill.name}已打开。${skill.suggested_question ? "已填入一条可直接体验的示例问题，可修改后发送。" : "请按受控流程准备输入并确认分析计划。"}`);
  }

  async function downloadResearchReport(messageId, automatic = false) {
    try {
      const response = await authorizedFetch(`/api/research/messages/${messageId}/report.pdf`);
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "无法生成 PDF 报告。");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `农业科研分析报告-${new Date().toISOString().slice(0, 10)}.pdf`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      if (!automatic) setNotice("PDF 报告已开始下载，并已保存到当前账号的结果库。");
    } catch (error) {
      const detail = error instanceof Error ? error.message : "PDF 报告生成失败。";
      setNotice(automatic ? `回答已完成，但 PDF 未能自动下载：${detail}。可点击回答下方“下载 PDF 报告”重试。` : detail);
    }
  }

  async function submitQuestion({ content, clarificationMessageId = "", clarificationAction = "" }) {
    content = content.trim();
    if (!content || sending || !activeSessionId) return;
    setSending(true);
    setPaused(false);
    setCanPause(false);
    setProgress("正在提交问题");
    setNotice("");
    if (!clarificationAction) setDraft("");
    followLatestRef.current = true;
    setShowLatestButton(false);
    const currentTurnAttachmentIds = clarificationAction ? [] : composerAttachmentIds.filter((id) => attachmentById.has(id));
    const requestFingerprint = JSON.stringify({ content, knowledgeScope, attachmentIds: [...currentTurnAttachmentIds].sort(), clarificationMessageId, clarificationAction });
    const idempotencyKey = retryRequestRef.current?.fingerprint === requestFingerprint
      ? retryRequestRef.current.key
      : crypto.randomUUID();
    const abortController = new AbortController();
    activeAiAbortRef.current = abortController;
    activeAiTaskIdRef.current = "";
    const currentTurnAttachments = currentTurnAttachmentIds.map((id) => attachmentById.get(id));
    const userEntry = {
      ...localMessage("user", content),
      evidence: currentTurnAttachments.map((item) => ({
        type: "message_attachment",
        attachment_id: item.id,
        title: item.file_name,
        is_image: isVisionAttachment(item),
        size_bytes: item.size_bytes,
      })),
    };
    const assistantEntry = { ...localMessage("assistant", ""), streaming: true };
    if (!clarificationAction) setComposerAttachmentIds([]);
    setMessages((items) => [...items, userEntry, assistantEntry]);

    try {
      const response = await authorizedFetch(`/api/research/sessions/${activeSessionId}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content,
          knowledge_scope: knowledgeScope,
          attachment_ids: currentTurnAttachmentIds,
          idempotency_key: idempotencyKey,
          ...(clarificationAction ? { clarification_message_id: clarificationMessageId, clarification_action: clarificationAction } : {}),
        }),
        signal: abortController.signal,
      });
      if (!response.ok || !response.body) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || "无法开始模型分析。");
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let completed = false;
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const events = buffer.split("\n\n");
        buffer = events.pop() || "";
        for (const rawEvent of events) {
          const parsed = parseSseBlock(rawEvent);
          if (!parsed) continue;
          if (parsed.event === "session_title") {
            setSessions((items) => items.map((item) => item.id === parsed.data.session_id
              ? { ...item, title: parsed.data.title }
              : item));
          } else if (parsed.event === "status") {
            if (parsed.data.task_id) activeAiTaskIdRef.current = parsed.data.task_id;
            if (parsed.data.label === "AI 任务已取得执行槽") setCanPause(true);
            if (typeof parsed.data.paused === "boolean") setPaused(parsed.data.paused);
            setProgress(parsed.data.label || "正在处理");
          } else if (parsed.event === "token") {
            setMessages((items) => items.map((item) => item.id === assistantEntry.id
              ? { ...item, content: item.content + parsed.data.text }
              : item));
          } else if (parsed.event === "complete") {
            completed = true;
            retryRequestRef.current = null;
            if (clarificationAction) setClarificationDraft("");
            setMessages((items) => items.map((item) => item.id === assistantEntry.id ? parsed.data.message : item));
            if (parsed.data.message?.report_available) {
              // The user already explicitly asked for a report in this turn.
              // Start its transient download without another confirmation; the
              // visible button remains available if a browser blocks it.
              void downloadResearchReport(parsed.data.message.id, true);
            }
            // The streamed answer is already in local state. Refresh only the sidebar
            // metadata so completing a response never pulls the reader back to the bottom.
            await loadSessions(activeSessionId, { reloadConversation: false });
          } else if (parsed.event === "error") {
            throw new Error(parsed.data.detail || "模型分析未完成。");
          }
        }
        if (done) break;
      }
      if (!completed) throw new Error("模型连接已结束，但没有返回完整答案。");
    } catch (error) {
      const detail = error instanceof Error && error.message
        ? error.message
        : "模型分析未完成，未获得可用的错误说明。";
      if (clarificationAction) {
        setMessages((items) => items.filter((item) => item.id !== userEntry.id && item.id !== assistantEntry.id));
      } else {
        setMessages((items) => items.map((item) => item.id === assistantEntry.id
          ? { ...item, streaming: false, content: `分析未完成：${detail}`, error: true }
          : item));
        setComposerAttachmentIds((items) => [...new Set([...currentTurnAttachmentIds, ...items])]);
        setDraft(content);
      }
      retryRequestRef.current = { fingerprint: requestFingerprint, key: idempotencyKey };
      setNotice(detail);
    } finally {
      activeAiTaskIdRef.current = "";
      activeAiAbortRef.current = null;
      setSending(false);
      setPaused(false);
      setCanPause(false);
      setProgress("");
    }
  }

  function sendQuestion(event) {
    event?.preventDefault();
    void submitQuestion({ content: draft });
  }

  function submitClarification(messageId, action) {
    const content = action === "skip" ? "暂不补充，按已有信息继续" : clarificationDraft.trim();
    if (!content) return;
    void submitQuestion({ content, clarificationMessageId: messageId, clarificationAction: action });
  }

  async function cancelGeneration() {
    const taskId = activeAiTaskIdRef.current;
    if (taskId) {
      await authorizedFetch(`/api/ai/tasks/${taskId}/cancel`, { method: "POST" }).catch(() => null);
    }
    activeAiAbortRef.current?.abort();
    setNotice("已请求取消当前 AI 任务；未完成内容不会保存为最终结果。");
  }

  async function togglePause() {
    const taskId = activeAiTaskIdRef.current;
    if (!taskId || pauseChanging) return;
    setPauseChanging(true);
    try {
      const action = paused ? "resume" : "pause";
      const response = await authorizedFetch(`/api/ai/tasks/${taskId}/${action}`, { method: "POST" });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || "无法切换任务状态。");
      }
      const task = await response.json();
      const isPaused = task.status === "paused";
      setPaused(isPaused);
      setProgress(isPaused ? "当前任务已暂停" : "正在继续执行当前任务");
      setNotice("");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "切换任务状态失败。");
    } finally {
      setPauseChanging(false);
    }
  }

  function sendOnEnter(event) {
    if (sending) return;
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    void sendQuestion();
  }

  if (loading) return <main className="assistant-loading"><LoaderCircle size={24} className="spin" />正在验证科研账号…</main>;

  return <div className="research-shell">
    <aside className="research-sidebar">
      <div className="research-brand"><div className="research-brand-mark brand-logo-mark" aria-hidden="true"><img src="/brand/longyun-agent-logo.png" alt="" /></div><div><strong>{AGENT_NAME}</strong><span>{businessPolicy?.shared_workspace ? "服务器共享业务数据 + 大模型" : "已发布标准数据 + 大模型"}</span></div></div>
      <section className="research-project-context" aria-label="当前机构"><span>{platformContext?.institution?.name || "云南省农业科学院"}</span></section>
      <nav className="research-workspaces" aria-label="科研工作台">
        <section className="research-workspace-group" aria-label="对话">
          <small>对话</small>
          <button type="button" className={workspace === "assistant" ? "active" : ""} onClick={() => setWorkspace("assistant")}><Bot size={16} />{AGENT_NAME}</button>
        </section>
        <section className="research-workspace-group" aria-label="管理">
          <small>管理</small>
          <button type="button" className={workspace === "structured" ? "active" : ""} onClick={() => { setWorkspace("structured"); setNotice(""); }}><Search size={16} />品种与原文件</button>
          <button type="button" className={workspace === "pedigree" ? "active" : ""} onClick={() => { setWorkspace("pedigree"); setNotice(""); }}><GitBranch size={16} />品种系谱图谱</button>
          <button type="button" className={workspace === "intelligence" ? "active" : ""} onClick={() => setWorkspace("intelligence")}><Scale size={16} />种质解析与亲本推荐</button>
          <button type="button" className={workspace === "trial-analysis" ? "active" : ""} onClick={() => setWorkspace("trial-analysis")}><BarChart3 size={16} />田间试验自动分析</button>
          <button type="button" className={workspace === "genotype" ? "active" : ""} onClick={() => setWorkspace("genotype")}><Dna size={16} />基因型导入与质控</button>
          <button type="button" className={workspace === "skills" ? "active" : ""} onClick={() => setWorkspace("skills")}><Sparkles size={16} />技能库</button>
          <button type="button" className={workspace === "knowledge" ? "active" : ""} onClick={() => setWorkspace("knowledge")}><FileText size={16} />知识库</button>
          <button type="button" className={workspace === "results" ? "active" : ""} onClick={() => setWorkspace("results")}><FileDown size={16} />结果库</button>
        </section>
      </nav>
      <button className="new-session-button" onClick={() => createSession()}><MessageSquarePlus size={17} />新建会话</button>
      <div className="conversation-list" aria-label="历史会话">
        <small>历史会话</small>
        {sessions.map((item) => <div key={item.id} className={`conversation-item-row ${item.id === activeSessionId ? "active" : ""}`}><button className="conversation-item" onClick={() => loadConversation(item.id)}>{item.title}</button><div className="conversation-item-actions"><button title="重命名会话" onClick={() => renameSession(item)}><Pencil size={14} /></button><button className="delete" title="删除会话" onClick={() => deleteSession(item)}><Trash2 size={14} /></button></div></div>)}
      </div>
      <div className="research-sidebar-bottom">
        {accountOpen && <div className="research-account-menu"><div><strong>{user?.display_name || user?.username}</strong><span>科研人员 · 已验证登录</span></div><button type="button" onClick={() => keycloak.logout({ redirectUri: window.location.origin })}><LogOut size={15} />退出登录</button></div>}
        <button className={`research-account ${accountOpen ? "expanded" : ""}`} type="button" aria-expanded={accountOpen} onClick={() => setAccountOpen((current) => !current)}><UserRound size={17} /><div><strong>{user?.display_name || user?.username}</strong><span>科研人员</span></div><ChevronDown size={15} /></button>
        <div className="research-sidebar-foot"><ShieldCheck size={16} /><span>私有附件与会话按登录账号隔离</span></div>
      </div>
    </aside>

    <main className={`research-main ${workspace === "pedigree" ? "pedigree-main" : workspace === "knowledge" ? "knowledge-main" : workspace === "structured" ? "structured-main" : workspace === "gwas" ? "gwas-main" : workspace === "genotype" ? "genotype-main" : workspace === "skills" ? "skills-main" : workspace === "results" ? "results-main" : workspace === "intelligence" || workspace === "trial-analysis" ? "intelligence-main" : ""}`}>
      {workspace !== "knowledge" && workspace !== "results" && workspace !== "skills" && workspace !== "intelligence" && workspace !== "trial-analysis" && workspace !== "pedigree" && <header className="research-topbar"><div><p>{platformContext?.institution?.name || "云南省农业科学院"} · {workspace === "gwas" ? "固定生信工作流" : workspace === "genotype" ? "基因型数据" : workspace === "structured" ? "国家水稻数据中心品种检索与院内原始文件" : workspace === "assistant" && businessPolicy?.shared_workspace ? "服务器业务数据按权限共享查询" : "仅查询院内已发布数据"}</p><h1>{workspace === "assistant" ? activeSession?.title || AGENT_NAME : workspace === "gwas" ? "水稻连续性状 GWAS" : workspace === "genotype" ? "基因型导入与水稻专用质控" : workspace === "structured" ? "品种与原文件" : "结构化查询"}</h1></div></header>}

      {notice && <div className="assistant-notice"><span>{notice}</span><button title="关闭提示" onClick={() => setNotice("")}><X size={16} /></button></div>}

      {workspace === "assistant" && <section className="assistant-boundary"><ShieldCheck size={18} /><span>{businessPolicy?.shared_workspace ? "服务器业务数据供获批人员共享查询；管理记录仅管理员可查。" : "院内已发布标准数据供获批人员查询；"}私人附件和会话按登录账号隔离。涉及病虫害、农药、施肥或种植建议时，结果需结合当地要求和专业人员意见确认。</span></section>}

      {workspace === "assistant" ? <>
      <div className="chat-pane">
      <section className="chat-log" ref={chatLogRef} onScroll={handleChatScroll}>
        {!messages.length && <div className="chat-empty"><Bot size={28} /><h2>有什么可以帮你？</h2><p>可以查询已有水稻品种资料、检索公开文献、尝试阅读公开网页，也可以讨论科研方法。涉及具体数据时，会核对来源；确实需要补充条件时再向你提问。</p><div className="trial-prompt-list" aria-label="对话示例问题"><span>试着问问</span>{["D优130的直链淀粉含量是多少？", "检索近五年水稻耐盐育种的公开文献，并附来源链接", "如何评价一个品种的稳产性？"].map((prompt) => <button type="button" key={prompt} onClick={() => applyTrialAnalysisPrompt(prompt)}>{prompt}</button>)}</div></div>}
        {messages.map((message) => {
          const content = message.content || (message.streaming ? progress || "正在准备问题…" : "");
          const messageAttachments = (message.evidence || []).filter((item) => item.type === "message_attachment");
          const sourceEvidence = (message.evidence || []).filter((item) => item.type !== "message_attachment");
          const clarification = (message.operation_state || []).find((item) => item?.state === "research_clarification");
          const clarificationActive = clarification && messages.at(-1)?.id === message.id && !message.streaming;
          return <article className={`chat-message ${message.role} ${message.error ? "error" : ""}`} key={message.id}>
            <div className="message-avatar">{message.role === "assistant" ? <Bot size={18} /> : <UserRound size={17} />}</div>
            <div className="message-content">
              <div className="message-role">{message.role === "assistant" ? AGENT_NAME : user?.display_name || "科研人员"}</div>
              <div className="message-text">
                {message.role === "assistant" ? <AssistantMarkdown content={content} streaming={message.streaming} suppressReportInstructions={message.report_available} /> : content}
              </div>
              {messageAttachments.length > 0 && <div className="message-attachment-row" aria-label="随本轮问题发送的附件">{messageAttachments.map((item) => {
                const attachment = attachmentById.get(item.attachment_id);
                const imageUrl = imagePreviewUrls[item.attachment_id];
                return <button className={`message-attachment ${item.is_image ? "image" : ""}`} type="button" key={item.attachment_id} onClick={() => attachment && showPreview(attachment)} disabled={!attachment}>
                  {item.is_image && imageUrl ? <img src={imageUrl} alt={item.title} /> : <FileText size={16} />}
                  <span><strong>{item.title}</strong><small>{item.is_image ? "图片已随本轮消息发送" : `附件已随本轮消息发送${item.size_bytes ? ` · ${formatSize(item.size_bytes)}` : ""}`}</small></span>
                </button>;
              })}</div>}
              {message.streaming && <span className="stream-cursor" />}
              {clarificationActive && <section className="assistant-clarification" aria-label="补充研究条件">
                <label htmlFor={`clarification-${message.id}`}><CircleHelp size={17} />补充条件后继续分析</label>
                {!!clarification.options?.length && <div className="assistant-query-options" aria-label="选择查询对象或审定记录">
                  {clarification.options.map((option, index) => <button type="button" className="secondary-button"
                    key={`${option.answer}-${index}`} disabled={sending}
                    onClick={() => void submitQuestion({ content: option.answer, clarificationMessageId: message.id, clarificationAction: "answer" })}>
                    {option.label}
                  </button>)}
                </div>}
                <textarea id={`clarification-${message.id}`} value={clarificationDraft} onChange={(event) => setClarificationDraft(event.target.value)} onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                    event.preventDefault();
                    submitClarification(message.id, "answer");
                  }
                }} placeholder={clarification.kind === "local_data" ? "例如：2000 年广西审定；或输入完整品种名称" : "填写补充条件；不确定时可跳过"} disabled={sending} />
                <div className="assistant-clarification-actions"><span>{clarification.allow_skip === false ? "请先确认材料身份；也可以填写完整名称或编号。" : clarification.kind === "local_data" ? "可填写省份、年份或审定编号；查看全部会按记录分别展示。" : "可只回答知道的部分；不确定时可跳过。"}</span>{clarification.allow_skip !== false && <button type="button" className="secondary-button" onClick={() => submitClarification(message.id, "skip")} disabled={sending}>{clarification.kind === "local_data" ? "查看全部" : "跳过"}</button>}<button type="button" className="primary-button" onClick={() => submitClarification(message.id, "answer")} disabled={sending || !clarificationDraft.trim()}><SendHorizontal size={15} />补充并继续</button></div>
              </section>}
              {message.role === "assistant" && message.report_available && <ReportDownloadCard message={message} onDownload={() => downloadResearchReport(message.id)} />}
              {sourceEvidence.length > 0 && <details className="evidence-card"><summary>{sourceEvidence.some((item) => item.excerpts?.length) ? "查看原文与来源" : "证据与数据来源"} <ChevronDown size={15} /></summary>{sourceEvidence.map((item, index) => <div className="evidence-item" key={`${item.type}-${index}`}><strong>{index + 1}. {item.title}</strong><span>{item.detail}</span>{Array.isArray(item.excerpts) && item.excerpts.map((excerpt, excerptIndex) => <section className="evidence-original" key={excerptIndex}><h5>{excerpt.title}</h5><p>{excerpt.text}</p></section>)}{item.query_template && <span>受控查询模板：{item.query_template}</span>}{item.query_parameters && <span>已验证参数：{JSON.stringify(item.query_parameters)}</span>}{item.query_planner && <span>参数解析方式：{item.query_planner}</span>}{item.url && <a href={item.url} target="_blank" rel="noreferrer">查看来源网页</a>}</div>)}</details>}
            </div>
          </article>;
        })}
        {progress && <div className="assistant-progress"><LoaderCircle size={15} className="spin" />{progress}</div>}
      </section>
      {showLatestButton && <button className="chat-latest-button" type="button" onClick={scrollChatToLatest}><ArrowDown size={16} />{isGenerating ? "回答仍在生成，回到最新" : "回到最新消息"}</button>}
      </div>

      <section className="assistant-composer">
        <div className="attachment-row">
          {pendingImages.map((item) => <div className="attachment-chip image pending" key={item.id}><img src={item.image_url} alt="正在上传的图片" /><span>{item.file_name}</span><small>正在保存</small></div>)}
          {composerAttachments.map((item) => <div className={`attachment-chip ${item.parsing_status} ${isVisionAttachment(item) ? "image" : ""}`} key={item.id}>
            {isVisionAttachment(item) && imagePreviewUrls[item.id] ? <button className="attachment-thumbnail" title="查看原图" onClick={() => showPreview(item)}><img src={imagePreviewUrls[item.id]} alt={item.file_name} /></button> : <FileText size={14} />}
            <button title={item.parsing_status === "image_ready" ? "查看原图" : "查看本地解析预览"} onClick={() => showPreview(item)}>{item.file_name}</button>
            <small>{item.parsing_status === "parsed" ? `已解析 · ${formatSize(item.size_bytes)}` : item.parsing_status === "image_ready" ? "原图视觉分析" : "解析失败"}</small>
            <button className="attachment-remove" title="从当前会话移除附件" onClick={() => removeAttachment(item.id)}><X size={14} /></button>
          </div>)}
        </div>
        <form className={isDragActive ? "is-dragging" : ""} onSubmit={sendQuestion} onDragEnter={(event) => { event.preventDefault(); setIsDragActive(true); }} onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; }} onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setIsDragActive(false); }} onDrop={dropFiles}>
          <textarea ref={composerTextareaRef} value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={sendOnEnter} onPaste={pasteImages} placeholder="输入问题：查询水稻资料、讨论科研方法或整理研究思路；也可粘贴、拖入或上传图片" />
          <div className="composer-actions">
            <input ref={fileInputRef} hidden type="file" multiple accept=".pdf,.docx,.xlsx,.xls,.pptx,.txt,.md,.markdown,.html,.htm,.csv,.json,.xml,.png,.jpg,.jpeg,.webp" onChange={uploadFileInput} />
            <button className="icon-button" type="button" title="上传、粘贴或拖入当前会话附件（单个不超过 10 MB）" onClick={() => fileInputRef.current?.click()} disabled={uploading}><Paperclip size={18} /></button>
            <label className="knowledge-scope-select">知识库<select value={knowledgeScope} onChange={(event) => setKnowledgeScope(event.target.value)}><option value="both">我的 + 公共</option><option value="private">仅我的</option><option value="public">仅公共</option></select></label>
            <span>{uploading ? "正在保存附件" : sending ? paused ? "当前任务已暂停；点击继续将接着处理同一任务，不会重新发送问题" : "模型正在生成，可继续编辑下一条问题或添加图片；当前问题完成后再发送" : businessPolicy?.shared_business_external_analysis_approved ? "共享业务数据可用于已授权模型分析；私人附件需本地模型；Enter 发送，Shift + Enter 换行" : "外部模型仅接收公开或脱敏文本；私人附件需切换本地 vLLM；按 Enter 发送，Shift + Enter 换行"}</span>
            {sending
              ? <><button className="secondary-button send-button" type="button" onClick={togglePause} disabled={!canPause || pauseChanging}>{paused ? <Play size={15} /> : <Pause size={15} />}{paused ? "继续" : "暂停"}</button><button className="secondary-button send-button" type="button" onClick={cancelGeneration}><Square size={15} />停止</button></>
              : <button className="primary-button send-button" type="submit" disabled={!draft.trim() || uploading}><SendHorizontal size={17} />发送</button>}
          </div>
        </form>
      </section>
      </> : workspace === "pedigree" ? <PedigreeWorkspace /> : workspace === "structured" ? <VarietySearchWorkspace /> : workspace === "intelligence" ? <BreedingIntelligenceWorkspace onNotice={setNotice} /> : workspace === "trial-analysis" ? <TrialAnalysisWorkspace onNotice={setNotice} /> : workspace === "gwas" ? <GwasWorkspace onNotice={setNotice} /> : workspace === "genotype" ? <GenotypeAssetsWorkspace onNotice={setNotice} /> : workspace === "skills" ? <SkillLibrary onNotice={setNotice} onOpenWorkspace={openResearchSkill} /> : workspace === "knowledge" ? <KnowledgeLibrary onNotice={setNotice} /> : <ResultsLibrary onNotice={setNotice} />}
    </main>

    {preview && <div className="attachment-preview-backdrop" onMouseDown={() => setPreview(null)}><section className={`attachment-preview ${preview.image_url ? "image-preview" : ""}`} onMouseDown={(event) => event.stopPropagation()}><header><div><p>{preview.image_url ? "当前会话私有原图" : "本地解析文字预览"}</p><h2>{preview.file_name}</h2></div><button className="icon-button" title="关闭预览" onClick={() => setPreview(null)}><X size={17} /></button></header>{preview.parser_warnings?.map((warning) => <div className="preview-warning" key={warning}>{warning}</div>)}{preview.image_url ? <div className="attachment-image-full"><img src={preview.image_url} alt={preview.file_name} /></div> : <pre>{preview.preview || "该附件未解析出可预览文本。"}</pre>}{preview.preview_truncated && <small>预览仅显示前 60,000 个字符；完整文本仍仅保存在当前会话的私有附件中。</small>}</section></div>}
  </div>;
}
