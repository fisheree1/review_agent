import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";

import {
  type DocumentSummary,
  getDocument,
  getDocumentPages,
  isProcessing,
  retryDocument,
} from "../api/documents";
import { AppHeader } from "../components/AppHeader";
import { ErrorState } from "../components/ErrorState";
import { Icon } from "../components/Icon";
import { ReaderSettings, type ReadingWidth } from "../components/ReaderSettings";
import { ReferencePanel } from "../components/ReferencePanel";
import { ShortcutsDialog } from "../components/ShortcutsDialog";
import { StatusBadge } from "../components/StatusBadge";
import { useDocuments } from "../hooks/useDocuments";

function storedFontSize(): number {
  const value = Number(localStorage.getItem("review-agent-font-size"));
  return Number.isFinite(value) && value >= 15 && value <= 21 ? value : 17;
}

function storedReadingWidth(): ReadingWidth {
  const value = localStorage.getItem("review-agent-reading-width");
  return value === "narrow" || value === "wide" ? value : "comfortable";
}

function processingMessage(status: DocumentSummary["status"]): string {
  if (status === "uploaded") return "文件已安全保存，正在准备解析任务。";
  if (status === "queued") return "资料已进入处理队列，可以先离开此页。";
  return "正在逐页提取正文与页码，通常只需要片刻。";
}

function isInteractiveTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && Boolean(target.closest("button, a, input, select, textarea, [contenteditable='true']"));
}

export function ReaderPage() {
  const { documentId = "" } = useParams();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const { documents } = useDocuments();
  const [isNavOpen, setIsNavOpen] = useState(false);
  const [isReferencesOpen, setIsReferencesOpen] = useState(true);
  const [isShortcutsOpen, setIsShortcutsOpen] = useState(false);
  const [fontSize, setFontSize] = useState(storedFontSize);
  const [readingWidth, setReadingWidth] = useState<ReadingWidth>(storedReadingWidth);
  const requestedPage = Number(searchParams.get("page"));
  const currentPage = Number.isInteger(requestedPage) && requestedPage > 0 ? requestedPage : 1;
  const documentQuery = useQuery({
    queryKey: ["documents", documentId],
    queryFn: () => getDocument(documentId),
    enabled: Boolean(documentId),
    refetchInterval: (query) => {
      const current = query.state.data;
      return current && isProcessing(current.status) ? 1800 : false;
    },
  });
  const pagesQuery = useQuery({
    queryKey: ["documents", documentId, "pages"],
    queryFn: () => getDocumentPages(documentId),
    enabled: documentQuery.data?.status === "ready",
  });
  const retryMutation = useMutation({
    mutationFn: () => retryDocument(documentId),
    onSuccess: (nextDocument) => {
      queryClient.setQueryData(["documents", documentId], nextDocument);
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
  });
  const document = documentQuery.data ?? null;
  const pages = pagesQuery.data?.pages ?? [];
  const error = documentQuery.error ?? pagesQuery.error ?? retryMutation.error;
  const isLoading = documentQuery.isPending || (document?.status === "ready" && pagesQuery.isPending);

  useEffect(() => {
    localStorage.setItem("review-agent-font-size", String(fontSize));
  }, [fontSize]);

  useEffect(() => {
    localStorage.setItem("review-agent-reading-width", readingWidth);
  }, [readingWidth]);

  useEffect(() => {
    if (pages.length === 0 || !searchParams.has("page")) return;
    const boundedPage = Math.min(Math.max(currentPage, 1), pages.length);
    if (boundedPage !== currentPage) {
      setSearchParams({ page: String(boundedPage) }, { replace: true });
      return;
    }
    window.requestAnimationFrame(() => {
      window.document.getElementById(`page-${boundedPage}`)?.scrollIntoView({ block: "start" });
    });
  }, [currentPage, pages.length, searchParams, setSearchParams]);

  const selectPage = useCallback((pageNumber: number) => {
    const bounded = Math.min(Math.max(pageNumber, 1), Math.max(pages.length, 1));
    setSearchParams({ page: String(bounded) }, { replace: true });
    window.requestAnimationFrame(() => {
      const target = window.document.getElementById(`page-${bounded}`);
      target?.scrollIntoView({ behavior: "smooth", block: "start" });
      target?.focus({ preventScroll: true });
    });
  }, [pages.length, setSearchParams]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (isInteractiveTarget(event.target)) return;
      if (event.key === "?") {
        event.preventDefault();
        setIsShortcutsOpen(true);
      } else if (event.key.toLowerCase() === "r") {
        event.preventDefault();
        setIsReferencesOpen((value) => !value);
      } else if (event.key === "[") {
        event.preventDefault();
        selectPage(currentPage - 1);
      } else if (event.key === "]") {
        event.preventDefault();
        selectPage(currentPage + 1);
      } else if (event.key === "Escape") {
        setIsNavOpen(false);
        setIsReferencesOpen(false);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [currentPage, selectPage]);

  const selectedReference = useMemo(
    () => pages.find((page) => page.page_number === currentPage),
    [currentPage, pages],
  );

  return (
    <div className="reader-page">
      <AppHeader onMenu={() => setIsNavOpen(true)}>
        <ReaderSettings
          fontSize={fontSize}
          onFontSizeChange={setFontSize}
          onReadingWidthChange={setReadingWidth}
          readingWidth={readingWidth}
        />
        <button aria-label="查看阅读快捷键" className="icon-button" onClick={() => setIsShortcutsOpen(true)} title="阅读快捷键" type="button"><Icon name="help" /></button>
        <button
          aria-expanded={isReferencesOpen}
          aria-label="切换引用面板"
          className="icon-button"
          onClick={() => setIsReferencesOpen((value) => !value)}
          title="切换引用面板 (R)"
          type="button"
        ><Icon name="panel" /></button>
      </AppHeader>

      <div className={`reader-shell${isReferencesOpen ? " reader-shell--with-references" : ""}`}>
        {isNavOpen ? <button aria-label="关闭资料导航" className="scrim" onClick={() => setIsNavOpen(false)} type="button" /> : null}
        <aside className={`reader-nav${isNavOpen ? " reader-nav--open" : ""}`}>
          <div className="reader-nav__header">
            <span className="eyebrow">资料库</span>
            <button aria-label="关闭资料导航" className="icon-button mobile-only" onClick={() => setIsNavOpen(false)} type="button"><Icon name="close" /></button>
          </div>
          <Link className="reader-nav__back" to="/"><Icon name="chevronLeft" />返回全部资料</Link>
          <nav aria-label="资料列表" className="reader-nav__list">
            {documents.map((item) => (
              <Link
                aria-current={item.id === documentId ? "page" : undefined}
                className={item.id === documentId ? "reader-nav__item reader-nav__item--active" : "reader-nav__item"}
                key={item.id}
                onClick={() => setIsNavOpen(false)}
                to={`/documents/${item.id}`}
              >
                <Icon name="document" />
                <span>{item.filename}</span>
              </Link>
            ))}
          </nav>
        </aside>

        <main className="reader-main" id="main-content">
          {isLoading ? (
            <div aria-label="正在打开资料" className="reader-loading" role="status">
              <div /><div /><div />
            </div>
          ) : error && !document ? (
            <ErrorState message={error.message} onRetry={() => void documentQuery.refetch()} />
          ) : document && isProcessing(document.status) ? (
            <section className="processing-state">
              <span className="processing-state__orbit"><Icon name="document" /></span>
              <StatusBadge status={document.status} />
              <h1>{document.filename}</h1>
              <p>{processingMessage(document.status)}</p>
              <Link className="button button--secondary" to="/">回到资料库</Link>
            </section>
          ) : document?.status === "failed" ? (
            <ErrorState
              message={document.failure_message ?? "系统没有提取到可阅读的正文。"}
              onRetry={retryMutation.isPending ? undefined : () => retryMutation.mutate()}
              title="这份资料没有处理成功"
            />
          ) : document ? (
            <article
              className={`document-reader document-reader--${readingWidth}`}
              style={{ "--reader-font-size": `${fontSize}px` } as React.CSSProperties}
            >
              <header className="document-reader__header">
                <Link className="text-link" to="/"><Icon name="chevronLeft" />资料库</Link>
                <span className="eyebrow">{document.page_count ?? pages.length} 页 · PDF</span>
                <h1>{document.filename}</h1>
                <p>正文按原始页码保留。使用 <kbd>[</kbd> 与 <kbd>]</kbd> 可以逐页移动。</p>
              </header>
              <div className="page-stack">
                {pages.map((page) => (
                  <section
                    aria-label={`第 ${page.page_number} 页`}
                    className={`reading-page${page.page_number === currentPage ? " reading-page--current" : ""}`}
                    id={`page-${page.page_number}`}
                    key={page.page_number}
                    onClick={() => setSearchParams({ page: String(page.page_number) }, { replace: true })}
                    tabIndex={-1}
                  >
                    <div className="reading-page__number"><span>第 {page.page_number} 页</span><span>PAGE {String(page.page_number).padStart(2, "0")}</span></div>
                    <div className="reading-page__content">
                      {page.content.split(/\n{2,}/).map((paragraph, index) => (
                        paragraph.trim() ? <p key={`${page.page_number}-${index}`}>{paragraph.trim()}</p> : null
                      ))}
                    </div>
                  </section>
                ))}
              </div>
            </article>
          ) : null}
        </main>

        <ReferencePanel
          currentPage={currentPage}
          isOpen={isReferencesOpen}
          onClose={() => setIsReferencesOpen(false)}
          onSelect={(page) => { selectPage(page); if (window.innerWidth < 1100) setIsReferencesOpen(false); }}
          pages={pages}
        />
      </div>

      {document?.status === "ready" && pages.length > 0 ? (
        <div aria-live="polite" className="reader-footer">
          <button aria-label="上一页" disabled={currentPage <= 1} onClick={() => selectPage(currentPage - 1)} type="button"><Icon name="chevronLeft" /></button>
          <span>第 {currentPage} / {pages.length} 页</span>
          <button aria-label="下一页" disabled={currentPage >= pages.length} onClick={() => selectPage(currentPage + 1)} type="button"><Icon name="chevronRight" /></button>
          {selectedReference ? <span className="reader-footer__hint">已定位来源页</span> : null}
        </div>
      ) : null}
      <ShortcutsDialog isOpen={isShortcutsOpen} onClose={() => setIsShortcutsOpen(false)} />
    </div>
  );
}
