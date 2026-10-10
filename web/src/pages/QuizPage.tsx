import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  createQuiz, getQuiz, listAttempts, listCollections, listQuizzes, startAttempt,
  type QuizConfig, type ScopeChoice,
} from "../api/learning";
import { AppHeader } from "../components/AppHeader";
import { ErrorState } from "../components/ErrorState";
import { SelectField } from "../components/SelectField";
import { PageHeading } from "../components/PageHeading";
import { ScopePicker } from "../components/ScopePicker";
import { useDocuments } from "../hooks/useDocuments";

const initialConfig: QuizConfig = {
  type_counts: { single: 2, multiple: 1, true_false: 1, short: 1 },
  difficulty: "medium", language: "zh", topic: "",
};
const difficultyOptions = [{ value: "easy", label: "入门" }, { value: "medium", label: "中等" }, { value: "hard", label: "进阶" }] as const;
const languageOptions = [{ value: "zh", label: "中文" }, { value: "en", label: "English" }, { value: "zh-en", label: "中英对照" }] as const;
const generationOptions = [{ value: "standard", label: "标准出题" }, { value: "agent", label: "自主规划出题（实验）" }] as const;

export function QuizPage() {
  const { quizId } = useParams();
  const navigate = useNavigate();
  const cache = useQueryClient();
  const { documents, error: documentsError, isLoading: documentsLoading, nextCursor, loadMore, refresh: refreshDocuments } = useDocuments();
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
  const error = documentsError ?? collections.error ?? quizzes.error ?? quiz.error ?? attempts.error ?? create.error ?? start.error;
  const requested = Object.values(config.type_counts).reduce((sum, count) => sum + count, 0);
  const hasScope = scope.document_ids.length > 0 || scope.collection_ids.length > 0;
  const quizStatus = { queued: "等待生成", processing: "生成中", ready: "可以作答", failed: "生成失败", cancelled: "已停止" };
  const labels = { single: "单选", multiple: "多选", true_false: "判断", short: "简答" } as const;

  return <div className="app-page">
    <AppHeader />
    <main className="learning-page quiz-page" id="main-content" tabIndex={-1}>
      <PageHeading title="练习" kicker="学以致用" description="把理解变成练习，在每一次作答中找到进步。" />
      {error ? <ErrorState message={error.message} onRetry={() => { void refreshDocuments(); void collections.refetch(); void quizzes.refetch(); if (quizId) { void quiz.refetch(); void attempts.refetch(); } }} /> : null}
      <div className="learning-grid quiz-layout">
        <div className="learning-content">
          {!quizId ? <section className="learning-card quiz-create"><h2>创建练习</h2>
            <form onSubmit={(event) => { event.preventDefault(); if (title.trim() && requested > 0 && requested <= 10 && hasScope) create.mutate(); }}>
              <div className="quiz-form-grid"><div className="quiz-form-source">
              <label htmlFor="quiz-title">标题</label>
              <input placeholder="例如：统计学第一章练习…" id="quiz-title" name="quiz-title" autoComplete="off" maxLength={160} onChange={(event) => setTitle(event.target.value)} value={title} />
              <ScopePicker collections={collections.data ?? []} documents={documents} hasMore={Boolean(nextCursor)} label="出题资料范围" loading={documentsLoading} onChange={setScope} value={scope} />
              {nextCursor ? <button className="button button--secondary" onClick={() => void loadMore()} type="button">加载更多资料</button> : null}
              </div><div className="quiz-form-settings">
              <fieldset className="quiz-settings"><legend>题型与数量（总计最多 10 题）</legend>
                {(Object.keys(labels) as (keyof typeof labels)[]).map((kind) => <label key={kind}>
                  <span>{labels[kind]}</span><input name={`count-${kind}`} autoComplete="off" inputMode="numeric" min={0} max={10} type="number" value={config.type_counts[kind]} onChange={(event) =>
                    setConfig((current) => ({ ...current, type_counts: { ...current.type_counts, [kind]: Number(event.target.value) } }))} />
                </label>)}
              </fieldset>
              <p className="quiz-blueprint-summary" role={requested > 10 || requested < 1 ? "alert" : "status"}>共 {requested} 题 · 请选择 1–10 题</p>
              <SelectField id="quiz-difficulty" name="quiz-difficulty" label="难度" value={config.difficulty} onChange={(value) => setConfig((current) => ({ ...current, difficulty: value as QuizConfig["difficulty"] }))} options={difficultyOptions} />
              <label htmlFor="quiz-topic">重点知识点（可选）</label>
              <input id="quiz-topic" name="quiz-topic" autoComplete="off" maxLength={120} value={config.topic} onChange={(event) => setConfig({ ...config, topic: event.target.value })} />
              <details className="quiz-advanced"><summary>更多设置</summary>
              <SelectField id="quiz-language" name="quiz-language" label="语言" value={config.language} onChange={(value) => setConfig((current) => ({ ...current, language: value as QuizConfig["language"] }))} options={languageOptions} />
              <p>中英对照会在每道题的题干、选项、答案和解析中同时显示中文与英文。</p>
              <SelectField id="quiz-generation-mode" name="quiz-generation-mode" label="出题方式" descriptionId="quiz-generation-help" value={config.generation_mode ?? "standard"} onChange={(value) => setConfig((current) => ({ ...current, generation_mode: value as QuizConfig["generation_mode"] }))} options={generationOptions} />
              <p id="quiz-generation-help">自主规划出题可能耗时更长，题目仍会检查答案与来源。</p>
              </details>
              </div></div>
              <div className="quiz-create-actions">
              <button className="button button--primary" disabled={!title.trim() || !hasScope || requested < 1 || requested > 10 || create.isPending} type="submit">生成 {requested} 题</button><span>题目根据所选资料生成</span></div>
            </form>
          </section> : null}
          {quizId ? <section className="learning-card quiz-overview" aria-label="当前 Quiz">
            {quiz.isPending ? <p role="status">正在打开 Quiz…</p> : null}
            {quiz.data ? <><h2>{quiz.data.title}</h2>
              <span className="quiz-status">{quizStatus[quiz.data.status as keyof typeof quizStatus] ?? "处理中"}</span>
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
        <aside className="learning-sidebar"><section className="learning-card"><div className="sidebar-heading"><h2>历史练习</h2><Link className="button button--secondary" to="/quizzes">新建</Link></div>
          {quizzes.isPending ? <p role="status">正在读取 Quiz…</p> : null}
          {quizzes.data?.length === 0 ? <p>还没有 Quiz。</p> : null}
          <nav aria-label="历史 Quiz">{quizzes.data?.map((item) =>
            <Link aria-current={item.id === quizId ? "page" : undefined} key={item.id} to={`/quizzes/${item.id}`}>{item.title} · {item.status === "ready" ? `${item.question_count} 题` : (quizStatus[item.status as keyof typeof quizStatus] ?? "处理中")}</Link>)}</nav>
        </section></aside>
      </div>
    </main>
  </div>;
}
