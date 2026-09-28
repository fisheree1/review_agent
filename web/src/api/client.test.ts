import { afterEach, expect, test, vi } from "vitest";

import { apiFetch, apiUrl, setCsrfToken } from "./client";

afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); setCsrfToken(null); });

test("subpath deployment keeps authentication and API calls inside the project prefix", async () => {
  vi.stubEnv("BASE_URL", "/review/");
  const fetch = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  setCsrfToken("csrf-fixture");
  await apiFetch("/api/v1/collections", { method: "POST" });
  expect(fetch.mock.calls[0][0]).toBe("/review/api/v1/collections");
  expect(fetch.mock.calls[0][1].credentials).toBe("same-origin");
  expect(fetch.mock.calls[0][1].headers.get("X-CSRF-Token")).toBe("csrf-fixture");
  expect(apiUrl("/api/v1/auth/config")).toBe("/review/api/v1/auth/config");
});

test("root deployment retains its existing API paths", () => {
  vi.stubEnv("BASE_URL", "/");
  expect(apiUrl("/api/v1/documents")).toBe("/api/v1/documents");
});
