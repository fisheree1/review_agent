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

test("waiting study exposes original scope, actual count and the bound attempt", async () => {
  show(fixture);
  expect(await screen.findByText("原范围：讲义.pdf")).toBeVisible();
  expect(screen.getByText(/已生成 2 \/ 5 题/)).toBeVisible();
  expect(screen.getByText("作答记录 bound-attempt")).toBeVisible();
  expect(screen.getByText(/下方继续提问/)).toBeVisible();
  expect(screen.getByText("等待作答 · 当前步骤")).toHaveAttribute("aria-current", "step");
});

test("clarification resumes one run with its revision and stable request key", async () => {
  show({ ...fixture, stage: "clarify", plan: { action: "clarify" }, outputs: [], clarification: "请说明章节" });
  vi.mocked(learning.respondAgentRun).mockResolvedValue({ ...fixture, status: "queued" });
  const user = userEvent.setup();
  await user.type(await screen.findByRole("textbox", { name: "请说明章节" }), "第三章");
  await user.click(screen.getByRole("button", { name: "补充并继续" }));
  await waitFor(() => expect(learning.respondAgentRun).toHaveBeenCalledWith("run", "第三章", 3, expect.any(String)));
});

test("cancel failure remains visible and completed outputs remain available", async () => {
  show(fixture);
  vi.mocked(learning.cancelAgentRun).mockRejectedValue(new Error("连接中断"));
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "停止后续步骤" }));
  expect(await screen.findByText("连接中断")).toBeVisible();
  expect(screen.getByText("作答记录 bound-attempt")).toBeVisible();
});
