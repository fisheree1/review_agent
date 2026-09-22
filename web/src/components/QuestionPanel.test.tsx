import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, vi, test, expect } from "vitest";
import * as rag from "../api/rag";
import { QuestionPanel } from "./QuestionPanel";

vi.mock("../api/rag", async (importOriginal) => ({ ...(await importOriginal<typeof rag>()), getIndex: vi.fn(), getQuestions: vi.fn(), askQuestion: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

test("ready document accepts a keyboard-submitted question and exposes its source", async () => {
  vi.mocked(rag.getIndex).mockResolvedValue({ status: "ready", completed: 2, total: 2, failure_message: null });
  const citation = { source_id: "source", unit: 2, quote: "The median is robust.", locator: { kind: "page" as const, position: 2, title: null, path: [] } };
  vi.mocked(rag.getQuestions).mockResolvedValue([{ id: "q", question: "Why median?", version: 1, status: "answered", failure_message: null, answer: { insufficient_evidence: false, claims: [{ text: "Median is robust.", citations: [citation] }] } }]);
  const onCitation = vi.fn();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}><QuestionPanel documentId="doc" filename="sample.pdf" onCitation={onCitation} /></QueryClientProvider>);
  fireEvent.click(await screen.findByRole("button", { name: /查看引用/ }));
  expect(onCitation).toHaveBeenCalledWith(citation);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Explain median" } });
  fireEvent.submit(screen.getByRole("textbox").closest("form")!);
  await waitFor(() => expect(rag.askQuestion).toHaveBeenCalledWith("doc", "Explain median", expect.any(String)));
});

test("failed index explains recovery and disables question submission", async () => {
  vi.mocked(rag.getIndex).mockResolvedValue({ status: "failed", completed: 16, total: 20, failure_message: "额度不足" });
  vi.mocked(rag.getQuestions).mockResolvedValue([]);
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><QuestionPanel documentId="doc" filename="sample.pdf" onCitation={() => {}} /></QueryClientProvider>);
  expect(await screen.findByText("额度不足")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重试建立索引" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "提问" })).toBeDisabled();
});
