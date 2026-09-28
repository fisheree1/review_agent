import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { getDocument } from "../api/documents";
import type { Citation } from "../api/rag";
import { citationLabel } from "../citations";
import { useFocusTrap } from "../hooks/useFocusTrap";
import { Icon } from "./Icon";

const PdfPreview = lazy(() => import("./PdfPreview").then((module) => ({ default: module.PdfPreview })));

export function StudySourcePanel({ source, filename, onClose }: { source: Citation; filename: string; onClose: () => void }) {
  const panel = useRef<HTMLElement>(null);
  const [compact, setCompact] = useState(() => window.matchMedia("(max-width: 1299px)").matches);
  const document = useQuery({ queryKey: ["documents", source.document_id], queryFn: () => getDocument(source.document_id!), enabled: Boolean(source.document_id) });
  const close = useCallback(() => onClose(), [onClose]);
  useFocusTrap(panel, compact, close);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 1299px)");
    const update = () => setCompact(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return <aside aria-label="原文预览" aria-modal={compact || undefined} className="study-source-panel" ref={panel} role={compact ? "dialog" : undefined}>
    <header className="study-source-panel__header">
      <div><h2 title={filename}>{filename || "原文"}</h2><span>{citationLabel(source.locator)}</span></div>
      <button aria-label="关闭原文预览" className="icon-button" onClick={onClose} type="button"><Icon name="close" /></button>
    </header>
    <div className="study-source-panel__body">
      {document.isPending && source.document_id ? <p role="status">正在打开原文…</p> : null}
      {!source.document_id ? <p role="status">这条来源没有可预览的原文件。</p> : null}
      {document.error ? <p role="alert">原文暂时无法打开，请稍后重试。</p> : null}
      {document.data?.media_type === "application/pdf" && source.locator.kind === "page" && source.document_id ? <Suspense fallback={<p role="status">正在打开原文…</p>}>
        <PdfPreview documentId={source.document_id} key={`${source.document_id}-${source.unit}`} page={source.unit} />
      </Suspense> : !document.isPending && !document.error && document.data ? <p role="status">这份旧资料暂不支持预览。</p> : null}
      <details className="study-source-panel__quote"><summary>引用摘录</summary><blockquote>{source.quote}</blockquote></details>
    </div>
    {source.document_id ? <Link className="study-source-panel__full-link" to={`/documents/${source.document_id}?unit=${source.unit}${source.version_id ? `&version=${source.version_id}` : ""}`}>在阅读页打开</Link> : null}
  </aside>;
}
