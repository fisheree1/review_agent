import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { deleteDocument, getDocument, retryDocument } from "../api/documents";
import { ReaderPage } from "./ReaderPage";

vi.mock("../api/documents", async (loadOriginal) => {
  const original = await loadOriginal<typeof import("../api/documents")>();
  return { ...original, deleteDocument: vi.fn(), getDocument: vi.fn(), retryDocument: vi.fn() };
});
vi.mock("../hooks/useDocuments", () => ({
  useDocuments: () => ({
    documents: [], error: null, isLoading: false, isLoadingMore: false, nextCursor: null,
    refresh: vi.fn(), loadMore: vi.fn(),
  }),
}));
vi.mock("../components/PdfPreview", () => ({
  PdfPreview: ({ page }: { page: number }) => <div aria-label={`PDF 原文件第 ${page} 页`} />,
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
  return render(<QueryClientProvider client={queryClient}>
    <MemoryRouter initialEntries={[`/documents/${readyDocument.id}?page=1`]}>
      <Routes>
        <Route element={<ReaderPage />} path="/documents/:documentId" />
        <Route element={<p>资料库</p>} path="/" />
      </Routes>
    </MemoryRouter>
  </QueryClientProvider>);
}

describe("ReaderPage", () => {
  afterEach(() => { cleanup(); vi.clearAllMocks(); });

  beforeEach(() => {
    vi.mocked(getDocument).mockResolvedValue(readyDocument);
    vi.mocked(deleteDocument).mockResolvedValue(undefined);
    vi.mocked(retryDocument).mockResolvedValue(readyDocument);
  });

  it("opens PDF original pages directly and navigates without extracted text controls", async () => {
    renderReader();

    expect(await screen.findByLabelText("PDF 原文件第 1 页")).toBeVisible();
    expect(screen.queryByRole("button", { name: "提取文字" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "放大正文字号" })).not.toBeInTheDocument();
    fireEvent.keyDown(window, { key: "]" });
    expect(await screen.findByLabelText("PDF 原文件第 2 页")).toBeVisible();
    expect(screen.getByText("第 2 / 2 页")).toBeVisible();
  });

  it.each([
    ["Review.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "DOCX"],
    ["Slides.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "PPTX"],
  ])("shows unsupported preview for %s", async (filename, mediaType, label) => {
    vi.mocked(getDocument).mockResolvedValue({ ...readyDocument, filename, media_type: mediaType });
    renderReader();

    expect(await screen.findByRole("heading", { name: "暂不支持预览" })).toBeVisible();
    expect(screen.getByText(`${label} 文件仍可用于后台处理、资料问答和练习。`)).toBeVisible();
    expect(screen.queryByLabelText("PDF 原文件第 1 页")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "下一页" })).not.toBeInTheDocument();
  });

  it("previews a PDF while background processing is queued", async () => {
    vi.mocked(getDocument).mockResolvedValue({ ...readyDocument, status: "queued", page_count: null });
    renderReader();

    expect(await screen.findByLabelText("PDF 原文件第 1 页")).toBeVisible();
    expect(screen.getByText(/原文件可以先行预览/)).toBeVisible();
  });

  it("keeps PDF preview available after parsing fails and offers retry", async () => {
    vi.mocked(getDocument).mockResolvedValue({
      ...readyDocument, status: "failed", page_count: null,
      failure_code: "PDF_TEXT_NOT_FOUND", failure_message: "未识别到可阅读文字",
    });
    renderReader();

    expect(await screen.findByLabelText("PDF 原文件第 1 页")).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent("未识别到可阅读文字");
    await userEvent.click(screen.getByRole("button", { name: "重新处理" }));
    await waitFor(() => expect(retryDocument).toHaveBeenCalledWith(readyDocument.id));
  });

  it("shows an actionable metadata loading error", async () => {
    vi.mocked(getDocument).mockRejectedValue(new Error("无法连接资料服务"));
    renderReader();

    expect(await screen.findByRole("alert")).toHaveTextContent("无法连接资料服务");
    expect(screen.getByRole("button", { name: /重新尝试/ })).toBeEnabled();
  });

  it("requires confirmation before permanently deleting a document", async () => {
    renderReader();
    await screen.findByLabelText("PDF 原文件第 1 页");

    await userEvent.click(screen.getByRole("button", { name: "删除资料" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("永久删除这份资料");
    await userEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(deleteDocument).toHaveBeenCalledWith(readyDocument.id));
    expect(await screen.findByText("资料库")).toBeVisible();
  });
});
