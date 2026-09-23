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
    await user.click(screen.getByRole("button", { name: "开始上传 1 份" }));
    expect(input).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "停止批量上传" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("请刷新资料列表确认");
    expect(input).toBeEnabled();
  });

  it("uploads several files and retries only the failed file with its original request key", async () => {
    vi.mocked(uploadDocument).mockReset();
    vi.mocked(uploadDocument)
      .mockResolvedValueOnce({ id: "first" } as Awaited<ReturnType<typeof uploadDocument>>)
      .mockRejectedValueOnce(new ApiError("服务暂时不可用", "PROVIDER_UNAVAILABLE"))
      .mockResolvedValueOnce({ id: "second" } as Awaited<ReturnType<typeof uploadDocument>>);
    const onUploaded = vi.fn();
    const user = userEvent.setup();
    const { container } = render(<UploadPanel onUploaded={onUploaded} />);
    const input = container.querySelector<HTMLInputElement>("input[type='file']")!;
    await user.upload(input, [
      new File(["%PDF-1.7"], "one.pdf", { type: "application/pdf" }),
      new File(["%PDF-1.7"], "two.pdf", { type: "application/pdf" }),
    ]);
    await user.click(screen.getByRole("button", { name: "开始上传 2 份" }));
    expect(await screen.findByText("已提交 1 份资料，解析会在后台继续。")).toBeVisible();
    expect(await screen.findByRole("button", { name: "继续上传剩余 1 份" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "继续上传剩余 1 份" }));
    expect(await screen.findByText("已提交 2 份资料，解析会在后台继续。")).toBeVisible();
    expect(onUploaded).toHaveBeenCalledTimes(2);
    expect(vi.mocked(uploadDocument).mock.calls[1][3]).toBe(vi.mocked(uploadDocument).mock.calls[2][3]);
    expect(vi.mocked(uploadDocument).mock.calls[0][3]).not.toBe(vi.mocked(uploadDocument).mock.calls[1][3]);
  });
});
