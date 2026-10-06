import { expect, test } from "@playwright/test";

const documentId = "11111111-1111-4111-8111-111111111111";
const conversationId = "33333333-3333-4333-8333-333333333333";
const quizId = "44444444-4444-4444-8444-444444444444";

test("library filtering, mobile keyboard navigation and task-first learning remain usable in both themes", async ({ page }) => {
  const documents = [
    { id: documentId, filename: "Statistics.pdf", status: "ready" },
    { id: "22222222-2222-4222-8222-222222222222", filename: "History.pptx", status: "parsing" },
  ].map((document) => ({ ...document, media_type: "application/pdf", byte_size: 100,
    content_count: 1, page_count: 1, created_at: "2026-09-23T00:00:00Z", updated_at: "2026-09-23T00:00:00Z", failure_code: null, failure_message: null }));
  const laterDocument = { ...documents[0], id: "99999999-9999-4999-8999-999999999999", filename: "Beyond first page.pdf" };
  const collections = [{ id: "88888888-8888-4888-8888-888888888888", name: "统计集合", description: "", document_ids: [documentId] }];
  const messages: Record<string, unknown>[] = Array.from({ length: 12 }, (_, index) => ({
    id: `77777777-7777-4777-8777-${String(index + 1).padStart(12, "0")}`, question: `历史问题 ${index + 1}`, scope: [], status: "answered", feedback: null, failure_message: null,
    answer: { claims: [{ text: "这是保留用于复习的历史回答。".repeat(20), citations: [] }], insufficient_evidence: false },
  }));
  let complete = false;
  const scope = [{ document_id: documentId, version_id: 1, filename: "Statistics.pdf" }];
  const conversation = { id: conversationId, title: "统计复习", scope, created_at: "2026-09-23T00:00:00Z" };
  const quiz = { id: quizId, title: "统计练习", scope, status: "ready", question_count: 5,
    config: { type_counts: { single: 5, multiple: 0, true_false: 0, short: 0 }, difficulty: "medium", language: "zh", topic: "统计", generation_mode: "standard" } };
  await page.route("**/api/v1/conversation-groups", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: {
    user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", workspace_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", email: "learner@example.test", csrf_token: "csrf-e2e",
  } }));
  await page.route("**/api/v1/learning/resume", (route) => route.fulfill({ json: { conversation: { id: conversationId, title: "统计复习", working: true }, attempt: null } }));
  await page.route("**/api/v1/learning/checkins**", (route) => route.fulfill({ json: { month: "2026-09", today: "2026-09-28", timezone: "UTC", dates: [], checked_today: false, streak: 0, total: 0 } }));
  await page.route("**/api/v1/documents**", (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const search = url.searchParams.get("search")?.toLowerCase() ?? "";
    const state = url.searchParams.get("status");
    const collection = url.searchParams.get("collection_id");
    let items = search ? [...documents, laterDocument].filter((item) => item.filename.toLowerCase().includes(search)) : documents;
    if (state) items = items.filter((item) => state === "processing" ? item.status === "parsing" : item.status === state);
    if (collection) items = items.filter((item) => collections.find((entry) => entry.id === collection)?.document_ids.includes(item.id));
    if (url.searchParams.get("sort") === "oldest") items = [...items].reverse();
    const locator = { kind: "page", position: 1, title: null, path: [] };
    return route.fulfill({ json: path.endsWith("/content") ? { document_id: documentId, content_count: 1, contents: [{ ordinal: 1, content: "The median resists extreme outliers.", citation_locator: locator }], locations: [{ ordinal: 1, citation_locator: locator }] }
      : path.endsWith(documentId) ? documents[0] : { items, next_cursor: search || state || collection ? null : "next" } });
  });
  await page.route("**/api/v1/collections**", (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON() as { name: string; document_ids: string[] };
      const entry = { id: "aaaaaaaa-1111-4111-8111-111111111111", name: body.name, description: "", document_ids: body.document_ids };
      collections.push(entry); return route.fulfill({ json: entry });
    }
    return route.fulfill({ json: collections });
  });
  await page.route("**/api/v1/conversations**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/messages")) {
      const question = (route.request().postDataJSON() as { question: string }).question;
      const message = { id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd", question, scope, status: "queued", answer: null, feedback: null, failure_message: null };
      messages.push(message); return route.fulfill({ json: message });
    }
    const updated = messages.map((message) => complete && message.status === "queued" ? { ...message, status: "answered", answer: { insufficient_evidence: false, claims: [{ text: "中位数的定义来自当前资料。", citations: [{ source_id: "source", document_id: documentId, version_id: 1, unit: 1, quote: "The median resists extreme outliers.", locator: { kind: "page", position: 1, title: null, path: [] } }] }] } } : message);
    return route.fulfill({ json: path.endsWith(conversationId) ? { ...conversation, messages: updated } : [conversation] });
  });
  await page.route("**/api/v1/quizzes**", (route) => route.fulfill({ json: new URL(route.request().url()).pathname.endsWith("/attempts") ? [] : new URL(route.request().url()).pathname.endsWith(quizId) ? quiz : [quiz] }));
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Statistics.pdf" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "主要导航" }).getByRole("link", { name: "资料库", exact: true })).toHaveAttribute("aria-current", "page");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect.poll(() => page.locator("main").evaluate((element) =>
    element.getAnimations({ subtree: true }).filter((animation) => animation.playState === "running").length)).toBe(0);
  const search = page.getByRole("searchbox", { name: "搜索全部资料" });
  await search.fill("beyond");
  await expect(page.getByRole("heading", { name: "Beyond first page.pdf" })).toBeVisible();
  await expect(page).toHaveURL(/search=beyond/);
  await search.fill("statistics");
  await expect(page.getByRole("heading", { name: "Statistics.pdf" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "History.pptx" })).toHaveCount(0);
  await page.getByRole("button", { name: /处理状态/ }).click();
  await expect(page.getByRole("menuitemradio", { name: "全部状态" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "清除筛选" })).toBeFocused();
  await expect(page.getByRole("menuitemradio", { name: "全部状态" })).toHaveCount(0);
  await page.getByRole("button", { name: /处理状态/ }).click();
  await page.getByRole("menuitemradio", { name: "处理中" }).click();
  await expect(page.getByRole("heading", { name: "没有匹配的资料" })).toBeVisible();
  await page.getByRole("button", { name: "清除筛选" }).click();
  await page.getByText("更多筛选与批量操作").click();
  await page.getByRole("button", { name: /资料集合/ }).click();
  await page.getByRole("menuitemradio", { name: collections[0].name }).click();
  await expect(page.getByRole("heading", { name: "History.pptx" })).toHaveCount(0);
  await page.getByRole("button", { name: "批量选择", exact: true }).click();
  await page.getByRole("checkbox", { name: "选择 Statistics.pdf" }).check();
  await page.getByLabel("新集合名称").fill("期末复习");
  await page.getByRole("button", { name: "将所选资料建为集合" }).click();
  await expect(page.getByText("集合已建立，可在学习空间选择它。")).toBeVisible();
  expect(collections.at(-1)?.document_ids).toEqual([documentId]);
  await page.getByRole("button", { name: "清除筛选" }).click();
  await page.getByText("更多筛选与批量操作").click();
  await expect(page.getByText("已显示 2 份资料，可继续加载更多")).toBeVisible();
  await page.evaluate(() => { (document.activeElement as HTMLElement)?.blur(); window.scrollTo({ top: 0, behavior: "instant" }); });
  await page.screenshot({ path: "/private/tmp/review-agent-library-desktop.png", fullPage: true });
  await page.getByRole("button", { name: "切换到深色模式" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect.poll(() => page.getByRole("main").evaluate((element) => getComputedStyle(element).backgroundColor)).toBe("rgb(23, 26, 32)");
  await page.screenshot({ path: "/private/tmp/review-agent-library-dark.png", fullPage: true });
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.setViewportSize({ width: 390, height: 844 });
  const menu = page.getByRole("button", { name: "打开主导航", exact: true });
  await menu.focus(); await menu.press("Enter");
  const dialog = page.getByRole("dialog", { name: "学习导航" });
  await expect(dialog).toBeVisible();
  for (let index = 0; index < 7; index += 1) {
    await page.keyboard.press("Tab");
    expect(await dialog.evaluate((element) => element.contains(document.activeElement))).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible(); await expect(menu).toBeFocused();
  await menu.press("Enter"); await dialog.getByRole("link", { name: "学习空间" }).click();
  await expect(page).toHaveURL("/study");
  await page.getByRole("navigation", { name: "历史对话" }).getByRole("link", { name: "统计复习" }).click();
  await expect(page.getByLabel("发送任务或问题")).toBeVisible();
  const history = page.getByRole("region", { name: "对话记录" });
  for (const height of [650, 844]) {
    await page.setViewportSize({ width: 390, height });
    const recordBounds = await history.boundingBox();
    const composerBounds = await page.locator(".agent-composer").boundingBox();
    expect(composerBounds!.y).toBeGreaterThanOrEqual(recordBounds!.y + recordBounds!.height);
  }
  await history.evaluate((element) => { element.scrollTop = 0; element.dispatchEvent(new Event("scroll")); });
  await page.getByLabel("发送任务或问题").fill("解释中位数");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByRole("button", { name: "有新回复 · 查看最新" })).toBeVisible();
  await expect.poll(() => history.evaluate((element) => element.scrollTop)).toBe(0);
  await history.getByRole("heading", { name: "历史问题 1", exact: true }).scrollIntoViewIfNeeded();
  await expect(history.getByRole("heading", { name: "历史问题 1", exact: true })).toBeInViewport();
  complete = true;
  await expect(history.getByText("中位数的定义来自当前资料。")).toBeVisible();
  await expect(history.getByRole("heading", { name: "历史问题 1", exact: true })).toBeInViewport();
  await page.getByRole("button", { name: "有新回复 · 查看最新" }).click();
  await expect(history.getByRole("heading", { name: "解释中位数", exact: true })).toBeInViewport();
  const latest = history.locator(".learning-message").filter({ has: page.getByRole("heading", { name: "解释中位数", exact: true }) });
  await latest.getByRole("button", { name: "知识点 1 查看原文" }).click();
  const source = page.getByRole("dialog", { name: "原文预览", exact: true });
  await source.getByText("引用摘录", { exact: true }).click();
  await expect(source.locator("blockquote")).toHaveText("The median resists extreme outliers.");
  await source.getByRole("button", { name: "关闭原文预览" }).click();
  await expect(page).toHaveURL(`/study/${conversationId}`);
  const content = await page.getByRole("region", { name: "当前对话" }).boundingBox();
  const expandHistory = page.getByRole("button", { name: "展开对话列表" });
  if (await expandHistory.isVisible()) await expandHistory.click();
  const management = await page.getByRole("complementary", { name: "集合与对话" }).boundingBox();
  expect(content!.y).toBeLessThan(management!.y);
  await page.evaluate(() => { (document.activeElement as HTMLElement)?.blur(); window.scrollTo({ top: 0, behavior: "instant" }); });
  await page.screenshot({ path: "/private/tmp/review-agent-study-mobile.png", fullPage: true });
  await page.goto(`/quizzes/${quizId}`);
  await expect(page.getByRole("button", { name: "开始作答" })).toBeVisible();
  await expect(page.getByLabel("标题", { exact: true })).not.toBeVisible();
  await page.screenshot({ path: "/private/tmp/review-agent-quiz-mobile.png", fullPage: true });
  for (const theme of ["dark", "light"] as const) {
    if (theme === "light") await page.getByRole("button", { name: "切换到浅色模式" }).click();
    const contrast = await page.getByRole("button", { name: "开始作答" }).evaluate((element) => {
      const style = getComputedStyle(element);
      const luminance = (color: string) => color.match(/[\d.]+/g)!.slice(0, 3).map((channel) => {
        const value = Number(channel) / 255;
        return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
      }).reduce((sum, channel, index) => sum + channel * [.2126, .7152, .0722][index], 0);
      const text = luminance(style.color); const background = luminance(style.backgroundColor);
      return (Math.max(text, background) + .05) / (Math.min(text, background) + .05);
    });
    expect(contrast).toBeGreaterThanOrEqual(4.5);
  }
  for (const width of [1100, 390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.goto(`/documents/${documentId}`);
    await expect(page.getByRole("heading", { name: "Statistics.pdf" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    for (const button of await page.locator(".app-header button").all()) {
      if (await button.isVisible()) { const box = await button.boundingBox(); expect(box!.x + box!.width).toBeLessThanOrEqual(width); expect(box!.height).toBeGreaterThanOrEqual(44); }
    }
  }
});
