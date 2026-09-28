import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";

import * as auth from "../api/auth";
import { AccountPage } from "./AccountPage";
import { LoginPage } from "./LoginPage";

vi.mock("../api/auth", async (original) => ({ ...(await original<typeof auth>()), getAuthConfig: vi.fn() }));
vi.mock("../components/AppHeader", () => ({ AppHeader: () => <header>Review Agent</header> }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

function show(page: React.ReactNode) {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter>{page}</MemoryRouter>
  </QueryClientProvider>);
}

test("portal login sends users to the existing portal without asking for a second password", async () => {
  vi.mocked(auth.getAuthConfig).mockResolvedValue({ signup_enabled: false, login_mode: "portal" });
  show(<LoginPage identity={null} />);
  expect(await screen.findByRole("link", { name: "使用门户账号登录" })).toHaveAttribute("href", "/login");
  expect(screen.queryByLabelText("密码")).not.toBeInTheDocument();
});

test("portal account management keeps password and logout controls in the portal", () => {
  show(<AccountPage identity={{ user_id: "owner", workspace_id: "workspace", email: "portal@example.test", csrf_token: "csrf", login_mode: "portal" }} />);
  expect(screen.getByRole("link", { name: "返回门户管理登录" })).toHaveAttribute("href", "/");
  expect(screen.queryByLabelText("当前密码")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "退出登录" })).not.toBeInTheDocument();
});
