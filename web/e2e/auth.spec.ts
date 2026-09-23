import { expect, test } from "@playwright/test";

test("sign in and sign out with a protected workspace", async ({ page }) => {
  const identity = {
    user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    workspace_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    email: "learner@example.test",
    csrf_token: "csrf-e2e",
  };
  let signedIn = false;
  let logoutCsrf = "";
  await page.route("**/api/v1/auth/config", (route) => route.fulfill({ json: { signup_enabled: false } }));
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({
    status: signedIn ? 200 : 401,
    json: signedIn ? identity : { error: { code: "AUTHENTICATION_REQUIRED", message: "请登录" } },
  }));
  await page.route("**/api/v1/auth/login", (route) => {
    signedIn = true;
    return route.fulfill({ json: identity, headers: { "set-cookie": "review_agent_session=opaque; HttpOnly; SameSite=Strict; Path=/" } });
  });
  await page.route("**/api/v1/auth/logout", (route) => {
    logoutCsrf = route.request().headers()["x-csrf-token"] ?? "";
    signedIn = false;
    return route.fulfill({ status: 204 });
  });
  await page.route("**/api/v1/documents**", (route) => route.fulfill({ json: { items: [], next_cursor: null } }));

  await page.goto("/login");
  await page.getByLabel("邮箱").fill(identity.email);
  await page.getByLabel("密码").fill("correct horse battery staple");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByText("你的阅读空间")).toBeVisible();
  await page.getByRole("link", { name: "账号", exact: true }).click();
  await expect(page.getByText(`当前登录：${identity.email}`)).toBeVisible();
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page.getByRole("heading", { name: "登录学习空间" })).toBeVisible();
  expect(logoutCsrf).toBe(identity.csrf_token);
});
