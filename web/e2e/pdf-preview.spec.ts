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

test("PDF preview opens directly while parsing without requesting extracted text", async ({ page }) => {
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
    return route.fulfill({ json: path.endsWith(documentId) ? document : { items: [document], next_cursor: null } });
  });

  await page.goto(`/documents/${documentId}`);
  const firstPage = page.getByRole("img", { name: "PDF 原文件第 1 页" });
  await expect(firstPage).toBeVisible();
  await expect.poll(() => firstPage.evaluate((canvas: HTMLCanvasElement) => canvas.width)).toBeGreaterThan(0);

  await expect(page.getByText("第 1 / 2 页")).toBeVisible();
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.getByRole("img", { name: "PDF 原文件第 2 页" })).toBeVisible();
  expect(contentRequests).toBe(0);
  expect(originalRequests).toBeGreaterThan(0);
  expect(contentRequests).toBe(0);
});
