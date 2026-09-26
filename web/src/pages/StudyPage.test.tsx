import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";

import * as learning from "../api/learning";
import { StudyPage } from "./StudyPage";

vi.mock("../api/learning", async (original) => ({
  ...(await original<typeof learning>()), listCollections: vi.fn(), listConversations: vi.fn(),
  getConversation: vi.fn(), askConversation: vi.fn(), cancelConversationMessage: vi.fn(),
}));
vi.mock("../hooks/useDocuments", () => ({ useDocuments: () => ({ documents: [], nextCursor: null }) }));
vi.mock("../components/AppHeader", () => ({ AppHeader: () => <header>Review Agent</header> }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

function show(messages: learning.ConversationMessage[] = []) {
  const conversation = { id: "conversation", title: "统计复习", created_at: "2026-09-26T00:00:00Z", scope: [] };
  vi.mocked(learning.listCollections).mockResolvedValue([]);
  vi.mocked(learning.listConversations).mockResolvedValue([conversation]);
  vi.mocked(learning.getConversation).mockResolvedValue({ ...conversation, messages });
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
    <MemoryRouter initialEntries={["/study/conversation"]}><Routes><Route path="/study/:conversationId" element={<StudyPage />} /></Routes></MemoryRouter>
  </QueryClientProvider>);
}

test("task examples only draft a request and keyboard submission creates one task", async () => {
  const user = userEvent.setup();
  vi.mocked(learning.askConversation).mockResolvedValue({ id: "message", question: "task", status: "queued", scope: [], answer: null, failure_message: null, feedback: null });
  show();
  const composer = await screen.findByRole("textbox", { name: "发送任务或问题" });
  await user.click(screen.getByRole("button", { name: "生成练习" }));
  expect(composer).toHaveFocus();
  expect(composer).toHaveValue("根据所选资料生成 5 道中等难度单选题。");
  expect(learning.askConversation).not.toHaveBeenCalled();
  await user.keyboard("{Control>}{Enter}{/Control}");
  await waitFor(() => expect(learning.askConversation).toHaveBeenCalledWith("conversation", "根据所选资料生成 5 道中等难度单选题。", expect.any(String)));
  expect(composer).toHaveValue("");
});

test("running task has a stop control and prevents overlapping submissions", async () => {
  const user = userEvent.setup();
  const message: learning.ConversationMessage = { id: "active", question: "生成练习", status: "processing", scope: [], answer: null, failure_message: null, feedback: null };
  vi.mocked(learning.cancelConversationMessage).mockResolvedValue({ ...message, status: "cancelled" });
  show([message]);
  await user.type(await screen.findByRole("textbox", { name: "发送任务或问题" }), "下一项任务");
  expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "停止任务" }));
  await waitFor(() => expect(learning.cancelConversationMessage).toHaveBeenCalledWith("conversation", "active"));
});

test("task clarification is readable without showing answer feedback or creating artifacts", async () => {
  show([{ id: "clarify", question: "做个东西", status: "answered", scope: [], answer: null, failure_message: null, feedback: null,
    task_result: { kind: "clarification", text: "请说明题量和复习目标。", quiz_id: null, attempt_id: null, title: null },
  }]);
  expect(await screen.findByText("请说明题量和复习目标。")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "有帮助" })).not.toBeInTheDocument();
  expect(learning.askConversation).not.toHaveBeenCalled();
});
