import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";

import { getDocument, type DocumentSummary } from "../api/documents";
import type { StudySection } from "./StudyAnswer";
import { StudySourcePanel } from "./StudySourcePanel";

vi.mock("../api/documents", () => ({ getDocument: vi.fn() }));
vi.mock("./PdfPreview", () => ({ PdfPreview: ({ page }: { page: number }) => <div>PDF 第 {page} 页</div> }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const section: StudySection = { key: "one", title: "中位数", paragraphs: [], sources: [1, 2].map((unit) => ({
  filename: "stats.pdf", citation: { source_id: `source-${unit}`, document_id: "doc", version_id: 7, unit,
    quote: "The median resists outliers", locator: { kind: "page", position: unit, title: null, path: [] } },
})) };
function show(value?: StudySection) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><StudySourcePanel section={value} mobileOpen={false} onClose={vi.fn()} /></MemoryRouter>
  </QueryClientProvider>);
}

test("empty panel makes no document request before an answer exists", () => {
  show();
  expect(screen.getByText("讲解生成后，在这里对照 PDF 原始页面。")).toBeVisible();
  expect(getDocument).not.toHaveBeenCalled();
});

test("unavailable original can be retried without losing its associated pages", async () => {
  vi.mocked(getDocument).mockRejectedValueOnce(new Error("Unavailable"))
    .mockResolvedValue({ id: "doc", media_type: "application/pdf" } as DocumentSummary);
  show(section);
  await userEvent.click(await screen.findByRole("button", { name: "重试打开原文" }));
  expect(await screen.findByText("PDF 第 1 页")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "stats.pdf · 第 2 页" }));
  expect(screen.getByText("PDF 第 2 页")).toBeVisible();
  expect(screen.getByRole("link", { name: "在阅读页打开" })).toHaveAttribute("href", "/documents/doc?unit=2&version=7");
});

test("historical non-PDF sources keep their excerpt and explain preview availability", async () => {
  vi.mocked(getDocument).mockResolvedValue({ id: "doc", media_type: "text/plain" } as DocumentSummary);
  show({ ...section, sources: section.sources.slice(0, 1) });
  expect(await screen.findByText("这份旧资料暂不支持预览。")).toBeVisible();
  await userEvent.click(screen.getByText("引用摘录"));
  expect(screen.getByText("The median resists outliers")).toBeVisible();
});
