import { expect, test } from "@playwright/test";

test("sign in and sign out with a protected workspace", async ({ page }) => {
  const prefix = process.env.PLAYWRIGHT_APP_PREFIX ?? "";
  const requests: string[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.includes("/api/v1/")) requests.push(path);
  });
  const identity = {
    user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    workspace_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    email: "learner@example.test",
    csrf_token: "csrf-e2e",
  };
  let signedIn = false;
  let logoutCsrf = "";
  let loginCalls = 0;
  await page.route("**/api/v1/auth/config", (route) => route.fulfill({ json: { signup_enabled: false } }));
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({
    status: signedIn ? 200 : 401,
    json: signedIn ? identity : { error: { code: "AUTHENTICATION_REQUIRED", message: "请登录" } },
  }));
  await page.route("**/api/v1/auth/login", (route) => {
    loginCalls += 1;
    if (loginCalls === 1) return route.fulfill({ status: 401, json: { error: { code: "INVALID_CREDENTIALS", message: "邮箱或密码不正确，请重新输入" } } });
    signedIn = true;
    return route.fulfill({ json: identity, headers: { "set-cookie": "review_agent_session=opaque; HttpOnly; SameSite=Strict; Path=/" } });
  });
  await page.route("**/api/v1/auth/logout", (route) => {
    logoutCsrf = route.request().headers()["x-csrf-token"] ?? "";
    signedIn = false;
    return route.fulfill({ status: 204 });
  });
  await page.route("**/api/v1/documents**", (route) => route.fulfill({ json: { items: [], next_cursor: null } }));
  await page.route("**/api/v1/learning/checkins**", (route) => route.fulfill({ json: { month: "2026-09", today: "2026-09-28", timezone: "UTC", dates: [], checked_today: false, streak: 0, total: 0 } }));

  await page.route("**/api/v1/learning/resume", (route) => route.fulfill({ json: { conversation: null, attempt: null } }));
  await page.goto(`${prefix}/login`);
  await page.getByLabel("邮箱").fill(identity.email);
  await page.getByLabel("密码").fill("correct horse battery staple");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("alert")).toBeFocused();
  await expect(page.getByLabel("邮箱")).toHaveValue(identity.email);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("heading", { name: "资料库", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "账号", exact: true }).click();
  await expect(page.getByText(`当前登录：${identity.email}`)).toBeVisible();
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page.getByRole("heading", { name: "登录学习空间" })).toBeVisible();
  expect(logoutCsrf).toBe(identity.csrf_token);
  expect(requests.length).toBeGreaterThan(0);
  expect(requests.every((path) => path.startsWith(`${prefix}/api/`))).toBe(true);
  await expect(page).toHaveURL(new RegExp(`${prefix}/login$`));
});
