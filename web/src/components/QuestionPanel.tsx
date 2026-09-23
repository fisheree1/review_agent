import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { askQuestion, cancelQuestion, getIndex, getQuestions, isAnswering, startIndex, type Citation } from "../api/rag";
import { citationLabel } from "../citations";
import { ErrorState } from "./ErrorState";

interface Props {
  documentId: string;
  filename: string;
  onCitation: (citation: Citation) => void;
}

export function QuestionPanel({ documentId, filename, onCitation }: Props) {
  const cache = useQueryClient();
  const [question, setQuestion] = useState("");
  const [copyMessage, setCopyMessage] = useState("");
  const request = useRef<{ text: string; key: string } | null>(null);
  const indexKey = useRef<string | null>(null);
  const index = useQuery({
    queryKey: ["rag", documentId, "index"], queryFn: () => getIndex(documentId),
    refetchInterval: (query) => ["queued", "processing"].includes(query.state.data?.status ?? "") ? 2000 : false,
  });
  const history = useQuery({
    queryKey: ["rag", documentId, "questions"], queryFn: () => getQuestions(documentId),
    refetchInterval: (query) => query.state.data?.some(isAnswering) ? 1500 : false,
  });
  const refresh = () => cache.invalidateQueries({ queryKey: ["rag", documentId] });
  const indexing = useMutation({
    mutationFn: () => {
      indexKey.current ??= crypto.randomUUID();
      return startIndex(documentId, indexKey.current);
    },
    onSuccess: async () => { indexKey.current = null; await refresh(); },
  });
  const asking = useMutation({
    mutationFn: (text: string) => {
      if (request.current?.text !== text) request.current = { text, key: crypto.randomUUID() };
      return askQuestion(documentId, text, request.current.key);
    },
    onSuccess: async () => { request.current = null; setQuestion(""); await refresh(); },
  });
  const cancel = useMutation({ mutationFn: (id: string) => cancelQuestion(documentId, id), onSuccess: refresh });
  const error = index.error ?? history.error ?? indexing.error ?? asking.error ?? cancel.error;
  const busy = history.data?.some(isAnswering) || asking.isPending;
  const indexBusy = ["queued", "processing"].includes(index.data?.status ?? "");
  return (
    <section aria-labelledby="questions-title" className="question-panel">
      <h2 id="questions-title">与资料一起思考</h2>
      <p className="question-panel__scope">回答范围：仅「{filename}」</p>
      {index.isPending ? <p role="status">正在检查问答准备状态…</p> : null}
      {index.data?.status === "not_indexed" || index.data?.status === "failed" ? (
        <div className="question-panel__setup">
          <p>建立索引后即可提问。资料正文会发送至 Voyage；提问时，相关片段会发送至 DeepSeek。</p>
          {index.data.failure_message ? <p role="alert">{index.data.failure_message}</p> : null}
          <button className="button button--primary" disabled={indexing.isPending} onClick={() => indexing.mutate()} type="button">
            {indexing.isPending ? "正在提交…" : index.data.status === "failed" ? "重试建立索引" : "准备资料问答"}
          </button>
        </div>
      ) : null}
      {indexBusy ? <div role="status"><p>正在准备资料问答，已完成 {index.data?.completed} / {index.data?.total || "…"} 段。你可以继续阅读。</p><progress aria-label="资料问答准备进度" max={index.data?.total || 1} value={index.data?.completed || 0} /></div> : null}
      {error ? <ErrorState message={error.message} onRetry={() => void refresh()} /> : null}
      <form onSubmit={(event) => { event.preventDefault(); if (question.trim().length >= 2 && !busy) asking.mutate(question.trim()); }}>
        <label htmlFor="rag-question">你想了解这份资料的什么内容？</label>
        <textarea id="rag-question" maxLength={2000} onChange={(event) => setQuestion(event.target.value)} placeholder="例如：这份资料如何解释缺失值处理？" rows={3} value={question} />
        <button className="button button--primary" disabled={index.data?.status !== "ready" || busy || question.trim().length < 2} type="submit">{asking.isPending ? "正在提交…" : "提问"}</button>
      </form>
      <div aria-live="polite" aria-atomic="false">
        {history.isPending ? <p role="status">正在读取提问记录…</p> : null}
        {history.data?.length === 0 && index.data?.status === "ready" ? <p>资料已准备好。提出第一个问题，回答会附上可核对的原文。</p> : null}
        {history.data?.map((item) => (
          <article className="question-answer" key={item.id}>
            <h3>{item.question}</h3>
            {isAnswering(item) ? <div role="status"><p>{item.status === "queued" ? "问题已排队…" : "正在查找依据并整理回答…"}</p><button className="button button--secondary" disabled={cancel.isPending} onClick={() => cancel.mutate(item.id)} type="button">停止回答</button></div> : null}
            {item.status === "cancelled" ? <p>已停止。已发送给服务商的请求可能仍会计费。</p> : null}
            {item.status === "failed" ? <div role="alert"><p>{item.failure_message}</p><button className="button button--secondary" disabled={busy} onClick={() => asking.mutate(item.question)} type="button">重新提问</button></div> : null}
            {item.status === "insufficient" ? <p>在所选资料中没有找到足够依据。请补充相关资料，或把问题问得更具体。</p> : null}
            {item.answer?.claims.map((claim, claimIndex) => (
              <div className="answer-claim" key={claimIndex}>
                <p>{claim.text}</p>
                {claim.citations.map((citation, citationIndex) => <button className="citation-link" key={`${citation.source_id}-${citationIndex}`} onClick={() => onCitation(citation)} type="button" aria-label={`查看引用：${citationLabel(citation.locator)}`}>[{claimIndex + 1}.{citationIndex + 1}] {citationLabel(citation.locator)}</button>)}
              </div>
            ))}
            {item.status === "answered" ? <button className="button button--secondary" type="button" onClick={() => {
              const text = item.answer?.claims.map((claim) => `${claim.text}\n${claim.citations.map((c) => `${citationLabel(c.locator)}：${c.quote}`).join("\n")}`).join("\n\n") ?? "";
              void navigator.clipboard.writeText(text).then(() => setCopyMessage("已复制回答及来源"), () => setCopyMessage("无法复制，请手动选择文字"));
            }}>复制回答与引用</button> : null}
          </article>
        ))}
      </div>
      {copyMessage ? <p role="status">{copyMessage}</p> : null}
    </section>
  );
}
