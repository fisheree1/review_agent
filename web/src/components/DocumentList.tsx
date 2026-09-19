import { Link } from "react-router-dom";

import { type DocumentSummary } from "../api/documents";
import { Icon } from "./Icon";
import { StatusBadge } from "./StatusBadge";

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
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
}

export function DocumentList({
  documents,
  isLoading,
  isLoadingMore,
  hasMore,
  onLoadMore,
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
        <h3>资料库还是空的</h3>
        <p>上传第一份文本型 PDF，解析完成后就能按页阅读。</p>
      </div>
    );
  }

  return (
    <>
      <div className="document-grid">
        {documents.map((document) => {
          const canOpen = document.status === "ready" || document.status === "failed";
          return (
            <article className="document-card" key={document.id}>
              <div className="document-card__icon"><Icon name="document" /></div>
              <div className="document-card__body">
                <div className="document-card__topline">
                  <StatusBadge status={document.status} />
                  <span>{formatDate(document.updated_at)}</span>
                </div>
                <h3>{document.filename}</h3>
                <p>
                  {formatBytes(document.byte_size)}
                  <span aria-hidden="true"> · </span>
                  {document.page_count ? `${document.page_count} 页` : "正在确认页数"}
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
