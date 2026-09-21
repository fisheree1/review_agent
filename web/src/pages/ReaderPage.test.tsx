import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { deleteDocument, getDocument, getDocumentContent } from "../api/documents";
import { ReaderPage } from "./ReaderPage";

vi.mock("../api/documents", async (loadOriginal) => {
  const original = await loadOriginal<typeof import("../api/documents")>();
  return {
    ...original,
    deleteDocument: vi.fn(),
    getDocument: vi.fn(),
    getDocumentContent: vi.fn(),
    retryDocument: vi.fn(),
  };
});

vi.mock("../hooks/useDocuments", () => ({
  useDocuments: () => ({
    documents: [], error: null, isLoading: false, isLoadingMore: false, nextCursor: null,
    refresh: vi.fn(), loadMore: vi.fn(),
  }),
}));

const readyDocument = {
  id: "11111111-1111-4111-8111-111111111111",
  filename: "Data Profiling.pdf",
  media_type: "application/pdf",
  byte_size: 1024,
  status: "ready" as const,
  page_count: 2,
  content_count: 2,
  failure_code: null,
  failure_message: null,
  created_at: "2026-09-19T02:00:00Z",
  updated_at: "2026-09-19T02:00:00Z",
};

function renderReader() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/documents/${readyDocument.id}?page=1`]}>
        <Routes>
          <Route element={<ReaderPage />} path="/documents/:documentId" />
          <Route element={<p>资料库</p>} path="/" />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ReaderPage", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    localStorage.clear();
    vi.mocked(getDocument).mockResolvedValue(readyDocument);
    vi.mocked(getDocumentContent).mockImplementation(async (_documentId, ordinal) => ({
      document_id: readyDocument.id,
      content_count: 2,
      contents: [ordinal === 2
        ? {
            ordinal: 2,
            content: "Page two cited content",
            citation_locator: { kind: "page", position: 2, title: null, path: [] },
          }
        : {
            ordinal: 1,
            content: "Page one learning content",
            citation_locator: { kind: "page", position: 1, title: null, path: [] },
          }],
      locations: [
        { ordinal: 1, citation_locator: { kind: "page", position: 1, title: null, path: [] } },
        { ordinal: 2, citation_locator: { kind: "page", position: 2, title: null, path: [] } },
      ],
    }));
    vi.mocked(deleteDocument).mockResolvedValue(undefined);
  });

  it("shows readable pages and supports keyboard page navigation", async () => {
    renderReader();

    const firstPage = await screen.findByLabelText("第 1 页");
    expect(within(firstPage).getByText("Page one learning content")).toBeVisible();
    fireEvent.keyDown(window, { key: "]" });

    const secondPage = await screen.findByLabelText("第 2 页");
    await waitFor(() => expect(secondPage).toHaveClass("reading-page--current"));
    expect(within(secondPage).getByText("Page two cited content")).toBeVisible();
  });

  it("shows a DOCX heading path as the source locator", async () => {
    vi.mocked(getDocument).mockResolvedValue({
      ...readyDocument,
      filename: "Review.docx",
      media_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });
    vi.mocked(getDocumentContent).mockResolvedValue({
      document_id: readyDocument.id,
      content_count: 2,
      contents: [{
        ordinal: 1,
        content: "Evidence under the heading.",
        citation_locator: {
          kind: "heading",
          position: 1,
          title: "Detail",
          path: ["Core idea", "Detail"],
        },
      }],
      locations: [
        {
          ordinal: 1,
          citation_locator: {
            kind: "heading",
            position: 1,
            title: "Core idea",
            path: ["Core idea"],
          },
        },
        {
          ordinal: 2,
          citation_locator: {
            kind: "heading",
            position: 2,
            title: "Detail",
            path: ["Core idea", "Detail"],
          },
        },
      ],
    });
    renderReader();

    const section = await screen.findByLabelText("Core idea › Detail");
    expect(within(section).getByText("Evidence under the heading.")).toBeVisible();
    expect(screen.getByText(/2 个章节 · DOCX/)).toBeVisible();
    expect(screen.getByRole("button", { name: /Core idea › Detail.*打开此处/ })).toBeVisible();
  });

  it("persists an accessible reader font size", async () => {
    const user = userEvent.setup();
    renderReader();
    const firstPage = await screen.findByLabelText("第 1 页");
    expect(within(firstPage).getByText("Page one learning content")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "放大正文字号" }));

    expect(screen.getByText("18px")).toBeVisible();
    expect(localStorage.getItem("review-agent-font-size")).toBe("18");
  });

  it("shows an actionable loading error", async () => {
    vi.mocked(getDocument).mockRejectedValue(new Error("无法连接资料服务"));
    renderReader();

    expect(await screen.findByRole("alert")).toHaveTextContent("无法连接资料服务");
    expect(screen.getByRole("button", { name: /重新尝试/ })).toBeEnabled();
  });

  it("explains a parsing failure and offers retry", async () => {
    vi.mocked(getDocument).mockResolvedValue({
      ...readyDocument,
      status: "failed",
      page_count: null,
      content_count: null,
      failure_code: "PDF_TEXT_NOT_FOUND",
      failure_message: "没有提取到可阅读文字，这可能是一份扫描版 PDF。",
    });
    renderReader();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("没有提取到可阅读文字");
    expect(screen.getByRole("button", { name: /重新尝试/ })).toBeEnabled();
  });

  it("requires confirmation before permanently deleting a document", async () => {
    const user = userEvent.setup();
    renderReader();
    const firstPage = await screen.findByLabelText("第 1 页");
    expect(within(firstPage).getByText("Page one learning content")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "删除资料" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("永久删除这份资料");

    await user.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(deleteDocument).toHaveBeenCalledWith(readyDocument.id));
    expect(await screen.findByText("资料库")).toBeVisible();
  });
});
