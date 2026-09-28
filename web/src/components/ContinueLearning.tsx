import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { getLearningResume } from "../api/learning";

export function ContinueLearning() {
  const resume = useQuery({ queryKey: ["learning-resume"], queryFn: getLearningResume });
  const recent = resume.data?.conversation;
  const unfinished = resume.data?.attempt;
  if (!recent && !unfinished && !resume.error) return null;
  return <section className="continue-learning" aria-labelledby="continue-title">
    <h2 id="continue-title">继续学习</h2>
    {recent ? <Link to={`/study/${recent.id}`}>{recent.working ? "继续学习任务" : "最近对话"} · {recent.title}</Link> : null}
    {unfinished ? <Link to={`/quizzes/${unfinished.quiz_id}/attempts/${unfinished.id}`}>
      {unfinished.status === "grading" ? "查看评分进度" : "继续作答"} · {unfinished.title}
    </Link> : null}
    {resume.error ? <span role="status">暂时无法读取进度。<button className="button button--secondary" type="button" onClick={() => void resume.refetch()}>重试</button></span> : null}
  </section>;
}
