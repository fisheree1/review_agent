import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";

const original = readFileSync(fileURLToPath(new URL("./preview-fixture.pdf", import.meta.url)));
const doc = "11111111-1111-4111-8111-111111111111";
const secondDoc = "22222222-2222-4222-8222-222222222222";
const conversation = "33333333-3333-4333-8333-333333333333";

for (const width of [1440, 390]) test(`knowledge points link to reusable PDF pages at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  const requests: string[] = [];
  const docs = [doc, secondDoc].map((id, index) => ({ id, filename: index ? "案例.pdf" : "统计讲义.pdf", media_type: "application/pdf", status: "ready", page_count: 2, content_count: 2, byte_size: original.length, created_at: "2026-10-06T00:00:00Z", updated_at: "2026-10-06T00:00:00Z" }));
  const scope = docs.map((item) => ({ document_id: item.id, version_id: 1, filename: item.filename }));
  const detail = { id: conversation, title: "知识点学习", scope, created_at: "2026-10-06T00:00:00Z" };
  const citation = (unit: number, document_id = doc) => ({ source_id: `${document_id}-${unit}`, document_id, version_id: 1, unit, locator: { kind: "page", position: unit, title: null, path: [] }, quote: "The median is robust to outliers." });
  const claims = [
    { title: "中位数", text: "中位数是排序后处于中间位置的数值。", explanation: Array.from({ length: 10 }, () => "先将数据从小到大排序，再找到中间的位置。极端值变化时，中间位置的数值可能保持不变。可以用班级成绩理解这个特点。").join("\n"), citations: [citation(1), citation(2)] },
    { title: "异常值", text: "异常值可能影响平均数。", explanation: Array.from({ length: 8 }, () => "比较不同统计量能够帮助理解异常值的影响。先观察数据分布，再选择适合的统计量。").join("\n"), citations: [citation(2)] },
    { title: "案例比较", text: "比较不同资料中的案例。", explanation: "将统计方法应用到另一组数据。", citations: [citation(1, secondDoc)] },
  ];
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: { user_id: "user", workspace_id: "workspace", email: "learner@example.test", csrf_token: "test" } }));
  await page.route("**/api/v1/documents**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/original")) { requests.push(path); return route.fulfill({ body: original, contentType: "application/pdf" }); }
    return route.fulfill({ json: docs.find((item) => path.endsWith(item.id)) ?? { items: docs, next_cursor: null } });
  });
  await page.route("**/api/v1/collections**", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/conversation-groups", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/conversations**", (route) => route.fulfill({ json: new URL(route.request().url()).pathname.endsWith("/conversations") ? [detail] : { ...detail, messages: [{
    id: "message", question: "解释这些知识点", status: "answered", scope, answer: { insufficient_evidence: false, claims }, feedback: null, failure_message: null,
  }] } }));
  await page.goto(`/study/${conversation}`);
  const first = page.getByRole("button", { name: "中位数 查看原文", exact: true });
  const second = page.getByRole("button", { name: "异常值 查看原文", exact: true });
  const follow = page.getByRole("checkbox", { name: "跟随讲解" });
  await expect(follow).toBeChecked();
  const panel = page.getByLabel("原文预览", { exact: true });
  if (width === 390) await expect(panel).toBeHidden();
  else await expect(panel).toBeVisible();
  await follow.uncheck();
  await first.click();
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("img", { name: "PDF 原文件第 1 页" })).toBeVisible();
  const initialDownloads = requests.filter((path) => path.includes(doc)).length;
  await panel.getByRole("button", { name: "统计讲义.pdf · 第 2 页" }).click();
  await expect(panel.getByRole("img", { name: "PDF 原文件第 2 页" })).toBeVisible();
  expect(requests.filter((path) => path.includes(doc))).toHaveLength(initialDownloads);

  if (width === 390) {
    await page.keyboard.press("Escape");
    await expect(panel).toBeHidden();
    await expect(first).toBeFocused();
    await first.press("Enter");
    await expect(panel.getByRole("img", { name: "PDF 原文件第 2 页" })).toBeVisible();
    expect(requests.filter((path) => path.includes(doc))).toHaveLength(initialDownloads);
    await panel.getByRole("button", { name: "关闭原文预览" }).click();
  } else {
    await panel.getByRole("button", { name: "统计讲义.pdf · 第 1 页" }).click();
    await second.scrollIntoViewIfNeeded();
    // With follow disabled, reading another section must not steal the manual PDF page.
    await expect(panel.getByRole("img", { name: "PDF 原文件第 1 页" })).toBeVisible();
    await follow.check();
    await second.evaluate((button) => button.closest(".study-knowledge")!.scrollIntoView({ block: "start", behavior: "instant" }));
    await expect(panel.getByRole("img", { name: "PDF 原文件第 2 页" })).toBeVisible();
    expect(requests.filter((path) => path.includes(doc))).toHaveLength(initialDownloads);
    await follow.uncheck();
  }
  await second.click();
  await expect(panel.getByRole("img", { name: "PDF 原文件第 2 页" })).toBeVisible();
  expect(requests.filter((path) => path.includes(doc))).toHaveLength(initialDownloads);
  if (width === 390) await panel.getByRole("button", { name: "关闭原文预览" }).click();
  await page.getByRole("button", { name: "案例比较 查看原文", exact: true }).click();
  await expect(panel.getByText("案例.pdf · 第 1 页", { exact: true })).toBeVisible();
  await expect(panel.getByRole("img", { name: "PDF 原文件第 1 页" })).toBeVisible();
  await expect(panel.getByRole("link", { name: "在阅读页打开" })).toHaveAttribute("href", `/documents/${secondDoc}?unit=1&version=1`);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/tmp/study-linked-${width}.png` });
});
