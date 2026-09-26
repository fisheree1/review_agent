import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  createQuiz, getQuiz, listAttempts, listCollections, listQuizzes, startAttempt,
  type QuizConfig, type ScopeChoice,
} from "../api/learning";
import { AppHeader } from "../components/AppHeader";
import { ErrorState } from "../components/ErrorState";
import { ScopePicker } from "../components/ScopePicker";
import { useDocuments } from "../hooks/useDocuments";

const initialConfig: QuizConfig = {
  type_counts: { single: 2, multiple: 1, true_false: 1, short: 1 },
  difficulty: "medium", language: "zh", topic: "",
};

export function QuizPage() {
  const { quizId } = useParams();
  const navigate = useNavigate();
  const cache = useQueryClient();
  const { documents, nextCursor, loadMore } = useDocuments();
  const collections = useQuery({ queryKey: ["collections"], queryFn: listCollections });
  const quizzes = useQuery({ queryKey: ["quizzes"], queryFn: listQuizzes });
  const quiz = useQuery({
    queryKey: ["quiz", quizId], queryFn: () => getQuiz(quizId!), enabled: Boolean(quizId),
    refetchInterval: (query) => query.state.data?.status === "queued" || query.state.data?.status === "processing" ? 2000 : false,
  });
  const attempts = useQuery({
    queryKey: ["quiz", quizId, "attempts"], queryFn: () => listAttempts(quizId!), enabled: Boolean(quizId),
  });
  const [title, setTitle] = useState("");
  const [scope, setScope] = useState<ScopeChoice>({ document_ids: [], collection_ids: [] });
  const [config, setConfig] = useState<QuizConfig>(initialConfig);
  const createKey = useRef<{ fingerprint: string; key: string } | null>(null);
  const create = useMutation({
    mutationFn: () => {
      const fingerprint = JSON.stringify({ title, config, scope });
      if (createKey.current?.fingerprint !== fingerprint) {
        createKey.current = { fingerprint, key: crypto.randomUUID() };
      }
      return createQuiz(title, config, scope, createKey.current.key);
    },
    onSuccess: (created) => { createKey.current = null; setTitle(""); void cache.invalidateQueries({ queryKey: ["quizzes"] }); navigate(`/quizzes/${created.id}`); },
  });
  const start = useMutation({
    mutationFn: () => startAttempt(quizId!, crypto.randomUUID()),
    onSuccess: (attempt) => navigate(`/quizzes/${quizId}/attempts/${attempt.id}`),
  });
  const error = collections.error ?? quizzes.error ?? quiz.error ?? attempts.error ?? create.error ?? start.error;
  const requested = Object.values(config.type_counts).reduce((sum, count) => sum + count, 0);
  const labels = { single: "单选", multiple: "多选", true_false: "判断", short: "简答" } as const;

  return <div className="app-page">
    <AppHeader />
    <main className="learning-page" id="main-content">
      <header className="learning-heading"><span className="eyebrow">Quiz</span><h1>用练习检验理解</h1>
        <p>题目只会从选定资料的有效来源生成；无效题不会发布。</p></header>
      {error ? <ErrorState message={error.message} onRetry={() => { void quizzes.refetch(); void quiz.refetch(); }} /> : null}
      <div className="learning-grid">
        <aside className="learning-sidebar"><section className="learning-card"><h2>历史 Quiz</h2>
          {quizzes.isPending ? <p role="status">正在读取 Quiz…</p> : null}
          {quizzes.data?.length === 0 ? <p>还没有 Quiz。</p> : null}
          <nav aria-label="历史 Quiz">{quizzes.data?.map((item) =>
            <Link aria-current={item.id === quizId ? "page" : undefined} key={item.id} to={`/quizzes/${item.id}`}>{item.title} · {item.status === "ready" ? `${item.question_count} 题` : item.status}</Link>)}</nav>
        </section></aside>
        <div className="learning-content">
          <section className="learning-card"><h2>创建 Quiz</h2>
            <form onSubmit={(event) => { event.preventDefault(); if (title.trim() && requested > 0) create.mutate(); }}>
              <label htmlFor="quiz-title">标题</label>
              <input id="quiz-title" maxLength={160} onChange={(event) => setTitle(event.target.value)} value={title} />
              <ScopePicker collections={collections.data ?? []} documents={documents} label="出题资料范围" onChange={setScope} value={scope} />
              {nextCursor ? <button className="button button--secondary" onClick={() => void loadMore()} type="button">加载更多资料</button> : null}
              <fieldset className="quiz-settings"><legend>题型与数量（总计最多 10 题）</legend>
                {(Object.keys(labels) as (keyof typeof labels)[]).map((kind) => <label key={kind}>
                  <span>{labels[kind]}</span><input min={0} max={10} type="number" value={config.type_counts[kind]} onChange={(event) =>
                    setConfig((current) => ({ ...current, type_counts: { ...current.type_counts, [kind]: Number(event.target.value) } }))} />
                </label>)}
              </fieldset>
              <label htmlFor="quiz-difficulty">难度</label>
              <select id="quiz-difficulty" value={config.difficulty} onChange={(event) => setConfig({ ...config, difficulty: event.target.value as QuizConfig["difficulty"] })}>
                <option value="easy">入门</option><option value="medium">中等</option><option value="hard">进阶</option>
              </select>
              <label htmlFor="quiz-language">语言</label>
              <select id="quiz-language" value={config.language} onChange={(event) => setConfig({ ...config, language: event.target.value as QuizConfig["language"] })}>
                <option value="zh">中文</option><option value="en">English</option>
              </select>
              <label htmlFor="quiz-topic">重点知识点（可选）</label>
              <input id="quiz-topic" maxLength={120} value={config.topic} onChange={(event) => setConfig({ ...config, topic: event.target.value })} />
              <label htmlFor="quiz-generation-mode">出题方式</label>
              <select id="quiz-generation-mode" aria-describedby="quiz-generation-help" value={config.generation_mode ?? "standard"} onChange={(event) => setConfig({ ...config, generation_mode: event.target.value as QuizConfig["generation_mode"] })}>
                <option value="standard">标准出题</option><option value="agent">自主规划出题（实验）</option>
              </select>
              <p id="quiz-generation-help">自主规划会按知识点选择检索与阅读步骤，可能耗时更长；题目仍须通过答案与来源校验。</p>
              <button className="button button--primary" disabled={!title.trim() || requested < 1 || requested > 10 || create.isPending} type="submit">生成 {requested} 题</button>
            </form>
          </section>
          {quizId ? <section className="learning-card" aria-label="当前 Quiz">
            {quiz.isPending ? <p role="status">正在打开 Quiz…</p> : null}
            {quiz.data ? <><h2>{quiz.data.title}</h2>
              <p className="learning-scope">资料范围：{quiz.data.scope.map((item) => item.filename).join("、")}</p>
              <p>出题方式：{quiz.data.config.generation_mode === "agent" ? "自主规划出题（实验）" : "标准出题"}</p>
              {quiz.data.status === "queued" || quiz.data.status === "processing" ? <p role="status">正在检索依据并校验题目…</p> : null}
              {quiz.data.status === "failed" ? <p role="alert">{quiz.data.failure_message ?? "题目生成失败，请重新创建。"}</p> : null}
              {quiz.data.status === "ready" ? <><p>已生成 {quiz.data.question_count} 题；若少于请求数量，表示其余候选题没有通过校验。</p>
                <button className="button button--primary" disabled={start.isPending} onClick={() => start.mutate()} type="button">开始作答</button>
                <h3>作答记录</h3>
                {attempts.data?.length === 0 ? <p>尚无作答。</p> : null}
                <ul>{attempts.data?.map((attempt) => <li key={attempt.id}>
                  <Link to={`/quizzes/${quizId}/attempts/${attempt.id}`}>{attempt.status === "submitted" ? `得分 ${attempt.score ?? 0}` : "继续作答"}</Link>
                </li>)}</ul>
              </> : null}
            </> : null}
          </section> : null}
        </div>
      </div>
    </main>
  </div>;
}
