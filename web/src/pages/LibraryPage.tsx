import { useNavigate } from "react-router-dom";

import { AppHeader } from "../components/AppHeader";
import { DocumentList } from "../components/DocumentList";
import { ErrorState } from "../components/ErrorState";
import { UploadPanel } from "../components/UploadPanel";
import { useDocuments } from "../hooks/useDocuments";

export function LibraryPage() {
  const navigate = useNavigate();
  const { documents, error, isLoading, isLoadingMore, nextCursor, refresh, loadMore } = useDocuments();

  return (
    <div className="app-page">
      <AppHeader />
      <main className="library" id="main-content">
        <UploadPanel onUploaded={(documentId) => navigate(`/documents/${documentId}`)} />
        <section aria-labelledby="library-title" className="library-section">
          <div className="section-heading">
            <div>
              <span className="eyebrow">最近资料</span>
              <h2 id="library-title">你的阅读空间</h2>
            </div>
            <p>{documents.length > 0 ? `${documents.length} 份资料` : "资料会按最近上传排序"}</p>
          </div>
          {error && documents.length === 0 ? (
            <ErrorState message={error.message} onRetry={() => void refresh()} />
          ) : (
            <DocumentList
              documents={documents}
              hasMore={nextCursor !== null}
              isLoading={isLoading}
              isLoadingMore={isLoadingMore}
              onLoadMore={() => void loadMore()}
            />
          )}
          {error && documents.length > 0 ? (
            <p className="inline-notice" role="status">资料状态暂时未能刷新：{error.message}</p>
          ) : null}
        </section>
      </main>
    </div>
  );
}
