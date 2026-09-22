import { expect, test } from "@playwright/test";

test("ask within a document, inspect a citation with the keyboard, and open its source", async ({ page }) => {
  const id = "11111111-1111-4111-8111-111111111111";
  const document = { id, filename: "Synthetic learning notes.pdf", media_type: "application/pdf", status: "ready", content_count: 2, page_count: 2, byte_size: 100, created_at: "2026-09-22T00:00:00Z", updated_at: "2026-09-22T00:00:00Z", failure_code: null, failure_message: null };
  const locator = (position: number) => ({ kind: "page", position, title: null, path: [] });
  let asked = false;
  const answer = { id: "question", question: "Why use the median?", version: 1, status: "answered", failure_message: null, answer: { insufficient_evidence: false, claims: [{ text: "The median is robust against extreme outliers.", citations: [{ source_id: "source", quote: "The median is robust against extreme outliers.", unit: 2, locator: locator(2) }] }] } };
  await page.route("**/api/v1/documents**", async (route) => {
    const url = new URL(route.request().url());
    let response: unknown;
    if (url.pathname.endsWith("/index")) response = { status: "ready", completed: 2, total: 2, failure_message: null };
    else if (url.pathname.endsWith("/questions")) {
      if (route.request().method() === "POST") { asked = true; response = answer; }
      else response = asked ? [answer] : [];
    } else if (url.pathname.endsWith("/content")) {
      const ordinal = Number(url.searchParams.get("ordinal") || 1);
      response = { document_id: id, content_count: 2, locations: [1, 2].map((unit) => ({ ordinal: unit, citation_locator: locator(unit) })), contents: [{ ordinal, citation_locator: locator(ordinal), content: ordinal === 2 ? "The median is robust against extreme outliers." : "A short introduction to statistics." }] };
    } else if (url.pathname.endsWith(id)) response = document;
    else response = { items: [document], next_cursor: null };
    await route.fulfill({ json: response });
  });
  await page.goto(`/documents/${id}`);
  await page.getByRole("button", { name: "基于此资料提问" }).click();
  await expect(page.getByText("回答范围：仅「Synthetic learning notes.pdf」")).toBeVisible();
  await page.getByRole("textbox").fill("Why use the median?");
  await page.getByRole("button", { name: "提问", exact: true }).focus();
  await page.keyboard.press("Enter");
  const citation = page.getByRole("button", { name: "查看引用：第 2 页" });
  await expect(citation).toBeVisible();
  await citation.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("region", { name: "回答引用原文" })).toContainText("The median is robust");
  await expect(page).not.toHaveURL(/unit=2/);
  await page.getByRole("button", { name: "在原文中打开" }).click();
  await expect(page).toHaveURL(/unit=2/);
  await expect(page.locator("#content-2")).toContainText("The median is robust");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "关闭引用面板" }).click();
  await citation.click();
  await expect(page.getByRole("dialog", { name: "来源定位" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "来源定位" })).not.toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
