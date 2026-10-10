import { expect, test } from "@playwright/test";

test("desktop panels keep files and conversation actions in view; narrow screens retain keyboard navigation", async ({ page }) => {
  const documents = Array.from({ length: 65 }, (_, index) => ({
    id: `11111111-1111-4111-8111-${String(index + 1).padStart(12, "0")}`,
    filename: index === 1 ? `${"VeryLongUnbrokenFilename".repeat(12)}.pdf` : `课件 ${index + 1}.pdf`, media_type: "application/pdf", byte_size: 204800,
    status: "ready", page_count: 12, content_count: 12,
    created_at: "2026-09-27T00:00:00Z", updated_at: "2026-09-27T00:00:00Z",
    failure_code: null, failure_message: null,
  }));
  const conversation = {
    id: "33333333-3333-4333-8333-333333333333", title: "统计复习",
    scope: [{ document_id: documents[0].id, version_id: 1, filename: documents[0].filename }],
    created_at: "2026-09-27T00:00:00Z",
  };
  const messages = Array.from({ length: 12 }, (_, index) => ({
    id: `message-${index}`, question: `复习问题 ${index + 1}`, scope: conversation.scope,
    status: "answered", feedback: null, failure_message: null,
    answer: { insufficient_evidence: false, claims: [{ text: "中位数可以描述典型水平。".repeat(20), citations: [] }] },
  }));
  let failCreate = true;
  let deleted = false;
  let deleteCalls = 0;
  await page.route("**/api/v1/conversation-groups", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: {
    user_id: "owner", workspace_id: "workspace", email: "learner@example.test", csrf_token: "csrf-test",
  } }));
  await page.route("**/api/v1/documents**", (route) => route.fulfill({ json: { items: documents, next_cursor: null } }));
  await page.route("**/api/v1/collections**", (route) => {
    if (route.request().method() === "DELETE") {
      deleteCalls += 1;
      if (deleteCalls === 1) return route.fulfill({ status: 503, json: { error: { code: "UNAVAILABLE", message: "暂时无法删除，请重试" } } });
      deleted = true;
      return route.fulfill({ status: 204 });
    }
    return route.fulfill({ json: deleted ? [] : [{ id: "collection", name: "复习资料", description: "", document_ids: [documents[0].id] }] });
  });
  await page.route("**/api/v1/learning/resume", (route) => route.fulfill({ json: { conversation: null, attempt: null } }));
  await page.route("**/api/v1/learning/checkins**", (route) => route.fulfill({ json: { month: "2026-09", today: "2026-09-28", timezone: "UTC", dates: [], checked_today: false, streak: 0, total: 0 } }));
  await page.route("**/api/v1/quizzes", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/conversations**", (route) => {
    if (route.request().method() === "POST") return route.fulfill(failCreate
      ? { status: 503, json: { error: { code: "UNAVAILABLE", message: "暂时无法创建" } } }
      : { status: 201, json: conversation });
    return route.fulfill({ json: new URL(route.request().url()).pathname.endsWith(conversation.id)
      ? { ...conversation, messages } : [conversation] });
  });
  await page.emulateMedia({ reducedMotion: "reduce" });
  for (const width of [1440, 1024]) {
    await page.setViewportSize({ width, height: 768 });
    await page.goto("/");
    await expect(page.getByRole("heading", { name: documents[0].filename })).toBeVisible();
    await page.getByRole("link", { name: "跳到正文" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("main")).toBeFocused();
    await expect(page.getByRole("button", { name: "选择文件", exact: true })).toBeInViewport({ ratio: 1 });
    const list = page.getByRole("region", { name: "资料列表" });
    expect(await list.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await list.focus(); await page.keyboard.press("End");
    await expect.poll(() => list.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
    if (width === 1440) await page.screenshot({ path: "/private/tmp/review-agent-workspace-library.png" });
    await page.goto(`/study/${conversation.id}`);
    await expect(page.getByRole("textbox", { name: "发送任务或问题" })).toBeInViewport({ ratio: 1 });
    await expect(page.getByRole("button", { name: "发送", exact: true })).toBeInViewport({ ratio: 1 });
    expect(await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight)).toBe(true);
    const history = page.getByRole("region", { name: "对话记录" });
    await history.evaluate((element) => { element.scrollTop = 0; });
    await expect(page.getByRole("button", { name: "发送", exact: true })).toBeInViewport({ ratio: 1 });
    if (width === 1440) await page.screenshot({ path: "/private/tmp/review-agent-workspace-study.png" });
    await page.goto("/quizzes");
    await expect(page.getByRole("button", { name: "生成 5 题" })).toBeInViewport({ ratio: 1 });
    expect(await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight)).toBe(true);
    if (width === 1440) await page.screenshot({ path: "/private/tmp/review-agent-workspace-quiz.png" });
  }
  await page.goto("/study");
  const newConversation = page.getByRole("button", { name: "新建对话", exact: true });
  await newConversation.click();
  await expect(page.getByRole("alert")).toContainText("创建失败");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  failCreate = false;
  await page.getByRole("button", { name: "重试新建" }).click();
  await expect(page).toHaveURL(`/study/${conversation.id}`);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const uploadPosition = await page.getByRole("button", { name: "选择文件", exact: true }).boundingBox();
  const listPosition = await page.getByRole("region", { name: "资料列表" }).boundingBox();
  expect(uploadPosition).not.toBeNull();
  expect(listPosition).not.toBeNull();
  expect(uploadPosition!.y).toBeLessThan(listPosition!.y);
  await page.goto(`/study/${conversation.id}`);
  const menu = page.getByRole("button", { name: "打开主导航" });
  await menu.focus(); await page.keyboard.press("Enter");
  const navigation = page.getByRole("dialog", { name: "学习导航" });
  await expect(navigation).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(menu).toBeFocused();
  await page.getByRole("button", { name: "展开对话列表" }).click();
  await page.getByLabel(`管理对话 ${conversation.title}`).click();
  await page.getByRole("button", { name: "重命名", exact: true }).click();
  const renameDialog = page.getByRole("dialog", { name: "重命名对话" });
  await expect(renameDialog).toBeVisible();
  for (let index = 0; index < 5; index += 1) {
    await page.keyboard.press("Tab");
    expect(await renameDialog.evaluate((element) => element.contains(document.activeElement))).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(renameDialog).not.toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.setViewportSize({ width: 1440, height: 768 });
  await page.getByRole("complementary", { name: "集合与对话" }).locator("summary").filter({ hasText: "资料集合" }).click();
  const remove = page.getByRole("button", { name: "删除集合", exact: true });
  await remove.click();
  const confirmation = page.getByRole("dialog", { name: "删除集合“复习资料”？" });
  await expect(confirmation).toBeVisible();
  await expect(confirmation.getByRole("button", { name: "保留集合" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(remove).toBeFocused();
  expect(deleteCalls).toBe(0);
  await remove.click();
  await confirmation.getByRole("button", { name: "确认删除集合" }).click();
  await expect(page.getByRole("alert")).toContainText("暂时无法删除");
  await expect(remove).toBeEnabled();
  await remove.click();
  await confirmation.getByRole("button", { name: "确认删除集合" }).click();
  await expect(remove).not.toBeVisible();
  expect(deleteCalls).toBe(2);
  await page.getByRole("button", { name: "切换到深色模式" }).click();
  await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute("content", "#171a20");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});
