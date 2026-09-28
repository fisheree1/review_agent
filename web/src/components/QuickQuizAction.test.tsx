import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";

import { createQuiz } from "../api/learning";
import { getIndex, startIndex } from "../api/rag";
import { QuickQuizAction } from "./QuickQuizAction";

vi.mock("../api/learning", async (original) => ({
  ...(await original<typeof import("../api/learning")>()), createQuiz: vi.fn(),
}));
vi.mock("../api/rag", async (original) => ({
  ...(await original<typeof import("../api/rag")>()), getIndex: vi.fn(), startIndex: vi.fn(),
}));

afterEach(() => { cleanup(); vi.clearAllMocks(); });

function show() {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
    <MemoryRouter initialEntries={["/documents/document"]}><Routes>
      <Route path="/documents/:documentId" element={<QuickQuizAction documentId="document" filename="统计学.pdf" />} />
      <Route path="/quizzes/:quizId" element={<p>练习已创建</p>} />
    </Routes></MemoryRouter>
  </QueryClientProvider>);
}

test("one click creates five scoped questions when the index is ready", async () => {
  vi.mocked(getIndex).mockResolvedValue({ status: "ready", completed: 1, total: 1, failure_message: null });
  vi.mocked(createQuiz).mockResolvedValue({ id: "quiz" } as Awaited<ReturnType<typeof createQuiz>>);
  const user = userEvent.setup(); show();

  await user.click(await screen.findByRole("button", { name: "用这份资料生成 5 题" }));
  await waitFor(() => expect(createQuiz).toHaveBeenCalledWith(
    "统计学 · 5 题练习",
    expect.objectContaining({ type_counts: { single: 5, multiple: 0, true_false: 0, short: 0 }, difficulty: "medium", language: "zh" }),
    { document_ids: ["document"], collection_ids: [] }, expect.any(String),
  ));
  expect(await screen.findByText("练习已创建")).toBeVisible();
  expect(createQuiz).toHaveBeenCalledTimes(1);
  expect(startIndex).not.toHaveBeenCalled();
});

test("one consented action prepares an unindexed PDF and generates after it is ready", async () => {
  vi.mocked(getIndex)
    .mockResolvedValueOnce({ status: "not_indexed", completed: 0, total: 0, failure_message: null })
    .mockResolvedValue({ status: "ready", completed: 1, total: 1, failure_message: null });
  vi.mocked(startIndex).mockResolvedValue({ status: "queued", completed: 0, total: 1, failure_message: null });
  vi.mocked(createQuiz).mockResolvedValue({ id: "quiz" } as Awaited<ReturnType<typeof createQuiz>>);
  const user = userEvent.setup(); show();

  expect(await screen.findByText(/正文会发送至阿里云百炼/)).toBeVisible();
  expect(startIndex).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "准备资料并生成 5 题" }));
  await waitFor(() => expect(startIndex).toHaveBeenCalledWith("document", expect.any(String)));
  expect(await screen.findByText("练习已创建")).toBeVisible();
  expect(createQuiz).toHaveBeenCalledTimes(1);
});

test("failed preparation keeps the action available for retry", async () => {
  vi.mocked(getIndex).mockResolvedValue({ status: "not_indexed", completed: 0, total: 0, failure_message: null });
  vi.mocked(startIndex).mockRejectedValue(new Error("服务暂时不可用"));
  const user = userEvent.setup(); show();

  await user.click(await screen.findByRole("button", { name: "准备资料并生成 5 题" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("服务暂时不可用");
  expect(screen.getByRole("button", { name: "准备资料并生成 5 题" })).toBeEnabled();
  expect(createQuiz).not.toHaveBeenCalled();
});

test("failed creation can retry with the same request key", async () => {
  vi.mocked(getIndex).mockResolvedValue({ status: "ready", completed: 1, total: 1, failure_message: null });
  vi.mocked(createQuiz)
    .mockRejectedValueOnce(new Error("暂时无法生成"))
    .mockResolvedValue({ id: "quiz" } as Awaited<ReturnType<typeof createQuiz>>);
  const user = userEvent.setup(); show();

  await user.click(await screen.findByRole("button", { name: "用这份资料生成 5 题" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法生成");
  await user.click(screen.getByRole("button", { name: "用这份资料生成 5 题" }));
  expect(await screen.findByText("练习已创建")).toBeVisible();
  expect(vi.mocked(createQuiz).mock.calls[0][3]).toBe(vi.mocked(createQuiz).mock.calls[1][3]);
});
