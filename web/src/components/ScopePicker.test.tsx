import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { useState } from "react";
import { afterEach, expect, test } from "vitest";

import type { DocumentSummary } from "../api/documents";
import type { Collection, ScopeChoice } from "../api/learning";
import { ScopePicker } from "./ScopePicker";

afterEach(cleanup);

const document = (id: string): DocumentSummary => ({
  id, filename: `${id}.pdf`, media_type: "application/pdf", byte_size: 100,
  status: "ready", page_count: 1, content_count: 1, failure_code: null, failure_message: null,
  created_at: "2026-09-28T00:00:00Z", updated_at: "2026-09-28T00:00:00Z",
});

function Picker({ documents, collections, initial = { document_ids: [], collection_ids: [] }, selectedLabels = [] }: {
  documents: DocumentSummary[]; collections: Collection[]; initial?: ScopeChoice;
  selectedLabels?: { id: string; filename: string }[];
}) {
  const [value, onChange] = useState<ScopeChoice>(initial);
  return <MemoryRouter><ScopePicker collections={collections} documents={documents} label="资料范围" onChange={onChange} selectedLabels={selectedLabels} value={value} /></MemoryRouter>;
}

test("selection stops at five unique documents including collection members and allows deselection", async () => {
  const user = userEvent.setup();
  const documents = ["a", "b", "c", "d", "e", "f"].map(document);
  render(<Picker documents={documents} collections={[
    { id: "group", name: "基础", description: "", document_ids: ["a", "b"] },
    { id: "large", name: "超大集合", description: "", document_ids: ["a", "b", "c", "d", "e", "f"] },
  ]} />);

  expect(screen.getByRole("checkbox", { name: /超大集合/ })).toBeDisabled();
  await user.click(screen.getByRole("checkbox", { name: /基础/ }));
  for (const id of ["c", "d", "e"]) await user.click(screen.getByRole("checkbox", { name: `${id}.pdf` }));
  expect(screen.getByText(/已选 5 \/ 5 份资料/)).toBeVisible();
  expect(screen.getByRole("checkbox", { name: "f.pdf" })).toBeDisabled();
  await user.click(screen.getByRole("checkbox", { name: "e.pdf" }));
  expect(screen.getByRole("checkbox", { name: "f.pdf" })).toBeEnabled();
});

test("empty source selection links directly to PDF upload", () => {
  render(<Picker documents={[]} collections={[]} />);
  expect(screen.getByRole("link", { name: "前往资料库上传 PDF" })).toHaveAttribute("href", "/#upload-materials");
});

test("a selected document outside the current page remains visible and can be removed", async () => {
  const user = userEvent.setup();
  render(<Picker documents={[document("new")]} collections={[]} initial={{ document_ids: ["a", "b", "c", "d", "e"], collection_ids: [] }}
    selectedLabels={["a", "b", "c", "d", "e"].map((id) => ({ id, filename: `${id}.pdf` }))} />);

  expect(screen.getByRole("checkbox", { name: "new.pdf" })).toBeDisabled();
  await user.click(screen.getByRole("checkbox", { name: "a.pdf" }));
  expect(screen.getByRole("checkbox", { name: "new.pdf" })).toBeEnabled();
});
