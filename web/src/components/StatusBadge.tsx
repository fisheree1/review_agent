import { type DocumentStatus } from "../api/documents";

const labels: Record<DocumentStatus, string> = {
  uploaded: "已上传",
  queued: "等待处理",
  parsing: "正在解析",
  ready: "可以阅读",
  failed: "处理失败",
  deleting: "正在删除",
};

export function StatusBadge({ status }: { status: DocumentStatus }) {
  const isBusy = status === "uploaded" || status === "queued" || status === "parsing";
  return (
    <span className={`status status--${status}`}>
      <span aria-hidden="true" className={isBusy ? "status__pulse" : "status__dot"} />
      {labels[status]}
    </span>
  );
}
