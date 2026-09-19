import path from "node:path";

import { expect, test } from "@playwright/test";

const pdfPath = process.env.E2E_PDF_PATH;

test("upload a PDF and read its paginated text with the keyboard", async ({ page }) => {
  test.skip(!pdfPath, "Set E2E_PDF_PATH to a local text PDF");
  await page.goto("/");

  await page.locator("#document-file").setInputFiles(path.resolve(pdfPath!));
  await page.getByRole("button", { name: "开始上传" }).click();

  await expect(page).toHaveURL(/\/documents\/[0-9a-f-]+/);
  await expect(page.locator(".reading-page__number").filter({ hasText: "PAGE 01" })).toBeVisible({ timeout: 90_000 });
  await expect(page.locator(".reading-page__content").first()).not.toBeEmpty();

  await page.keyboard.press("]");
  await expect(page.getByLabel("第 2 页")).toHaveClass(/reading-page--current/);
  await expect(page.getByText(/第 2 \/ \d+ 页/)).toBeVisible();

  await page.getByRole("button", { name: "放大正文字号" }).click();
  await expect(page.getByText("18px")).toBeVisible();
});
