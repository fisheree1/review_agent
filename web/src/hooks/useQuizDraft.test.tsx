import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";

import * as learning from "../api/learning";
import { useQuizDraft } from "./useQuizDraft";

vi.mock("../api/learning", async (original) => ({ ...(await original<typeof learning>()), saveQuizAnswer: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

function DraftPage() {
  const draft = useQuizDraft("quiz", "attempt", 1);
  return <><button onClick={() => draft.edit("question", "中位数")} type="button">作答</button><Link to="/next">返回练习</Link></>;
}

test("navigation continues after an in-flight answer save succeeds", async () => {
  let resolveSave!: (value: { question_id: string; revision: number }) => void;
  vi.mocked(learning.saveQuizAnswer).mockImplementation(() => new Promise((resolve) => { resolveSave = resolve; }));
  render(<MemoryRouter initialEntries={["/attempt"]}><Routes>
    <Route path="/attempt" element={<DraftPage />} />
    <Route path="/next" element={<p>练习列表</p>} />
  </Routes></MemoryRouter>);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "作答" }));
  await user.click(screen.getByRole("link", { name: "返回练习" }));
  expect(screen.queryByText("练习列表")).not.toBeInTheDocument();
  expect(learning.saveQuizAnswer).toHaveBeenCalledWith("quiz", "attempt", "question", "中位数", 1);
  resolveSave({ question_id: "question", revision: 2 });
  expect(await screen.findByText("练习列表")).toBeVisible();
  await waitFor(() => expect(learning.saveQuizAnswer).toHaveBeenCalledTimes(1));
});

test("failed answer save keeps the user on the draft", async () => {
  vi.mocked(learning.saveQuizAnswer).mockRejectedValue(new Error("保存失败"));
  render(<MemoryRouter initialEntries={["/attempt"]}><Routes>
    <Route path="/attempt" element={<DraftPage />} />
    <Route path="/next" element={<p>练习列表</p>} />
  </Routes></MemoryRouter>);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "作答" }));
  await user.click(screen.getByRole("link", { name: "返回练习" }));
  await waitFor(() => expect(learning.saveQuizAnswer).toHaveBeenCalledTimes(1));
  expect(screen.queryByText("练习列表")).not.toBeInTheDocument();
});
