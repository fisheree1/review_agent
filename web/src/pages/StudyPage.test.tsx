import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";

import * as learning from "../api/learning";
import { StudyPage } from "./StudyPage";

vi.mock("../api/learning", async (original) => ({
  ...(await original<typeof learning>()), listCollections: vi.fn(), listConversations: vi.fn(),
  listConversationGroups: vi.fn(), getConversation: vi.fn(), askConversation: vi.fn(), cancelConversationMessage: vi.fn(),
}));
vi.mock("../hooks/useDocuments", () => ({ useDocuments: () => ({ documents: [], nextCursor: null }) }));
vi.mock("../components/AppHeader", () => ({ AppHeader: () => <header>Review Agent</header> }));
vi.mock("../components/StudySourcePanel", () => ({ StudySourcePanel: ({ source, onClose }: { source: { unit: number }; onClose: () => void }) =>
  <aside aria-label="原文预览"><span>PDF 第 {source.unit} 页</span><button onClick={onClose} type="button">关闭原文预览</button></aside> }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

function show(messages: learning.ConversationMessage[] = [], scoped = true) {
  const conversation = { id: "conversation", title: "统计复习", created_at: "2026-09-26T00:00:00Z", scope: scoped ? [{ document_id: "document", version_id: 1, filename: "stats.pdf" }] : [] };
  vi.mocked(learning.listConversationGroups).mockResolvedValue([]);
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


test("empty conversation keeps draft but refuses sending until a source is selected", async () => {
  const user = userEvent.setup(); show([], false);
  await user.type(await screen.findByRole("textbox", { name: "发送任务或问题" }), "解释统计学");
  await user.keyboard("{Control>}{Enter}{/Control}");
  expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
  expect(learning.askConversation).not.toHaveBeenCalled();
  expect(screen.getByRole("link", { name: "前往资料库上传 PDF" })).toHaveAttribute("href", "/#upload-materials");
});

test("a first-time visitor without materials is sent to upload before creating a conversation", async () => {
  vi.mocked(learning.listConversationGroups).mockResolvedValue([]);
  vi.mocked(learning.listCollections).mockResolvedValue([]);
  vi.mocked(learning.listConversations).mockResolvedValue([]);
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter initialEntries={["/study"]}><Routes><Route path="/study" element={<StudyPage />} /></Routes></MemoryRouter>
  </QueryClientProvider>);

  expect(await screen.findByRole("heading", { name: "先上传一份 PDF" })).toBeVisible();
  expect(screen.getByRole("link", { name: "前往资料库上传 PDF" })).toHaveAttribute("href", "/#upload-materials");
  expect(screen.queryByRole("button", { name: "开始新对话" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "新建对话" })).not.toBeInTheDocument();
});

test("groups repeated citations after the answer and previews the PDF in a side panel", async () => {
  const citation = { source_id: "source", document_id: "document", version_id: 1, unit: 3, quote: "A cited passage",
    locator: { kind: "page" as const, position: 3, title: null, path: [] } };
  show([{ id: "answer", question: "关键概念是什么？", status: "answered",
    scope: [{ document_id: "document", version_id: 1, filename: "stats.pdf" }],
    answer: { insufficient_evidence: false, claims: [
      { text: "第一个结论。", citations: [citation] }, { text: "第二个结论。", citations: [citation] },
    ] }, failure_message: null, feedback: null }]);

  expect(await screen.findByText("第二个结论。")).toBeVisible();
  expect(screen.queryByRole("link", { name: /第 3 页/ })).not.toBeInTheDocument();
  await userEvent.click(screen.getByText("来源 · 1"));
  await userEvent.click(screen.getByRole("button", { name: "stats.pdf · 第 3 页" }));
  expect(screen.getByLabelText("原文预览")).toHaveTextContent("PDF 第 3 页");
  await userEvent.click(screen.getByRole("button", { name: "关闭原文预览" }));
  expect(screen.queryByLabelText("原文预览")).not.toBeInTheDocument();
});
