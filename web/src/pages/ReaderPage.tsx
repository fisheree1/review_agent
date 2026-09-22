import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import {
  type DocumentSummary,
  deleteDocument,
  getDocumentContent,
  getDocument,
  isProcessing,
  retryDocument,
} from "../api/documents";
import { citationLabel, contentCountLabel, documentTypeLabel } from "../citations";
import { type Citation } from "../api/rag";
import { QuestionPanel } from "../components/QuestionPanel";
import { AppHeader } from "../components/AppHeader";
import { DeleteDocumentDialog } from "../components/DeleteDocumentDialog";
import { ErrorState } from "../components/ErrorState";
import { Icon } from "../components/Icon";
import { ReaderSettings, type ReadingWidth } from "../components/ReaderSettings";
import { ReferencePanel } from "../components/ReferencePanel";
import { ShortcutsDialog } from "../components/ShortcutsDialog";
import { StatusBadge } from "../components/StatusBadge";
import { useDocuments } from "../hooks/useDocuments";
import { useFocusTrap } from "../hooks/useFocusTrap";

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
  return "正在提取正文与来源位置，通常只需要片刻。";
}

function isInteractiveTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && Boolean(target.closest("button, a, input, select, textarea, [contenteditable='true']"));
}

export function ReaderPage() {
  const { documentId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const { documents } = useDocuments();
  const [isNavOpen, setIsNavOpen] = useState(false);
  const [isReferencesOpen, setIsReferencesOpen] = useState(
    () => window.matchMedia("(min-width: 1100px)").matches,
  );
  const [isShortcutsOpen, setIsShortcutsOpen] = useState(false);
  const [isDeleteOpen, setIsDeleteOpen] = useState(false);
  const [isQuestionsOpen, setIsQuestionsOpen] = useState(false);
  const [answerCitation, setAnswerCitation] = useState<Citation | null>(null);
  const [fontSize, setFontSize] = useState(storedFontSize);
  const [readingWidth, setReadingWidth] = useState<ReadingWidth>(storedReadingWidth);
  const navRef = useRef<HTMLElement>(null);
  const closeNav = useCallback(() => setIsNavOpen(false), []);
  const closeReferences = useCallback(() => setIsReferencesOpen(false), []);
  useFocusTrap(navRef, isNavOpen, closeNav);
  const requestedOrdinal = Number(searchParams.get("unit") ?? searchParams.get("page"));
  const currentOrdinal = Number.isInteger(requestedOrdinal) && requestedOrdinal > 0
    ? requestedOrdinal
    : 1;
  const documentQuery = useQuery({
    queryKey: ["documents", documentId],
    queryFn: () => getDocument(documentId),
    enabled: Boolean(documentId),
    refetchInterval: (query) => {
      const current = query.state.data;
      return current && isProcessing(current.status) ? 1800 : false;
    },
  });
  const contentQuery = useQuery({
    queryKey: ["documents", documentId, "content", currentOrdinal],
    queryFn: () => getDocumentContent(documentId, currentOrdinal),
    enabled: documentQuery.data?.status === "ready",
  });
  const retryMutation = useMutation({
    mutationFn: () => retryDocument(documentId),
    onSuccess: (nextDocument) => {
      queryClient.setQueryData(["documents", documentId], nextDocument);
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
  });
  const deleteMutation = useMutation({
    mutationFn: () => deleteDocument(documentId),
    onSuccess: async () => {
      queryClient.removeQueries({ queryKey: ["rag", documentId] });
      await queryClient.invalidateQueries({ queryKey: ["documents"] });
      navigate("/", { replace: true });
    },
  });
  const document = documentQuery.data ?? null;
  useEffect(() => { setAnswerCitation(null); setIsQuestionsOpen(false); }, [documentId]);
  const contents = contentQuery.data?.contents ?? [];
  const contentCount = document?.content_count ?? contentQuery.data?.content_count ?? 0;
  const error = documentQuery.error ?? contentQuery.error ?? retryMutation.error;
  const isLoading = documentQuery.isPending || (document?.status === "ready" && contentQuery.isPending);

  useEffect(() => {
    localStorage.setItem("review-agent-font-size", String(fontSize));
  }, [fontSize]);

  useEffect(() => {
    localStorage.setItem("review-agent-reading-width", readingWidth);
  }, [readingWidth]);

  useEffect(() => {
    if (contentCount === 0 || (!searchParams.has("unit") && !searchParams.has("page"))) return;
    const boundedOrdinal = Math.min(Math.max(currentOrdinal, 1), contentCount);
    if (boundedOrdinal !== currentOrdinal || searchParams.has("page")) {
      setSearchParams({ unit: String(boundedOrdinal) }, { replace: true });
      return;
    }
    window.requestAnimationFrame(() => {
      window.document.getElementById(`content-${boundedOrdinal}`)?.scrollIntoView({ block: "start" });
    });
  }, [contentCount, currentOrdinal, searchParams, setSearchParams]);

  const selectContent = useCallback((ordinal: number) => {
    const bounded = Math.min(Math.max(ordinal, 1), Math.max(contentCount, 1));
    setSearchParams({ unit: String(bounded) }, { replace: true });
    window.requestAnimationFrame(() => {
      const target = window.document.getElementById(`content-${bounded}`);
      target?.scrollIntoView({ behavior: "smooth", block: "start" });
      target?.focus({ preventScroll: true });
    });
  }, [contentCount, setSearchParams]);

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
        selectContent(currentOrdinal - 1);
      } else if (event.key === "]") {
        event.preventDefault();
        selectContent(currentOrdinal + 1);
      } else if (event.key === "Escape") {
        closeNav();
        closeReferences();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [closeNav, closeReferences, currentOrdinal, selectContent]);

  const selectedReference = useMemo(
    () => contents.find((content) => content.ordinal === currentOrdinal),
    [contents, currentOrdinal],
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
        {isNavOpen ? <button aria-label="关闭资料导航" className="scrim" onClick={closeNav} type="button" /> : null}
        <aside
          aria-label="资料导航"
          aria-modal={isNavOpen || undefined}
          className={`reader-nav${isNavOpen ? " reader-nav--open" : ""}`}
          ref={navRef}
          role={isNavOpen ? "dialog" : undefined}
        >
          <div className="reader-nav__header">
            <span className="eyebrow">资料库</span>
            <button aria-label="关闭资料导航" className="icon-button mobile-only" onClick={closeNav} type="button"><Icon name="close" /></button>
          </div>
          <Link className="reader-nav__back" to="/"><Icon name="chevronLeft" />返回全部资料</Link>
          <nav aria-label="资料列表" className="reader-nav__list">
            {documents.map((item) => (
              <Link
                aria-current={item.id === documentId ? "page" : undefined}
                className={item.id === documentId ? "reader-nav__item reader-nav__item--active" : "reader-nav__item"}
                key={item.id}
                onClick={closeNav}
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
                <span className="eyebrow">{contentCountLabel(document)} · {documentTypeLabel(document.media_type)}</span>
                <h1>{document.filename}</h1>
                <p>正文保留原始来源位置。使用 <kbd>[</kbd> 与 <kbd>]</kbd> 可以前后移动。</p>
                <button aria-expanded={isQuestionsOpen} className="button button--secondary" onClick={() => setIsQuestionsOpen((value) => !value)} type="button">{isQuestionsOpen ? "收起资料问答" : "基于此资料提问"}</button>
                <button className="button button--danger-quiet document-reader__delete" onClick={() => setIsDeleteOpen(true)} type="button">
                  删除资料
                </button>
              </header>
              {isQuestionsOpen ? <QuestionPanel documentId={documentId} filename={document.filename} key={documentId} onCitation={(citation) => { setAnswerCitation(citation); setIsReferencesOpen(true); }} /> : null}
              <div className="page-stack">
                {contents.map((content) => (
                  <section
                    aria-label={citationLabel(content.citation_locator)}
                    className={`reading-page${content.ordinal === currentOrdinal ? " reading-page--current" : ""}`}
                    id={`content-${content.ordinal}`}
                    key={content.ordinal}
                    onClick={() => setSearchParams({ unit: String(content.ordinal) }, { replace: true })}
                    tabIndex={-1}
                  >
                    <div className="reading-page__number">
                      <span>{citationLabel(content.citation_locator)}</span>
                      <span>SOURCE {String(content.ordinal).padStart(2, "0")}</span>
                    </div>
                    <div className="reading-page__content">
                      {content.content.split(/\n{2,}/).map((paragraph, index) => (
                        paragraph.trim() ? <p key={`${content.ordinal}-${index}`}>{paragraph.trim()}</p> : null
                      ))}
                    </div>
                  </section>
                ))}
              </div>
            </article>
          ) : null}
        </main>

        <ReferencePanel
          answerCitation={answerCitation}
          currentOrdinal={currentOrdinal}
          isOpen={isReferencesOpen}
          onClose={closeReferences}
          onSelect={(ordinal) => { selectContent(ordinal); if (window.innerWidth < 1100) setIsReferencesOpen(false); }}
          contentCount={contentCount}
          contents={contents}
          locations={contentQuery.data?.locations ?? []}
        />
      </div>

      {document?.status === "ready" && contentCount > 0 ? (
        <div aria-live="polite" className="reader-footer">
          <button aria-label="上一个来源" disabled={currentOrdinal <= 1} onClick={() => selectContent(currentOrdinal - 1)} type="button"><Icon name="chevronLeft" /></button>
          <span>{selectedReference ? citationLabel(selectedReference.citation_locator) : `来源 ${currentOrdinal}`} · {currentOrdinal} / {contentCount}</span>
          <button aria-label="下一个来源" disabled={currentOrdinal >= contentCount} onClick={() => selectContent(currentOrdinal + 1)} type="button"><Icon name="chevronRight" /></button>
          {selectedReference ? <span className="reader-footer__hint">已定位来源</span> : null}
        </div>
      ) : null}
      <ShortcutsDialog isOpen={isShortcutsOpen} onClose={() => setIsShortcutsOpen(false)} />
      {document ? (
        <DeleteDocumentDialog
          error={deleteMutation.error?.message ?? null}
          filename={document.filename}
          isDeleting={deleteMutation.isPending}
          isOpen={isDeleteOpen}
          onClose={() => { if (!deleteMutation.isPending) setIsDeleteOpen(false); }}
          onConfirm={() => deleteMutation.mutate()}
        />
      ) : null}
    </div>
  );
}
