import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";

import * as learning from "../api/learning";
import { QuizPage } from "./QuizPage";

vi.mock("../api/learning", async (original) => ({
  ...(await original<typeof learning>()), listCollections: vi.fn(), listQuizzes: vi.fn(), createQuiz: vi.fn(),
}));
vi.mock("../hooks/useDocuments", () => ({ useDocuments: () => ({ documents: [{ id: "document", filename: "stats.pdf", status: "ready" }], nextCursor: null }) }));
vi.mock("../components/AppHeader", () => ({ AppHeader: () => <header>Review Agent</header> }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

test("quiz keeps standard mode by default and submits the keyboard-selected experimental Agent", async () => {
  vi.mocked(learning.listCollections).mockResolvedValue([]);
  vi.mocked(learning.listQuizzes).mockResolvedValue([]);
  vi.mocked(learning.createQuiz).mockRejectedValue(new Error("暂时无法生成，请重试"));
  const user = userEvent.setup();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
    <MemoryRouter><QuizPage /></MemoryRouter>
  </QueryClientProvider>);
  await user.click(screen.getByText("更多设置"));
  const mode = screen.getByRole("button", { name: /出题方式/ });
  const language = screen.getByRole("button", { name: /语言/ });
  expect(mode).toHaveTextContent("标准出题");
  await user.type(screen.getByRole("textbox", { name: "标题" }), "统计复习");
  await user.click(screen.getByRole("checkbox", { name: "stats.pdf" }));
  mode.focus();
  expect(mode).toHaveFocus();
  await user.keyboard("{ArrowDown}{ArrowDown}{Enter}");
  language.focus();
  await user.keyboard("{ArrowDown}{End}{Enter}");
  await user.click(screen.getByRole("button", { name: "生成 5 题" }));
  await waitFor(() => expect(learning.createQuiz).toHaveBeenCalledWith(
    "统计复习", expect.objectContaining({ generation_mode: "agent", language: "zh-en" }),
    { document_ids: ["document"], collection_ids: [] }, expect.any(String),
  ));
  expect(await screen.findByText("暂时无法生成，请重试")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "生成 5 题" })).toBeEnabled();
  expect(mode).toHaveTextContent("自主规划出题（实验）");
  expect(language).toHaveTextContent("中英对照");
});

test("quiz requires a source and a valid question count before generation", async () => {
  vi.mocked(learning.listCollections).mockResolvedValue([]);
  vi.mocked(learning.listQuizzes).mockResolvedValue([]);
  const user = userEvent.setup();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><QuizPage /></MemoryRouter>
  </QueryClientProvider>);
  await user.type(screen.getByRole("textbox", { name: "标题" }), "统计复习");
  expect(screen.getByRole("button", { name: "生成 5 题" })).toBeDisabled();
  await user.click(screen.getByRole("checkbox", { name: "stats.pdf" }));
  expect(screen.getByRole("button", { name: "生成 5 题" })).toBeEnabled();
  const counts = screen.getAllByRole("spinbutton");
  for (const count of counts) { await user.clear(count); await user.type(count, "0"); }
  expect(screen.getByRole("button", { name: "生成 0 题" })).toBeDisabled();
  expect(learning.createQuiz).not.toHaveBeenCalled();
});
