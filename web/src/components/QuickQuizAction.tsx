import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";

import { createQuiz, type QuizConfig } from "../api/learning";
import { getIndex, startIndex } from "../api/rag";

const quickConfig: QuizConfig = {
  type_counts: { single: 5, multiple: 0, true_false: 0, short: 0 },
  difficulty: "medium", language: "zh", topic: "",
};

export function QuickQuizAction({ documentId, filename }: { documentId: string; filename: string }) {
  const navigate = useNavigate();
  const cache = useQueryClient();
  const [generateWhenReady, setGenerateWhenReady] = useState(false);
  const generationStarted = useRef(false);
  const mounted = useRef(true);
  const indexKey = useRef<string | null>(null);
  const quizKey = useRef<string | null>(null);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const index = useQuery({
    queryKey: ["rag", documentId, "index"], queryFn: () => getIndex(documentId),
    refetchInterval: (query) => ["queued", "processing"].includes(query.state.data?.status ?? "") ? 2000 : false,
  });
  const prepare = useMutation({
    mutationFn: () => {
      indexKey.current ??= crypto.randomUUID();
      return startIndex(documentId, indexKey.current);
    },
    onSuccess: (next) => {
      indexKey.current = null;
      cache.setQueryData(["rag", documentId, "index"], next);
      void cache.invalidateQueries({ queryKey: ["rag", documentId, "index"] });
    },
  });
  const create = useMutation({
    mutationFn: () => {
      quizKey.current ??= crypto.randomUUID();
      const title = `${(filename.replace(/\.pdf$/i, "").trim() || "资料").slice(0, 140)} · 5 题练习`;
      return createQuiz(title, quickConfig, { document_ids: [documentId], collection_ids: [] }, quizKey.current);
    },
    onSuccess: (quiz) => {
      quizKey.current = null;
      void cache.invalidateQueries({ queryKey: ["quizzes"] });
      if (mounted.current) navigate(`/quizzes/${quiz.id}`);
    },
  });

  useEffect(() => {
    if (generateWhenReady && index.data?.status === "ready" && !generationStarted.current) {
      generationStarted.current = true;
      create.mutate();
    }
  }, [create.mutate, generateWhenReady, index.data?.status]);

  const status = index.data?.status;
  const busy = prepare.isPending || create.isPending || (generateWhenReady && (status === "queued" || status === "processing"));
  const begin = () => {
    if (status === "ready") {
      if (generationStarted.current && !create.error) return;
      generationStarted.current = true;
      create.mutate();
    } else if (status === "queued" || status === "processing") {
      setGenerateWhenReady(true);
    } else {
      setGenerateWhenReady(true);
      prepare.mutate();
    }
  };

  return <section aria-label="快速生成练习" className="reader-quick-quiz">
    <div className="reader-quick-quiz__actions">
      <button className="button button--primary" disabled={index.isPending || Boolean(index.error) || busy} onClick={begin} type="button">
        {create.isPending ? "正在创建练习…" : busy ? "准备完成后将自动出题…" : status === "ready" ? "用这份资料生成 5 题" : status === "queued" || status === "processing" ? "准备完成后生成 5 题" : "准备资料并生成 5 题"}
      </button>
      <Link className="button button--secondary" to="/quizzes">自定义题型与语言</Link>
    </div>
    {index.isPending ? <p role="status">正在检查资料是否可以出题…</p> : null}
    {status === "not_indexed" || status === "failed" ? <p>准备资料时，提取正文会发送至阿里云百炼；出题时相关片段会发送至 DeepSeek。默认生成 5 道中文单选题。</p> : null}
    {status === "ready" ? <p>默认生成 5 道中文单选题；出题时相关片段会发送至 DeepSeek。</p> : null}
    {status === "queued" || status === "processing" ? <p role="status">资料正在准备{generateWhenReady ? "；留在此页，完成后会自动生成练习" : "，可以先继续阅读"}。</p> : null}
    {index.data?.failure_message ? <p role="alert">{index.data.failure_message}</p> : null}
    {index.error ? <p role="alert">无法读取资料准备状态。<button className="text-action" onClick={() => void index.refetch()} type="button">重试检查</button></p> : null}
    {prepare.error ? <p role="alert">准备资料失败：{prepare.error.message}。可重试。</p> : null}
    {create.error ? <p role="alert">生成练习失败：{create.error.message}。可重试。</p> : null}
  </section>;
}
