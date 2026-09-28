import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { PageLoadBoundary } from "./PageLoadBoundary";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("page loading failure offers recovery without showing internal error details", () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  function FailedPage(): never { throw new Error("private chunk diagnostics"); }
  render(<PageLoadBoundary><FailedPage /></PageLoadBoundary>);
  expect(screen.getByRole("alert")).toHaveTextContent("已保存的资料和学习记录不会受影响");
  expect(screen.getByRole("button", { name: "重新加载页面" })).toBeEnabled();
  expect(screen.queryByText(/private chunk diagnostics/)).not.toBeInTheDocument();
});
