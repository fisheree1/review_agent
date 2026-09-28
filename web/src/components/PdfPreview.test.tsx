import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { getDocument } from "pdfjs-dist";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { getOriginalPdf } from "../api/documents";
import { PdfPreview } from "./PdfPreview";

vi.mock("../api/documents", () => ({ getOriginalPdf: vi.fn() }));
vi.mock("pdfjs-dist", () => ({ GlobalWorkerOptions: { workerSrc: "" }, getDocument: vi.fn() }));

beforeEach(() => {
  vi.mocked(getOriginalPdf).mockReset();
  vi.mocked(getDocument).mockReset();
  vi.stubGlobal("ResizeObserver", class {
    observe() { /* width is read when the observer starts */ }
    disconnect() { /* no native observer in this test */ }
  });
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(600);
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({} as CanvasRenderingContext2D);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("fetches the authorized PDF once and renders the selected page", async () => {
  const renderPage = vi.fn(() => ({ promise: Promise.resolve(), cancel: vi.fn() }));
  const getPage = vi.fn(async () => ({
    getViewport: ({ scale }: { scale: number }) => ({ width: 400 * scale, height: 600 * scale }),
    render: renderPage,
  }));
  const onPageCount = vi.fn();
  vi.mocked(getOriginalPdf).mockResolvedValue(new Uint8Array([37, 80, 68, 70]));
  vi.mocked(getDocument).mockReturnValue({
    promise: Promise.resolve({ getPage, numPages: 2 }), destroy: vi.fn(),
  } as unknown as ReturnType<typeof getDocument>);

  const { rerender } = render(<PdfPreview documentId="document-1" onPageCount={onPageCount} page={1} />);
  await waitFor(() => expect(getPage).toHaveBeenCalledWith(1));
  await waitFor(() => expect(renderPage).toHaveBeenCalledTimes(1));
  expect(onPageCount).toHaveBeenCalledWith(2);

  rerender(<PdfPreview documentId="document-1" onPageCount={onPageCount} page={2} />);
  await waitFor(() => expect(getPage).toHaveBeenCalledWith(2));
  expect(getOriginalPdf).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("img", { name: "PDF 原文件第 2 页" })).toBeVisible();
});

it("offers a retry when the private PDF cannot be fetched", async () => {
  const getPage = vi.fn(async () => ({
    getViewport: ({ scale }: { scale: number }) => ({ width: 400 * scale, height: 600 * scale }),
    render: () => ({ promise: Promise.resolve(), cancel: vi.fn() }),
  }));
  vi.mocked(getOriginalPdf)
    .mockRejectedValueOnce(new Error("原文件暂不可用"))
    .mockResolvedValue(new Uint8Array([37, 80, 68, 70]));
  vi.mocked(getDocument).mockReturnValue({
    promise: Promise.resolve({ getPage }), destroy: vi.fn(),
  } as unknown as ReturnType<typeof getDocument>);

  render(<PdfPreview documentId="document-1" page={1} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("原文件暂不可用");
  await userEvent.click(screen.getByRole("button", { name: "重试预览" }));

  await waitFor(() => expect(getPage).toHaveBeenCalledWith(1));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("shows consecutive pages and only draws pages near the viewport", async () => {
  const observed = new Map<string, IntersectionObserverCallback>();
  vi.stubGlobal("IntersectionObserver", class {
    constructor(private callback: IntersectionObserverCallback) { /* observer callbacks are controlled by the test */ }
    observe(element: Element) { observed.set(element.id, this.callback); }
    disconnect() { /* no native observer in this test */ }
  });
  Object.defineProperty(Element.prototype, "scrollIntoView", { configurable: true, value: vi.fn() });
  const renderPage = vi.fn(() => ({ promise: Promise.resolve(), cancel: vi.fn() }));
  const getPage = vi.fn(async () => ({
    getViewport: ({ scale }: { scale: number }) => ({ width: 400 * scale, height: 600 * scale }),
    render: renderPage,
  }));
  vi.mocked(getOriginalPdf).mockResolvedValue(new Uint8Array([37, 80, 68, 70]));
  vi.mocked(getDocument).mockReturnValue({ promise: Promise.resolve({ getPage, numPages: 3 }), destroy: vi.fn() } as unknown as ReturnType<typeof getDocument>);

  render(<PdfPreview continuous documentId="document-1" page={1} />);
  await waitFor(() => expect(screen.getByRole("region", { name: "第 3 页" })).toBeInTheDocument());
  expect(getPage).not.toHaveBeenCalledWith(2);
  observed.get("content-2")?.([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver);
  await waitFor(() => expect(getPage).toHaveBeenCalledWith(2));
  expect(getOriginalPdf).toHaveBeenCalledTimes(1);
});
