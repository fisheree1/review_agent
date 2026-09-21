import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, uploadDocument } from "../api/documents";
import { UploadPanel } from "./UploadPanel";

vi.mock("../api/documents", async (loadOriginal) => {
  const original = await loadOriginal<typeof import("../api/documents")>();
  return { ...original, uploadDocument: vi.fn() };
});

describe("UploadPanel", () => {
  afterEach(() => cleanup());

  it("cancels an active upload without promising the server discarded it", async () => {
    vi.mocked(uploadDocument).mockImplementation((_file, _onProgress, signal) => (
      new Promise((_resolve, reject) => {
        signal?.addEventListener("abort", () => {
          reject(new ApiError(
            "已停止等待上传结果；如果文件刚好上传完成，请刷新资料列表确认。",
            "UPLOAD_CANCELLED",
          ));
        }, { once: true });
      })
    ));
    const user = userEvent.setup();
    const { container } = render(<UploadPanel onUploaded={vi.fn()} />);
    const input = container.querySelector<HTMLInputElement>("input[type='file']");
    expect(input).not.toBeNull();

    await user.upload(input!, new File(["%PDF-1.7"], "notes.pdf", { type: "application/pdf" }));
    await user.click(screen.getByRole("button", { name: "开始上传" }));
    expect(input).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "取消上传" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("请刷新资料列表确认");
    expect(input).toBeEnabled();
  });
});
