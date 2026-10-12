import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { getDocument } from "../api/documents";
import { citationLabel } from "../citations";
import { useFocusTrap } from "../hooks/useFocusTrap";
import type { StudySection } from "./StudyAnswer";
import { Icon } from "./Icon";

const PdfPreview = lazy(() => import("./PdfPreview").then((module) => ({ default: module.PdfPreview })));
const pageKey = (source: StudySection["sources"][number]) =>
  `${source.citation.document_id}-${source.citation.version_id}-${source.citation.locator.kind}-${source.citation.unit}`;

export function StudySourcePanel({ section, mobileOpen, onClose }: {
  section?: StudySection; mobileOpen: boolean; onClose: () => void;
}) {
  const panel = useRef<HTMLElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const [compact, setCompact] = useState(() => window.matchMedia("(max-width: 1099px)").matches);
  const [selection, setSelection] = useState<{ section: string; page: string } | null>(null);
  const pages = Array.from(new Map(section?.sources.map((source) => [pageKey(source), source]) ?? []).values());
  const current = (selection?.section === section?.key ? pages.find((source) => pageKey(source) === selection?.page) : undefined) ?? pages[0];
  const source = current?.citation;
  const document = useQuery({ queryKey: ["documents", source?.document_id], queryFn: () => getDocument(source!.document_id!), enabled: Boolean(source?.document_id) });
  useFocusTrap(panel, compact && mobileOpen, onClose);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 1099px)");
    const update = () => setCompact(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  useEffect(() => { if (body.current) body.current.scrollTop = 0; }, [section?.key, source?.document_id, source?.unit]);
  const hidden = compact && !mobileOpen;
  return <aside aria-label="原文预览" aria-modal={compact && mobileOpen || undefined} className="study-source-panel" hidden={hidden} ref={panel} role={compact ? "dialog" : undefined}>
    <header className="study-source-panel__header">
      <div><h2>对应原文</h2><p>{section?.title ?? "选择一个知识点查看原文"}</p></div>
      {compact ? <button aria-label="关闭原文预览" className="icon-button" onClick={onClose} type="button"><Icon name="close" /></button> : null}
    </header>
    {pages.length > 1 ? <nav aria-label="关联原文页" className="study-source-panel__pages">{pages.map((item) =>
      <button aria-pressed={pageKey(item) === pageKey(current!)} key={pageKey(item)} onClick={() => setSelection({ section: section!.key, page: pageKey(item) })} type="button">
        {item.filename} · {citationLabel(item.citation.locator)}
      </button>)}</nav> : null}
    <div className="study-source-panel__body" ref={body}>
      {source ? <>
        <p className="study-source-panel__location">{current.filename} · {citationLabel(source.locator)}</p>
        {document.isPending && source.document_id ? <p role="status">正在打开原文…</p> : null}
        {!source.document_id ? <p role="status">这条来源没有可预览的原文件。</p> : null}
        {document.error ? <div role="alert"><p>原文暂时无法打开。</p><button className="button button--secondary" onClick={() => void document.refetch()} type="button">重试打开原文</button></div> : null}
        {document.data?.media_type === "application/pdf" && source.locator.kind === "page" && source.document_id ? <Suspense fallback={<p role="status">正在打开原文…</p>}>
          <PdfPreview documentId={source.document_id} page={source.unit} />
        </Suspense> : !document.isPending && !document.error && document.data ? <p role="status">这份旧资料暂不支持预览。</p> : null}
        <details className="study-source-panel__quote"><summary>引用摘录</summary>
          {section?.sources.filter((item) => pageKey(item) === pageKey(current)).map((item) => <blockquote key={item.citation.source_id}>{item.citation.quote}</blockquote>)}
        </details>
      </> : <p className="study-source-panel__empty">讲解生成后，在这里对照 PDF 原始页面。</p>}
    </div>
    {source?.document_id ? <Link className="study-source-panel__full-link" to={`/documents/${source.document_id}?unit=${source.unit}${source.version_id ? `&version=${source.version_id}` : ""}`}>在阅读页打开</Link> : null}
  </aside>;
}
