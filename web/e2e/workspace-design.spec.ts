import { expect, test } from "@playwright/test";

test("refreshed workspace keeps readable themes, collapsible navigation and mobile upload", async ({ page }) => {
  const names = ["数据科学导论 · Week 1.pdf", "概率论与数理统计.pdf", "机器学习基础 · 课程讲义.pdf", "Python 数据分析实践.pdf", "研究方法与论文阅读.pdf", "线性代数 · 核心概念.pdf"];
  const documents = names.map((filename, index) => ({ id: `document-${index}`, filename,
    media_type: "application/pdf", status: index === 2 ? "parsing" : "ready", page_count: 24 + index * 8,
    content_count: 24 + index * 8, byte_size: 1024 * 1024 * (index + 1), failure_code: null, failure_message: null,
    created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-06T00:00:00Z" }));
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: { user_id: "learner", workspace_id: "workspace", email: "learner@example.test", csrf_token: "test" } }));
  await page.route("**/api/v1/documents**", (route) => route.fulfill({ json: { items: documents, next_cursor: null } }));
  await page.route("**/api/v1/collections**", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/learning/resume", (route) => route.fulfill({ json: {
    conversation: { id: "conversation", title: "理解结构化数据", working: false }, attempt: null,
  } }));
  await page.route("**/api/v1/learning/checkins**", (route) => route.fulfill({ json: {
    month: "2026-10", today: "2026-10-06", timezone: "Asia/Singapore", dates: ["2026-10-03", "2026-10-04", "2026-10-05"], checked_today: false, streak: 3, total: 12,
  } }));
  await page.emulateMedia({ reducedMotion: "reduce", colorScheme: "light" });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: names[0], exact: true })).toBeVisible();
  const contrast = async (selector: string) => page.locator(selector).first().evaluate((element) => {
    const luminance = (color: string) => {
      const channels = color.match(/[\d.]+/g)!.slice(0, 3).map(Number).map((value) => {
        const channel = value / 255;
        return channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4;
      });
      return channels.reduce((sum, channel, index) => sum + channel * [.2126, .7152, .0722][index], 0);
    };
    let parent: Element | null = element;
    let background = "rgba(0, 0, 0, 0)";
    while (parent && background === "rgba(0, 0, 0, 0)") { background = getComputedStyle(parent).backgroundColor; parent = parent.parentElement; }
    const foreground = luminance(getComputedStyle(element).color);
    const surface = luminance(background);
    return (Math.max(foreground, surface) + .05) / (Math.min(foreground, surface) + .05);
  });
  for (const theme of ["light", "dark"] as const) {
    if (theme === "dark") await page.getByRole("button", { name: "切换到深色模式" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    for (const selector of [".primary-nav a[aria-current]", ".primary-nav a:not([aria-current])", ".page-intro .button", ".page-intro__copy p", ".results-count"]) {
      expect(await contrast(selector), `${theme}: ${selector}`).toBeGreaterThanOrEqual(4.5);
    }
    await expect(page.getByRole("button", { name: "选择文件", exact: true })).toBeInViewport({ ratio: 1 });
    await page.screenshot({ path: `/tmp/review-agent-ui-${theme}.png` });
  }
  await page.getByRole("button", { name: "收起侧边栏" }).click();
  await expect(page.getByRole("navigation", { name: "主要导航" }).getByRole("link", { name: "学习空间", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "展开侧边栏" }).click();
  await page.getByRole("button", { name: "切换到浅色模式" }).click();
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    await expect(page.getByRole("button", { name: "选择文件", exact: true })).toBeVisible();
    const upload = await page.getByRole("button", { name: "选择文件", exact: true }).boundingBox();
    const list = await page.getByRole("region", { name: "资料列表" }).boundingBox();
    expect(upload!.y).toBeLessThan(list!.y);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (width === 390) await page.screenshot({ path: "/tmp/review-agent-ui-mobile.png", fullPage: true });
  }
});

test("login redesign preserves the form on desktop and small screens", async ({ page }) => {
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ status: 401, json: { error: { code: "UNAUTHENTICATED", message: "请登录" } } }));
  await page.route("**/api/v1/auth/config", (route) => route.fulfill({ json: { login_mode: "local", signup_enabled: true } }));
  await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "登录学习空间" })).toBeVisible();
    await page.getByLabel("邮箱", { exact: true }).fill("learner@example.test");
    await page.getByLabel("密码", { exact: true }).fill("example-password");
    await expect(page.getByRole("button", { name: "登录", exact: true })).toBeInViewport({ ratio: 1 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `/tmp/review-agent-ui-login-${width}.png` });
  }
});
