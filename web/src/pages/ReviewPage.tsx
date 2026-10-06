import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { getReviewQueue, rateReview, revealReviewAnswer, type ReviewCard, type ReviewRating, type ReviewResult } from "../api/review";
import { AppHeader } from "../components/AppHeader";
import { ErrorState } from "../components/ErrorState";
import { PageHeading } from "../components/PageHeading";
import { SourcePreview } from "../components/SourcePreview";

const dateTime = (value: string) => new Date(value).toLocaleString(undefined, { month: "long", day: "numeric", hour: "2-digit", minute: "2-digit" });

function ReviewPractice({ card, onRated, onRefresh }: { card: ReviewCard; onRated: (result: ReviewResult) => void; onRefresh: () => void }) {
  const [revealed, setRevealed] = useState(false);
  const title = useRef<HTMLHeadingElement>(null);
  // Keep the same request identity on a retry after an uncertain network response.
  const pending = useRef<{ rating: ReviewRating; key: string } | null>(null);
  const answer = useQuery({ queryKey: ["review-answer", card.id], queryFn: () => revealReviewAnswer(card.id), enabled: revealed });
  const rating = useMutation({
    mutationFn: (value: ReviewRating) => {
      if (!pending.current) pending.current = { rating: value, key: crypto.randomUUID() };
      return rateReview(card, pending.current.rating, pending.current.key);
    },
    onSuccess: onRated,
  });
  useEffect(() => { title.current?.focus(); }, []);
  const content = answer.data;
  const answerText = Array.isArray(content?.answer) ? content.answer.join("、") : typeof content?.answer === "boolean" ? content.answer ? "正确" : "错误" : content?.answer;
  return <section className="learning-card review-practice" aria-label="回忆练习">
    <span className="eyebrow">{card.question.topic} · 已复习 {card.review_count} 次</span>
    <h2 ref={title} tabIndex={-1}>{card.question.stem}</h2>
    {card.question.options.length ? <ul>{card.question.options.map((option) => <li key={option}>{option}</li>)}</ul> : null}
    <p>先尝试回忆答案，再查看解析，按本次记忆情况安排下次复习。</p>
    {!revealed ? <button className="button button--primary" onClick={() => setRevealed(true)} type="button">查看答案</button> : null}
    {revealed && answer.isPending ? <p role="status">正在读取答案…</p> : null}
    {answer.error ? <ErrorState message={answer.error.message} onRetry={() => void answer.refetch()} /> : null}
    {revealed && content ? <div className="quiz-review">
      <h3>参考答案</h3><p>{answerText}</p><p>{content.explanation}</p>
      {content.sources.length ? <details><summary>查看来源</summary>{content.sources.map((source) => <SourcePreview key={source.source_id} source={source} />)}</details> : null}
      <fieldset className="review-ratings" disabled={rating.isPending || rating.isError}>
        <legend>这次记得怎么样？</legend>
        <button className="button button--secondary" type="button" onClick={() => rating.mutate("again")}>忘记</button>
        <button className="button button--secondary" type="button" onClick={() => rating.mutate("hard")}>模糊</button>
        <button className="button button--primary" type="button" onClick={() => rating.mutate("good")}>记得</button>
      </fieldset>
      <p>根据你的自评安排间隔；连续记得会逐步延长，忘记后从 1 天开始。</p>
    </div> : null}
    {rating.isPending ? <p role="status">正在保存复习记录…</p> : null}
    {rating.error ? <><ErrorState message={rating.error.message} onRetry={() => { if (pending.current) rating.mutate(pending.current.rating); }} />
      <button className="button button--secondary" type="button" onClick={onRefresh}>刷新复习队列</button></> : null}
  </section>;
}

export function ReviewPage() {
  const cache = useQueryClient();
  const queue = useQuery({ queryKey: ["review-queue"], queryFn: getReviewQueue, refetchInterval: 60_000 });
  const [notice, setNotice] = useState("");
  const [refresh, setRefresh] = useState(0);
  const card = queue.data?.items[0];
  const rated = (result: ReviewResult) => {
    setNotice(`已保存，下次复习：${dateTime(result.due_at)}（${result.interval_days} 天后）`);
    // Advance immediately, then obtain authoritative counts and the next batch.
    cache.setQueryData<Awaited<ReturnType<typeof getReviewQueue>>>(["review-queue"], (current) => current ? {
      ...current, due_count: Math.max(0, current.due_count - 1), upcoming_count: current.upcoming_count + 1,
      items: current.items.filter((item) => item.id !== result.id),
    } : current);
    void cache.invalidateQueries({ queryKey: ["review-queue"] });
  };
  return <div className="app-page"><AppHeader /><main className="learning-page review-page" id="main-content" tabIndex={-1}>
    <PageHeading title="长期复习" kicker="循序复习，日有所获" description="完成练习后，将错题加入这里，按间隔持续回顾。" />
    {notice ? <p role="status">{notice}</p> : null}
    {queue.isPending ? <p role="status">正在读取复习计划…</p> : null}
    {queue.error ? <ErrorState message={queue.error.message} onRetry={() => void queue.refetch()} /> : null}
    {queue.data ? <>
      <div className="review-totals" aria-label="复习概况"><p><strong>{queue.data.due_count}</strong> 待复习</p><p><strong>{queue.data.upcoming_count}</strong> 已安排后续复习</p></div>
      {card ? <ReviewPractice key={`${card.id}-${card.revision}-${refresh}`} card={card} onRated={rated} onRefresh={() => { void queue.refetch().then(() => setRefresh((value) => value + 1)); }} /> : <section className="learning-card">
        <h2>{queue.data.upcoming_count ? "本轮复习已完成" : "还没有待复习题目"}</h2>
        <p>{queue.data.upcoming_count ? "下次到期后，题目会再次出现在这里。" : "在练习结果中点击“将错题加入长期复习”，即可开始。"}</p><Link className="button button--secondary" to="/quizzes">前往练习</Link>
      </section>}
      {queue.data.upcoming.length ? <section className="learning-card"><h2>后续安排</h2><p>显示最近到期的 {queue.data.upcoming.length} 项。</p>
        <ul className="review-upcoming">{queue.data.upcoming.map((item) => <li key={item.id}><span>{item.topic}</span><time dateTime={item.due_at}>{dateTime(item.due_at)}</time></li>)}</ul>
      </section> : null}
    </> : null}
  </main></div>;
}
