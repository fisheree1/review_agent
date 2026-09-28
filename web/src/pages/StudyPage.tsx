import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import type { DocumentSummary } from "../api/documents";
import {
  askConversation, cancelConversationMessage, createCollection, createConversation,
  deleteCollection, getConversation, listCollections, listConversations,
  rateConversationMessage, setCollectionDocuments, setConversationScope,
  type Collection, type ScopeChoice,
} from "../api/learning";
import { SourcePreview } from "../components/SourcePreview";
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
  const [historyCollapsed, setHistoryCollapsed] = useState(false);
  const [collectionName, setCollectionName] = useState("");
  const [activeScope, setActiveScope] = useState<ScopeChoice>(emptyScope);
  const [question, setQuestion] = useState("");
  const askKey = useRef<{ fingerprint: string; key: string } | null>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
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
    onSuccess: refreshConversations,
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
  const legacyWorking = detail.data?.messages.some((message) => !message.run_id && (message.status === "queued" || message.status === "processing")) ?? false;
  const canSend = Boolean(detail.data?.scope.length) && question.trim().length >= 2 && !ask.isPending && !legacyWorking;
  const hasAvailableSource = Boolean(nextCursor) || documents.some((document) => document.status === "ready") || (collections.data ?? []).some((collection) => collection.document_ids.length > 0);
  const processingDocuments = documents.some((document) => ["uploaded", "queued", "parsing"].includes(document.status));
  const firstUse = !conversationId && !documentsLoading && !collections.isPending && !conversations.isPending
    && !error && !hasAvailableSource && !(conversations.data?.pages.some((page) => page.length) ?? false);
  const draftTask = (request: string) => { setQuestion(request); composer.current?.focus(); };

  return <div className="app-page">
    <AppHeader />
    <main className="learning-page study-page" id="main-content" tabIndex={-1}>
      <header className="learning-heading study-heading"><h1>学习空间</h1>{!firstUse ? <button className="icon-button" aria-label={historyCollapsed ? "展开对话列表" : "收起对话列表"} aria-expanded={!historyCollapsed} aria-controls="conversation-history" onClick={() => setHistoryCollapsed((value) => !value)} type="button"><Icon name="panel" /></button> : null}</header>
      {createConversationMutation.error ? <p role="alert">创建失败，请重试。<button className="button button--secondary" type="button" onClick={openCreate}>重试新建</button></p> : null}
      {error ? <ErrorState message={error.message} onRetry={() => { void refreshDocuments(); refreshCollections(); refreshConversations(); if (conversationId) void detail.refetch(); }} /> : null}
      <div className={`learning-grid study-layout${historyCollapsed || firstUse ? " study-layout--history-collapsed" : ""}`}>
        <div className="learning-content">
          {conversationId ? <section className="learning-card agent-conversation" aria-label="当前对话">
            {detail.isPending ? <p role="status">正在打开对话…</p> : null}
            {detail.data ? <>
              <div className="conversation-heading"><h2>{detail.data.title}</h2>
              <p className="learning-scope">{detail.data.scope.length ? `${detail.data.scope.length} 份资料` : "选择资料后开始"}</p>
              <details className="agent-scope" open={detail.data.scope.length === 0 ? true : undefined}><summary>{detail.data.scope.length ? "资料范围" : "选择资料"}</summary>
                <ScopePicker collections={collections.data ?? []} documents={documents} hasMore={Boolean(nextCursor)} label="资料范围" loading={documentsLoading} onChange={setActiveScope} selectedLabels={detail.data.scope.map((item) => ({ id: item.document_id, filename: item.filename }))} value={activeScope} />
                {nextCursor ? <button className="button button--secondary" onClick={() => void loadMore()} type="button">加载更多资料</button> : null}
                {switchScope.error ? <p role="alert">{switchScope.error.message}，当前范围未改变，可重试保存。</p> : null}
                <button className="button button--secondary" disabled={switchScope.isPending} onClick={() => switchScope.mutate()} type="button">保存新范围</button>
              </details>
              </div>
              <ConversationMessages key={conversationId} updateKey={JSON.stringify(detail.data.messages.map((message) => [message.id, message.status, message.answer, message.task_result]))}>{detail.data.messages.length === 0 ? <div className="agent-empty">
                <p>{detail.data.scope.length ? "可以从这些任务开始" : "先选择资料，再开始对话"}</p>
                {detail.data.scope.length ? <div className="agent-suggestions" aria-label="任务示例">{taskExamples.map(([label, request]) =>
                  <button className="text-action" disabled={ask.isPending || legacyWorking} key={label} onClick={() => draftTask(request)} type="button">{label}</button>)}</div> : null}
              </div> : null}
                {detail.data.messages.map((message) => <article className="learning-message" key={message.id}>
                  <div className="agent-request"><h3>{message.question}</h3></div>
                  <details className="message-scope"><summary>资料来源 · {message.scope.length} 份</summary><p className="learning-scope">{message.scope.map((item) => item.filename).join("、")}</p></details>
                  {!message.run_id && (message.status === "queued" || message.status === "processing") ? <div className="agent-progress"><p role="status">{message.task_result?.kind === "quiz" ? message.task_result.text : "正在规划任务并寻找依据…"}</p>
                    <button className="button button--secondary" disabled={cancel.isPending} onClick={() => cancel.mutate(message.id)} type="button">停止任务</button></div> : null}
                  {message.status === "cancelled" ? <p role="status">已停止任务。</p> : null}
                  {message.status === "failed" ? <div role="alert"><p>{message.failure_message ?? "任务执行失败，请调整要求后重试。"}</p>
                    <button className="button button--secondary" onClick={() => draftTask(message.question)} type="button">重新编辑任务</button></div> : null}
                  {message.status === "insufficient" ? <p>在所选资料中找不到足够依据。</p> : null}
                  <ConversationTaskResult message={message} onRequest={draftTask} />
                  {message.answer?.claims.map((claim, index) => <div key={index}>
                    <p>{claim.text}</p>
                    {claim.citations.map((citation) => <SourcePreview key={citation.source_id} source={citation} />)}
                  </div>)}
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
                <textarea placeholder="提问或安排学习任务…" aria-describedby="agent-composer-help" ref={composer} id="conversation-question" name="conversation-question" autoComplete="off" maxLength={2000} onChange={(event) => setQuestion(event.target.value)} rows={2} value={question}
                  onKeyDown={(event) => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); if (canSend) ask.mutate(question); } }} />
                <div className="agent-composer-footer"><p id="agent-composer-help">⌘ / Ctrl + Enter</p>
                  <button className="button button--primary" disabled={!canSend} type="submit">{ask.isPending ? "正在发送…" : "发送"}</button></div>
              </form>
            </> : null}
          </section> : <section className="workspace-empty learning-card">
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
      </div>
    </main>
  </div>;
}
