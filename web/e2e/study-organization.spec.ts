import { expect, test } from "@playwright/test";

test("collapse navigation, organize a direct conversation, and check in once", async ({ page }) => {
  const conversationId = "33333333-3333-4333-8333-333333333333";
  const groupId = "44444444-4444-4444-8444-444444444444";
  const documentId = "11111111-1111-4111-8111-111111111111";
  const now = new Date();
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  let conversation: Record<string, unknown> | null = null;
  let group: { id: string; name: string } | null = null;
  let checked = false;
  let checkinCalls = 0;
  let deleted = false;

  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: {
    user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    workspace_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    email: "learner@example.test", csrf_token: "csrf-test",
  } }));
  await page.route("**/api/v1/documents**", (route) => route.fulfill({ json: {
    items: [{ id: documentId, filename: "统计.pdf", status: "ready", media_type: "application/pdf", byte_size: 100,
      page_count: 1, content_count: 1, created_at: now.toISOString(), updated_at: now.toISOString(),
      failure_code: null, failure_message: null }], next_cursor: null,
  } }));
  await page.route("**/api/v1/collections**", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/learning/resume", (route) => route.fulfill({ json: { conversation: null, attempt: null } }));
  await page.route("**/api/v1/conversation-groups**", (route) => {
    const request = route.request();
    if (request.method() === "GET") return route.fulfill({ json: group ? [group] : [] });
    if (request.method() === "DELETE") { group = null; return route.fulfill({ status: 204 }); }
    const body = request.postDataJSON() as { name: string };
    group = { id: groupId, name: body.name };
    return route.fulfill({ json: group, status: request.method() === "POST" ? 201 : 200 });
  });
  await page.route("**/api/v1/conversations**", (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/conversations")) {
      if (request.method() === "POST") {
        const body = request.postDataJSON() as { title: string; document_ids: string[] };
        expect(body.document_ids).toEqual([]);
        conversation = { id: conversationId, title: body.title, group_id: null, scope: [], created_at: now.toISOString(), updated_at: now.toISOString() };
        return route.fulfill({ status: 201, json: conversation });
      }
      return route.fulfill({ json: conversation ? [conversation] : [] });
    }
    if (path.endsWith("/scope")) {
      conversation = { ...conversation, scope: [{ document_id: documentId, version_id: 1, filename: "统计.pdf" }] };
      return route.fulfill({ json: conversation });
    }
    if (request.method() === "PATCH") {
      conversation = { ...conversation, ...(request.postDataJSON() as Record<string, unknown>) };
      return route.fulfill({ json: conversation });
    }
    if (request.method() === "DELETE") { deleted = true; conversation = null; return route.fulfill({ status: 204 }); }
    return route.fulfill({ json: { ...conversation, messages: [] } });
  });
  await page.route("**/api/v1/learning/checkins**", (route) => {
    const request = route.request();
    const month = request.method() === "GET" ? new URL(request.url()).searchParams.get("month")! : today.slice(0, 7);
    if (request.method() === "POST") { checked = true; checkinCalls += 1; }
    return route.fulfill({ json: { month, today, timezone: "UTC", dates: checked && month === today.slice(0, 7) ? [today] : [],
      checked_today: checked, streak: Number(checked), total: Number(checked) } });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "收起侧边栏" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-sidebar-collapsed", "true");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-sidebar-collapsed", "true");
  await page.getByRole("navigation", { name: "主要导航" }).getByRole("link", { name: "学习空间", exact: true }).click();
  await page.getByRole("button", { name: "新建对话", exact: true }).click();
  await expect(page).toHaveURL(`/study/${conversationId}`);
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await expect(page.getByRole("button", { name: "发送", exact: true })).toBeDisabled();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("group", { name: "资料范围" }).getByLabel("统计.pdf").check();
  await expect(page.getByRole("button", { name: "保存新范围" })).toBeInViewport();
  await page.getByRole("button", { name: "保存新范围" }).click();
  await expect(page.getByText("1 份资料", { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 800 });

  await page.getByRole("button", { name: "新建分组" }).click();
  await page.getByRole("dialog", { name: "新建分组" }).getByLabel("名称").fill("考试准备");
  await page.getByRole("dialog", { name: "新建分组" }).getByRole("button", { name: "保存" }).click();
  await expect(page.getByText("考试准备", { exact: true })).toBeVisible();
  await page.getByLabel("管理对话 新对话").click();
  await page.getByRole("button", { name: "重命名", exact: true }).click();
  await page.getByRole("dialog", { name: "重命名对话" }).getByLabel("名称").fill("统计复习");
  await page.getByRole("dialog", { name: "重命名对话" }).getByRole("button", { name: "保存" }).click();
  await expect(page.getByRole("link", { name: "统计复习" })).toBeVisible();
  await page.getByLabel("管理对话 统计复习").click();
  await page.getByRole("button", { name: "移动到分组" }).click();
  await page.getByRole("dialog", { name: "移动对话" }).getByRole("button", { name: /分组/ }).click();
  await page.getByRole("menuitemradio", { name: "考试准备" }).click();
  await page.getByRole("dialog", { name: "移动对话" }).getByRole("button", { name: "保存" }).click();
  await expect(page.locator(".chat-group").filter({ hasText: "考试准备" }).getByRole("link", { name: "统计复习" })).toBeVisible();
  await page.getByLabel("管理对话 统计复习").click();
  await page.getByRole("button", { name: "删除对话" }).click();
  const confirmation = page.getByRole("dialog", { name: "删除对话？" });
  await confirmation.getByRole("button", { name: "取消" }).click();
  expect(deleted).toBe(false);
  await page.getByLabel("管理对话 统计复习").click();
  await page.getByRole("button", { name: "删除对话" }).click();
  await confirmation.getByRole("button", { name: "确认删除" }).click();
  await expect(page).toHaveURL("/study");
  expect(deleted).toBe(true);

  await page.getByRole("navigation", { name: "主要导航" }).getByRole("link", { name: "资料库", exact: true }).click();
  await expect(page.getByRole("heading", { name: "学习打卡" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "主要导航" }).getByRole("link", { name: "学习日历" })).toHaveCount(0);
  await page.getByRole("button", { name: "今日打卡" }).click();
  await expect(page.getByRole("button", { name: "今日已打卡" })).toBeDisabled();
  await page.getByText("查看月历", { exact: true }).click();
  await expect(page.getByRole("listitem", { name: `${today} 已打卡` })).toBeVisible();
  await page.getByRole("button", { name: "上个月" }).click();
  await expect(page.getByRole("button", { name: "今日已打卡" })).toBeDisabled();
  expect(checkinCalls).toBe(1);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
