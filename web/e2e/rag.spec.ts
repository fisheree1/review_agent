import { expect, test } from "@playwright/test";

test("ask within a document, inspect a citation with the keyboard, and open its source", async ({ page }) => {
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: {
    user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    workspace_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    email: "learner@example.test", csrf_token: "csrf-e2e",
  } }));
  await page.setViewportSize({ width: 1440, height: 900 });
  const id = "11111111-1111-4111-8111-111111111111";
  const document = { id, filename: "Synthetic learning notes.pdf", media_type: "application/pdf", status: "ready", content_count: 2, page_count: 2, byte_size: 100, created_at: "2026-09-22T00:00:00Z", updated_at: "2026-09-22T00:00:00Z", failure_code: null, failure_message: null };
  const locator = (position: number) => ({ kind: "page", position, title: null, path: [] });
  let asked = false;
  const answer = { id: "question", question: "Why use the median?", version: 1, status: "answered", failure_message: null, answer: { insufficient_evidence: false, claims: [{ text: "The median is robust against extreme outliers. ".repeat(35), citations: [{ source_id: "source", quote: "The median is robust against extreme outliers.", unit: 2, locator: locator(2) }] }] } };
  await page.route("**/api/v1/documents**", async (route) => {
    const url = new URL(route.request().url());
    let response: unknown;
    if (url.pathname.endsWith("/index")) response = { status: "ready", completed: 2, total: 2, failure_message: null };
    else if (url.pathname.endsWith("/questions")) {
      if (route.request().method() === "POST") { asked = true; response = answer; }
      else response = asked ? [answer] : [];
    } else if (url.pathname.endsWith("/content")) {
      const ordinal = Number(url.searchParams.get("ordinal") || 1);
      response = { document_id: id, content_count: 2, locations: [1, 2].map((unit) => ({ ordinal: unit, citation_locator: locator(unit) })), contents: [{ ordinal, citation_locator: locator(ordinal), content: ordinal === 2 ? "The median is robust against extreme outliers." : "A short introduction to statistics. ".repeat(180) }] };
    } else if (url.pathname.endsWith(id)) response = document;
    else response = { items: [document], next_cursor: null };
    await route.fulfill({ json: response });
  });
  await page.goto(`/documents/${id}`);
  await expect(page.locator("#content-1")).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 480));
  const readingPosition = await page.evaluate(() => window.scrollY);
  expect(readingPosition).toBeGreaterThan(200);
  await page.getByRole("button", { name: "打开资料问答" }).click();
  const questions = page.getByRole("complementary", { name: "资料问答" });
  await expect(questions).toBeVisible();
  expect(await page.locator("main .question-panel").count()).toBe(0);
  expect(await questions.evaluate((aside) => aside.getBoundingClientRect().left)).toBeGreaterThan(
    await page.locator("main").evaluate((main) => main.getBoundingClientRect().left),
  );
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(readingPosition);
  await expect(page.getByText("回答范围：仅「Synthetic learning notes.pdf」")).toBeVisible();
  await page.getByRole("textbox").fill("Why use the median?");
  await page.getByRole("button", { name: "提问", exact: true }).focus();
  await page.keyboard.press("Enter");
  const citation = page.getByRole("button", { name: "查看引用：第 2 页" });
  await expect(citation).toBeVisible();
  expect(await questions.evaluate((aside) => aside.scrollHeight > aside.clientHeight)).toBe(true);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(readingPosition);
  await citation.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("region", { name: "回答引用原文" })).toContainText("The median is robust");
  await expect(page).not.toHaveURL(/unit=2/);
  await page.getByRole("button", { name: "在原文中打开" }).click();
  await expect(page).toHaveURL(/unit=2/);
  await expect(page.locator("#content-2 mark")).toHaveText("The median is robust against extreme outliers.");
  await page.getByRole("button", { name: "打开资料问答" }).click();
  await page.getByRole("button", { name: "收起资料问答" }).first().click();
  await expect(page).toHaveURL(/unit=1/);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(readingPosition);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "关闭引用面板" }).click();
  await page.getByRole("button", { name: "打开资料问答" }).click();
  await expect(page.getByRole("dialog", { name: "资料问答" })).toBeVisible();
  await citation.click();
  await expect(page.getByRole("dialog", { name: "来源定位" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "来源定位" })).not.toBeVisible();
  await expect(page.getByRole("dialog", { name: "资料问答" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "资料问答" })).not.toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
