import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test } from "vitest";

import { SourcePreview } from "./SourcePreview";

afterEach(cleanup);

test("shows the saved citation quote without requesting extracted document text", async () => {
  render(<MemoryRouter><SourcePreview source={{
    source_id: "source", document_id: "document", version_id: 7, unit: 2,
    quote: "The median resists outliers.",
    locator: { kind: "page", position: 2, title: null, path: [] },
  }} /></MemoryRouter>);

  expect(screen.getByRole("link")).toHaveAttribute("href", "/documents/document?unit=2&version=7");
  await userEvent.click(screen.getByText("预览引用摘录"));
  expect(screen.getByText("The median resists outliers.")).toBeVisible();
  expect(screen.queryByText(/Before|After/)).not.toBeInTheDocument();
});
