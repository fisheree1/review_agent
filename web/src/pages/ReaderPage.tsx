import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { type Citation } from "../api/rag";
import { type DocumentSummary, deleteDocument, getDocument, isProcessing, retryDocument } from "../api/documents";
import { AppHeader } from "../components/AppHeader";
import { DeleteDocumentDialog } from "../components/DeleteDocumentDialog";
import { ErrorState } from "../components/ErrorState";
import { Icon } from "../components/Icon";
import { QuickQuizAction } from "../components/QuickQuizAction";
import { QuestionPanel } from "../components/QuestionPanel";
import { ReferencePanel } from "../components/ReferencePanel";
import { StatusBadge } from "../components/StatusBadge";
import { useDocuments } from "../hooks/useDocuments";
import { useFocusTrap } from "../hooks/useFocusTrap";

const PdfPreview = lazy(() => import("../components/PdfPreview").then((module) => ({ default: module.PdfPreview })));

type SidePanel = "questions" | "references" | null;

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
  const [isCompact, setIsCompact] = useState(() => window.matchMedia("(max-width: 1099px)").matches);
  const [activePanel, setActivePanel] = useState<SidePanel>(null);
  const [hasOpenedQuestions, setHasOpenedQuestions] = useState(false);
  const [isDeleteOpen, setIsDeleteOpen] = useState(false);
  const [answerCitation, setAnswerCitation] = useState<Citation | null>(null);
  const [loadedPdfPages, setLoadedPdfPages] = useState<{ documentId: string; count: number } | null>(null);
  const navRef = useRef<HTMLElement>(null);
  const questionRef = useRef<HTMLElement>(null);
  const readingPositionRef = useRef<number | null>(null);
  const previousPanelRef = useRef<SidePanel>(null);
  const pendingPageRef = useRef<number | null>(null);
  const closeNav = useCallback(() => setIsNavOpen(false), []);
  useFocusTrap(navRef, isNavOpen, closeNav);

  const requestedPage = Number(searchParams.get("unit") ?? searchParams.get("page"));
  const currentPage = Number.isInteger(requestedPage) && requestedPage > 0 ? requestedPage : 1;
  const hasExplicitPage = searchParams.has("unit") || searchParams.has("page");
  const hasLegacyPage = searchParams.has("page") || searchParams.has("view");
  const requestedVersion = Number(searchParams.get("version"));
  const historicalVersion = Number.isInteger(requestedVersion) && requestedVersion > 0;
  const selectPageParams = useCallback((page: number) => {
    const next = new URLSearchParams(searchParams);
    next.set("unit", String(page));
    next.delete("page");
    next.delete("view");
    setSearchParams(next, { replace: true, preventScrollReset: true });
  }, [searchParams, setSearchParams]);

  const documentQuery = useQuery({
    queryKey: ["documents", documentId],
    queryFn: () => getDocument(documentId),
    enabled: Boolean(documentId),
    refetchInterval: (query) => {
      const current = query.state.data;
      return current && isProcessing(current.status) ? 1800 : false;
    },
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
  const isPdf = document?.media_type === "application/pdf";
  const pageCount = loadedPdfPages?.documentId === documentId
    ? loadedPdfPages.count
    : document?.page_count ?? 0;
  const onPageCount = useCallback((count: number) => {
    setLoadedPdfPages({ documentId, count });
  }, [documentId]);

  const selectPage = useCallback((page: number) => {
    const bounded = Math.min(Math.max(page, 1), Math.max(pageCount, 1));
    pendingPageRef.current = bounded;
    window.document.getElementById(`content-${bounded}`)?.scrollIntoView({ block: "start", behavior: "instant" });
    selectPageParams(bounded);
  }, [pageCount, selectPageParams]);
  const onVisiblePage = useCallback((page: number) => {
    if (pendingPageRef.current !== null) {
      if (page !== pendingPageRef.current) return;
      pendingPageRef.current = null;
    }
    if (page !== currentPage) selectPageParams(page);
  }, [currentPage, selectPageParams]);

  const closeQuestions = useCallback(() => {
    setActivePanel(previousPanelRef.current);
    setAnswerCitation(null);
    const position = readingPositionRef.current;
    readingPositionRef.current = null;
    pendingPageRef.current = null;
    if (position !== null) window.requestAnimationFrame(() => window.scrollTo(0, position));
  }, []);
  useFocusTrap(questionRef, isCompact && activePanel === "questions", closeQuestions);
  const openQuestions = useCallback(() => {
    if (!readingPositionRef.current) {
      previousPanelRef.current = activePanel;
      readingPositionRef.current = window.scrollY;
      setHasOpenedQuestions(true);
    }
    setActivePanel("questions");
  }, [activePanel]);
  const closeReferences = useCallback(() => setActivePanel(readingPositionRef.current !== null ? "questions" : null), []);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 1099px)");
    const update = () => setIsCompact(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    if (activePanel === "questions") questionRef.current?.querySelector("textarea")?.focus({ preventScroll: true });
  }, [activePanel]);
  useEffect(() => {
    setAnswerCitation(null);
    setActivePanel(null);
    setHasOpenedQuestions(false);
    readingPositionRef.current = null;
  }, [documentId]);

  useEffect(() => {
    if (!isPdf || pageCount === 0 || !hasExplicitPage) return;
    const bounded = Math.min(Math.max(currentPage, 1), pageCount);
    if (bounded !== currentPage || hasLegacyPage) selectPageParams(bounded);
  }, [currentPage, hasExplicitPage, hasLegacyPage, isPdf, pageCount, selectPageParams]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.defaultPrevented) return;
      if (event.key === "Escape") {
        if (activePanel === "references") closeReferences();
        else if (activePanel === "questions") closeQuestions();
        else closeNav();
        return;
      }
      if (isInteractiveTarget(event.target)) return;
      if (isPdf && event.key === "[") {
        event.preventDefault();
        selectPage(currentPage - 1);
      } else if (isPdf && event.key === "]") {
        event.preventDefault();
        selectPage(currentPage + 1);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [activePanel, closeNav, closeQuestions, closeReferences, currentPage, isPdf, selectPage]);

  return (
    <div className="reader-page">
      <AppHeader onMenu={() => setIsNavOpen(true)}>
        {document?.status === "ready" && !historicalVersion ? <button
          aria-controls={hasOpenedQuestions ? "reader-questions" : undefined}
          aria-expanded={activePanel === "questions"}
          aria-label={activePanel === "questions" ? "收起资料问答" : "打开资料问答"}
          className="icon-button"
          onClick={() => activePanel === "questions" ? closeQuestions() : openQuestions()}
          title="资料问答"
          type="button"
        ><Icon name="chat" /></button> : null}
      </AppHeader>

      <div className={`reader-shell${activePanel ? " reader-shell--with-sidebar" : ""}`}>
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
              ><Icon name="document" /><span>{item.filename}</span></Link>
            ))}
          </nav>
        </aside>

        <main className="reader-main" id="main-content" tabIndex={-1}>
          {documentQuery.isPending ? <div aria-label="正在打开资料" className="reader-loading" role="status"><div /><div /><div /></div>
            : documentQuery.error ? <ErrorState message={documentQuery.error.message} onRetry={() => void documentQuery.refetch()} />
              : document?.status === "deleting" ? <ErrorState title="资料正在删除" message="原文件暂时无法预览。" />
                : document ? <article className="document-reader document-reader--pdf">
                  <header className="document-reader__header">
                    <h1 title={document.filename}>{document.filename}</h1>
                    {isPdf && pageCount > 0 ? <span className="document-reader__page-count">{pageCount} 页</span> : null}
                    {isProcessing(document.status) ? <div role="status"><StatusBadge status={document.status} /></div> : null}
                    {document.status === "failed" ? <div className="document-reader__processing-error" role="alert">
                      <p>处理失败：{document.failure_message ?? "请重新处理资料。"}</p>
                      <button className="button button--secondary" disabled={retryMutation.isPending} onClick={() => retryMutation.mutate()} type="button">{retryMutation.isPending ? "正在提交…" : "重新处理"}</button>
                      {retryMutation.error ? <p>{retryMutation.error.message}</p> : null}
                    </div> : null}
                    <details className="document-reader__actions"><summary aria-label="资料操作" title="资料操作"><Icon name="menu" /></summary>
                      <div className="document-reader__action-menu">
                        {isPdf && document.status === "ready" && !historicalVersion ? <QuickQuizAction documentId={documentId} filename={document.filename} key={documentId} /> : null}
                        <button className="button button--danger-quiet" onClick={() => setIsDeleteOpen(true)} type="button">删除资料</button>
                      </div>
                    </details>
                  </header>
                  {isPdf ? <section aria-label="PDF 原文件" className="document-reader__preview">
                    <Suspense fallback={<p role="status">正在加载 PDF 预览…</p>}>
                      <PdfPreview continuous documentId={documentId} key={documentId} onPageChange={onVisiblePage} onPageCount={onPageCount} page={currentPage} />
                    </Suspense>
                  </section> : <section className="preview-unavailable" role="status">
                    <Icon name="document" />
                    <h2>暂不支持预览</h2>
                    <p>仅支持 PDF 预览。</p>
                  </section>}
                </article> : null}
        </main>

        {hasOpenedQuestions && document?.status === "ready" && !historicalVersion ? <aside
          aria-label="资料问答"
          aria-modal={isCompact && activePanel === "questions" || undefined}
          className={`question-sidebar${activePanel === "questions" ? " question-sidebar--open" : ""}`}
          hidden={activePanel !== "questions"}
          id="reader-questions"
          ref={questionRef}
          role={isCompact && activePanel === "questions" ? "dialog" : undefined}
        >
          <div className="question-sidebar__header"><span className="eyebrow">资料问答</span><button aria-label="收起资料问答" className="icon-button" onClick={closeQuestions} type="button"><Icon name="close" /></button></div>
          <QuestionPanel documentId={documentId} filename={document.filename} key={documentId} onCitation={(citation) => { setAnswerCitation(citation); setActivePanel("references"); }} />
        </aside> : null}

        {answerCitation ? <ReferencePanel
          answerCitation={answerCitation}
          isCompact={isCompact}
          isOpen={activePanel === "references"}
          onClose={closeReferences}
          onOpenCitation={(citation) => { selectPage(citation.unit); if (isCompact) setActivePanel(null); }}
        /> : null}
      </div>

      {isPdf && document && pageCount > 0 && document.status !== "deleting" ? <div aria-live="polite" className="reader-footer">
        <button aria-label="上一页" disabled={currentPage <= 1} onClick={() => selectPage(currentPage - 1)} title="上一页 ([)" type="button"><Icon name="chevronLeft" /></button>
        <span>第 {currentPage} / {pageCount} 页</span>
        <button aria-label="下一页" disabled={currentPage >= pageCount} onClick={() => selectPage(currentPage + 1)} title="下一页 (])" type="button"><Icon name="chevronRight" /></button>
      </div> : null}
      {document ? <DeleteDocumentDialog
        error={deleteMutation.error?.message ?? null}
        filename={document.filename}
        isDeleting={deleteMutation.isPending}
        isOpen={isDeleteOpen}
        onClose={() => { if (!deleteMutation.isPending) setIsDeleteOpen(false); }}
        onConfirm={() => deleteMutation.mutate()}
      /> : null}
    </div>
  );
}
