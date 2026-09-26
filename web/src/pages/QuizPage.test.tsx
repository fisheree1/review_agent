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
vi.mock("../hooks/useDocuments", () => ({ useDocuments: () => ({ documents: [], nextCursor: null }) }));
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
  const mode = screen.getByRole("combobox", { name: "出题方式" });
  expect(mode).toHaveValue("standard");
  await user.type(screen.getByRole("textbox", { name: "标题" }), "统计复习");
  mode.focus();
  expect(mode).toHaveFocus();
  await user.selectOptions(mode, "agent");
  await user.click(screen.getByRole("button", { name: "生成 5 题" }));
  await waitFor(() => expect(learning.createQuiz).toHaveBeenCalledWith(
    "统计复习", expect.objectContaining({ generation_mode: "agent" }),
    { document_ids: [], collection_ids: [] }, expect.any(String),
  ));
  expect(await screen.findByText("暂时无法生成，请重试")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "生成 5 题" })).toBeEnabled();
  expect(mode).toHaveValue("agent");
});
