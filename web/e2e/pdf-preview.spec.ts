import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const documentId = "11111111-1111-4111-8111-111111111111";
const original = readFileSync(fileURLToPath(new URL("./preview-fixture.pdf", import.meta.url)));
const document = {
  id: documentId, filename: "Preview.pdf", media_type: "application/pdf", byte_size: original.length,
  status: "queued", page_count: null, content_count: null, failure_code: null, failure_message: null,
  created_at: "2026-09-28T00:00:00Z", updated_at: "2026-09-28T00:00:00Z",
};

test("PDF preview opens directly while parsing; Office files show unsupported preview", async ({ page }) => {
  let contentRequests = 0;
  let originalRequests = 0;
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: {
    user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    workspace_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    email: "learner@example.test", csrf_token: "csrf-e2e",
  } }));
  await page.route("**/api/v1/documents**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/original")) {
      originalRequests += 1;
      return route.fulfill({ body: original, contentType: "application/pdf" });
    }
    if (path.endsWith("/content")) {
      contentRequests += 1;
      return route.fulfill({ status: 500 });
    }
    const officeType = path.endsWith("22222222-2222-4222-8222-222222222222")
      ? "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
      : "application/vnd.openxmlformats-officedocument.presentationml.presentation";
    const office = { ...document, id: path.split("/").at(-1), filename: officeType.includes("wordprocessing") ? "Notes.docx" : "Slides.pptx", media_type: officeType, status: "ready" };
    return route.fulfill({ json: path.endsWith(documentId) ? document : path.endsWith("22222222-2222-4222-8222-222222222222") || path.endsWith("33333333-3333-4333-8333-333333333333") ? office : { items: [document], next_cursor: null } });
  });

  await page.goto(`/documents/${documentId}`);
  const firstPage = page.getByRole("img", { name: "PDF 原文件第 1 页" });
  await expect(firstPage).toBeVisible();
  await expect.poll(() => firstPage.evaluate((canvas: HTMLCanvasElement) => canvas.width)).toBeGreaterThan(0);

  await expect(page.getByText("第 1 / 2 页")).toBeVisible();
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.getByRole("img", { name: "PDF 原文件第 2 页" })).toBeVisible();
  expect(contentRequests).toBe(0);
  const pdfOriginalRequests = originalRequests;
  expect(pdfOriginalRequests).toBeGreaterThan(0);

  for (const officeId of ["22222222-2222-4222-8222-222222222222", "33333333-3333-4333-8333-333333333333"]) {
    await page.goto(`/documents/${officeId}`);
    await expect(page.getByRole("heading", { name: "暂不支持预览" })).toBeVisible();
    await expect(page.getByRole("img", { name: /PDF 原文件/ })).toHaveCount(0);
  }
  expect(originalRequests).toBe(pdfOriginalRequests);
  expect(contentRequests).toBe(0);
});
