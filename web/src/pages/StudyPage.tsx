import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useInfiniteQuery, useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import type { DocumentSummary } from "../api/documents";
import {
  askConversation, cancelConversationMessage, createCollection, createConversation,
  deleteCollection, getAgentRun, getConversation, listCollections, listConversations,
  rateConversationMessage, setCollectionDocuments, setConversationScope,
  type Collection, type ScopeChoice,
} from "../api/learning";
import { PageHeading } from "../components/PageHeading";
import { StudyAnswer, studySections, type StudySection } from "../components/StudyAnswer";
import { StudySourcePanel } from "../components/StudySourcePanel";
import { ConversationSidebar } from "../components/ConversationSidebar";
import { ConversationMessages } from "../components/ConversationMessages";
import { Icon } from "../components/Icon";
import { AppHeader } from "../components/AppHeader";
import { AgentRunCard } from "../components/AgentRunCard";
import { ConversationTaskResult } from "../components/ConversationTaskResult";
import { ErrorState } from "../components/ErrorState";
import { ScopePicker } from "../components/ScopePicker";
import { useDocuments } from "../hooks/useDocuments";
import { useFocusTrap } from "../hooks/useFocusTrap";

const emptyScope = (): ScopeChoice => ({ document_ids: [], collection_ids: [] });
const taskExamples = [
  ["完整学习", "根据所选资料总结主要知识点，再生成 5 道中等难度单选题。我提交后解释错题，再练习薄弱点。"],
  ["比较资料", "比较所选资料的主要观点，并给出来源。"],
  ["生成练习", "根据所选资料生成 5 道中等难度单选题。"],
  ["讲解错题", "解释我最近一次练习的错题，并展示解析和来源。"],
  ["薄弱点练习", "根据我最近练习的薄弱知识点，再生成 5 道中等难度单选题。"],
] as const;

function CollectionRow({ collection, documents, refresh }: {
  collection: Collection; documents: DocumentSummary[]; refresh: () => void;
}) {
  const [selected, setSelected] = useState(collection.document_ids);
  const [error, setError] = useState("");
  const deleteDialog = useRef<HTMLDialogElement>(null);
  const deleteTitle = useId();
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const closeDelete = useCallback(() => deleteDialog.current?.close(), []);
  useFocusTrap(deleteDialog, confirmingDelete, closeDelete);
  const save = useMutation({
    mutationFn: () => setCollectionDocuments(collection.id, selected),
    onSuccess: () => { setError(""); refresh(); },
    onError: (caught) => setError(caught.message),
  });
  const remove = useMutation({
    mutationFn: () => deleteCollection(collection.id),
    onSuccess: refresh,
    onError: (caught) => setError(caught.message),
  });
  return (
    <article className="learning-card">
      <h3>{collection.name}</h3>
      {collection.description ? <p>{collection.description}</p> : null}
      <div className="scope-picker__items">
        {documents.map((document) => <label key={document.id}>
          <input type="checkbox" checked={selected.includes(document.id)} onChange={() =>
            setSelected((current) => current.includes(document.id)
              ? current.filter((id) => id !== document.id) : [...current, document.id])} />
          <span>{document.filename}</span>
        </label>)}
      </div>
      <div className="learning-actions">
        <button className="button button--secondary" disabled={save.isPending} onClick={() => save.mutate()} type="button">保存集合</button>
        <button className="button button--danger-quiet" disabled={remove.isPending} onClick={() => { deleteDialog.current?.showModal(); setConfirmingDelete(true); }} type="button">删除集合</button>
      </div>
      {error ? <p role="alert">{error}</p> : null}
      <dialog className="workspace-dialog learning-card" aria-labelledby={deleteTitle} ref={deleteDialog} onClose={() => setConfirmingDelete(false)}>
        <h2 id={deleteTitle}>删除集合“{collection.name}”？</h2>
        <p>此操作会移除集合，原始资料仍保留在资料库中。删除后无法恢复该集合。</p>
        <div className="learning-actions">
          <button className="button button--secondary" type="button" onClick={closeDelete}>保留集合</button>
          <button className="button button--danger-quiet" type="button" onClick={() => { closeDelete(); remove.mutate(); }}>确认删除集合</button>
        </div>
      </dialog>
    </article>
  );
}

export function StudyPage() {
  const { conversationId } = useParams();
  const navigate = useNavigate();
  const cache = useQueryClient();
  const { documents, error: documentsError, isLoading: documentsLoading, nextCursor, loadMore, refresh: refreshDocuments } = useDocuments();
  const collections = useQuery({ queryKey: ["collections"], queryFn: listCollections });
  const conversations = useInfiniteQuery({ queryKey: ["conversations"], initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => listConversations(pageParam),
    getNextPageParam: (page) => page.length === 50 ? page.at(-1)?.id : undefined,
  });
  const detail = useQuery({
    queryKey: ["conversation", conversationId], queryFn: () => getConversation(conversationId!),
    enabled: Boolean(conversationId),
    refetchInterval: (query) => query.state.data?.messages.some((message) =>
      message.status === "queued" || message.status === "processing") ? 1500 : false,
  });
  const runMessages = detail.data?.messages.filter((message) => message.run_id) ?? [];
  const runStates = useQueries({ queries: runMessages.map((message) => ({
    queryKey: ["agent-run", message.run_id],
    queryFn: () => getAgentRun(message.run_id!),
    refetchInterval: (query: { state: { data?: { status: string } } }) => query.state.data && ["queued", "running"].includes(query.state.data.status) ? 1500 : false,
  })) });
  const [historyCollapsed, setHistoryCollapsed] = useState(() => Boolean(conversationId));
  const [collectionName, setCollectionName] = useState("");
  const [activeScope, setActiveScope] = useState<ScopeChoice>(emptyScope);
  const [scopeOpen, setScopeOpen] = useState(false);
  const [question, setQuestion] = useState("");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [sourceOpen, setSourceOpen] = useState(false);
  const [followExplanation, setFollowExplanation] = useState(true);
  const sectionsByMessage = useMemo(() => new Map(detail.data?.messages.map((message) => [message.id, studySections(message)]) ?? []), [detail.data?.messages]);
  const sections = useMemo(() => Array.from(sectionsByMessage.values()).flat(), [sectionsByMessage]);
  const selectedSection = sections.find((section) => section.key === selectedKey)
    ?? Array.from(sectionsByMessage.values()).reverse().find((items) => items.length)?.[0];
  const closeSource = useCallback(() => setSourceOpen(false), []);
  const followFrame = useRef(0);
  const selectSection = useCallback((section: StudySection) => {
    window.cancelAnimationFrame(followFrame.current);
    setSelectedKey(section.key);
    setSourceOpen(window.matchMedia("(max-width: 1099px)").matches);
  }, []);
  useEffect(() => () => window.cancelAnimationFrame(followFrame.current), [followExplanation, conversationId]);
  const followScroll = (target: EventTarget) => {
    if (!followExplanation || sourceOpen && window.matchMedia("(max-width: 1099px)").matches) return;
    if (!(target instanceof HTMLElement) || !target.classList.contains("learning-messages")) return;
    window.cancelAnimationFrame(followFrame.current);
    followFrame.current = window.requestAnimationFrame(() => {
      const bounds = target.getBoundingClientRect();
      const anchor = bounds.top + Math.min(120, bounds.height * 0.25);
      let nearest: HTMLElement | null = null;
      let distance = Infinity;
      target.querySelectorAll<HTMLElement>("[data-knowledge-key]").forEach((element) => {
        const rect = element.getBoundingClientRect();
        if (rect.bottom <= bounds.top || rect.top >= bounds.bottom) return;
        const delta = anchor < rect.top ? rect.top - anchor : anchor > rect.bottom ? anchor - rect.bottom : 0;
        if (delta < distance) { nearest = element; distance = delta; }
      });
      if (nearest) setSelectedKey((nearest as HTMLElement).dataset.knowledgeKey ?? null);
    });
  };
  const askKey = useRef<{ fingerprint: string; key: string } | null>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const input = composer.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(Math.max(input.scrollHeight, 46), 160)}px`;
  }, [question, conversationId]);
  const createKey = useRef<{ fingerprint: string; key: string } | null>(null);
  const feedbackKeys = useRef<Record<string, string>>({});
  const refreshCollections = () => { void cache.invalidateQueries({ queryKey: ["collections"] }); };
  const refreshConversations = () => {
    void cache.invalidateQueries({ queryKey: ["conversations"] });
    void cache.invalidateQueries({ queryKey: ["conversation", conversationId] });
  };
  const scopeIdentity = detail.data?.scope.map((item) => item.document_id).join("|");
  useEffect(() => {
    setActiveScope({ document_ids: detail.data?.scope.map((item) => item.document_id) ?? [], collection_ids: [] });
  }, [conversationId, scopeIdentity]);
  useEffect(() => {
    if (detail.data) setScopeOpen(detail.data.scope.length === 0);
  }, [conversationId, scopeIdentity]);
  useEffect(() => { setSelectedKey(null); setSourceOpen(false); }, [conversationId]);

  const createCollectionMutation = useMutation({
    mutationFn: () => createCollection(collectionName, ""),
    onSuccess: () => { setCollectionName(""); refreshCollections(); },
  });
  const createConversationMutation = useMutation({
    mutationFn: () => {
      const document_ids = detail.data?.scope.map((item) => item.document_id) ?? [];
      const fingerprint = JSON.stringify({ conversationId, document_ids });
      if (createKey.current?.fingerprint !== fingerprint) createKey.current = { fingerprint, key: crypto.randomUUID() };
      return createConversation("新对话", { document_ids, collection_ids: [] }, createKey.current.key);
    },
    onSuccess: (created) => { createKey.current = null; refreshConversations(); navigate(`/study/${created.id}`); },
  });
  const openCreate = () => { if (!createConversationMutation.isPending) createConversationMutation.mutate(); };
  const switchScope = useMutation({
    mutationFn: () => setConversationScope(conversationId!, activeScope),
    onSuccess: () => { setScopeOpen(false); refreshConversations(); },
  });
  const ask = useMutation({
    mutationFn: (request: string) => {
      const fingerprint = JSON.stringify({ conversationId, request: request.trim(), scopeIdentity });
      if (askKey.current?.fingerprint !== fingerprint) askKey.current = { fingerprint, key: crypto.randomUUID() };
      return askConversation(conversationId!, request.trim(), askKey.current.key);
    },
    onSuccess: (_, request) => {
      askKey.current = null;
      setQuestion((current) => current.trim() === request.trim() ? "" : current);
      refreshConversations();
    },
  });
  const cancel = useMutation({
    mutationFn: (messageId: string) => cancelConversationMessage(conversationId!, messageId),
    onSuccess: refreshConversations,
  });
  const feedback = useMutation({
    mutationFn: ({ messageId, rating }: { messageId: string; rating: "helpful" | "unhelpful" | "citation_inaccurate" }) => {
      feedbackKeys.current[messageId] ??= crypto.randomUUID();
      return rateConversationMessage(conversationId!, messageId, rating, feedbackKeys.current[messageId]);
    },
    onSuccess: refreshConversations,
  });
  const error = documentsError ?? collections.error ?? conversations.error ?? detail.error
    ;
  const activeMessage = runMessages.find((_, index) => ["queued", "running"].includes(runStates[index]?.data?.status ?? ""))
    ?? detail.data?.messages.find((message) => message.status === "queued" || message.status === "processing");
  const canSend = Boolean(detail.data?.scope.length) && question.trim().length >= 2 && !ask.isPending && !activeMessage;
  const hasAvailableSource = Boolean(nextCursor) || documents.some((document) => document.status === "ready") || (collections.data ?? []).some((collection) => collection.document_ids.length > 0);
  const processingDocuments = documents.some((document) => ["uploaded", "queued", "parsing"].includes(document.status));
  const firstUse = !conversationId && !documentsLoading && !collections.isPending && !conversations.isPending
    && !error && !hasAvailableSource && !(conversations.data?.pages.some((page) => page.length) ?? false);
  const draftTask = (request: string) => { setQuestion(request); composer.current?.focus(); };

  return <div className="app-page">
    <AppHeader />
    <main className="learning-page study-page" id="main-content" tabIndex={-1}>
      <PageHeading title="学习空间" kicker="让每一个知识点，真正被理解" description="一边深入讲解，一边对照原文。" className="study-heading" actions={!firstUse ? <button className="icon-button" aria-label={historyCollapsed ? "展开对话列表" : "收起对话列表"} aria-expanded={!historyCollapsed} aria-controls="conversation-history" onClick={() => setHistoryCollapsed((value) => !value)} type="button"><Icon name="panel" /></button> : null} />
      {createConversationMutation.error ? <p role="alert">创建失败，请重试。<button className="button button--secondary" type="button" onClick={openCreate}>重试新建</button></p> : null}
      {error ? <ErrorState message={error.message} onRetry={() => { void refreshDocuments(); refreshCollections(); refreshConversations(); if (conversationId) void detail.refetch(); }} /> : null}
      <div className={`learning-grid study-layout${historyCollapsed || firstUse ? " study-layout--history-collapsed" : ""}${conversationId ? " study-layout--source-open" : ""}`}>
        <div className="learning-content" onScrollCapture={(event) => followScroll(event.target)}>
          {conversationId ? <section className="learning-card agent-conversation" aria-label="当前对话">
            {detail.isPending ? <p role="status">正在打开对话…</p> : null}
            {detail.data ? <>
              <div className="study-reading-controls">
                <label><input checked={followExplanation} onChange={(event) => setFollowExplanation(event.target.checked)} type="checkbox" />跟随讲解</label>
                <button className="text-action study-open-source" disabled={!selectedSection} onClick={() => setSourceOpen(true)} type="button">查看原文</button>
              </div>
              <ConversationMessages key={conversationId} updateKey={JSON.stringify(detail.data.messages.map((message) => [message.id, message.status, message.answer, message.task_result]))}>{detail.data.messages.length === 0 ? <div className="agent-empty">
                {detail.data.scope.length === 0 ? <p>选择资料后开始对话</p> : null}
                {detail.data.scope.length ? <div className="agent-suggestions" aria-label="任务示例">{taskExamples.map(([label, request]) =>
                  <button className="text-action" disabled={ask.isPending || Boolean(activeMessage)} key={label} onClick={() => draftTask(request)} type="button">{label}</button>)}</div> : null}
              </div> : null}
                {detail.data.messages.map((message) => <article className="learning-message" key={message.id}>
                  <div className="agent-request"><h3>{message.question}</h3></div>
                  {!message.run_id && (message.status === "queued" || message.status === "processing") ? <div className="agent-progress"><p role="status">{message.task_result?.kind === "quiz" ? message.task_result.text : "正在规划任务并寻找依据…"}</p></div> : null}
                  {message.status === "cancelled" ? <p role="status">已停止任务。</p> : null}
                  {message.status === "failed" ? <div role="alert"><p>{message.failure_message ?? "任务执行失败，请调整要求后重试。"}</p>
                    <button className="button button--secondary" onClick={() => draftTask(message.question)} type="button">重新编辑任务</button></div> : null}
                  {message.status === "insufficient" ? <p>在所选资料中找不到足够依据。</p> : null}
                  <ConversationTaskResult message={message} onRequest={draftTask} />
                  <StudyAnswer sections={sectionsByMessage.get(message.id) ?? []} activeKey={selectedSection?.key} onSelect={selectSection} />
                  {message.answer && (message.status === "answered" || message.status === "insufficient") ?
                    <div className="learning-actions" aria-label="回答反馈">
                      {message.feedback ? <p>已反馈：{message.feedback === "helpful" ? "有帮助" : message.feedback === "unhelpful" ? "无帮助" : "引用不准确"}</p>
                        : <>{([ ["helpful", "有帮助"], ["unhelpful", "无帮助"], ["citation_inaccurate", "引用不准确"] ] as const).map(([rating, label]) =>
                          <button className="button button--secondary" disabled={feedback.isPending} key={rating} onClick={() => feedback.mutate({ messageId: message.id, rating })} type="button">{label}</button>)}</>}
                    </div> : null}
                  {message.run_id ? <AgentRunCard runId={message.run_id} conversationId={conversationId} /> : null}
                </article>)}
              </ConversationMessages>
              {ask.error ? <p role="alert">{ask.error.message}，输入已保留，可重新发送。</p> : null}
              {cancel.error ? <p role="alert">{cancel.error.message}，可再次停止任务。</p> : null}
              {feedback.error ? <p role="alert">{feedback.error.message}，可重新提交反馈。</p> : null}
              <form className="agent-composer" onSubmit={(event) => { event.preventDefault(); if (canSend) ask.mutate(question); }}>
                <label className="visually-hidden" htmlFor="conversation-question">发送任务或问题</label>
                <div className="agent-composer__shell">
                  <textarea placeholder="提问或安排学习任务…" ref={composer} id="conversation-question" name="conversation-question" autoComplete="off" maxLength={2000} onChange={(event) => setQuestion(event.target.value)} rows={1} value={question}
                    onKeyDown={(event) => {
                      if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
                      event.preventDefault();
                      if (canSend) ask.mutate(question);
                    }} />
                  <div className="agent-composer-footer">
                    <details className="agent-scope" onToggle={(event) => setScopeOpen(event.currentTarget.open)} open={scopeOpen}><summary>{detail.data.scope.length ? `资料 · ${detail.data.scope.length}` : "选择资料"}</summary>
                      <div className="agent-scope__panel">
                        <ScopePicker collections={collections.data ?? []} documents={documents} hasMore={Boolean(nextCursor)} label="资料范围" loading={documentsLoading} onChange={setActiveScope} selectedLabels={detail.data.scope.map((item) => ({ id: item.document_id, filename: item.filename }))} value={activeScope} />
                        {nextCursor ? <button className="button button--secondary" onClick={() => void loadMore()} type="button">加载更多资料</button> : null}
                        {switchScope.error ? <p role="alert">{switchScope.error.message}，可重试保存。</p> : null}
                        <button className="button button--secondary" disabled={switchScope.isPending} onClick={() => switchScope.mutate()} type="button">保存范围</button>
                      </div>
                    </details>
                    {activeMessage ? <button className="agent-composer__action" aria-label="停止生成" disabled={cancel.isPending} onClick={() => cancel.mutate(activeMessage.id)} type="button"><Icon name="stop" /></button>
                      : <button className="agent-composer__action" aria-label="发送" disabled={!canSend} type="submit"><Icon name="send" /></button>}
                  </div>
                </div>
              </form>
            </> : null}
          </section> : <section className="workspace-empty learning-card">
            <span className="workspace-empty__mark" aria-hidden="true"><Icon name="book" /></span>
            {documentsLoading || collections.isPending ? <p role="status">正在读取可用资料…</p> : hasAvailableSource ? <>
              <h2>今天想学些什么？</h2><button className="button button--primary" disabled={createConversationMutation.isPending} onClick={openCreate} type="button">开始新对话</button>
            </> : <>
              <h2>{documents.length ? processingDocuments ? "资料正在处理" : "资料暂不可用" : "先上传一份 PDF"}</h2>
              <p>{documents.length ? processingDocuments ? "处理完成后即可开始提问和练习。" : "请查看失败原因并重新处理资料。" : "上传后即可预览，处理完成后可以提问和生成练习。"}</p>
              <Link className="button button--primary" to={documents.length ? "/" : "/#upload-materials"}>{documents.length ? "查看资料状态" : "前往资料库上传 PDF"}</Link>
            </>}
          </section>}
        </div>
        {!firstUse ? <aside className="learning-sidebar" id="conversation-history" aria-label="集合与对话">
          <ConversationSidebar conversations={conversations.data?.pages.flat() ?? []} currentId={conversationId} loading={conversations.isPending} hasMore={conversations.hasNextPage} loadingMore={conversations.isFetchingNextPage} onLoadMore={() => void conversations.fetchNextPage()} creating={createConversationMutation.isPending} onNew={openCreate} onDeleted={(id) => { if (id === conversationId) navigate("/study"); }} />
          <details className="learning-card collection-management"><summary>资料集合</summary>
            <form onSubmit={(event) => { event.preventDefault(); if (collectionName.trim()) createCollectionMutation.mutate(); }}>
              <label htmlFor="collection-name">新集合名称</label>
              <input id="collection-name" name="collection-name" autoComplete="off" maxLength={120} onChange={(event) => setCollectionName(event.target.value)} value={collectionName} />
              {createCollectionMutation.error ? <p role="alert">{createCollectionMutation.error.message}，可重试建立集合。</p> : null}
              <button className="button button--secondary" disabled={!collectionName.trim() || createCollectionMutation.isPending} type="submit">建立集合</button>
            </form>
            {collections.isPending ? <p role="status">正在读取集合…</p> : null}
            {collections.data?.length === 0 ? <p>还没有集合。创建后可将资料加入其中。</p> : null}
            {collections.data?.map((collection) => <CollectionRow key={collection.id} collection={collection} documents={documents} refresh={refreshCollections} />)}
          </details>
        </aside> : null}
        {conversationId ? <StudySourcePanel key={conversationId} section={selectedSection} mobileOpen={sourceOpen} onClose={closeSource} /> : null}
      </div>
    </main>
  </div>;
}
