import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";

import * as learning from "../api/learning";
import { QuizAttemptPanel } from "./QuizAttemptPanel";

vi.mock("../api/learning", async (original) => ({
  ...(await original<typeof learning>()), getAttempt: vi.fn(), saveQuizAnswer: vi.fn(), submitAttempt: vi.fn(),
}));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const attempt: learning.AttemptDetail = {
  id: "attempt", status: "in_progress", score: null, weak_topics: null, failure_code: null, revision: 4,
  questions: [{ id: "question", ordinal: 1, kind: "single", difficulty: "medium", topic: "统计", stem: "选择稳健的统计量", options: ["中位数", "均值"], answer: null, explanation: null, sources: [], response: null, earned: null, feedback: null, grading_method: null }],
};
function show() {
  vi.mocked(learning.getAttempt).mockResolvedValue(attempt);
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
    <MemoryRouter><QuizAttemptPanel quizId="quiz" attemptId="attempt" /></MemoryRouter>
  </QueryClientProvider>);
}

test("draft saves against server revision before submitting and never reveals answers early", async () => {
  show();
  vi.mocked(learning.saveQuizAnswer).mockResolvedValue({ question_id: "question", revision: 5 });
  vi.mocked(learning.submitAttempt).mockResolvedValue({ id: "attempt", status: "submitted", score: 100, weak_topics: [] });
  const user = userEvent.setup();
  expect(await screen.findByRole("progressbar", { name: "已作答题数" })).toHaveAttribute("value", "0");
  await user.click(await screen.findByRole("radio", { name: "中位数" }));
  expect(screen.getByRole("progressbar", { name: "已作答题数" })).toHaveAttribute("value", "1");
  await waitFor(() => expect(learning.saveQuizAnswer).toHaveBeenCalledWith("quiz", "attempt", "question", "中位数", 4));
  expect(await screen.findByText("作答已保存")).toBeVisible();
  expect(screen.queryByText(/参考答案/)).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "提交作答" }));
  await waitFor(() => expect(learning.submitAttempt).toHaveBeenCalledTimes(1));
});

test("failed autosave preserves checked answer and prevents submission", async () => {
  show();
  vi.mocked(learning.saveQuizAnswer).mockRejectedValue(new Error("作答已在其他页面更新"));
  const user = userEvent.setup();
  await user.click(await screen.findByRole("radio", { name: "均值" }));
  await user.click(screen.getByRole("button", { name: "提交作答" }));
  expect(await screen.findByText(/本页草稿仍保留/)).toBeVisible();
  expect(screen.getByRole("radio", { name: "均值" })).toBeChecked();
  expect(learning.submitAttempt).not.toHaveBeenCalled();
});


test("revision conflict can explicitly discard unsaved edits and reload server answers", async () => {
  show();
  vi.mocked(learning.saveQuizAnswer).mockRejectedValue(new Error("作答已在其他页面更新"));
  const user = userEvent.setup();
  await user.click(await screen.findByRole("radio", { name: "均值" }));
  await screen.findByText(/本页草稿仍保留/);
  vi.mocked(learning.getAttempt).mockResolvedValue({ ...attempt, revision: 5,
    questions: [{ ...attempt.questions[0], response: "中位数" }] });
  await user.click(screen.getByRole("button", { name: "放弃本页未保存修改，加载已保存作答" }));
  await waitFor(() => expect(screen.getByRole("radio", { name: "中位数" })).toBeChecked());
  expect(screen.queryByText(/本页草稿仍保留/)).not.toBeInTheDocument();
  vi.mocked(learning.saveQuizAnswer).mockResolvedValue({ question_id: "question", revision: 6 });
  await user.click(screen.getByRole("radio", { name: "均值" }));
  await waitFor(() => expect(learning.saveQuizAnswer).toHaveBeenLastCalledWith("quiz", "attempt", "question", "均值", 5));
});

test("question navigation treats false as answered and preserves keyboard focus while answering filtered questions", async () => {
  vi.mocked(learning.getAttempt).mockResolvedValue({ ...attempt, questions: [
    { ...attempt.questions[0], kind: "true_false", stem: "判断统计量定义" },
    { ...attempt.questions[0], id: "second", ordinal: 2 },
  ] });
  vi.mocked(learning.saveQuizAnswer).mockResolvedValue({ question_id: "question", revision: 5 });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><QuizAttemptPanel quizId="quiz" attemptId="attempt" /></MemoryRouter>
  </QueryClientProvider>);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("radio", { name: "错误" }));
  expect(screen.getByRole("button", { name: "第 1 题 · 已答" })).toBeVisible();
  await user.click(screen.getByRole("button", { name: "只看未答题" }));
  expect(screen.queryByText("判断统计量定义")).not.toBeInTheDocument();
  const answer = screen.getByRole("radio", { name: "中位数" });
  await user.click(answer);
  expect(answer).toHaveFocus();
  expect(answer).toBeChecked();
  await user.click(screen.getByRole("button", { name: "第 1 题 · 已答" }));
  expect(await screen.findByText("判断统计量定义")).toBeVisible();
  expect(screen.getByText("判断统计量定义").closest("section")).toHaveFocus();
});
