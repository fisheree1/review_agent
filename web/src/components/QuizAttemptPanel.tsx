import { useEffect, useId, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { getAttempt, retryGrading, saveQuizAnswer, submitAttempt, type AttemptQuestion } from "../api/learning";
import { citationLabel } from "../citations";
import { ErrorState } from "./ErrorState";

type ResponseValue = string | string[] | boolean;

function AnswerEditor({ question, value, onChange, disabled, idPrefix }: {
  question: AttemptQuestion; value: ResponseValue | undefined;
  onChange: (value: ResponseValue) => void; disabled: boolean;
  idPrefix: string;
}) {
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
    <label className="quiz-option"><input checked={value === true} disabled={disabled} name={`${idPrefix}-${question.id}`} onChange={() => onChange(true)} type="radio" />正确</label>
    <label className="quiz-option"><input checked={value === false} disabled={disabled} name={`${idPrefix}-${question.id}`} onChange={() => onChange(false)} type="radio" />错误</label>
  </fieldset>;
  return <><label htmlFor={`answer-${idPrefix}-${question.id}`}>你的简答</label>
    <textarea disabled={disabled} id={`answer-${idPrefix}-${question.id}`} maxLength={2000} onChange={(event) => onChange(event.target.value)} rows={5} value={typeof value === "string" ? value : ""} />
    <p>简答评分由模型提供学习提示，不是人工评分。</p></>;
}

function answerText(value: string | string[] | boolean | null): string {
  if (Array.isArray(value)) return value.join("、");
  if (typeof value === "boolean") return value ? "正确" : "错误";
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
      for (const question of attempt.data?.questions ?? []) {
        const response = responses[question.id];
        if (response !== undefined && !(Array.isArray(response) && response.length === 0)) {
          await saveQuizAnswer(quizId, attemptId, question.id, response);
        }
      }
      return submitAttempt(quizId, attemptId);
    },
    onSuccess: () => { void cache.invalidateQueries({ queryKey: ["attempt", quizId, attemptId] }); void cache.invalidateQueries({ queryKey: ["quiz", quizId, "attempts"] }); },
  });
  const retry = useMutation({
    mutationFn: () => retryGrading(quizId, attemptId),
    onSuccess: () => { void cache.invalidateQueries({ queryKey: ["attempt", quizId, attemptId] }); },
  });
  const error = attempt.error ?? submit.error ?? retry.error;
  const complete = attempt.data?.status === "submitted";
  const reviewedQuestions = attempt.data?.questions ?? [];
  const visibleQuestions = attempt.data?.questions.filter((question) =>
    !weakOnly || !complete || (question.earned ?? 0) < 0.7
  );

  return <div className="quiz-attempt-panel">
      {error ? <ErrorState message={error.message} onRetry={() => { void attempt.refetch(); }} /> : null}
      {attempt.isPending ? <p role="status">正在读取作答记录…</p> : null}
      {attempt.data?.status === "grading" ? <p role="status">正在为简答题生成学习评分提示…</p> : null}
      {attempt.data?.status === "failed" ? <div role="alert"><p>评分失败。已保存的作答仍可查看。</p>
        <button className="button button--secondary" disabled={retry.isPending} onClick={() => retry.mutate()} type="button">重新评分</button>
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
      {visibleQuestions?.map((question) => <section className="learning-card quiz-question" id={`quiz-question-${panelId}-${question.id}`} key={question.id}>
        <span className="eyebrow">第 {question.ordinal} 题 · {question.topic}</span>
        <h2>{question.stem}</h2>
        <AnswerEditor idPrefix={panelId} disabled={attempt.data?.status !== "in_progress"} onChange={(value) => setResponses((current) => ({ ...current, [question.id]: value }))} question={question} value={responses[question.id]} />
        {complete ? <div className="quiz-review">
          <p>你的答案：{answerText(question.response)}</p>
          <p>参考答案：{answerText(question.answer)}</p>
          <p>得分：{Math.round((question.earned ?? 0) * 100)}%</p>
          {question.grading_method === "model_hint" ? <p>简答题为模型评分提示，请核对原文。</p> : null}
          <p>{question.feedback}</p><p>{question.explanation}</p>
          <h3>核对来源</h3>
          {question.sources.map((source) => <p key={source.source_id}>
            {source.document_id ? <Link to={`/documents/${source.document_id}?unit=${source.unit}${source.version_id ? `&version=${source.version_id}` : ""}`}>{citationLabel(source.locator)} · 返回原文</Link> : citationLabel(source.locator)}
            <span className="learning-quote">{source.quote}</span>
          </p>)}
        </div> : null}
      </section>)}
      {attempt.data?.status === "in_progress" ? <button className="button button--primary" disabled={submit.isPending} onClick={() => submit.mutate()} type="button">
        {submit.isPending ? "正在保存并提交…" : "提交作答"}
      </button> : null}
  </div>;
}
