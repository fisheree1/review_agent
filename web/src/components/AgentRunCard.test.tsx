import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import * as learning from "../api/learning";
import { AgentRunCard } from "./AgentRunCard";

vi.mock("../api/learning", async (original) => ({
  ...(await original<typeof learning>()), getAgentRun: vi.fn(), cancelAgentRun: vi.fn(), respondAgentRun: vi.fn(),
}));
vi.mock("./QuizAttemptPanel", () => ({ QuizAttemptPanel: ({ attemptId }: { attemptId: string }) => <p>作答记录 {attemptId}</p> }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const fixture: learning.AgentRun = {
  id: "run", status: "waiting_input", stage: "wait", revision: 3,
  scope: [{ document_id: "doc", version_id: 1, filename: "讲义.pdf" }],
  plan: { action: "study", review_after_submit: true, practice_after_review: true },
  outputs: [{ kind: "summary", text: "总结" }, { kind: "quiz", quiz_id: "quiz", attempt_id: "bound-attempt", title: "练习", text: "已生成 2 / 5 题" }],
  clarification: null, failure_message: null, expires_at: "2026-10-01T00:00:00Z",
};
function show(run: learning.AgentRun) {
  vi.mocked(learning.getAgentRun).mockResolvedValue(run);
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
    <AgentRunCard runId="run" conversationId="conversation" />
  </QueryClientProvider>);
}

test("waiting study keeps the bound attempt visible and lets the user stop later steps", async () => {
  show(fixture);
  expect(await screen.findByText("等待你作答")).toBeVisible();
  expect(screen.getByText(/已生成 2 \/ 5 题/)).toBeVisible();
  expect(screen.getByText("作答记录 bound-attempt")).toBeVisible();
  expect(screen.queryByText("原范围：讲义.pdf")).not.toBeInTheDocument();
  vi.mocked(learning.cancelAgentRun).mockResolvedValue({ ...fixture, status: "cancelled" });
  await userEvent.click(screen.getByRole("button", { name: "停止后续步骤" }));
  await waitFor(() => expect(learning.cancelAgentRun).toHaveBeenCalledWith("run"));
});

test("clarification resumes one run with its revision and stable request key", async () => {
  show({ ...fixture, stage: "clarify", plan: { action: "clarify" }, outputs: [], clarification: "请说明章节" });
  vi.mocked(learning.respondAgentRun).mockResolvedValue({ ...fixture, status: "queued" });
  const user = userEvent.setup();
  await user.type(await screen.findByRole("textbox", { name: "请说明章节" }), "第三章");
  await user.click(screen.getByRole("button", { name: "补充并继续" }));
  await waitFor(() => expect(learning.respondAgentRun).toHaveBeenCalledWith("run", "第三章", 3, expect.any(String)));
  expect(screen.getByRole("button", { name: "停止后续步骤" })).toBeVisible();
});

test("a failed stop request keeps the waiting task and retry control visible", async () => {
  show(fixture);
  vi.mocked(learning.cancelAgentRun).mockRejectedValue(new Error("连接中断"));
  await userEvent.click(await screen.findByRole("button", { name: "停止后续步骤" }));
  expect(await screen.findByText("连接中断")).toBeVisible();
  expect(screen.getByRole("button", { name: "停止后续步骤" })).toBeEnabled();
  expect(screen.getByText("作答记录 bound-attempt")).toBeVisible();
});

test("a completed run retains its PDF download and quiz without a stop button", async () => {
  show({ ...fixture, status: "completed", stage: "done", outputs: [...fixture.outputs,
    { kind: "pdf", text: "知识点 PDF 已准备好" }] });
  expect(await screen.findByRole("link", { name: "下载知识点 PDF" })).toHaveAttribute("href", "/api/v1/agent-runs/run/notes.pdf");
  expect(screen.getByText("作答记录 bound-attempt")).toBeVisible();
  expect(screen.queryByRole("button", { name: "停止后续步骤" })).not.toBeInTheDocument();
});

test("long PDF progress distinguishes pages seen from complete text and survives a limit", async () => {
  show({ ...fixture, status: "completed", stage: "done", outputs: [{ kind: "summary", text: "整理完成", coverage: {
    sampled_pages: 32, indexed_pages: 300, full_pages: 20, processed_chunks: 32, indexed_chunks: 800,
    completed_batches: 4, total_batches: 8, budget_limited: 1, knowledge_points: 19, cited_pages: 15,
  } }] });
  expect(await screen.findByText("已阅读 32 / 300 页，32 / 800 个正文片段")).toBeVisible();
  expect(screen.getByText(/其中 20 页的索引正文已完整读取/)).toBeVisible();
  expect(screen.getByText(/本次整理已达到处理上限/)).toBeVisible();
  expect(screen.getByText(/尚未读完全部正文/)).toBeVisible();
});

test("an overview batch shows recoverable progress while still working", async () => {
  show({ ...fixture, status: "queued", stage: "overview_1", outputs: [{ kind: "summary", text: "正在分批整理资料", coverage: {
    sampled_pages: 8, indexed_pages: 80, full_pages: 8, processed_chunks: 8, indexed_chunks: 80,
    completed_batches: 1, total_batches: 8, knowledge_points: 6,
  } }] });
  expect(await screen.findByText("正在分批整理资料")).toBeVisible();
  expect(screen.getByText("已完成 1 / 8 批")).toBeVisible();
});
