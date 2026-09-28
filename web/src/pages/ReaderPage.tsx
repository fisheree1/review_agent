import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { type Citation } from "../api/rag";
import { type DocumentSummary, deleteDocument, getDocument, isProcessing, retryDocument } from "../api/documents";
import { documentTypeLabel } from "../citations";
import { AppHeader } from "../components/AppHeader";
import { DeleteDocumentDialog } from "../components/DeleteDocumentDialog";
import { ErrorState } from "../components/ErrorState";
import { Icon } from "../components/Icon";
import { QuestionPanel } from "../components/QuestionPanel";
import { ReferencePanel } from "../components/ReferencePanel";
import { ShortcutsDialog } from "../components/ShortcutsDialog";
import { StatusBadge } from "../components/StatusBadge";
import { useDocuments } from "../hooks/useDocuments";
import { useFocusTrap } from "../hooks/useFocusTrap";

const PdfPreview = lazy(() => import("../components/PdfPreview").then((module) => ({ default: module.PdfPreview })));

type SidePanel = "questions" | "references" | null;

function processingMessage(status: DocumentSummary["status"]): string {
  if (status === "uploaded") return "文件已保存，后台正在准备解析。原文件可以先行预览。";
  if (status === "queued") return "资料已进入处理队列，原文件可以先行预览。";
  return "后台正在提取资料内容，原文件可以先行预览。";
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
  const [isCompact, setIsCompact] = useState(() => window.matchMedia("(max-width: 1099px)").matches);
  const [activePanel, setActivePanel] = useState<SidePanel>(null);
  const [hasOpenedQuestions, setHasOpenedQuestions] = useState(false);
  const [isShortcutsOpen, setIsShortcutsOpen] = useState(false);
  const [isDeleteOpen, setIsDeleteOpen] = useState(false);
  const [answerCitation, setAnswerCitation] = useState<Citation | null>(null);
  const [loadedPdfPages, setLoadedPdfPages] = useState<{ documentId: string; count: number } | null>(null);
  const navRef = useRef<HTMLElement>(null);
  const questionRef = useRef<HTMLElement>(null);
  const readingPositionRef = useRef<{ page: number; top: number } | null>(null);
  const pendingRestoreRef = useRef<{ page: number; top: number } | null>(null);
  const previousPanelRef = useRef<SidePanel>(null);
  const pendingFocusRef = useRef(false);
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
    setSearchParams(next, { replace: true });
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
    pendingFocusRef.current = true;
    selectPageParams(bounded);
    if (bounded === currentPage) {
      window.requestAnimationFrame(() => {
        const source = window.document.getElementById(`content-${bounded}`);
        source?.scrollIntoView({ block: "start", behavior: "smooth" });
        source?.focus({ preventScroll: true });
        pendingFocusRef.current = false;
      });
    }
  }, [currentPage, pageCount, selectPageParams]);

  const closeQuestions = useCallback(() => {
    setActivePanel(previousPanelRef.current);
    setAnswerCitation(null);
    const position = readingPositionRef.current;
    readingPositionRef.current = null;
    if (!position) return;
    pendingRestoreRef.current = position;
    if (currentPage !== position.page) selectPageParams(position.page);
    else window.requestAnimationFrame(() => {
      window.scrollTo(0, position.top);
      pendingRestoreRef.current = null;
    });
  }, [currentPage, selectPageParams]);
  useFocusTrap(questionRef, isCompact && activePanel === "questions", closeQuestions);
  const openQuestions = useCallback(() => {
    if (!readingPositionRef.current) {
      previousPanelRef.current = activePanel;
      readingPositionRef.current = { page: currentPage, top: window.scrollY };
      setHasOpenedQuestions(true);
    }
    setActivePanel("questions");
  }, [activePanel, currentPage]);
  const closeReferences = useCallback(() => setActivePanel(readingPositionRef.current ? "questions" : null), []);

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
    pendingRestoreRef.current = null;
  }, [documentId]);

  useEffect(() => {
    if (!isPdf || pageCount === 0 || !hasExplicitPage) return;
    const bounded = Math.min(Math.max(currentPage, 1), pageCount);
    if (bounded !== currentPage || hasLegacyPage) selectPageParams(bounded);
  }, [currentPage, hasExplicitPage, hasLegacyPage, isPdf, pageCount, selectPageParams]);

  useEffect(() => {
    if (!isPdf || !hasExplicitPage) return;
    window.requestAnimationFrame(() => {
      const restore = pendingRestoreRef.current;
      if (restore?.page === currentPage) {
        window.scrollTo(0, restore.top);
        pendingRestoreRef.current = null;
        pendingFocusRef.current = false;
        return;
      }
      const source = window.document.getElementById(`content-${currentPage}`);
      source?.scrollIntoView({ block: "start", behavior: pendingFocusRef.current ? "smooth" : "instant" });
      if (pendingFocusRef.current) source?.focus({ preventScroll: true });
      pendingFocusRef.current = false;
    });
  }, [currentPage, documentId, hasExplicitPage, isPdf]);

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
      if (event.key === "?") {
        event.preventDefault();
        setIsShortcutsOpen(true);
      } else if (isPdf && event.key === "[") {
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
        {isPdf ? <button aria-label="查看预览快捷键" className="icon-button" onClick={() => setIsShortcutsOpen(true)} title="预览快捷键" type="button"><Icon name="help" /></button> : null}
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
                    <Link className="text-link" to="/"><Icon name="chevronLeft" />资料库</Link>
                    <span className="eyebrow">{documentTypeLabel(document.media_type)}{isPdf && pageCount > 0 ? ` · ${pageCount} 页` : ""}</span>
                    <h1>{document.filename}</h1>
                    {historicalVersion ? <p role="status">正在查看该资料的原文件。引用所用的解析版本保留在链接中。</p> : null}
                    {isProcessing(document.status) ? <div role="status"><StatusBadge status={document.status} /><p>{processingMessage(document.status)}</p></div> : null}
                    {document.status === "failed" ? <div className="document-reader__processing-error" role="alert">
                      <p>后台处理失败：{document.failure_message ?? "请重新处理资料。"}原文件仍可预览。</p>
                      <button className="button button--secondary" disabled={retryMutation.isPending} onClick={() => retryMutation.mutate()} type="button">{retryMutation.isPending ? "正在提交…" : "重新处理"}</button>
                      {retryMutation.error ? <p>{retryMutation.error.message}</p> : null}
                    </div> : null}
                    {isPdf ? <p>使用底部导航或 <kbd>[</kbd> 与 <kbd>]</kbd> 切换 PDF 页面。</p> : null}
                    {document.status === "ready" && !historicalVersion ? <button aria-controls={hasOpenedQuestions ? "reader-questions" : undefined} aria-expanded={activePanel === "questions"} className="button button--secondary" onClick={() => activePanel === "questions" ? closeQuestions() : openQuestions()} type="button">{activePanel === "questions" ? "收起资料问答" : "基于此资料提问"}</button> : null}
                    <button className="button button--danger-quiet document-reader__delete" onClick={() => setIsDeleteOpen(true)} type="button">删除资料</button>
                  </header>
                  {isPdf ? <section aria-label={`第 ${currentPage} 页`} className="document-reader__preview" id={`content-${currentPage}`} tabIndex={-1}>
                    <Suspense fallback={<p role="status">正在加载 PDF 预览…</p>}>
                      <PdfPreview documentId={documentId} key={documentId} onPageCount={onPageCount} page={currentPage} />
                    </Suspense>
                  </section> : <section className="preview-unavailable" role="status">
                    <Icon name="document" />
                    <h2>暂不支持预览</h2>
                    <p>{documentTypeLabel(document.media_type)} 文件仍可用于后台处理、资料问答和练习。</p>
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
          onOpenCitation={(citation) => { selectPageParams(citation.unit); if (isCompact) setActivePanel(null); }}
        /> : null}
      </div>

      {isPdf && document && pageCount > 0 && document.status !== "deleting" ? <div aria-live="polite" className="reader-footer">
        <button aria-label="上一页" disabled={currentPage <= 1} onClick={() => selectPage(currentPage - 1)} type="button"><Icon name="chevronLeft" /></button>
        <span>第 {currentPage} / {pageCount} 页</span>
        <button aria-label="下一页" disabled={currentPage >= pageCount} onClick={() => selectPage(currentPage + 1)} type="button"><Icon name="chevronRight" /></button>
      </div> : null}
      <ShortcutsDialog isOpen={isShortcutsOpen} onClose={() => setIsShortcutsOpen(false)} />
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
