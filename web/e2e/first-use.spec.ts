import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const documentId = "11111111-1111-4111-8111-111111111111";
const quizId = "22222222-2222-4222-8222-222222222222";
const fixturePath = fileURLToPath(new URL("./preview-fixture.pdf", import.meta.url));
const original = readFileSync(fixturePath);

test("a first-time learner uploads a PDF and starts a five-question practice from its preview", async ({ page }) => {
  let uploaded = false;
  let prepared = false;
  let quiz: Record<string, unknown> | null = null;
  const document = {
    id: documentId, filename: "preview-fixture.pdf", media_type: "application/pdf", byte_size: original.length,
    status: "ready", page_count: 2, content_count: 2, failure_code: null, failure_message: null,
    created_at: "2026-09-28T00:00:00Z", updated_at: "2026-09-28T00:00:00Z",
  };
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: {
    user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    workspace_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    email: "learner@example.test", csrf_token: "csrf-e2e",
  } }));
  await page.route("**/api/v1/documents**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/index")) {
      if (route.request().method() === "POST") {
        prepared = true;
        return route.fulfill({ json: { status: "queued", completed: 0, total: 1, failure_message: null } });
      }
      return route.fulfill({ json: { status: prepared ? "ready" : "not_indexed", completed: prepared ? 1 : 0, total: prepared ? 1 : 0, failure_message: null } });
    }
    if (path.endsWith("/original")) return route.fulfill({ body: original, contentType: "application/pdf" });
    if (path.endsWith(`/documents/${documentId}`)) return route.fulfill({ json: document });
    if (route.request().method() === "POST") {
      uploaded = true;
      return route.fulfill({ status: 201, json: { document } });
    }
    return route.fulfill({ json: { items: uploaded ? [document] : [], next_cursor: null } });
  });
  await page.route("**/api/v1/collections**", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/conversation-groups", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/conversations**", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/learning/resume", (route) => route.fulfill({ json: { conversation: null, attempt: null } }));
  await page.route("**/api/v1/learning/checkins**", (route) => route.fulfill({ json: { month: "2026-09", today: "2026-09-28", timezone: "UTC", dates: [], checked_today: false, streak: 0, total: 0 } }));
  await page.route("**/api/v1/quizzes**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON() as { title: string; config: Record<string, unknown>; document_ids: string[] };
      quiz = { id: quizId, title: body.title, config: body.config, scope: [{ document_id: documentId, version_id: 1, filename: document.filename }], status: "queued", question_count: 0, failure_message: null };
      return route.fulfill({ status: 201, json: quiz });
    }
    if (path.endsWith("/attempts")) return route.fulfill({ json: [] });
    if (path.endsWith(quizId)) return route.fulfill({ json: quiz });
    return route.fulfill({ json: quiz ? [quiz] : [] });
  });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/study");
  await page.getByRole("link", { name: "前往资料库上传 PDF" }).click();
  await expect(page).toHaveURL(/#upload-materials$/);
  await expect(page.locator("#upload-materials")).toBeFocused();
  await expect(page.getByRole("button", { name: "选择文件" })).toBeInViewport();
  await page.locator("#document-file").setInputFiles(fixturePath);
  await page.getByRole("button", { name: "开始上传 1 份" }).click();
  await page.getByRole("link", { name: /预览 preview-fixture.pdf/ }).click();
  await expect(page.getByRole("img", { name: "PDF 原文件第 1 页" })).toBeVisible();
  await page.getByRole("button", { name: "准备资料并生成 5 题" }).click();
  await expect(page).toHaveURL(`/quizzes/${quizId}`);
  expect(quiz).toMatchObject({
    config: { type_counts: { single: 5, multiple: 0, true_false: 0, short: 0 }, difficulty: "medium", language: "zh" },
  });
});
