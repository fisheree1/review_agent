import { useEffect, useId, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getAttempt, retryGrading, submitAttempt, type AttemptQuestion } from "../api/learning";
import { useQuizDraft } from "../hooks/useQuizDraft";
import { SourcePreview } from "./SourcePreview";
import { ErrorState } from "./ErrorState";

type ResponseValue = string | string[] | boolean;
const isAnswered = (response: ResponseValue | undefined) => typeof response === "string"
  ? response.trim().length > 0 : Array.isArray(response) ? response.length > 0 : typeof response === "boolean";

function AnswerEditor({ question, value, onChange, disabled, idPrefix }: {
  question: AttemptQuestion; value: ResponseValue | undefined;
  onChange: (value: ResponseValue) => void; disabled: boolean;
  idPrefix: string;
}) {
  const bilingual = question.stem.startsWith("中文：");
  if (question.kind === "single") return <fieldset><legend>选择一项</legend>
    {question.options.map((option) => <label className="quiz-option" key={option}>
      <input checked={value === option} disabled={disabled} name={`${idPrefix}-${question.id}`} onChange={() => onChange(option)} type="radio" />{option}
    </label>)}
  </fieldset>;
  if (question.kind === "multiple") return <fieldset><legend>选择所有正确项</legend>
    {question.options.map((option) => <label className="quiz-option" key={option}>
      <input checked={Array.isArray(value) && value.includes(option)} disabled={disabled} onChange={() => {
        const selected = Array.isArray(value) ? value : [];
        onChange(selected.includes(option) ? selected.filter((item) => item !== option) : [...selected, option]);
      }} type="checkbox" />{option}
    </label>)}
  </fieldset>;
  if (question.kind === "true_false") return <fieldset><legend>判断正误</legend>
    <label className="quiz-option"><input checked={value === true} disabled={disabled} name={`${idPrefix}-${question.id}`} onChange={() => onChange(true)} type="radio" />{bilingual ? "正确 / True" : "正确"}</label>
    <label className="quiz-option"><input checked={value === false} disabled={disabled} name={`${idPrefix}-${question.id}`} onChange={() => onChange(false)} type="radio" />{bilingual ? "错误 / False" : "错误"}</label>
  </fieldset>;
  return <><label htmlFor={`answer-${idPrefix}-${question.id}`}>你的简答</label>
    <textarea disabled={disabled} id={`answer-${idPrefix}-${question.id}`} maxLength={2000} onChange={(event) => onChange(event.target.value)} rows={5} value={typeof value === "string" ? value : ""} />
    <p>简答评分由模型提供学习提示，不是人工评分。</p></>;
}

function answerText(value: string | string[] | boolean | null, bilingual: boolean): string {
  if (Array.isArray(value)) return value.join(bilingual ? "\n\n" : "、");
  if (typeof value === "boolean") return bilingual ? value ? "正确 / True" : "错误 / False" : value ? "正确" : "错误";
  return value ?? "未作答";
}

export function QuizAttemptPanel({ quizId, attemptId, weakOnlyInitially = false, onPractice }: {
  quizId: string; attemptId: string; weakOnlyInitially?: boolean; onPractice?: () => void;
}) {
  const panelId = useId();
  const cache = useQueryClient();
  const attempt = useQuery({
    queryKey: ["attempt", quizId, attemptId], queryFn: () => getAttempt(quizId, attemptId),
    refetchInterval: (query) => query.state.data?.status === "grading" ? 2000 : false,
  });
  const [responses, setResponses] = useState<Record<string, ResponseValue>>({});
  const draft = useQuizDraft(quizId, attemptId, attempt.data?.revision ?? 0);
  const unansweredAtFilter = useRef(new Set<string>());
  const [unansweredOnly, setUnansweredOnly] = useState(false);
  const [weakOnly, setWeakOnly] = useState(weakOnlyInitially);
  const initialized = useRef("");
  useEffect(() => {
    if (!attempt.data || initialized.current === attemptId) return;
    initialized.current = attemptId;
    const saved: Record<string, ResponseValue> = {};
    for (const question of attempt.data.questions) {
      if (question.response !== null) saved[question.id] = question.response;
    }
    setResponses(saved);
  }, [attempt.data, attemptId]);
  const submit = useMutation({
    mutationFn: async () => {
      await draft.flush();
      return submitAttempt(quizId, attemptId);
    },
    onSuccess: () => { void cache.invalidateQueries({ queryKey: ["attempt", quizId, attemptId] }); void cache.invalidateQueries({ queryKey: ["quiz", quizId, "attempts"] }); },
  });
  const retry = useMutation({
    mutationFn: () => retryGrading(quizId, attemptId),
    onSuccess: () => { void cache.invalidateQueries({ queryKey: ["attempt", quizId, attemptId] }); },
  });
  const error = attempt.error ?? submit.error ?? retry.error;
  const reloadSaved = async () => {
    const result = await attempt.refetch();
    if (!result.data || !draft.discard(result.data.revision)) return;
    const saved: Record<string, ResponseValue> = {};
    for (const question of result.data.questions) {
      if (question.response !== null) saved[question.id] = question.response;
    }
    setResponses(saved);
  };
  const complete = attempt.data?.status === "submitted";
  const reviewedQuestions = attempt.data?.questions ?? [];
  const visibleQuestions = attempt.data?.questions.filter((question) =>
    (!weakOnly || !complete || (question.earned ?? 0) < 0.7) &&
    (!unansweredOnly || complete || unansweredAtFilter.current.has(question.id))
  );

  const answeredCount = attempt.data?.questions.filter((question) => isAnswered(responses[question.id])).length ?? 0;
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  useEffect(() => {
    if (!pendingQuestion) return;
    const question = document.getElementById(`quiz-question-${panelId}-${pendingQuestion}`);
    question?.scrollIntoView({ block: "start", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
    question?.focus({ preventScroll: true });
    setPendingQuestion(null);
  }, [pendingQuestion, panelId]);
  const total = attempt.data?.questions.length ?? 0;
  return <div className="quiz-attempt-panel">
      {attempt.data?.status === "in_progress" ? <section className="quiz-progress" aria-label="作答进度">
        <div><strong key={answeredCount}>已答 {answeredCount} / {total} 题</strong><span>{total - answeredCount ? `还有 ${total - answeredCount} 题未答` : "已完成全部作答，可以提交"}</span></div>
        <progress className="visually-hidden" max={Math.max(total, 1)} value={answeredCount} aria-label="已作答题数" />
        <div className="quiz-progress__bar" aria-hidden="true"><span style={{ transform: `scaleX(${answeredCount / Math.max(total, 1)})` }} /></div>
      </section> : null}
      {attempt.data && total > 0 ? <nav className="quiz-navigation" aria-label="题号导航">
        {reviewedQuestions.map((question) => {
          const answered = isAnswered(responses[question.id]);
          const weak = complete && (question.earned ?? 0) < .7;
          return <button className={`question-number${answered ? " question-number--answered" : ""}${weak ? " question-number--weak" : ""}`} key={question.id} type="button"
            aria-label={`第 ${question.ordinal} 题 · ${complete ? weak ? "建议复习" : "已完成" : answered ? "已答" : "未答"}`}
            onClick={() => { setWeakOnly(false); setUnansweredOnly(false); setPendingQuestion(question.id); }}>{question.ordinal}{!complete && !answered ? <span aria-hidden="true">·</span> : null}</button>;
        })}
        {!complete && attempt.data.status === "in_progress" ? <button className="button button--secondary" type="button" onClick={() => { unansweredAtFilter.current = new Set(reviewedQuestions.filter((question) => !isAnswered(responses[question.id])).map((question) => question.id)); setUnansweredOnly(!unansweredOnly); }}>{unansweredOnly ? "显示全部题目" : "只看未答题"}</button> : null}
      </nav> : null}
      {unansweredOnly && visibleQuestions?.length === 0 ? <p role="status">所有题目已作答，可以提交。</p> : null}
      {attempt.data?.status === "in_progress" ? <p role="status">{draft.status === "saving" ? "正在保存，请保存完成后离开…" : draft.status === "saved" ? "作答已保存" : "作答会自动保存"}</p> : null}
      {draft.error ? <div role="alert"><p>{draft.error}。本页草稿仍保留，提交前必须保存成功。</p>
        <button className="button button--secondary" onClick={() => void draft.flush().catch(() => undefined)} type="button">重试保存</button>
        <button className="button button--secondary" onClick={() => void reloadSaved()} type="button">放弃本页未保存修改，加载已保存作答</button>
      </div> : null}
      {error ? <ErrorState message={error.message} onRetry={() => { void attempt.refetch(); }} /> : null}
      {attempt.isPending ? <p role="status">正在读取作答记录…</p> : null}
      {attempt.data?.status === "grading" ? <p role="status">正在为简答题生成学习评分提示…</p> : null}
      {attempt.data?.status === "failed" ? <div role="alert"><p>评分失败。已保存的作答仍可查看。</p>
        {attempt.data.failure_code?.startsWith("RUN_") || attempt.data.failure_code === "AGENT_RUN_INACTIVE" ? <p>关联学习任务已停止或结果未确认。请保留当前记录，在 Quiz 页面开始新的作答。</p> :
        <button className="button button--secondary" disabled={retry.isPending} onClick={() => retry.mutate()} type="button">重新评分</button>
        }
      </div> : null}
      {complete ? <section className="learning-card" aria-label="成绩与薄弱知识点">
        <h2>得分 {attempt.data?.score ?? 0} / 100</h2>
        {attempt.data?.weak_topics?.length ? <><h3>建议复习</h3><ul>{attempt.data.weak_topics.map((topic) => <li key={topic}>
          <a href={`#quiz-question-${panelId}-${reviewedQuestions.find((question) => question.topic === topic && (question.earned ?? 0) < 0.7)?.id}`}>{topic}</a>
        </li>)}</ul>
          <button className="button button--secondary" onClick={() => setWeakOnly((value) => !value)} type="button">{weakOnly ? "查看全部题目" : "只看薄弱题"}</button></>
          : <p>本次没有识别出明显薄弱知识点。</p>}
        {attempt.data?.weak_topics?.length && onPractice ? <button className="button button--secondary" onClick={onPractice} type="button">练习薄弱知识点</button> : null}
      </section> : null}
      {visibleQuestions?.map((question) => <section className="learning-card quiz-question" tabIndex={-1} id={`quiz-question-${panelId}-${question.id}`} key={question.id}>
        <span className="eyebrow">第 {question.ordinal} 题 · {question.topic}</span>
        <h2>{question.stem}</h2>
        <AnswerEditor idPrefix={panelId} disabled={attempt.data?.status !== "in_progress" || submit.isPending} onChange={(value) => { setResponses((current) => ({ ...current, [question.id]: value })); draft.edit(question.id, value); }} question={question} value={responses[question.id]} />
        {complete ? <div className="quiz-review">
          <p>你的答案：{answerText(question.response, question.stem.startsWith("中文："))}</p>
          <p>参考答案：{answerText(question.answer, question.stem.startsWith("中文："))}</p>
          <p>得分：{Math.round((question.earned ?? 0) * 100)}%</p>
          {question.grading_method === "model_hint" ? <p>简答题为模型评分提示，请核对原文。</p> : null}
          <p>{question.feedback}</p><p>{question.explanation}</p>
          <h3>核对来源</h3>
          {question.sources.map((source) => <SourcePreview key={source.source_id} source={source} action="返回原文" />)}
        </div> : null}
      </section>)}
      {attempt.data?.status === "in_progress" ? <div className="quiz-submit"><p>{total - answeredCount ? `${total - answeredCount} 题未答，提交后将按未答计分。` : "提交后查看成绩、解析与来源。"}</p><button className="button button--primary" disabled={submit.isPending} onClick={() => submit.mutate()} type="button">
        {submit.isPending ? "正在保存并提交…" : "提交作答"}
      </button></div> : null}
  </div>;
}
