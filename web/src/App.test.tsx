import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";

import { App } from "./App";
import { getCsrfToken, setCsrfToken } from "./api/client";

vi.mock("./api/auth", () => ({ getCurrentUser: vi.fn(() => new Promise(() => undefined)) }));
vi.mock("./pages/LibraryPage", () => ({ LibraryPage: () => <p>私人资料页</p> }));
vi.mock("./pages/LoginPage", () => ({ LoginPage: () => <p>登录页</p> }));
afterEach(() => { cleanup(); setCsrfToken(null); vi.clearAllMocks(); });

test("session expiry clears private cache before another account can use the same tab", async () => {
  const cache = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  cache.setQueryData(["auth-me"], { user_id: "first", workspace_id: "first-workspace", email: "first@example.test", csrf_token: "first-token" });
  cache.setQueryData(["documents"], { pages: [{ items: [{ filename: "private.pdf" }] }] });
  cache.setQueryData(["conversation", "old"], { messages: [{ question: "private question" }] });
  setCsrfToken("first-token");
  render(<QueryClientProvider client={cache}><MemoryRouter initialEntries={["/"]}><App /></MemoryRouter></QueryClientProvider>);
  expect(await screen.findByText("私人资料页")).toBeVisible();
  act(() => window.dispatchEvent(new Event("review-agent-auth-expired")));
  await waitFor(() => expect(cache.getQueryData(["auth-me"])).toBeNull());
  expect(cache.getQueryData(["documents"])).toBeUndefined();
  expect(cache.getQueryData(["conversation", "old"])).toBeUndefined();
  expect(getCsrfToken()).toBeNull();
});
