import { expect, test } from "@playwright/test";

test("long PDF overview retains validated partial notes and reports accurate coverage after refresh", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  let complete = false;
  const doc = "11111111-1111-4111-8111-111111111111";
  const conversation = "33333333-3333-4333-8333-333333333333";
  const scope = [{ document_id: doc, version_id: 1, filename: "长篇讲义.pdf" }];
  const detail = { id: conversation, title: "知识点整理", scope, created_at: "2026-10-04T00:00:00Z" };
  const claim = { text: "首批已整理的知识点", citations: [{ source_id: "source", document_id: doc, version_id: 1, unit: 1, locator: { kind: "page", position: 1, title: null, path: [] }, quote: "可核对的讲义原文片段。" }] };
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: { user_id: "user", workspace_id: "workspace", email: "learner@example.test", csrf_token: "test" } }));
  await page.route("**/api/v1/documents**", (route) => route.fulfill({ json: { items: [{ id: doc, filename: "长篇讲义.pdf", media_type: "application/pdf", status: "ready", page_count: 300, content_count: 300, byte_size: 1000, created_at: detail.created_at, updated_at: detail.created_at }], next_cursor: null } }));
  await page.route("**/api/v1/collections**", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/conversation-groups", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/conversations**", (route) => route.fulfill({ json: new URL(route.request().url()).pathname.endsWith("/conversations") ? [detail] : { ...detail, messages: [{
    id: "message", run_id: "run", question: "整理整份讲义的知识点", status: complete ? "answered" : "processing", scope,
    answer: { insufficient_evidence: false, claims: [claim], explanation: "知识点的连贯讲解。" }, feedback: null, failure_message: null,
  }] } }));
  await page.route("**/api/v1/agent-runs/run", (route) => route.fulfill({ json: {
    id: "run", status: complete ? "completed" : "queued", stage: complete ? "done" : "overview_1", revision: complete ? 10 : 3,
    scope, plan: { steps: ["summary", "pdf"] }, clarification: null, failure_message: null, expires_at: "2026-10-11T00:00:00Z",
    outputs: [{ kind: "summary", text: "知识点整理", coverage: { sampled_pages: complete ? 64 : 8, indexed_pages: 300, processed_chunks: complete ? 64 : 8, indexed_chunks: 800, full_pages: complete ? 30 : 3, completed_batches: complete ? 8 : 1, total_batches: 8, knowledge_points: complete ? 40 : 5 } }, ...(complete ? [{ kind: "pdf", text: "PDF 已准备好" }] : [])],
  } }));
  await page.goto(`/study/${conversation}`);
  await expect(page.getByText("首批已整理的知识点", { exact: true })).toBeVisible();
  await expect(page.getByText("已完成 1 / 8 批")).toBeVisible();
  await page.reload();
  await expect(page.getByText("已完成 1 / 8 批")).toBeVisible();
  complete = true;
  await page.reload();
  await expect(page.getByText("已阅读 64 / 300 页，64 / 800 个正文片段")).toBeVisible();
  await expect(page.getByText(/其中 30 页的索引正文已完整读取/)).toBeVisible();
  await expect(page.getByText("尚未读完全部正文，请结合原文核对。")).toBeVisible();
  await expect(page.getByRole("link", { name: "下载知识点 PDF" })).toHaveAttribute("href", "/api/v1/agent-runs/run/notes.pdf");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
