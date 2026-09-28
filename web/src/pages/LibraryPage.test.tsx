import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";

import * as documents from "../api/documents";
import * as learning from "../api/learning";
import { LibraryPage } from "./LibraryPage";

vi.mock("../api/documents", async (original) => ({ ...(await original<typeof documents>()), listDocuments: vi.fn() }));
vi.mock("../api/learning", async (original) => ({ ...(await original<typeof learning>()), listCollections: vi.fn(), getLearningResume: vi.fn(), createCollection: vi.fn(), getStudyCalendar: vi.fn(), checkinToday: vi.fn() }));
vi.mock("../components/AppHeader", () => ({ AppHeader: () => <header>Review Agent</header> }));
vi.mock("../components/UploadPanel", () => ({ UploadPanel: () => <section id="upload-materials" tabIndex={-1}>上传学习资料</section> }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const fixture = (id: string, filename: string, status: documents.DocumentStatus): documents.DocumentSummary => ({
  id, filename, status, media_type: "application/pdf", byte_size: 100, page_count: 1, content_count: 1,
  created_at: "2026-09-23T00:00:00Z", updated_at: "2026-09-23T00:00:00Z", failure_code: null, failure_message: null,
});
function show(entry = "/") {
  vi.mocked(learning.listCollections).mockResolvedValue([]);
  vi.mocked(learning.getLearningResume).mockResolvedValue({ conversation: { id: "conversation", title: "统计复习", working: true }, attempt: { id: "attempt", quiz_id: "quiz", title: "统计练习", status: "in_progress" } });
  vi.mocked(learning.getStudyCalendar).mockResolvedValue({ month: "2026-09", today: "2026-09-28", timezone: "Asia/Singapore", dates: [], checked_today: false, streak: 0, total: 0 });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
    <MemoryRouter initialEntries={[entry]}><LibraryPage /></MemoryRouter>
  </QueryClientProvider>);
}

test("upload deep link focuses the first task", async () => {
  vi.mocked(documents.listDocuments).mockResolvedValue({ items: [], next_cursor: null });
  show("/#upload-materials");
  expect(screen.getByText("上传学习资料")).toHaveFocus();
});

test("search requests all matching documents from the server and resets pagination when cleared", async () => {
  vi.mocked(documents.listDocuments).mockImplementation(async (cursor, filters) => ({
    items: filters?.search ? filters.status === "processing" ? [] : [fixture("beyond", "Beyond first page.pdf", "ready")]
      : cursor ? [fixture("later", "Later.pdf", "ready")] : [fixture("ready", "Statistics.pdf", "ready")],
    next_cursor: filters?.search || cursor ? null : "next",
  }));
  const user = userEvent.setup(); show();
  await screen.findByRole("heading", { name: "Statistics.pdf" });
  await user.type(screen.getByRole("searchbox", { name: "搜索全部资料" }), "Beyond");
  expect(await screen.findByRole("heading", { name: "Beyond first page.pdf" })).toBeVisible();
  expect(documents.listDocuments).toHaveBeenCalledWith(undefined, expect.objectContaining({ search: "Beyond" }));
  const status = screen.getByRole("button", { name: /处理状态/ });
  await user.click(status);
  await user.keyboard("{Escape}");
  expect(status).toHaveFocus();
  expect(status).toHaveAttribute("aria-expanded", "false");
  await user.click(status);
  await user.click(screen.getByRole("menuitemradio", { name: "处理中" }));
  expect(await screen.findByRole("heading", { name: "没有匹配的资料" })).toBeVisible();
  await user.click(screen.getByRole("button", { name: "清除筛选" }));
  await screen.findByRole("heading", { name: "Statistics.pdf" });
  await user.click(screen.getByRole("button", { name: "加载更多资料" }));
  expect(await screen.findByRole("heading", { name: "Later.pdf" })).toBeVisible();
});

test("continue links use server identities and batch collection failure retains selected documents for retry", async () => {
  vi.mocked(documents.listDocuments).mockResolvedValue({ items: [fixture("ready", "Statistics.pdf", "ready")], next_cursor: null });
  vi.mocked(learning.createCollection).mockRejectedValueOnce(new Error("暂时无法保存")).mockResolvedValue({ id: "collection", name: "复习资料", description: "", document_ids: ["ready"] });
  const user = userEvent.setup(); show();
  expect(await screen.findByRole("link", { name: /继续学习任务/ })).toHaveAttribute("href", "/study/conversation");
  expect(screen.getByRole("link", { name: /继续作答/ })).toHaveAttribute("href", "/quizzes/quiz/attempts/attempt");
  await user.click(screen.getByText("更多筛选与批量操作"));
  await user.click(screen.getByRole("button", { name: "批量选择" }));
  await user.click(screen.getByRole("checkbox", { name: "选择 Statistics.pdf" }));
  await user.type(screen.getByLabelText("新集合名称"), "复习资料");
  await user.click(screen.getByRole("button", { name: "将所选资料建为集合" }));
  expect(await screen.findByText(/选择已保留/)).toBeVisible();
  expect(screen.getByRole("checkbox")).toBeChecked();
  await user.click(screen.getByRole("button", { name: "将所选资料建为集合" }));
  await waitFor(() => expect(learning.createCollection).toHaveBeenLastCalledWith("复习资料", "", ["ready"]));
  expect(await screen.findByText("集合已建立，可在学习空间选择它。")).toBeVisible();
});
