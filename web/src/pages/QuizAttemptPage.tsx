import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { getQuiz } from "../api/learning";
import { AppHeader } from "../components/AppHeader";
import { ErrorState } from "../components/ErrorState";
import { QuizAttemptPanel } from "../components/QuizAttemptPanel";

export function QuizAttemptPage() {
  const { quizId = "", attemptId = "" } = useParams();
  const quiz = useQuery({ queryKey: ["quiz", quizId], queryFn: () => getQuiz(quizId) });
  return <div className="app-page">
    <AppHeader />
    <main className="learning-page learning-page--narrow" id="main-content">
      <Link to={`/quizzes/${quizId}`}>← 返回 Quiz</Link>
      <header className="learning-heading"><span className="eyebrow">作答与复习</span>
        <h1>{quiz.data?.title ?? "正在打开 Quiz…"}</h1>
        <p>资料范围：{quiz.data?.scope.map((item) => item.filename).join("、")}</p>
      </header>
      {quiz.error ? <ErrorState message={quiz.error.message} onRetry={() => void quiz.refetch()} /> : null}
      <QuizAttemptPanel quizId={quizId} attemptId={attemptId} />
    </main>
  </div>;
}
