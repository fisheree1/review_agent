import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { cancelAgentRun, getAgentRun, respondAgentRun } from "../api/learning";
import { ErrorState } from "./ErrorState";
import { QuizAttemptPanel } from "./QuizAttemptPanel";

const stageNames: Record<string, string> = {
  plan: "正在规划任务", replan: "正在处理补充信息", summary: "正在总结资料",
  quiz: "正在生成练习", wait: "等待你作答", grade: "已提交，正在评分",
  review: "正在整理错题解析", weak: "正在生成薄弱点练习", done: "已完成",
  clarify: "需要补充信息",
};
const terminal = new Set(["completed", "failed", "cancelled", "expired"]);

export function AgentRunCard({ runId, conversationId }: { runId: string; conversationId: string }) {
  const cache = useQueryClient();
  const [answer, setAnswer] = useState("");
  const responseKey = useRef<{ answer: string; key: string } | null>(null);
  const run = useQuery({
    queryKey: ["agent-run", runId], queryFn: () => getAgentRun(runId),
    refetchInterval: (query) => {
      const value = query.state.data;
      if (value && (terminal.has(value.status) || value.status === "blocked")) return false;
      return value?.status === "waiting_input" ? 5000 : 1500;
    },
  });
  useEffect(() => {
    if (run.data) void cache.invalidateQueries({ queryKey: ["conversation", conversationId] });
  }, [cache, conversationId, run.data?.revision]);
  const refresh = () => {
    void cache.invalidateQueries({ queryKey: ["agent-run", runId] });
    void cache.invalidateQueries({ queryKey: ["conversation", conversationId] });
  };
  const cancel = useMutation({ mutationFn: () => cancelAgentRun(runId), onSuccess: refresh });
  const respond = useMutation({
    mutationFn: () => {
      if (responseKey.current?.answer !== answer.trim()) responseKey.current = { answer: answer.trim(), key: crypto.randomUUID() };
      return respondAgentRun(runId, answer.trim(), run.data!.revision, responseKey.current.key);
    },
    onSuccess: () => { setAnswer(""); responseKey.current = null; refresh(); },
  });
  const value = run.data;
  const error = run.error ?? cancel.error ?? respond.error;
  if (!value) return error ? <ErrorState message={error.message} onRetry={() => void run.refetch()} /> : <p role="status">正在读取任务进度…</p>;
  const study = value.plan?.action === "study";
  const quizzes = value.outputs.filter((item) => item.kind === "quiz");
  const reviews = value.outputs.filter((item) => item.kind === "review");
  const labels = ["章节总结", "练习"];
  if (value.plan?.review_after_submit) labels.push("等待作答", "错题复习");
  if (value.plan?.practice_after_review) labels.push("薄弱点练习");
  const status = value.status === "cancelled" ? "已停止后续步骤" : value.status === "expired" ? "等待已过期，请新建任务"
    : value.status === "failed" || value.status === "blocked" ? value.failure_message ?? "任务暂时无法继续，已完成内容仍可查看"
      : value.status === "completed" ? "已完成" : stageNames[value.stage] ?? "任务正在继续";
  const stepState = (label: string) => {
    const done = label === "章节总结" ? value.outputs.some((item) => item.kind === "summary")
      : label === "练习" ? quizzes.length > 0
        : label === "等待作答" ? reviews.length > 0 || value.stage === "grade"
          : label === "错题复习" ? reviews.length > 0 : quizzes.length > 1;
    if (done) return "完成";
    if (label === "薄弱点练习" && value.outputs.some((item) => item.kind === "notice")) return "跳过";
    const active = { "章节总结": "summary", "练习": "quiz", "等待作答": "wait", "错题复习": "review", "薄弱点练习": "weak" }[label];
    return value.stage === active && !terminal.has(value.status) ? "当前步骤" : "待进行";
  };

  return <section className="agent-task-result agent-run-card" aria-label="学习任务进度">
    <span className="eyebrow">{study ? "完整学习任务" : "任务进度"}</span>
    <p role="status"><span className="agent-stage-label" key={`${value.stage}-${value.status}`}>{status}</span></p>
    {study ? <><p className="learning-scope">原范围：{value.scope.map((item) => item.filename).join("、")}</p>
      <ol className="agent-stages">{labels.map((label) => <li aria-current={stepState(label) === "当前步骤" ? "step" : undefined} key={label}>{label} · {stepState(label)}</li>)}</ol></> : null}
    {error ? <ErrorState message={error.message} onRetry={() => { void run.refetch(); cancel.reset(); respond.reset(); }} /> : null}
    {value.status === "waiting_input" && value.stage === "clarify" ? <form onSubmit={(event) => { event.preventDefault(); if (answer.trim().length >= 2) respond.mutate(); }}>
      <label htmlFor={`run-input-${runId}`}>{value.clarification}</label>
      <textarea id={`run-input-${runId}`} maxLength={1200} value={answer} onChange={(event) => setAnswer(event.target.value)} />
      <button className="button button--primary" disabled={respond.isPending || answer.trim().length < 2} type="submit">补充并继续</button>
    </form> : null}
    {study ? value.outputs.map((output, index) => output.kind === "quiz" ? <details key={output.quiz_id} open={!reviews.some((review) => review.quiz_id === output.quiz_id)}>
      <summary>{output.title ?? "练习"} · {output.text}</summary>
      {output.quiz_id && output.attempt_id ? <QuizAttemptPanel key={output.attempt_id} quizId={output.quiz_id} attemptId={output.attempt_id} /> : null}
    </details> : output.kind === "review" ? <section key={output.attempt_id} aria-label="任务错题复习"><p>{output.text}</p>
      {output.quiz_id && output.attempt_id ? <QuizAttemptPanel key={output.attempt_id} quizId={output.quiz_id} attemptId={output.attempt_id} weakOnlyInitially /> : null}
    </section> : output.kind === "notice" ? <p key={index}>{output.text}</p> : null) : null}
    {value.status === "waiting_input" && value.stage === "wait" ? <p>完成并提交后自动继续。你可以离开页面，也可以在下方继续提问。</p> : null}
    {!terminal.has(value.status) ? <button className="button button--secondary" disabled={cancel.isPending} onClick={() => cancel.mutate()} type="button">停止后续步骤</button> : null}
  </section>;
}
