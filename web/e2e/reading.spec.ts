import path from "node:path";

import { expect, test } from "@playwright/test";

const pdfPath = process.env.E2E_PDF_PATH;

test("upload a PDF and preview its original pages with the keyboard", async ({ page }) => {
  test.skip(!pdfPath, "Set E2E_PDF_PATH to a local text PDF");
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: {
    user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    workspace_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    email: "learner@example.test", csrf_token: "csrf-e2e",
  } }));
  await page.goto("/");

  await page.locator("#document-file").setInputFiles(path.resolve(pdfPath!));
  await page.getByRole("button", { name: "开始上传" }).click();

  await page.getByRole("link", { name: /阅读/ }).first().click();
  await expect(page).toHaveURL(/\/documents\/[0-9a-f-]+/);
  await expect(page.getByRole("img", { name: "PDF 原文件第 1 页" })).toBeVisible({ timeout: 90_000 });

  await page.keyboard.press("]");
  await expect(page.getByRole("img", { name: "PDF 原文件第 2 页" })).toBeVisible();
  await expect(page.getByText(/第 2 \/ \d+ 页/)).toBeVisible();

  await page.getByRole("button", { name: "放大 PDF" }).click();
  await expect(page.getByText("125%")).toBeVisible();
});
