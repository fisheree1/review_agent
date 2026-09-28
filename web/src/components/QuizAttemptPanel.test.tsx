import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";

import { getAttempt, type AttemptDetail } from "../api/learning";
import { QuizAttemptPanel } from "./QuizAttemptPanel";

vi.mock("../api/learning", async (original) => ({
  ...(await original<typeof import("../api/learning")>()), getAttempt: vi.fn(),
}));

afterEach(() => { cleanup(); vi.clearAllMocks(); });

test("a submitted bilingual judgment question shows both languages and readable answers", async () => {
  const detail: AttemptDetail = {
    id: "attempt", status: "submitted", score: 100, weak_topics: [], revision: 1,
    failure_code: null,
    questions: [{
      id: "question", ordinal: 1, kind: "true_false", difficulty: "medium",
      topic: "中文：统计\nEnglish: Statistics",
      stem: "中文：中位数抵抗极端值。\nEnglish: The median resists outliers.",
      options: [], answer: true, response: true, earned: 1, grading_method: "exact",
      feedback: "答对了", explanation: "中文：中位数稳定。\nEnglish: The median is robust.",
      sources: [],
    }],
  };
  vi.mocked(getAttempt).mockResolvedValue(detail);
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><QuizAttemptPanel quizId="quiz" attemptId="attempt" /></MemoryRouter>
  </QueryClientProvider>);

  expect(await screen.findByRole("heading", { name: /中位数抵抗极端值/ })).toHaveTextContent("The median resists outliers.");
  expect(screen.getByRole("radio", { name: "正确 / True" })).toBeInTheDocument();
  expect(screen.getByText("参考答案：正确 / True")).toBeVisible();
  expect(screen.getByText(/The median is robust/)).toBeVisible();
});
