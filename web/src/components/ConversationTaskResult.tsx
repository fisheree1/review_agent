import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { getQuiz, listAttempts, startAttempt, type ConversationMessage } from "../api/learning";
import { ErrorState } from "./ErrorState";
import { QuizAttemptPanel } from "./QuizAttemptPanel";

function ConversationQuiz({ quizId, messageId, onRequest }: {
  quizId: string; messageId: string; onRequest: (request: string) => void;
}) {
  const cache = useQueryClient();
  const quiz = useQuery({ queryKey: ["quiz", quizId], queryFn: () => getQuiz(quizId) });
  const attempts = useQuery({ queryKey: ["quiz", quizId, "attempts"], queryFn: () => listAttempts(quizId) });
  const [attemptId, setAttemptId] = useState<string | null>(null);
  useEffect(() => {
    if (!attemptId && attempts.data?.[0]) setAttemptId(attempts.data[0].id);
  }, [attemptId, attempts.data]);
  const start = useMutation({
    mutationFn: () => startAttempt(quizId, `conversation-practice:${messageId}`),
    onSuccess: (attempt) => {
      setAttemptId(attempt.id);
      void cache.invalidateQueries({ queryKey: ["quiz", quizId, "attempts"] });
    },
  });
  const error = quiz.error ?? attempts.error ?? start.error;
  if (error) return <ErrorState message={error.message} onRetry={() => { void quiz.refetch(); void attempts.refetch(); start.reset(); }} />;
  if (quiz.isPending || attempts.isPending) return <p role="status">正在打开练习…</p>;
  return <div className="conversation-quiz">
    <div className="learning-actions"><Link to={`/quizzes/${quizId}`}>在 Quiz 页面查看</Link></div>
    {attemptId ? <QuizAttemptPanel key={attemptId} quizId={quizId} attemptId={attemptId}
      onPractice={() => onRequest("根据我最近练习的薄弱知识点，再生成 5 道中等难度单选题。")}
    /> : <>
      <p>准备好后直接在这里作答，提交前不会显示答案和解析。</p>
      <button className="button button--primary" disabled={start.isPending || quiz.data?.status !== "ready"}
        onClick={() => start.mutate()} type="button">{start.isPending ? "正在打开…" : "在对话中作答"}</button>
    </>}
  </div>;
}

export function ConversationTaskResult({ message, onRequest }: {
  message: ConversationMessage; onRequest: (request: string) => void;
}) {
  const result = message.task_result;
  if (!result || message.status !== "answered") return null;
  return <section className="agent-task-result" aria-label={result.kind === "quiz" ? "练习任务结果" : result.kind === "review" ? "错题复习结果" : "任务说明"}>
    <span className="eyebrow">{result.kind === "quiz" ? "练习已准备" : result.kind === "review" ? "作答复习" : "需要补充"}</span>
    {result.title ? <h4>{result.title}</h4> : null}
    <p>{result.text}</p>
    {result.kind === "quiz" && result.quiz_id ? <ConversationQuiz quizId={result.quiz_id} messageId={message.id} onRequest={onRequest} /> : null}
    {result.kind === "review" && result.quiz_id && result.attempt_id ? <QuizAttemptPanel
      quizId={result.quiz_id} attemptId={result.attempt_id} weakOnlyInitially
      onPractice={() => onRequest("根据我最近练习的薄弱知识点，再生成 5 道中等难度单选题。")}
    /> : null}
  </section>;
}
