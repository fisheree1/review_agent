import { Link } from "react-router-dom";

import { type DocumentSummary } from "../api/documents";
import { contentCountLabel, documentTypeLabel } from "../citations";
import { Icon } from "./Icon";
import { StatusBadge } from "./StatusBadge";

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${new Intl.NumberFormat("zh-CN").format(Math.max(1, Math.round(bytes / 1024)))}\u00a0KB`;
  return `${new Intl.NumberFormat("zh-CN", { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(bytes / 1024 / 1024)}\u00a0MB`;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

interface DocumentListProps {
  documents: DocumentSummary[];
  isLoading: boolean;
  isLoadingMore: boolean;
  hasMore: boolean;
  onLoadMore: () => void;
  selectedIds?: string[];
  selectionDisabled?: boolean;
  onToggleSelection?: (id: string) => void;
  emptyTitle?: string;
  emptyDescription?: string;
}

export function DocumentList({
  documents,
  isLoading,
  isLoadingMore,
  hasMore,
  onLoadMore,
  selectedIds, selectionDisabled, onToggleSelection,
  emptyTitle = "资料库还是空的",
  emptyDescription = "上传第一份 PDF、DOCX 或 PPTX，解析完成后即可按来源位置阅读。",
}: DocumentListProps) {
  if (isLoading) {
    return (
      <div aria-label="正在加载资料" className="document-grid" role="status">
        {[0, 1, 2].map((item) => <div className="document-card document-card--skeleton" key={item} />)}
      </div>
    );
  }

  if (documents.length === 0) {
    return (
      <div className="empty-state">
        <span><Icon name="library" /></span>
        <h3>{emptyTitle}</h3>
        <p>{emptyDescription}</p>
      </div>
    );
  }

  return (
    <>
      <div className={`document-grid${documents.length > 50 ? " document-grid--large" : ""}`}>
        {documents.map((document) => {
          const canOpen = document.status === "ready" || document.status === "failed";
          return (
            <article className={`document-card${selectedIds ? " document-card--selecting" : ""}`} key={document.id}>
              {selectedIds ? <label className="document-selection"><input aria-label={`选择 ${document.filename}`} type="checkbox" checked={selectedIds.includes(document.id)} disabled={selectionDisabled || (selectedIds.length >= 100 && !selectedIds.includes(document.id))} onChange={() => onToggleSelection?.(document.id)} /></label> : null}
              <div className="document-card__body">
                <div className="document-card__topline">
                  <StatusBadge status={document.status} />
                  <time dateTime={document.updated_at}>{formatDate(document.updated_at)}</time>
                </div>
                <h3>{document.filename}</h3>
                <p>
                  {formatBytes(document.byte_size)}
                  <span aria-hidden="true"> · </span>
                  {contentCountLabel(document)}
                  <span aria-hidden="true"> · </span>
                  {documentTypeLabel(document.media_type)}
                </p>
                {document.status === "failed" ? (
                  <p className="document-card__error">{document.failure_message ?? "处理失败，请稍后重试"}</p>
                ) : null}
              </div>
              {canOpen ? (
                <Link aria-label={`${document.status === "ready" ? "阅读" : "查看失败原因"} ${document.filename}`} className="document-card__link" to={`/documents/${document.id}`}>
                  <span>{document.status === "ready" ? "继续阅读" : "查看并重试"}</span><Icon name="chevronRight" />
                </Link>
              ) : (
                <span className="document-card__wait">后台处理中</span>
              )}
            </article>
          );
        })}
      </div>
      {hasMore ? (
        <div className="load-more">
          <button className="button button--secondary" disabled={isLoadingMore} onClick={onLoadMore} type="button">
            {isLoadingMore ? "正在加载…" : "加载更多资料"}
          </button>
        </div>
      ) : null}
    </>
  );
}
