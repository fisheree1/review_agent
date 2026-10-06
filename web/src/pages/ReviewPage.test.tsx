import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";

import * as review from "../api/review";
import { ReviewPage } from "./ReviewPage";

vi.mock("../api/review", () => ({ getReviewQueue: vi.fn(), revealReviewAnswer: vi.fn(), rateReview: vi.fn() }));
vi.mock("../components/AppHeader", () => ({ AppHeader: () => <header>Review Agent</header> }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const question = { id: "question", ordinal: 1, kind: "single" as const, difficulty: "easy", topic: "中位数", stem: "哪个统计量抵抗极端值？", options: ["均值", "中位数"], answer: null, explanation: null, sources: [] };
const card = { id: "card", due_at: "2026-09-30T00:00:00Z", revision: 0, review_count: 0, question };
const empty = { due_count: 0, upcoming_count: 0, items: [], upcoming: [] };
function show() {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}><MemoryRouter><ReviewPage /></MemoryRouter></QueryClientProvider>);
}

test("recall hides answers until keyboard reveal and retry reuses the rating identity", async () => {
  const user = userEvent.setup();
  vi.mocked(review.getReviewQueue).mockResolvedValue({ ...empty, due_count: 1, items: [card] });
  vi.mocked(review.revealReviewAnswer).mockResolvedValue({ ...question, answer: "中位数", explanation: "排序后取中间位置。" });
  vi.mocked(review.rateReview).mockRejectedValueOnce(new Error("连接中断"));
  show();
  const reveal = await screen.findByRole("button", { name: "查看答案" });
  expect(review.revealReviewAnswer).not.toHaveBeenCalled();
  expect(screen.queryByText("排序后取中间位置。")).not.toBeInTheDocument();
  reveal.focus(); await user.keyboard("{Enter}");
  expect(await screen.findByText("排序后取中间位置。")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "记得" }));
  expect(await screen.findByText("连接中断")).toBeVisible();
  expect(screen.getByRole("button", { name: "忘记" })).toBeDisabled();
  const first = vi.mocked(review.rateReview).mock.calls[0];
  vi.mocked(review.rateReview).mockResolvedValue({ id: "card", due_at: "2026-10-01T00:00:00Z", interval_days: 1, revision: 1 });
  vi.mocked(review.getReviewQueue).mockResolvedValue({ ...empty, upcoming_count: 1 });
  await user.click(screen.getByRole("button", { name: "重新尝试" }));
  await waitFor(() => expect(review.rateReview).toHaveBeenLastCalledWith(...first));
  expect(await screen.findByRole("heading", { name: "本轮复习已完成" })).toBeVisible();
  expect(screen.getByText(/已保存，下次复习/)).toBeVisible();
});

test("queue renders loading then recoverable error and empty guidance", async () => {
  let reject!: (reason: Error) => void;
  vi.mocked(review.getReviewQueue).mockReturnValue(new Promise((_, fail) => { reject = fail; }));
  show();
  expect(screen.getByText("正在读取复习计划…")).toBeVisible();
  reject(new Error("暂时无法读取"));
  expect(await screen.findByText("暂时无法读取")).toBeVisible();
  vi.mocked(review.getReviewQueue).mockResolvedValue(empty);
  await userEvent.click(screen.getByRole("button", { name: "重新尝试" }));
  expect(await screen.findByRole("heading", { name: "还没有待复习题目" })).toBeVisible();
  expect(screen.getByRole("link", { name: "前往练习" })).toHaveAttribute("href", "/quizzes");
});
