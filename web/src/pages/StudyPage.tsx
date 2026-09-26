import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import type { DocumentSummary } from "../api/documents";
import {
  askConversation, cancelConversationMessage, createCollection, createConversation,
  deleteCollection, getConversation, listCollections, listConversations,
  rateConversationMessage, setCollectionDocuments, setConversationScope,
  type Collection, type ScopeChoice,
} from "../api/learning";
import { citationLabel } from "../citations";
import { AppHeader } from "../components/AppHeader";
import { ConversationTaskResult } from "../components/ConversationTaskResult";
import { ErrorState } from "../components/ErrorState";
import { ScopePicker } from "../components/ScopePicker";
import { useDocuments } from "../hooks/useDocuments";

const emptyScope = (): ScopeChoice => ({ document_ids: [], collection_ids: [] });
const taskExamples = [
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
        <button className="button button--danger-quiet" disabled={remove.isPending} onClick={() => remove.mutate()} type="button">删除集合</button>
      </div>
      {error ? <p role="alert">{error}</p> : null}
    </article>
  );
}

export function StudyPage() {
  const { conversationId } = useParams();
  const navigate = useNavigate();
  const cache = useQueryClient();
  const { documents, error: documentsError, nextCursor, loadMore } = useDocuments();
  const collections = useQuery({ queryKey: ["collections"], queryFn: listCollections });
  const conversations = useQuery({ queryKey: ["conversations"], queryFn: listConversations });
  const detail = useQuery({
    queryKey: ["conversation", conversationId], queryFn: () => getConversation(conversationId!),
    enabled: Boolean(conversationId),
    refetchInterval: (query) => query.state.data?.messages.some((message) =>
      message.status === "queued" || message.status === "processing") ? 1500 : false,
  });
  const [collectionName, setCollectionName] = useState("");
  const [newTitle, setNewTitle] = useState("");
  const [newScope, setNewScope] = useState<ScopeChoice>(emptyScope);
  const [activeScope, setActiveScope] = useState<ScopeChoice>(emptyScope);
  const [question, setQuestion] = useState("");
  const askKey = useRef<{ fingerprint: string; key: string } | null>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
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
    mutationFn: () => createConversation(newTitle, newScope),
    onSuccess: (created) => { setNewTitle(""); setNewScope(emptyScope()); refreshConversations(); navigate(`/study/${created.id}`); },
  });
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
    ?? createCollectionMutation.error ?? createConversationMutation.error ?? switchScope.error
    ?? ask.error ?? cancel.error ?? feedback.error;
  const working = detail.data?.messages.some((message) => message.status === "queued" || message.status === "processing") ?? false;
  const canSend = question.trim().length >= 2 && !ask.isPending && !working;
  const draftTask = (request: string) => { setQuestion(request); composer.current?.focus(); };

  return <div className="app-page">
    <AppHeader />
    <main className="learning-page" id="main-content">
      <header className="learning-heading"><span className="eyebrow">学习空间</span><h1>学习助手</h1>
        <p>在同一段对话里提问、比较资料、生成练习和复习错题。每次任务都保留所选资料范围。</p></header>
      {error ? <ErrorState message={error.message} onRetry={() => { refreshCollections(); refreshConversations(); }} /> : null}
      <div className="learning-grid">
        <aside className="learning-sidebar" aria-label="集合与对话">
          <section className="learning-card"><h2>资料集合</h2>
            <form onSubmit={(event) => { event.preventDefault(); if (collectionName.trim()) createCollectionMutation.mutate(); }}>
              <label htmlFor="collection-name">新集合名称</label>
              <input id="collection-name" maxLength={120} onChange={(event) => setCollectionName(event.target.value)} value={collectionName} />
              <button className="button button--secondary" disabled={!collectionName.trim() || createCollectionMutation.isPending} type="submit">建立集合</button>
            </form>
            {collections.isPending ? <p role="status">正在读取集合…</p> : null}
            {collections.data?.length === 0 ? <p>还没有集合。创建后可将资料加入其中。</p> : null}
          </section>
          {collections.data?.map((collection) => <CollectionRow key={collection.id} collection={collection} documents={documents} refresh={refreshCollections} />)}
          <section className="learning-card"><h2>历史对话</h2>
            {conversations.isPending ? <p role="status">正在读取对话…</p> : null}
            {conversations.data?.length === 0 ? <p>选择资料，开始第一段对话。</p> : null}
            <nav aria-label="历史对话">{conversations.data?.map((conversation) =>
              <Link aria-current={conversation.id === conversationId ? "page" : undefined} key={conversation.id} to={`/study/${conversation.id}`}>{conversation.title}</Link>)}</nav>
          </section>
        </aside>
        <div className="learning-content">
          <details className="learning-card agent-new-conversation" open={!conversationId}><summary>开始新对话</summary>
            <form onSubmit={(event) => { event.preventDefault(); if (newTitle.trim()) createConversationMutation.mutate(); }}>
              <label htmlFor="conversation-title">对话标题</label>
              <input id="conversation-title" maxLength={160} onChange={(event) => setNewTitle(event.target.value)} value={newTitle} />
              <ScopePicker collections={collections.data ?? []} documents={documents} label="选择资料范围" onChange={setNewScope} value={newScope} />
              {nextCursor ? <button className="button button--secondary" onClick={() => void loadMore()} type="button">加载更多资料</button> : null}
              <button className="button button--primary" disabled={!newTitle.trim() || createConversationMutation.isPending} type="submit">创建对话</button>
            </form>
          </details>
          {conversationId ? <section className="learning-card agent-conversation" aria-label="当前对话">
            {detail.isPending ? <p role="status">正在打开对话…</p> : null}
            {detail.data ? <>
              <h2>{detail.data.title}</h2>
              <p className="learning-scope">当前范围：{detail.data.scope.map((item) => item.filename).join("、")}</p>
              <details className="agent-scope"><summary>调整资料范围</summary>
                <p className="learning-scope">仅影响后续任务，已完成结果保留原来的范围。</p>
                <ScopePicker collections={collections.data ?? []} documents={documents} label="切换后续提问范围" onChange={setActiveScope} value={activeScope} />
                <button className="button button--secondary" disabled={switchScope.isPending} onClick={() => switchScope.mutate()} type="button">保存新范围</button>
              </details>
              <div className="agent-suggestions" aria-label="任务示例">{taskExamples.map(([label, request]) =>
                <button className="button button--secondary" disabled={ask.isPending || working} key={label} onClick={() => draftTask(request)} type="button">{label}</button>)}</div>
              <div className="learning-messages">{detail.data.messages.length === 0 ? <p className="agent-empty">直接描述你想完成的学习任务，例如“根据第三章生成 5 道题”。</p> : null}
                {detail.data.messages.map((message) => <article className="learning-message" key={message.id}>
                  <div className="agent-request"><span className="eyebrow">你的请求</span><h3>{message.question}</h3></div>
                  <p className="learning-scope">本次范围：{message.scope.map((item) => item.filename).join("、")}</p>
                  {message.status === "queued" || message.status === "processing" ? <div className="agent-progress"><p role="status">{message.task_result?.kind === "quiz" ? message.task_result.text : "正在规划任务并寻找依据…"}</p>
                    <button className="button button--secondary" disabled={cancel.isPending} onClick={() => cancel.mutate(message.id)} type="button">停止任务</button></div> : null}
                  {message.status === "cancelled" ? <p role="status">已停止任务。</p> : null}
                  {message.status === "failed" ? <div role="alert"><p>{message.failure_message ?? "任务执行失败，请调整要求后重试。"}</p>
                    <button className="button button--secondary" onClick={() => draftTask(message.question)} type="button">重新编辑任务</button></div> : null}
                  {message.status === "insufficient" ? <p>在所选资料中找不到足够依据。</p> : null}
                  <ConversationTaskResult message={message} onRequest={draftTask} />
                  {message.answer?.claims.map((claim, index) => <div key={index}>
                    <p>{claim.text}</p>
                    {claim.citations.map((citation) => <p key={citation.source_id}>
                      <Link to={`/documents/${citation.document_id}?unit=${citation.unit}${citation.version_id ? `&version=${citation.version_id}` : ""}`}>{citationLabel(citation.locator)} · 查看原文</Link>
                      <span className="learning-quote">{citation.quote}</span>
                    </p>)}
                  </div>)}
                  {message.answer && (message.status === "answered" || message.status === "insufficient") ?
                    <div className="learning-actions" aria-label="回答反馈">
                      {message.feedback ? <p>已反馈：{message.feedback === "helpful" ? "有帮助" : message.feedback === "unhelpful" ? "无帮助" : "引用不准确"}</p>
                        : <>{([ ["helpful", "有帮助"], ["unhelpful", "无帮助"], ["citation_inaccurate", "引用不准确"] ] as const).map(([rating, label]) =>
                          <button className="button button--secondary" disabled={feedback.isPending} key={rating} onClick={() => feedback.mutate({ messageId: message.id, rating })} type="button">{label}</button>)}</>}
                    </div> : null}
                </article>)}
              </div>
              <form className="agent-composer" onSubmit={(event) => { event.preventDefault(); if (canSend) ask.mutate(question); }}>
                <label htmlFor="conversation-question">发送任务或问题</label>
                <textarea aria-describedby="agent-composer-help" ref={composer} id="conversation-question" maxLength={2000} onChange={(event) => setQuestion(event.target.value)} rows={3} value={question}
                  onKeyDown={(event) => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); if (canSend) ask.mutate(question); } }} />
                <div className="agent-composer-footer"><p id="agent-composer-help">Ctrl / ⌘ + Enter 发送。{working ? "当前任务完成或停止后可继续。" : "回答与练习只使用当前资料范围。"}</p>
                  <button className="button button--primary" disabled={!canSend} type="submit">{ask.isPending ? "正在发送…" : "发送"}</button></div>
              </form>
            </> : null}
          </section> : null}
        </div>
      </div>
    </main>
  </div>;
}
