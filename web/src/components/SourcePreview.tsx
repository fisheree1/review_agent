import { Link } from "react-router-dom";

import type { Citation } from "../api/rag";
import { citationLabel } from "../citations";

export function SourcePreview({ source, action = "查看原文" }: { source: Citation; action?: string }) {
  const label = citationLabel(source.locator);
  return <div className="source-evidence">
    {source.document_id ? <Link to={`/documents/${source.document_id}?unit=${source.unit}${source.version_id ? `&version=${source.version_id}` : ""}`}>{label} · {action}</Link> : <span>{label}</span>}
    <details className="source-preview">
      <summary aria-label={`${label} · 预览引用摘录`}>预览引用摘录</summary>
      <blockquote>{source.quote}</blockquote>
    </details>
  </div>;
}
